from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError

from portal.business_models import BusinessLedgerGrant, BusinessLedgerRevision, BusinessLedgerWorkbook
from portal.models import User
from .base import PortalTestCase


class BusinessWorkbookImportTests(PortalTestCase):
    def setUp(self):
        self.manager = self.create_user('source-manager', 'general_manager')
        self.finance = {'records': [{
            'project_id': 'FIN-1-5', 'project_name': '合同项目', 'contract_amount': '100.00',
            'received_amount': '10.00', 'due_date': '',
        }], 'report': {'rows': 1}}
        self.presales = {'records': [{
            'project_id': 'PRO-1-2', 'project_name': '产品项目', 'status': '线索',
            'owner': '', 'amount': '20.00',
        }], 'report': {'rows': 1}}
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / 'source.xls'
        self.source.write_bytes(b'test-source')

    def run_import(self, **options):
        with patch('portal.management.commands.import_business_workbooks.parse_finance', return_value=deepcopy(self.finance)), \
             patch('portal.management.commands.import_business_workbooks.parse_presales', return_value=deepcopy(self.presales)), \
             patch('portal.management.commands.import_business_workbooks.sqlite3.connect'):
            call_command('import_business_workbooks', finance=str(self.source), presales=str(self.source),
                         actor=self.manager.username, as_of='2026-09-01', stdout=StringIO(), **options)

    def test_default_is_read_only(self):
        self.run_import()
        self.assertFalse(BusinessLedgerWorkbook.objects.exists())
        self.assertFalse(BusinessLedgerRevision.objects.exists())

    def test_apply_imports_drafts_without_assigning_writers_or_changing_accounts(self):
        users_before = list(User.objects.values('id', 'password', 'session_version', 'grant_version'))
        grants_before = list(BusinessLedgerGrant.objects.values())
        self.run_import(apply=True)
        self.assertEqual(BusinessLedgerWorkbook.objects.filter(state='draft', revision=1).count(), 2)
        self.assertEqual(BusinessLedgerRevision.objects.count(), 2)
        self.assertEqual(BusinessLedgerWorkbook.objects.get(department='finance').record_meta, {})
        self.assertFalse(BusinessLedgerWorkbook.objects.filter(department='engineering').exists())
        self.assertEqual(list(User.objects.values('id', 'password', 'session_version', 'grant_version')), users_before)
        self.assertEqual(list(BusinessLedgerGrant.objects.values()), grants_before)
        self.login(self.client, self.manager)
        self.assertFalse(self.client.get('/api/business/boards/finance/').json()['available'])
        self.assertFalse(self.client.get('/api/business/boards/engineering/').json()['available'])

    def test_repeat_is_idempotent_and_different_source_is_rejected(self):
        self.run_import(apply=True)
        self.run_import(apply=True)
        self.assertEqual(BusinessLedgerRevision.objects.count(), 2)
        self.presales['records'][0]['project_name'] = '不同内容'
        with self.assertRaisesMessage(CommandError, '拒绝覆盖'):
            self.run_import(apply=True)
        self.assertEqual(BusinessLedgerRevision.objects.count(), 2)

    def test_new_source_cannot_be_published_by_manager(self):
        with self.assertRaisesMessage(CommandError, '不能由管理账号整簿发布'):
            self.run_import(apply=True, publish=True)
        self.assertFalse(BusinessLedgerWorkbook.objects.exists())

    def test_invalid_second_department_does_not_partially_import(self):
        self.presales['records'][0]['amount'] = '-1'
        with self.assertRaises(CommandError):
            self.run_import(apply=True)
        self.assertFalse(BusinessLedgerWorkbook.objects.exists())

    def test_existing_department_is_not_overwritten(self):
        BusinessLedgerWorkbook.objects.create(department='presales', records=[],
                                              created_by=self.manager, updated_by=self.manager)
        with self.assertRaisesMessage(CommandError, '拒绝覆盖'):
            self.run_import(apply=True)
        self.assertFalse(BusinessLedgerWorkbook.objects.filter(department='finance').exists())

    def test_finance_mismatch_and_truncation_block_import(self):
        for report in [
                {'totals': {'contract_amount': {'matches': False}}},
                {'errors': [{'cell': 'E5', 'message': '#REF!'}]},
                {'truncated_fields': [{'field': 'project_name'}]}]:
            with self.subTest(report=report):
                self.finance['report'] = report
                with self.assertRaises(CommandError):
                    self.run_import(apply=True)
                self.assertFalse(BusinessLedgerWorkbook.objects.exists())

    def test_product_amount_errors_block_but_excluded_kpi_errors_do_not(self):
        self.presales['report'] = {'errors': [{'source_sheet': 'Sheet7', 'cell': 'E3', 'message': '#REF!'}]}
        with self.assertRaisesMessage(CommandError, '拒绝以空白替代'):
            self.run_import(apply=True)
        self.assertFalse(BusinessLedgerWorkbook.objects.exists())
        self.presales['report'] = {'errors': [{'source_sheet': '总表八月份', 'cell': 'D7', 'message': '#REF!'}]}
        self.run_import()
