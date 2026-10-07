import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from portal.business_models import BusinessLedgerGrant, BusinessLedgerRevision, BusinessLedgerWorkbook
from portal.models import AuditEvent

from .base import PortalTestCase


ENGINEERING_RECORD = {
    'project_id': 'P-001',
    'project_name': '园区智能化项目',
    'status': '实施中',
    'owner': '李工',
    'planned_end': '2026-09-01',
    'progress': '62.5',
}


class BusinessLedgerWorkflowTests(PortalTestCase):
    def setUp(self):
        self.editor = self.create_user('ledger-editor', 'engineering')
        self.submitter = self.create_user('ledger-submitter', 'engineering')
        self.publisher = self.create_user('ledger-publisher', 'engineering')
        self.manager = self.create_user('ledger-manager', 'general_manager')
        BusinessLedgerGrant.objects.create(user=self.editor, department='engineering', can_edit=True)
        BusinessLedgerGrant.objects.create(user=self.submitter, department='engineering', can_submit=True)
        BusinessLedgerGrant.objects.create(user=self.publisher, department='engineering', can_publish=True)
        self.editor_client, self.submitter_client = Client(), Client()
        self.publisher_client, self.manager_client = Client(), Client()
        for client, user in ((self.editor_client, self.editor), (self.submitter_client, self.submitter),
                             (self.publisher_client, self.publisher), (self.manager_client, self.manager)):
            self.login(client, user)

    def post_json(self, client, url, **body):
        return client.post(url, json.dumps(body), content_type='application/json')

    def test_explicit_permissions_and_admin_has_no_implicit_body_access(self):
        result = self.editor_client.get('/api/business/ledgers/permissions/').json()['departments']
        self.assertEqual(result, [{
            'department': 'engineering', 'title': '工程部看板',
            'can_edit': True, 'can_submit': False, 'can_publish': False,
        }])
        self.assertEqual(self.editor_client.get('/api/business/ledgers/finance/').status_code, 403)
        self.assertEqual(self.editor_client.get('/api/modules/business/').status_code, 200)
        self.assertIn('business', [item['code'] for item in self.editor_client.get('/api/modules/').json()])
        admin = self.create_admin()
        client = Client()
        self.login(client, admin, password='Admin!Pass9274-Qx')
        self.assertEqual(client.get('/api/business/ledgers/engineering/').status_code, 403)
        self.assertEqual(client.get('/api/business/boards/engineering/').status_code, 403)

    def test_platform_admin_can_grant_without_receiving_business_content_access(self):
        admin = self.create_admin('ledger-grant-admin')
        client = Client()
        self.login(client, admin, password='Admin!Pass9274-Qx')
        response = client.post('/admin/portal/businessledgergrant/add/', {
            'user': self.editor.pk,
            'department': 'finance',
            'can_edit': 'on',
            '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302, response.content)
        self.assertTrue(BusinessLedgerGrant.objects.filter(
            user=self.editor, department='finance', can_edit=True,
        ).exists())
        event = AuditEvent.objects.get(actor=admin, action='businessledgergrant_create')
        self.assertEqual(set(event.changes), {'user', 'department', 'can_edit'})
        self.assertEqual(client.get('/api/business/ledgers/finance/').status_code, 403)

    def test_crud_submit_return_publish_and_manager_reads_only_published(self):
        create = self.post_json(
            self.editor_client, '/api/business/ledgers/engineering/records/',
            expected_revision=0, record=ENGINEERING_RECORD,
        )
        self.assertEqual(create.status_code, 201, create.content)
        self.assertEqual(create.json()['revision'], 1)
        self.assertEqual(create.json()['state'], 'draft')
        self.assertFalse(self.manager_client.get('/api/business/boards/engineering/').json()['available'])

        updated_record = {**ENGINEERING_RECORD, 'progress': '75', 'project_id': 'P-002'}
        update = self.submitter_client.put(
            '/api/business/ledgers/engineering/records/P-001/',
            json.dumps({'expected_revision': 1, 'record': updated_record}), content_type='application/json',
        )
        self.assertEqual(update.status_code, 403)
        update = self.editor_client.put(
            '/api/business/ledgers/engineering/records/P-001/',
            json.dumps({'expected_revision': 1, 'record': updated_record}), content_type='application/json',
        )
        self.assertEqual(update.status_code, 200, update.content)
        self.assertEqual(update.json()['records'][0]['project_id'], 'P-002')

        submit = self.post_json(
            self.submitter_client, '/api/business/ledgers/engineering/submit/', expected_revision=2,
        )
        self.assertEqual(submit.status_code, 200, submit.content)
        self.assertEqual(submit.json()['state'], 'submitted')
        blocked = self.post_json(
            self.editor_client, '/api/business/ledgers/engineering/records/',
            expected_revision=3, record={**ENGINEERING_RECORD, 'project_id': 'P-003'},
        )
        self.assertEqual(blocked.status_code, 400)

        returned = self.post_json(
            self.publisher_client, '/api/business/ledgers/engineering/return/',
            expected_revision=3, reason='请补充项目进度依据。',
        )
        self.assertEqual(returned.status_code, 200, returned.content)
        self.assertEqual(returned.json()['state'], 'draft')
        self.assertEqual(returned.json()['last_return_reason'], '请补充项目进度依据。')

        submit = self.post_json(
            self.submitter_client, '/api/business/ledgers/engineering/submit/', expected_revision=4,
        )
        self.assertEqual(submit.status_code, 200, submit.content)
        published = self.post_json(
            self.publisher_client, '/api/business/ledgers/engineering/publish/', expected_revision=5,
        )
        self.assertEqual(published.status_code, 200, published.content)
        self.assertEqual(published.json()['state'], 'published')

        board = self.manager_client.get('/api/business/boards/engineering/').json()
        self.assertTrue(board['available'])
        self.assertEqual(board['records'][0]['project_id'], 'P-002')
        self.assertEqual(board['source']['kind'], 'department_published')
        self.assertEqual(board['source']['revision'], 6)
        self.assertEqual(BusinessLedgerRevision.objects.count(), 6)
        self.assertEqual(AuditEvent.objects.filter(action__startswith='business_ledger_').count(), 6)

    def test_product_presales_edits_are_visible_to_manager_after_publishing(self):
        product_user = self.create_user('presales-editor', 'product')
        BusinessLedgerGrant.objects.create(user=product_user, department='presales',
                                           can_edit=True, can_submit=True, can_publish=True)
        product_client = Client()
        self.login(product_client, product_user)
        record = {
            'project_id': 'OP-001', 'project_name': '能源项目', 'status': '方案编制',
            'owner': '产品人员', 'amount': '100000.00', 'follow_up_date': '2026-09-28',
            'project_type': '技改', 'project_progress': '方案已沟通', 'description': '客户需求待核实',
            'client_contact': '甲方代表', 'maturity': '重点',
        }
        created = self.post_json(product_client, '/api/business/ledgers/presales/records/',
                                 expected_revision=0, record=record)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()['state'], 'draft')
        self.assertEqual(product_client.get('/api/business/boards/presales/').status_code, 403)

        board = self.manager_client.get('/api/business/boards/presales/').json()
        self.assertFalse(board['available'])
        self.assertFalse(self.manager_client.get('/api/business/boards/engineering/').json()['available'])

        updated = product_client.put('/api/business/ledgers/presales/records/OP-001/',
                                     json.dumps({'expected_revision': 1, 'record': {
                                         **record, 'project_progress': '已更新方案', 'maturity': '方案确认',
                                     }}), content_type='application/json')
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertFalse(self.manager_client.get('/api/business/boards/presales/').json()['available'])
        self.post_json(product_client, '/api/business/ledgers/presales/submit/', expected_revision=2)
        self.post_json(product_client, '/api/business/ledgers/presales/publish/', expected_revision=3)
        latest = self.manager_client.get('/api/business/boards/presales/').json()
        self.assertEqual(latest['records'][0]['project_progress'], '已更新方案')
        self.assertEqual(latest['source']['revision'], 4)

        BusinessLedgerGrant.objects.filter(user=product_user, department='presales').delete()
        self.assertEqual(product_client.get('/api/business/ledgers/presales/').status_code, 403)

    def test_optimistic_concurrency_duplicate_validation_and_version_visibility(self):
        first = self.post_json(
            self.editor_client, '/api/business/ledgers/engineering/records/',
            expected_revision=0, record=ENGINEERING_RECORD,
        )
        self.assertEqual(first.status_code, 201)
        stale = self.post_json(
            self.editor_client, '/api/business/ledgers/engineering/records/',
            expected_revision=0, record={**ENGINEERING_RECORD, 'project_id': 'P-002'},
        )
        self.assertEqual(stale.status_code, 409)
        duplicate = self.post_json(
            self.editor_client, '/api/business/ledgers/engineering/records/',
            expected_revision=1, record=ENGINEERING_RECORD,
        )
        self.assertEqual(duplicate.status_code, 400)

        versions = self.editor_client.get('/api/business/ledgers/engineering/versions/').json()['versions']
        self.assertEqual(len(versions), 1)
        detail = self.editor_client.get(
            f"/api/business/ledgers/engineering/versions/{versions[0]['id']}/",
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()['records'][0]['project_id'], 'P-001')
        self.assertEqual(self.manager_client.get(
            f"/api/business/ledgers/engineering/versions/{versions[0]['id']}/",
        ).status_code, 403)

    def test_grant_revocation_and_delete_are_audited(self):
        grant = BusinessLedgerGrant.objects.get(user=self.editor, department='engineering')
        grant.delete()
        self.assertEqual(self.editor_client.get('/api/business/ledgers/engineering/').status_code, 403)

        self.post_json(
            self.editor_client, '/api/business/ledgers/engineering/records/',
            expected_revision=0, record=ENGINEERING_RECORD,
        )
        # Revoked requests cannot create hidden workbooks.
        self.assertFalse(BusinessLedgerWorkbook.objects.exists())

    def test_department_csv_import_metadata_and_single_record_delete(self):
        raw = (
            'project_id,project_name,status,owner,planned_end,progress\n'
            'P-001,园区项目,实施中,李工,2026-09-01,50\n'
            'P-002,车间项目,待验收,王工,2026-09-20,95\n'
        )
        imported = self.editor_client.post('/api/business/ledgers/engineering/import/', {
            'file': SimpleUploadedFile('engineering.csv', raw.encode('utf-8-sig'), content_type='text/csv'),
            'as_of': timezone.localdate().isoformat(), 'expected_revision': '0',
        })
        self.assertEqual(imported.status_code, 201, imported.content)
        self.assertEqual(imported.json()['total'], 2)
        self.assertEqual(self.editor_client.get('/api/business/boards/engineering/template/').status_code, 200)

        deleted = self.editor_client.delete(
            '/api/business/ledgers/engineering/records/P-002/',
            json.dumps({'expected_revision': 1}), content_type='application/json',
        )
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertEqual(deleted.json()['total'], 1)
        metadata = self.editor_client.patch(
            '/api/business/ledgers/engineering/',
            json.dumps({'expected_revision': 2, 'as_of': timezone.localdate().isoformat(), 'source_name': '工程部周报'}),
            content_type='application/json',
        )
        self.assertEqual(metadata.status_code, 200, metadata.content)
        self.assertEqual(metadata.json()['as_of'], timezone.localdate().isoformat())
        self.assertEqual(metadata.json()['source_name'], '工程部周报')
