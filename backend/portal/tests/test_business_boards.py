from datetime import date, datetime, timezone
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase
from portal.business_boards import BoardError, calculate, parse_csv, payload, validate_record, validate_records
from portal.business_models import BusinessLedgerSnapshot
from .base import PortalTestCase

ENGINEERING = '项目编号,项目名称,状态,负责人,计划完成日期,完成进度\nP1,一期项目,实施中,李工,2026-01-01,62.5\nP2,二期项目,已验收,张工,2026-01-01,100\n'
FINANCE = '项目编号,项目名称,合同金额,已收金额,应收日期\nP1,一期项目,1000.10,200.05,2026-01-01\nP2,二期项目,20.20,20.20,\n'
PRESALES = '项目编号,项目名称,状态,负责人,预计金额\nP1,园区项目,报价,李工,3000.10\nP2,改造项目,已赢单,张工,200.20\n'
FINANCE_SOURCE = (
    'project_id,project_name,contract_amount,opening_receivable,receivable_balance,received_01,received_02,'
    'planned_08,billing_entity,contract_type,client_name,source_sheet,source_row\n'
    'P1,一期项目,100.10,200.20,150.10,10.10,,20.20,挂账一,工程,"业主\n单位",财务表,2\n'
    'P2,二期项目,20.20,,,,,,挂账二,服务,业主二,财务表,3\n'
)
PRESALES_SOURCE = (
    'project_id,project_name,status,owner,amount,source_sheet,source_row,source_group,source_sequence,'
    'source_amount,amount_unit,follow_up_history,notes,annual_plan,expected_signing\n'
    'P1,园区项目,,,300000.00,表A,10,重点项目（已签单）,1,30,万元,"第一行\n第二行\t记录",备注,"推进\n计划",2026年10月\n'
    'P2,园区项目,报价,李工,100.00,表B,20,项目预算,2,100,元,跟进,备注,,2026-11\n'
)


