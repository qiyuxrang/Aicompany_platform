"""Rich-input contracts using synthetic files and real isolated native parsers."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.conf import settings

from portal.product_models import DocumentSource, DocumentTask, DocumentRevision
from portal.product_service import preserve_input_provenance, validate_input, ProductError
from portal.product_storage import parse_source_content, StorageError
from portal.product_intake import parser_environment, extraction_hash
from portal.product_worker import model_input
from .base import PortalTestCase, json_body
from .intake_fixtures import docx, xlsx, pdf, package, CT


class ProductIntakeTests(PortalTestCase):
    def setUp(self):
        self.storage = TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.owner = self.create_user('intake-owner', 'product')
        self.reviewer = self.create_user('intake-reviewer', 'product')
        self.other = self.create_user('intake-other', 'product')
        self.config = override_settings(PRODUCT_P1_ENABLED=True, PRODUCT_MODEL_CALLS_ALLOWED=False,
            PRODUCT_REVIEWER_IDS=(self.reviewer.pk,), PRODUCT_STORAGE_ROOT=Path(self.storage.name),
            PRODUCT_UPLOAD_MAX_BYTES=20 * 1024 * 1024)
        self.config.enable(); self.addCleanup(self.config.disable)
        self.client = Client(); self.login(self.client, self.owner)
        payload = {'project': '合成解析项目', 'requirements': '形成可追溯方案', 'background': '', 'conditions': [], 'items': []}
        response = self.client.post('/api/product/tasks/', json_body(title='合成解析项目', input=payload, reviewer_id=self.reviewer.pk), content_type='application/json', HTTP_IDEMPOTENCY_KEY='intake-task')
        self.assertEqual(response.status_code, 201, response.content)
        self.task = response.json()

    def upload(self, name, content):
        response = self.client.post(f"/api/product/tasks/{self.task['id']}/sources/", {'expected_version': self.task['version'], 'file': SimpleUploadedFile(name, content)})
        self.assertEqual(response.status_code, 201, response.content)
        self.task = response.json()['task']
        return response.json()['source_id']

    def detail(self, source_id, query=''):
        response = self.client.get(f'/api/product/sources/{source_id}/{query}')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def binding(self, source_id):
        source = self.detail(source_id)
        return {'expected_version': self.task['version'], 'source_sha256': source['sha256'], 'extraction_hash': source['summary']['extraction_hash']}

    def test_docx_native_table_locations_and_immutable_snapshot(self):
        source_id = self.upload('设计说明.docx', docx())
        source = self.detail(source_id)
        self.assertEqual(source['summary']['status'], 'completed')
        self.assertEqual(source['summary']['method'], 'native')
        self.assertEqual(source['summary']['item_count'], 2)
        self.assertEqual(self.task['input']['items'][0]['source_location']['table'], 1)
        self.assertEqual(self.task['input']['items'][0]['source_id'], source_id)
        self.assertIn('项目建设背景', self.task['input']['source_materials'][0]['text'])
        self.assertEqual(self.task['input']['background'], '')
        self.assertEqual(DocumentRevision.objects.filter(task_id=self.task['id'], kind='extraction').count(), 1)
        self.assertNotIn('blocks', self.task['sources'][0]['parsed'])

    def test_xlsx_formula_not_promoted_and_locations_disambiguate_same_row_id(self):
        source_id = self.upload('设备.xlsx', xlsx(formula=True, merged=True))
        source = self.detail(source_id)
        self.assertEqual(source['summary']['status'], 'needs_review')
        items = self.task['input']['items']
        self.assertEqual(items[1]['quantity'], '')
        self.assertEqual(items[0]['row_id'], items[2]['row_id'])
        self.assertNotEqual(items[0]['source_item_id'], items[2]['source_item_id'])
        self.assertEqual(items[2]['source_location']['sheet'], '第二清单')
        self.assertIn('formula_unverified', {item['code'] for item in source['warnings']})
        self.assertTrue(any('=1+1' in block['text'] for block in source['blocks']))

    def test_hidden_worksheet_is_not_silently_promoted(self):
        source_id = self.upload('hidden.xlsx', xlsx(hidden=True))
        self.assertEqual(self.detail(source_id)['summary']['status'], 'partial')
        self.assertEqual(len(self.task['input']['items']), 2)

    def test_pdf_native_text_and_private_preview(self):
        source_id = self.upload('text.pdf', pdf())
        data = self.detail(source_id)
        self.assertEqual(data['summary']['method'], 'native')
        self.assertEqual(data['blocks'][0]['location']['page'], 1)
        image = self.client.get(f'/api/product/sources/{source_id}/preview/?page=1')
        self.assertEqual(image.status_code, 200, image.content[:200])
        self.assertTrue(image.content.startswith(b'\x89PNG'))
        self.assertIn('no-store', image['Cache-Control'])
        self.assertEqual(self.client.get(f'/api/product/sources/{source_id}/preview/?page=101').status_code, 400)

    def test_correction_retains_raw_file_and_historical_extraction(self):
        source_id = self.upload('text.pdf', pdf())
        before = self.detail(source_id)
        raw_hash = before['sha256']
        response = self.client.patch(f'/api/product/sources/{source_id}/correction/', json_body(**self.binding(source_id), block_id='b1', text='已对照原件校正的项目说明', reason='人工核对'), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.task = response.json()['task']
        current = self.detail(source_id)
        history = self.detail(source_id, '?revision=1')
        self.assertEqual(current['sha256'], raw_hash)
        self.assertIn('校正的项目说明', current['blocks'][0]['text'])
        self.assertIn('Native PDF', history['blocks'][0]['text'])
        self.assertFalse(history['can_correct']); self.assertFalse(history['can_reparse'])
        self.assertEqual(len(current['revisions']), 2)
        self.assertIn('校正的项目说明', self.task['input']['source_materials'][0]['text'])
        self.assertEqual(self.task['blueprint_version'], 0)

    def test_table_cell_correction_rebuilds_only_its_source_rows(self):
        doc_id = self.upload('doc.docx', docx())
        sheet_id = self.upload('sheet.xlsx', xlsx())
        data = self.detail(doc_id)
        row = next(block for block in data['blocks'] if block.get('cells', [None])[0] == '001')
        cells = ['001', '配电柜', '5', '台']
        response = self.client.patch(f'/api/product/sources/{doc_id}/correction/', json_body(**self.binding(doc_id), block_id=row['id'], text='\t'.join(cells), cells=cells, reason='核对设备数量'), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        items = response.json()['task']['input']['items']
        self.assertEqual(next(item['quantity'] for item in items if item['source_id'] == doc_id and item['row_id'] == '001'), '5')
        self.assertEqual(len([item for item in items if item['source_id'] == sheet_id]), 3)

    def test_reparse_version_checks_and_no_duplicate_source_contributions(self):
        source_id = self.upload('doc.docx', docx())
        binding = self.binding(source_id)
        response = self.client.post(f'/api/product/sources/{source_id}/reparse/', json_body(**binding), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.task = response.json()['task']
        self.assertEqual(len(self.task['input']['items']), 2)
        self.assertEqual(len(self.task['input']['source_materials']), 1)
        replay = self.client.post(f'/api/product/sources/{source_id}/reparse/', json_body(**binding), content_type='application/json')
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(DocumentSource.objects.count(), 1)

    def test_unassigned_user_and_reviewer_cannot_change_source(self):
        source_id = self.upload('doc.docx', docx())
        binding = self.binding(source_id)
        for user in (self.reviewer, self.other):
            self.login(self.client, user)
            response = self.client.post(f'/api/product/sources/{source_id}/reparse/', json_body(**binding), content_type='application/json')
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(f'/api/product/sources/{source_id}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/product/sources/{source_id}/preview/').status_code, 404)

    def test_source_material_scope_follows_blueprint_source_selection(self):
        first = self.upload('one.docx', docx('第一来源唯一内容', table=False))
        second = self.upload('two.docx', docx('第二来源保密内容', table=False))
        task = DocumentTask.objects.get(pk=self.task['id'])
        context = model_input(task, self.task['input'], [first])
        self.assertIn('第一来源唯一内容', context['background'])
        self.assertNotIn('第二来源保密内容', str(context))
        self.assertNotIn(second, str(context))

    def test_malformed_active_and_traversal_archives_are_rejected(self):
        cases = [('fake.pdf', b'not pdf'), ('fake.docx', b'PKbroken'), ('macro.docx', docx(extras={'word/vbaProject.bin': b'fake'})),
                 ('path.docx', docx(extras={'../escape.xml': '<a/>'})),
                 ('entity.docx', docx(extras={'word/evil.xml': '<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///secrets">]><foo>&x;</foo>'}))]
        for name, content in cases:
            with self.subTest(name=name):
                response = self.client.post(f"/api/product/tasks/{self.task['id']}/sources/", {'expected_version': self.task['version'], 'file': SimpleUploadedFile(name, content)})
                self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(DocumentSource.objects.exists())
        self.assertFalse(any(Path(self.storage.name).rglob('*.docx')))

    def test_parse_permission_is_rechecked_after_slow_work(self):
        original = parse_source_content
        from portal.product_storage import parse_upload
        def revoke(upload):
            result = parse_upload(upload)
            self.owner.roles.clear()
            return result
        with patch('portal.product_service.parse_upload', side_effect=revoke):
            response = self.client.post(f"/api/product/tasks/{self.task['id']}/sources/", {'expected_version': self.task['version'], 'file': SimpleUploadedFile('a.docx', docx())})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(DocumentSource.objects.exists())

    def test_parser_does_not_inherit_business_secrets(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'not-a-real-key', 'PORTAL_DB_PASSWORD': 'synthetic', 'PORTAL_SECRET_KEY': 'synthetic'}):
            env = parser_environment()
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('PORTAL_DB_PASSWORD', env)
            self.assertNotIn('PORTAL_SECRET_KEY', env)

    def test_keyed_rows_do_not_steal_another_source_after_remove_insert(self):
        self.upload('sheet.xlsx', xlsx())
        previous = self.task['input']
        editable = {key: previous[key] for key in ('project', 'requirements', 'background', 'conditions')}
        selected = previous['items'][2]
        editable['items'] = [{key: selected[key] for key in ('row_id', 'name', 'quantity', 'unit', 'source_item_id')}, {'row_id': 'manual', 'name': '人工设备', 'quantity': '1', 'unit': '台'}]
        updated = preserve_input_provenance(previous, validate_input(editable))
        self.assertEqual(updated['items'][0]['source_location']['sheet'], '第二清单')
        self.assertNotIn('source_id', updated['items'][1])

    def test_history_diffs_do_not_disclose_revoked_input_or_extraction(self):
        source_id = self.upload('doc.docx', docx('已撤销资料中的敏感文字'))
        old_version = self.task['input_version']
        body = {**self.binding(source_id), 'block_id': 'b1', 'text': '新的普通文字', 'reason': '替换内容'}
        response = self.client.patch(f'/api/product/sources/{source_id}/correction/', json_body(**body), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.task = response.json()['task']
        with patch('portal.product_history.input_authorized', side_effect=lambda task, revision: revision.version != old_version):
            history = self.client.get(f"/api/product/tasks/{self.task['id']}/history/")
        self.assertEqual(history.status_code, 200, history.content)
        self.assertNotIn('已撤销资料中的敏感文字', history.content.decode())
        with patch('portal.product_source_api.input_authorized', return_value=False):
            self.assertEqual(self.client.get(f'/api/product/sources/{source_id}/?revision=1').status_code, 404)

    def test_reparse_refuses_to_overwrite_manually_edited_equipment(self):
        source_id = self.upload('sheet.xlsx', xlsx())
        previous = self.task['input']
        editable = {key: previous[key] for key in ('project', 'requirements', 'background', 'conditions')}
        editable['items'] = [{key: item[key] for key in ('row_id', 'name', 'quantity', 'unit', 'source_item_id')} for item in previous['items']]
        editable['items'][0]['quantity'] = '8'
        updated = self.client.patch(f"/api/product/tasks/{self.task['id']}/", json_body(expected_version=self.task['version'], input=editable), content_type='application/json')
        self.assertEqual(updated.status_code, 200, updated.content); self.task = updated.json()
        response = self.client.post(f'/api/product/sources/{source_id}/reparse/', json_body(**self.binding(source_id)), content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'source_manually_edited')

    def test_large_legacy_text_cannot_bypass_project_memory_limits(self):
        response = self.client.post(f"/api/product/tasks/{self.task['id']}/sources/", {'expected_version': self.task['version'], 'file': SimpleUploadedFile('large.txt', b'x' * 50001)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'background_limit')
        self.assertFalse(DocumentSource.objects.exists())
        self.assertFalse(any(Path(self.storage.name).rglob('*.txt')))

    def test_unavailable_parser_and_timeout_are_explicit(self):
        with override_settings(PRODUCT_PARSER_PYTHON=Path(self.storage.name) / 'missing.exe'):
            with self.assertRaises(StorageError) as caught: parse_source_content('test.pdf', pdf())
            self.assertEqual(caught.exception.code, 'parser_unavailable')
        import subprocess
        with patch('portal.product_intake.subprocess.run', side_effect=subprocess.TimeoutExpired('parser', 1)):
            with self.assertRaises(StorageError) as caught: parse_source_content('test.pdf', pdf())
            self.assertEqual(caught.exception.code, 'parse_timeout')
        self.assertEqual(list((Path(self.storage.name) / '.parsing').iterdir()), [])
