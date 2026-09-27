from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase
from portal.business_boards import BoardError, calculate, parse_csv
from portal.business_models import BusinessLedgerSnapshot
from .base import PortalTestCase

ENGINEERING = '项目编号,项目名称,状态,负责人,计划完成日期,完成进度\nP1,一期项目,实施中,李工,2026-01-01,62.5\nP2,二期项目,已验收,张工,2026-01-01,100\n'
FINANCE = '项目编号,项目名称,合同金额,已收金额,应收日期\nP1,一期项目,1000.10,200.05,2026-01-01\nP2,二期项目,20.20,20.20,\n'
PRESALES = '项目编号,项目名称,状态,负责人,预计金额\nP1,园区项目,报价,李工,3000.10\nP2,改造项目,已赢单,张工,200.20\n'


class LedgerParsingTests(SimpleTestCase):
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

    def test_engineering_and_presales_business_definitions(self):
        e = {x['key']: x['value'] for x in calculate('engineering', parse_csv(ENGINEERING.encode(), 'engineering'), '2026-02-01')}
        self.assertEqual(e, {'projects': 2, 'active': 1, 'overdue': 1, 'accepted': 1})
        p = {x['key']: x['value'] for x in calculate('presales', parse_csv(PRESALES.encode(), 'presales'), '2026-02-01')}
        self.assertEqual(p, {'opportunities': 2, 'active': 1, 'pipeline': '3000.10', 'won': 1})

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
