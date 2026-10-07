import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from portal.business_models import BusinessLedgerGrant, BusinessLedgerRevision, BusinessLedgerWorkbook
from portal.model_config import ModelCallLog

from .base import PortalTestCase


class FinanceAuthorTests(PortalTestCase):
    def setUp(self):
        self.alice = self.create_user('finance-alice')
        self.bob = self.create_user('finance-bob')
        self.manager = self.create_user('finance-manager', 'general_manager')
        for user in (self.alice, self.bob):
            BusinessLedgerGrant.objects.create(user=user, department='finance', can_edit=True)
        self.alice_client, self.bob_client, self.manager_client = Client(), Client(), Client()
        for client, user in ((self.alice_client, self.alice), (self.bob_client, self.bob),
                             (self.manager_client, self.manager)):
            self.login(client, user)

    @staticmethod
    def record(project_id, amount='100.00'):
        return {'project_id': project_id, 'project_name': project_id, 'contract_amount': amount,
                'received_amount': '10.00', 'due_date': ''}

    @staticmethod
    def post(client, path, **data):
        return client.post(path, json.dumps(data), content_type='application/json')

    @staticmethod
    def put(client, path, **data):
        return client.put(path, json.dumps(data), content_type='application/json')

    @staticmethod
    def delete(client, path, **data):
        return client.delete(path, json.dumps(data), content_type='application/json')

    def publish(self, client, revision, published_revision, record_id, entry):
        return self.post(client, '/api/business/ledgers/finance/publish/',
                         expected_revision=revision, expected_published_revision=published_revision,
                         records=[{'record_id': record_id, 'source_revision': entry['draft_revision'],
                                   'source_checksum': entry['draft_checksum']}])

    def test_cross_author_mutations_and_writer_spoof_are_atomic(self):
        first = self.post(self.alice_client, '/api/business/ledgers/finance/records/',
                          expected_revision=0, record=self.record('A'))
        self.assertEqual(first.status_code, 201, first.content)
        second = self.post(self.bob_client, '/api/business/ledgers/finance/records/',
                           expected_revision=1, record=self.record('B'))
        self.assertEqual(second.status_code, 201, second.content)
        for client, path, body, method in (
            (self.alice_client, '/api/business/ledgers/finance/records/B/',
             {'expected_revision': 2, 'record': self.record('B')}, self.put),
            (self.alice_client, '/api/business/ledgers/finance/records/B/',
             {'expected_revision': 2, 'record': self.record('B', '200.00')}, self.put),
            (self.alice_client, '/api/business/ledgers/finance/records/B/',
             {'expected_revision': 2}, self.delete),
            (self.alice_client, '/api/business/ledgers/finance/records/B/',
             {'expected_revision': 2, 'record': self.record('C')}, self.put),
        ):
            with self.subTest(path=path, body=body):
                self.assertEqual(method(client, path, **body).status_code, 400)
        forged = {**self.record('C'), 'record_author_id': self.alice.pk}
        self.assertEqual(self.post(self.alice_client, '/api/business/ledgers/finance/records/',
                                   expected_revision=2, record=forged).status_code, 400)
        raw = 'project_id,project_name,contract_amount,received_amount,due_date\nA,A,200.00,10.00,\n'
        imported = self.alice_client.post('/api/business/ledgers/finance/import/', {
            'expected_revision': '2', 'as_of': '2026-09-01',
            'file': SimpleUploadedFile('finance.csv', raw.encode()),
        })
        self.assertEqual(imported.status_code, 400, imported.content)
        workbook = BusinessLedgerWorkbook.objects.get(department='finance')
        self.assertEqual(workbook.revision, 2)
        self.assertEqual([row['project_id'] for row in workbook.records], ['A', 'B'])
        self.assertEqual(BusinessLedgerRevision.objects.count(), 2)
        valid = ('project_id,project_name,contract_amount,received_amount,due_date\n'
                 'A,A,200.00,10.00,\nB,B,100.00,10.00,\n')
        imported = self.alice_client.post('/api/business/ledgers/finance/import/', {
            'expected_revision': '2', 'as_of': '2026-09-01',
            'file': SimpleUploadedFile('finance.csv', valid.encode()),
        })
        self.assertEqual(imported.status_code, 201, imported.content)
        self.assertEqual(imported.json()['records'][1]['project_id'], 'B')
        bob_meta = next(item for item in imported.json()['record_meta'].values()
                        if item['record_author_id'] == self.bob.pk)
        self.assertEqual(bob_meta['draft_revision'], 2)

    def test_precise_self_publication_merges_only_selected_rows(self):
        first = self.post(self.alice_client, '/api/business/ledgers/finance/records/',
                          expected_revision=0, record=self.record('A')).json()
        second = self.post(self.bob_client, '/api/business/ledgers/finance/records/',
                           expected_revision=1, record=self.record('B')).json()
        self.assertFalse(self.manager_client.get('/api/business/boards/finance/').json()['available'])
        alice_id, alice_entry = next((key, value) for key, value in second['record_meta'].items()
                                     if value['record_author_id'] == self.alice.pk)
        bob_id, bob_entry = next((key, value) for key, value in second['record_meta'].items()
                                 if value['record_author_id'] == self.bob.pk)
        self.assertEqual(self.publish(self.bob_client, 2, 0, alice_id, alice_entry).status_code, 403)
        self.assertEqual(self.publish(self.alice_client, 2, 0, alice_id, alice_entry).status_code, 200)
        self.assertEqual(self.publish(self.alice_client, 2, 0, alice_id, alice_entry).status_code, 200)
        self.assertEqual(BusinessLedgerRevision.objects.count(), 3)
        board = self.manager_client.get('/api/business/boards/finance/').json()
        self.assertEqual([row['project_id'] for row in board['records']], ['A'])
        self.assertEqual(self.publish(self.bob_client, 3, 0, bob_id, bob_entry).status_code, 409)
        self.assertEqual(self.publish(self.bob_client, 3, 3, bob_id, bob_entry).status_code, 200)
        self.assertEqual([row['project_id'] for row in
                          self.manager_client.get('/api/business/boards/finance/').json()['records']], ['A', 'B'])
        changed = self.put(self.alice_client, '/api/business/ledgers/finance/records/A/',
                           expected_revision=4, record=self.record('A', '200.00'))
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(self.manager_client.get('/api/business/boards/finance/').json()['records'][0]
                         ['contract_amount'], '100.00')
        alice_entry = changed.json()['record_meta'][alice_id]
        self.assertEqual(self.publish(self.alice_client, 5, 4, alice_id,
                                      {**alice_entry, 'draft_checksum': '0' * 64}).status_code, 409)
        self.assertEqual(self.publish(self.alice_client, 5, 4, alice_id, alice_entry).status_code, 200)
        published = {row['project_id']: row for row in
                     self.manager_client.get('/api/business/boards/finance/').json()['records']}
        self.assertEqual(published['A']['contract_amount'], '200.00')
        deleted = self.delete(self.alice_client, '/api/business/ledgers/finance/records/A/',
                              expected_revision=6)
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertEqual(len(self.manager_client.get('/api/business/boards/finance/').json()['records']), 2)
        self.assertEqual(self.publish(self.alice_client, 7, 6, alice_id,
                                      deleted.json()['record_meta'][alice_id]).status_code, 200)
        self.assertEqual([row['project_id'] for row in
                          self.manager_client.get('/api/business/boards/finance/').json()['records']], ['B'])

    def test_legacy_row_has_no_inferred_author(self):
        BusinessLedgerWorkbook.objects.create(
            department='finance', created_by=self.alice, updated_by=self.alice,
            records=[self.record('OLD')], revision=0,
        )
        changed = self.put(self.alice_client, '/api/business/ledgers/finance/records/OLD/',
                           expected_revision=0, record=self.record('OLD', '200.00'))
        self.assertEqual(changed.status_code, 400)
        self.assertEqual(BusinessLedgerWorkbook.objects.get(department='finance').records[0]
                         ['contract_amount'], '100.00')

    def test_manager_usage_distinguishes_unknown_and_excludes_connection_tests(self):
        self.assertEqual(self.alice_client.get('/api/business/management/usage/').status_code, 403)
        ModelCallLog.objects.create(actor=self.alice, purpose='business', status='succeeded',
                                    duration_ms=10, prompt_tokens=7, completion_tokens=3,
                                    physical_call_id='known')
        ModelCallLog.objects.create(actor=self.bob, purpose='business', status='failed',
                                    duration_ms=10, physical_call_id='unknown')
        ModelCallLog.objects.create(actor=self.bob, purpose='test', status='succeeded',
                                    duration_ms=10, prompt_tokens=5, completion_tokens=5)
        usage = self.manager_client.get('/api/business/management/usage/')
        self.assertEqual(usage.status_code, 200, usage.content)
        self.assertEqual(usage.json(), {'calls': 2, 'known_calls': 1, 'unknown_calls': 1,
                                        'prompt_tokens': 7, 'completion_tokens': 3,
                                        'usage_complete': False})

    def test_manager_grant_cannot_enable_business_writes(self):
        BusinessLedgerGrant.objects.create(user=self.manager, department='finance',
                                           can_edit=True, can_submit=True, can_publish=True)
        permissions = self.manager_client.get('/api/business/ledgers/permissions/').json()['departments'][0]
        self.assertFalse(permissions['can_edit'])
        self.assertFalse(permissions['can_submit'])
        self.assertFalse(permissions['can_publish'])
        created = self.post(self.manager_client, '/api/business/ledgers/finance/records/',
                            expected_revision=0, record=self.record('GM'))
        self.assertEqual(created.status_code, 403)
        self.assertFalse(BusinessLedgerWorkbook.objects.exists())
