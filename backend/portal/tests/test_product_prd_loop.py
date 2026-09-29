import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from portal.product_models import DocumentTask
from portal.product_worker import run_once
from .base import PortalTestCase, json_body


@override_settings(PRODUCT_P1_ENABLED=True, PRODUCT_MODEL_CALLS_ALLOWED=True)
class ProductPrdLoopTests(PortalTestCase):
    def setUp(self):
        storage = TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        config = override_settings(PRODUCT_STORAGE_ROOT=Path(storage.name))
        config.enable()
        self.addCleanup(config.disable)
        self.owner = self.create_user('prd-product-owner', 'product')
        self.login(self.client, self.owner)
        self.input = {'project': '合成项目', 'requirements': '提升可靠性', 'background': '', 'items': [], 'conditions': []}
        self.blueprint = {'purpose': '提升可靠性', 'audience': '项目评审人员',
            'chapters': [{'id': 'c1', 'title': '项目建设', 'scope': '使用项目资料', 'source_ids': ['1']}],
            'conditions': [], 'missing': [], 'conflicts': [], 'template_version': 'frozen-original-v1'}

    def create(self, mode='equipment_background'):
        response = self.client.post('/api/product/tasks/', json_body(title='合成项目', input=self.input,
            intake_mode=mode), content_type='application/json', HTTP_IDEMPOTENCY_KEY='prd-create')
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def upload(self, task, purpose, text=None):
        equipment = purpose == 'equipment'
        content = text or ('row_id,name,quantity,unit\n1,设备A,2,台\n' if equipment else '甲方调研：改善现有设备的可靠性。')
        return self.client.post(f"/api/product/tasks/{task['id']}/sources/", {
            'expected_version': task['version'], 'purpose': purpose,
            'file': SimpleUploadedFile('清单.csv' if equipment else '调研.txt', content.encode('utf-8'))})

    def prepared(self):
        task = self.create()
        for purpose in ('equipment', 'background'):
            response = self.upload(task, purpose)
            self.assertEqual(response.status_code, 201, response.content)
            task = response.json()['task']
        return task

    def action(self, task, endpoint, **body):
        return self.client.post(f"/api/product/tasks/{task['id']}/{endpoint}/",
            json_body(expected_version=task['version'], **body), content_type='application/json')

    def detail(self, task):
        return self.client.get(f"/api/product/tasks/{task['id']}/").json()

    def test_new_project_requires_equipment_and_background_before_queue(self):
        task = self.create()
        denied = self.action(task, 'queue', action='blueprint')
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(denied.json()['code'], 'equipment_source_required')
        response = self.upload(task, 'equipment')
        self.assertEqual(response.status_code, 201, response.content)
        task = response.json()['task']
        denied = self.action(task, 'queue', action='blueprint')
        self.assertEqual(denied.json()['code'], 'background_source_required')
        uploaded = self.upload(task, 'background')
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        task = uploaded.json()['task']
        self.assertEqual(self.action(task, 'queue', action='blueprint').status_code, 200)

    def test_equipment_is_singleton_and_roles_are_persisted(self):
        task = self.prepared()
        self.assertEqual([source['purpose'] for source in task['sources']], ['equipment', 'background'])
        duplicate = self.upload(task, 'equipment')
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()['code'], 'equipment_source_exists')
        self.assertEqual(DocumentTask.objects.get(pk=task['id']).sources.count(), 2)
        invalid = self.upload(task, 'nonsense')
        self.assertEqual(invalid.status_code, 400)

    def test_background_tables_do_not_silently_add_equipment_rows(self):
        task = self.prepared()
        response = self.client.post(f"/api/product/tasks/{task['id']}/sources/", {
            'expected_version': task['version'], 'purpose': 'background',
            'file': SimpleUploadedFile('参考表.csv', b'row_id,name,quantity,unit\n99,Other,30,unit\n')})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(len(response.json()['task']['input']['items']), 1)
        self.assertEqual(response.json()['task']['input']['items'][0]['name'], '设备A')

    @patch('portal.product_worker._model')
    def test_actual_phases_and_feedback_are_used_in_next_blueprint_version(self, model):
        task = self.prepared()
        observed = []
        def generate(task_id, fence, attempt, route, payload):
            current = DocumentTask.objects.get(pk=task_id)
            observed.append(copy.deepcopy(current.checkpoint['analysis_progress']))
            return copy.deepcopy(self.blueprint)
        model.side_effect = generate
        self.assertEqual(self.action(task, 'queue', action='blueprint').status_code, 200)
        self.assertTrue(run_once())
        task = self.detail(task)
        self.assertEqual(task['state'], 'WAITING_REVIEW', task)
        self.assertEqual([observed[0][key]['status'] for key in ('documents', 'equipment', 'blueprint')],
            ['completed', 'completed', 'running'])
        self.assertEqual(task['analysis_progress']['equipment']['item_count'], 1)
        self.assertTrue(all(task['analysis_progress'][key]['status'] == 'completed' for key in ('documents', 'equipment', 'blueprint')))
        self.assertEqual(task['analysis_progress']['knowledge']['status'], 'skipped')
        self.assertEqual(task['analysis_progress']['web_search']['status'], 'skipped')
        old = task['blueprint']
        revise = self.action(task, 'decisions', target='blueprint', target_id=old['id'], sha256=old['sha256'],
            decision='revise', comment='增加分阶段实施范围，设备数量保持不变。')
        self.assertEqual(revise.status_code, 201, revise.content)
        queued = revise.json()['task']
        self.assertEqual((queued['state'], queued['pending_action']), ('QUEUED', 'blueprint'))
        replay = self.action(task, 'decisions', target='blueprint', target_id=old['id'], sha256=old['sha256'],
            decision='revise', comment='增加分阶段实施范围，设备数量保持不变。')
        self.assertEqual(replay.status_code, 200)
        self.blueprint['chapters'][0]['scope'] = '分阶段实施，保留设备数量'
        self.assertTrue(run_once())
        revised = self.detail(task)
        self.assertEqual(revised['state'], 'WAITING_REVIEW', revised)
        self.assertEqual(revised['blueprint']['version'], old['version'] + 1)
        outbound = model.call_args.args[4]
        self.assertEqual(outbound['previous_blueprint'], old['payload'])
        self.assertEqual(outbound['revision_request']['target_id'], old['id'])
        self.assertIn('分阶段实施', outbound['revision_request']['comment'])
        stale = self.action(revised, 'decisions', target='blueprint', target_id=old['id'], sha256=old['sha256'],
            decision='approve', comment='过期批准')
        self.assertIn(stale.status_code, (404, 409))
        approved = self.action(revised, 'decisions', target='blueprint', target_id=revised['blueprint']['id'],
            sha256=revised['blueprint']['sha256'], decision='approve', comment='已核对当前版本')
        self.assertEqual(approved.status_code, 201, approved.content)
        self.assertEqual(approved.json()['task']['pending_action'], 'generate_outputs')

    @patch('portal.product_worker._model')
    def test_failed_equipment_analysis_does_not_call_model(self, model):
        task = self.create()
        task = self.upload(task, 'equipment', 'row_id,name,quantity,unit\n').json()['task']
        task = self.upload(task, 'background').json()['task']
        self.assertEqual(self.action(task, 'queue', action='blueprint').status_code, 200)
        run_once()
        current = self.detail(task)
        model.assert_not_called()
        self.assertEqual(current['error_code'], 'equipment_analysis_required')
        self.assertEqual(current['analysis_progress']['equipment']['status'], 'failed')

    @patch('portal.product_worker._model')
    def test_revision_respects_model_permission_and_other_owner_cannot_submit(self, model):
        task = self.prepared()
        model.return_value = self.blueprint
        self.action(task, 'queue', action='blueprint')
        run_once()
        task = self.detail(task)
        blueprint = task['blueprint']
        other = self.create_user('prd-product-other', 'product')
        client = Client()
        self.login(client, other)
        body = dict(expected_version=task['version'], target='blueprint', target_id=blueprint['id'],
            sha256=blueprint['sha256'], decision='revise', comment='调整范围')
        self.assertEqual(client.post(f"/api/product/tasks/{task['id']}/decisions/", json_body(**body),
            content_type='application/json').status_code, 404)
        with self.settings(PRODUCT_MODEL_CALLS_ALLOWED=False):
            response = self.client.post(f"/api/product/tasks/{task['id']}/decisions/", json_body(**body),
                content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['task']['state'], 'WAITING_INPUT')
        self.assertEqual(response.json()['task']['error_code'], 'model_authorization_required')

    @patch('portal.product_worker._model')
    def test_blueprint_revision_is_limited_to_three_rounds_and_approval_remains_available(self, model):
        model.side_effect = lambda *args: copy.deepcopy(self.blueprint)
        task = self.prepared()
        self.assertEqual(self.action(task, 'queue', action='blueprint').status_code, 200)
        self.assertTrue(run_once())
        task = self.detail(task)
        for expected_count in range(1, 4):
            current = task['blueprint']
            response = self.action(task, 'decisions', target='blueprint', target_id=current['id'],
                sha256=current['sha256'], decision='revise', comment=f'第{expected_count}次修改意见')
            self.assertEqual(response.status_code, 201, response.content)
            self.assertTrue(run_once())
            task = self.detail(task)
            self.assertEqual(task['blueprint_review'], {
                'revision_count': expected_count,
                'revision_limit': 3,
                'revisions_remaining': 3 - expected_count,
            })
        current = task['blueprint']
        denied = self.action(task, 'decisions', target='blueprint', target_id=current['id'],
            sha256=current['sha256'], decision='revise', comment='第四次修改意见')
        self.assertEqual(denied.status_code, 409, denied.content)
        self.assertEqual(denied.json()['code'], 'blueprint_revision_limit_reached')
        approved = self.action(task, 'decisions', target='blueprint', target_id=current['id'],
            sha256=current['sha256'], decision='approve', comment='达到修改上限后批准当前版本')
        self.assertEqual(approved.status_code, 201, approved.content)
        self.assertEqual(approved.json()['task']['pending_action'], 'generate_outputs')

    @override_settings(PRODUCT_BLUEPRINT_KNOWLEDGE_MODE='ragflow_required')
    def test_formal_mode_fails_closed_until_ragflow_snapshot_exists(self):
        task = self.prepared()
        detail = self.detail(task)
        self.assertEqual(detail['blueprint_knowledge']['status'], 'waiting_for_ragflow')
        denied = self.action(task, 'queue', action='blueprint')
        self.assertEqual(denied.status_code, 409, denied.content)
        self.assertEqual(denied.json()['code'], 'ragflow_required')
        self.assertEqual(DocumentTask.objects.get(pk=task['id']).state, 'DRAFT')