class LedgerParsingTests(SimpleTestCase):
    def test_presales_optional_follow_up_fields_and_legacy_csv(self):
        legacy = parse_csv(PRESALES.encode(), 'presales')
        self.assertEqual(legacy[0]['follow_up_date'], '')
        self.assertEqual(legacy[0]['description'], '')
        self.assertEqual(validate_record(legacy[0], 'presales')['amount'], '3000.10')
        current = validate_record({
            **legacy[0], 'follow_up_date': '2026-09-28', 'description': '现场沟通',
            'project_progress': '方案编制', 'maturity': '重点',
        }, 'presales')
        self.assertEqual(current['description'], '现场沟通')
        with self.assertRaises(BoardError):
            validate_record({**current, 'follow_up_date': '2026-02-30'}, 'presales')

    def test_chinese_and_english_headers_accept_unicode_and_quoted_commas(self):
        text = 'project_id,project_name,status,owner,planned_end,progress\nP1,"甲,乙项目",实施中,李工,,0\n'
        self.assertEqual(parse_csv(text.encode(), 'engineering')[0]['project_name'], '甲,乙项目')

    def test_invalid_finance_is_rejected_without_floating_point_coercion(self):
        for amount in ['NaN', 'Infinity', '-1', '0.001', '1000000000000']:
            with self.subTest(amount=amount), self.assertRaises(BoardError):
                parse_csv(FINANCE.replace('1000.10', amount).encode(), 'finance')
        with self.assertRaises(BoardError):
            parse_csv(FINANCE.replace('200.05', '1001.00').encode(), 'finance')

    def test_exact_money_and_snapshot_date_metrics(self):
        rows = parse_csv(FINANCE.encode(), 'finance')
        metrics = {x['key']: x['value'] for x in calculate('finance', rows, '2026-02-01')}
        self.assertEqual(metrics, {'contract': '1020.30', 'received': '220.25', 'receivable': '800.05', 'overdue': '800.05'})
        before = {x['key']: x['value'] for x in calculate('finance', rows, '2026-01-01')}
        self.assertEqual(before['overdue'], '0.00')

    def test_finance_source_mode_preserves_blanks_and_uses_explicit_monthly_values(self):
        rows = parse_csv(FINANCE_SOURCE.encode(), 'finance')
        self.assertEqual(rows[0]['contract_type'], '工程')
        self.assertEqual(rows[0]['received_amount'], '')
        self.assertEqual(rows[0]['due_date'], '')
        metrics = {item['key']: item['value'] for item in calculate('finance', rows, '2026-09-29')}
        self.assertEqual(metrics['contract'], '120.30')
        self.assertEqual(metrics['opening_receivable'], '200.20')
        self.assertEqual(metrics['received'], '10.10')
        self.assertEqual(metrics['receivable'], '150.10')
        self.assertEqual(metrics['planned'], '20.20')
        self.assertIsNone(metrics['overdue'])
        stamp = datetime(2026, 9, 29, tzinfo=timezone.utc)
        result = payload('finance', SimpleNamespace(
            pk='finance-source', records=rows, as_of=date(2026, 9, 29), source_name='finance.csv',
            revision=1, state='published', created_at=stamp, updated_at=stamp,
        ))
        self.assertEqual(result['distribution'], [{'label': '挂账一', 'count': 1}, {'label': '挂账二', 'count': 1}])
        self.assertEqual(list(result['fields']), [
            'contract_type', 'project_name', 'client_name', 'affiliate', 'contract_amount',
            'opening_receivable', 'receivable_balance', 'received_01', 'received_02', 'received_03',
            'received_04', 'received_05', 'received_06', 'received_07', 'planned_08', 'planned_09',
            'planned_10', 'planned_11', 'planned_12', 'billing_entity', 'owner', 'current_status',
            'project_id', 'source_sheet', 'source_row',
        ])
        self.assertNotIn('received_amount', result['fields'])
        self.assertNotIn('due_date', result['fields'])
        self.assertNotIn('received_amount', result['records'][0])
        self.assertIn('缺失指标为“—”', result['scope'])

    def test_finance_source_money_fields_reject_inexact_or_negative_values(self):
        for value in ('1.001', '-1'):
            with self.subTest(value=value), self.assertRaises(BoardError):
                parse_csv(FINANCE_SOURCE.replace('200.20', value).encode(), 'finance')

    def test_engineering_and_presales_business_definitions(self):
        e = {x['key']: x['value'] for x in calculate('engineering', parse_csv(ENGINEERING.encode(), 'engineering'), '2026-02-01')}
        self.assertEqual(e, {'projects': 2, 'active': 1, 'overdue': 1, 'accepted': 1})
        p = {x['key']: x['value'] for x in calculate('presales', parse_csv(PRESALES.encode(), 'presales'), '2026-02-01')}
        self.assertEqual(p, {'opportunities': 2, 'active': 1, 'pipeline': '3000.10', 'won': 1})

    def test_presales_source_rows_allow_overlap_blanks_and_multiline_history(self):
        rows = parse_csv(PRESALES_SOURCE.encode(), 'presales')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['status'], '')
        self.assertIn('第二行\t记录', rows[0]['follow_up_history'])
        self.assertEqual(rows[0]['amount'], '300000.00')
        metrics = {item['key']: item['value'] for item in calculate('presales', rows, '2026-09-29')}
        self.assertEqual(metrics['source_entries'], 2)
        self.assertEqual(metrics['amount_known'], 2)
        self.assertEqual(metrics['signed_amount'], '300000.00')
        self.assertEqual(metrics['budget_amount'], '100.00')
        self.assertEqual(metrics['unknown_stage'], 1)
        with self.assertRaises(BoardError):
            parse_csv(PRESALES_SOURCE.replace('报价', '未知状态').encode(), 'presales')

    def test_presales_source_distribution_uses_groups_without_status(self):
        raw = (
            'project_id,project_name,source_sheet,source_row,source_group\n'
            'P1,项目一,表A,10,重点\nP2,项目二,表B,20,普通\n'
        )
        rows = parse_csv(raw.encode(), 'presales')
        stamp = datetime(2026, 9, 29, tzinfo=timezone.utc)
        result = payload('presales', SimpleNamespace(
            pk='presales-source', records=rows, as_of=date(2026, 9, 29), source_name='presales.csv',
            revision=1, state='draft', created_at=stamp, updated_at=stamp,
        ))
        self.assertEqual(result['distribution'], [{'label': '重点', 'count': 1}, {'label': '普通', 'count': 1}])
        self.assertIn('来源工作表可能重叠', result['scope'])

    def test_presales_canonical_amount_validation_is_idempotent(self):
        record = {
            'project_id': 'P-1651', 'project_name': '预算项目', 'status': '', 'owner': '',
            'amount': '16511700.00', 'follow_up_date': '', 'project_type': '', 'project_progress': '',
            'description': '', 'client_contact': '', 'maturity': '', 'source_sheet': 'Sheet7',
            'source_row': '8', 'source_group': '项目预算', 'source_sequence': '1',
            'source_amount': '1651.17', 'amount_unit': '万元', 'follow_up_history': '',
            'notes': '', 'annual_plan': '', 'expected_signing': '',
        }
        first = validate_records([record], 'presales')
        second = validate_records(first, 'presales')
        self.assertEqual(first, second)
        self.assertEqual(second[0]['amount'], '16511700.00')
        self.assertEqual(second[0]['source_amount'], '1651.17')
        self.assertEqual(second[0]['amount_unit'], '万元')

    def test_invalid_headers_duplicates_dates_status_and_limits(self):
        cases = [b'', b'x' * (2 * 1024 * 1024 + 1),
                 ENGINEERING.replace('完成进度', '负责人').encode(),
                 ENGINEERING.replace('P2,', 'P1,').encode(),
                 ENGINEERING.replace('2026-01-01', '2026-02-31').encode(),
                 ENGINEERING.replace('实施中', '不明状态').encode(),
                 ENGINEERING.replace('62.5', '101').encode(),
                 ENGINEERING.splitlines()[0].encode(),
                 (ENGINEERING.splitlines()[0] + '\n' + '\n'.join(f'P{i},项目,实施中,李工,,0' for i in range(2001))).encode()]
        for raw in cases:
            with self.subTest(length=len(raw)), self.assertRaises(BoardError):
                parse_csv(raw, 'engineering')


