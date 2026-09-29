from copy import deepcopy
from datetime import timedelta

from django.test import Client
from django.utils import timezone

from portal.business_models import BusinessLedgerRevision, BusinessLedgerWorkbook
from portal.models import Module, User

from .base import PortalTestCase


class BusinessProjectApiTests(PortalTestCase):
    def setUp(self):
        self.manager = self.create_user('project-manager', 'general_manager')
        self.login(self.client, self.manager)

    def workbook(self, code, actor, records, *, revision, state='draft', source_name='业务源表.xlsx'):
        return BusinessLedgerWorkbook.objects.create(
            department=code, state=state, revision=revision, source_name=source_name,
            as_of=timezone.localdate(), records=deepcopy(records), created_by=actor, updated_by=actor,
        )

    def revision(self, workbook, number, state, records, actor, action, at):
        item = BusinessLedgerRevision.objects.create(
            workbook=workbook, revision=number, state=state, source_name=workbook.source_name,
            as_of=workbook.as_of, records=deepcopy(records), checksum=f'{number:064x}',
            action=action, actor=actor,
        )
        BusinessLedgerRevision.objects.filter(pk=item.pk).update(created_at=at)
        item.created_at = at
        return item

    def presales_row(self, project_id, name, progress, owner):
        return {
            'project_id': project_id, 'project_name': name, 'status': '方案编制', 'owner': owner,
            'amount': '100.00', 'follow_up_date': '2026-09-20', 'project_type': '项目',
            'project_progress': progress, 'description': '', 'client_contact': '', 'maturity': '',
        }

    def finance_row(self, project_id, name, status, owner='回款负责人'):
        return {
            'project_id': project_id, 'project_name': name, 'contract_amount': '100.00',
            'received_amount': '', 'due_date': '', 'owner': owner, 'current_status': status,
            'opening_receivable': '100.00', 'receivable_balance': '100.00',
            'source_sheet': '应收明细', 'source_row': '5',
        }

    def test_project_actor_comes_from_its_content_diff_not_workbook_updater(self):
        importer = self.create_user('source-importer', 'general_manager')
        metadata_actor = self.create_user('metadata-editor', 'product')
        alpha_actor = self.create_user('alpha-editor', 'product')
        beta_actor = self.create_user('beta-editor', 'product')
        initial = [
            self.presales_row('P-A', '甲项目', '需求沟通', '甲负责人'),
            self.presales_row('P-B', '乙项目', '需求沟通', '乙负责人'),
        ]
        alpha_changed = deepcopy(initial)
        alpha_changed[0]['project_progress'] = '方案确认'
        final = deepcopy(alpha_changed)
        final[1]['project_progress'] = '报价完成'
        workbook = self.workbook('presales', beta_actor, final, revision=4)
        started = timezone.now() - timedelta(days=1)
        imported = self.revision(workbook, 1, 'draft', initial, importer, 'source_import', started)
        self.revision(workbook, 2, 'draft', initial, metadata_actor, 'metadata_update', started + timedelta(hours=1))
        alpha_update = self.revision(
            workbook, 3, 'draft', alpha_changed, alpha_actor, 'record_update', started + timedelta(hours=2),
        )
        self.revision(workbook, 4, 'draft', final, beta_actor, 'record_update', started + timedelta(hours=3))

        response = self.client.get('/api/business/boards/presales/projects/')
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        projects = {item['id']: item for item in body['projects']}
        self.assertEqual(projects['P-A']['owner'], '甲负责人')
        self.assertEqual(projects['P-A']['updated_by']['name'], alpha_actor.username)
        self.assertEqual(projects['P-A']['updated_at'], alpha_update.created_at.isoformat())
        self.assertEqual(projects['P-A']['update_kind'], 'record_update')
        self.assertEqual(projects['P-B']['updated_by']['name'], beta_actor.username)
        self.assertEqual(body['source'], self.client.get('/api/business/boards/presales/').json()['source'])
        self.assertIn('精确分组', body['scope'])

        detail = self.client.get('/api/business/boards/presales/projects/P-A/').json()
        self.assertEqual([item['action'] for item in detail['updates']], ['record_update', 'source_import'])
        self.assertEqual([item['actor']['name'] for item in detail['updates']], [alpha_actor.username, importer.username])
        self.assertEqual(detail['updates'][1]['id'], str(imported.pk))
        self.assertIn('导入人不代表原始业务记录作者', detail['updates'][1]['summary'])
        self.assertNotIn(metadata_actor.username, str(detail['updates']))
        self.assertNotIn(beta_actor.username, str(detail['updates']))

    def test_exact_whitespace_name_grouping_preserves_every_raw_row(self):
        importer = self.create_user('group-importer', 'general_manager')
        rows = [
            {**self.presales_row('G-1', ' 星河  项目 ', '方案沟通', '负责人甲'),
             'status': '', 'source_sheet': '项目表一', 'source_group': '项目预算'},
            {**self.presales_row('G-2', '星河\t项目', '商务谈判', '负责人乙'),
             'status': '', 'source_sheet': '项目表二', 'source_group': '已签单'},
            {**self.presales_row('G-3', '星河项目', '方案沟通', '负责人丙'),
             'status': '', 'source_sheet': '项目表三', 'source_group': '项目预算'},
        ]
        workbook = self.workbook('presales', importer, rows, revision=1)
        self.revision(workbook, 1, 'draft', rows, importer, 'source_import', timezone.now())

        body = self.client.get('/api/business/boards/presales/projects/').json()
        self.assertEqual(body['total'], 2)
        self.assertEqual(body['record_total'], 3)
        self.assertEqual([item['id'] for item in body['projects']], ['G-1', 'G-3'])
        self.assertEqual(body['projects'][0]['name'], '星河 项目')
        self.assertEqual(body['projects'][0]['record_count'], 2)
        self.assertEqual(body['projects'][0]['status'], '多来源状态')

        detail_response = self.client.get('/api/business/boards/presales/projects/G-1/')
        self.assertEqual(detail_response.status_code, 200, detail_response.content)
        detail = detail_response.json()
        self.assertEqual(detail['records'], rows[:2])
        self.assertEqual(detail['fields'], self.client.get('/api/business/boards/presales/').json()['fields'])
        self.assertEqual(self.client.get('/api/business/boards/presales/projects/G-2/').status_code, 404)

    def test_finance_uses_final_published_content_actor_and_hides_drafts(self):
        importer = self.create_user('finance-importer', 'general_manager')
        abandoned_actor = self.create_user('abandoned-editor', 'finance')
        final_actor = self.create_user('final-editor', 'finance')
        metadata_actor = self.create_user('finance-metadata', 'finance')
        publisher = self.create_user('finance-publisher', 'finance')
        hidden_actor = self.create_user('hidden-editor', 'finance')
        initial = [
            self.finance_row('F-1', '财务项目', '待回款'),
            self.finance_row('F-OLD', '已删除项目', '待回款'),
        ]
        abandoned = deepcopy(initial)
        abandoned[0]['current_status'] = '错误草稿状态'
        final = [self.finance_row('F-1', '财务项目', '已完成')]
        hidden = [self.finance_row('F-1', '财务项目', '未发布状态'),
                  self.finance_row('F-HIDDEN', '隐藏项目', '未发布')]
        workbook = self.workbook('finance', hidden_actor, hidden, revision=9, state='draft')
        started = timezone.now() - timedelta(days=2)
        self.revision(workbook, 1, 'draft', initial, importer, 'source_import', started)
        self.revision(workbook, 2, 'submitted', initial, importer, 'source_submit', started + timedelta(hours=1))
        self.revision(workbook, 3, 'published', initial, importer, 'source_publish', started + timedelta(hours=2))
        self.revision(workbook, 4, 'draft', abandoned, abandoned_actor, 'record_update', started + timedelta(hours=3))
        final_update = self.revision(
            workbook, 5, 'draft', final, final_actor, 'record_update', started + timedelta(hours=4),
        )
        self.revision(workbook, 6, 'draft', final, metadata_actor, 'metadata_update', started + timedelta(hours=5))
        self.revision(workbook, 7, 'submitted', final, publisher, 'submit', started + timedelta(hours=6))
        self.revision(workbook, 8, 'published', final, publisher, 'publish', started + timedelta(hours=7))
        hidden_update = self.revision(
            workbook, 9, 'draft', hidden, hidden_actor, 'record_update', started + timedelta(hours=8),
        )

        body = self.client.get('/api/business/boards/finance/projects/').json()
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['record_total'], 1)
        self.assertEqual(body['source']['revision'], 8)
        self.assertEqual(body['projects'][0]['status'], '已完成')
        self.assertEqual(body['projects'][0]['updated_by']['name'], final_actor.username)
        self.assertEqual(body['projects'][0]['updated_at'], final_update.created_at.isoformat())
        self.assertNotEqual(body['projects'][0]['updated_at'], hidden_update.created_at.isoformat())

        detail = self.client.get('/api/business/boards/finance/projects/F-1/').json()
        self.assertEqual(detail['records'], final)
        self.assertEqual([item['actor']['name'] for item in detail['updates']],
                         [final_actor.username, importer.username])
        self.assertTrue(all(item['state'] == 'published' for item in detail['updates']))
        hidden_names = {abandoned_actor.username, metadata_actor.username, publisher.username, hidden_actor.username}
        self.assertTrue(hidden_names.isdisjoint({item['actor']['name'] for item in detail['updates']}))
        self.assertEqual(self.client.get('/api/business/boards/finance/projects/F-OLD/').status_code, 404)
        self.assertEqual(self.client.get('/api/business/boards/finance/projects/F-HIDDEN/').status_code, 404)

    def test_empty_auth_scope_and_get_only_contract(self):
        empty = self.client.get('/api/business/boards/engineering/projects/')
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()['projects'], [])
        self.assertFalse(empty.json()['available'])
        self.assertEqual(empty.json()['record_total'], 0)
        self.assertEqual(self.client.get('/api/business/boards/engineering/projects/E-1/').status_code, 404)

        editor = self.create_user('engineering-source', 'engineering')
        row = {
            'project_id': 'E-1', 'project_name': '工程项目', 'status': '实施中',
            'owner': '工程负责人', 'planned_end': '2026-10-01', 'progress': '50',
        }
        workbook = self.workbook('engineering', editor, [row], revision=1, state='published')
        self.revision(workbook, 1, 'published', [row], editor, 'source_import', timezone.now())
        account_state = list(User.objects.order_by('pk').values(
            'pk', 'is_active', 'must_change_password', 'session_version', 'grant_version',
        ))
        counts = (BusinessLedgerWorkbook.objects.count(), BusinessLedgerRevision.objects.count())
        self.assertEqual(self.client.get('/api/business/boards/engineering/projects/').status_code, 200)
        self.assertEqual(self.client.get('/api/business/boards/engineering/projects/E-1/').status_code, 200)
        self.assertEqual(list(User.objects.order_by('pk').values(
            'pk', 'is_active', 'must_change_password', 'session_version', 'grant_version',
        )), account_state)
        self.assertEqual((BusinessLedgerWorkbook.objects.count(), BusinessLedgerRevision.objects.count()), counts)
        self.assertEqual(self.client.post('/api/business/boards/engineering/projects/').status_code, 405)
        self.assertEqual(self.client.post('/api/business/boards/engineering/projects/E-1/').status_code, 405)
        self.assertEqual((BusinessLedgerWorkbook.objects.count(), BusinessLedgerRevision.objects.count()), counts)
        self.assertEqual(self.client.get('/api/business/boards/finance/projects/E-1/').status_code, 404)

        self.assertEqual(Client().get('/api/business/boards/engineering/projects/').status_code, 401)
        ordinary = self.create_user('project-non-manager', 'engineering')
        ordinary_client = Client()
        self.login(ordinary_client, ordinary)
        self.assertEqual(ordinary_client.get('/api/business/boards/engineering/projects/').status_code, 403)

        module = Module.objects.get(code='business')
        Module.objects.filter(pk=module.pk).update(enabled=False)
        self.assertEqual(self.client.get('/api/business/boards/engineering/projects/').status_code, 403)
        Module.objects.filter(pk=module.pk).update(enabled=True)
        self.manager.roles.clear()
        self.assertIn(self.client.get('/api/business/boards/engineering/projects/').status_code, {401, 403})

        disabled = self.create_user('disabled-project-manager', 'general_manager')
        disabled_client = Client()
        self.login(disabled_client, disabled)
        User.objects.filter(pk=disabled.pk).update(is_active=False)
        self.assertIn(disabled_client.get('/api/business/boards/engineering/projects/').status_code, {401, 403})