class BusinessBoardApiTests(PortalTestCase):
    def setUp(self):
        self.manager = self.create_user('manager', 'general_manager')
        self.login(self.client, self.manager)

    def upload(self, code='engineering', text=ENGINEERING, expected='', as_of='2026-02-01', client=None):
        return (client or self.client).post(f'/api/business/boards/{code}/', {
            'file': SimpleUploadedFile('台账.csv', text.encode('utf-8-sig'), content_type='text/csv'),
            'as_of': as_of, 'expected_snapshot_id': expected})

    def test_empty_snapshot_is_unavailable_not_fake_zero(self):
        for department in ('engineering', 'finance', 'presales'):
            data = self.client.get(f'/api/business/boards/{department}/').json()
            self.assertFalse(data['available'])
            self.assertIsNone(data['source'])
            self.assertTrue(all(x['value'] is None for x in data['metrics']))

    def test_manager_cannot_import_or_create_fallback_snapshot(self):
        response = self.upload()
        self.assertEqual(response.status_code, 405, response.content)
        self.assertEqual(BusinessLedgerSnapshot.objects.count(), 0)
        self.assertFalse(self.client.get('/api/business/boards/engineering/').json()['available'])

    def test_permission_revocation(self):
        other = self.create_user('other-manager', 'general_manager')
        client = Client()
        self.login(client, other)
        self.assertFalse(client.get('/api/business/boards/engineering/').json()['available'])
        self.manager.roles.clear()
        self.assertEqual(self.client.get('/api/business/boards/engineering/').status_code, 403)

    def test_unauthorized_roles_and_admin_have_no_implicit_board_access(self):
        for role in ('product', 'engineering', 'hr', 'platform_admin'):
            user = self.create_user('no-board-' + role, role)
            client = Client()
            self.login(client, user)
            self.assertEqual(client.get('/api/business/boards/finance/').status_code, 403)
            self.assertIn(self.upload(client=client).status_code, (403, 405))
        self.assertEqual(Client().get('/api/business/boards/finance/').status_code, 401)

    def test_all_manager_import_variants_are_method_not_allowed(self):
        self.assertEqual(self.upload().status_code, 405)
        self.assertEqual(self.upload(text='bad').status_code, 405)
        self.assertEqual(BusinessLedgerSnapshot.objects.count(), 0)

    def test_templates_and_unknown_board(self):
        response = self.client.get('/api/business/boards/finance/template/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('合同金额', response.content.decode('utf-8-sig'))
        self.assertEqual(len(response.content.decode('utf-8-sig').splitlines()), 1)
        self.assertEqual(self.client.get('/api/business/boards/unknown/').status_code, 404)
        self.assertEqual(self.client.get('/api/business/boards/engineering/', {'status': 'n/a'}).status_code, 400)
