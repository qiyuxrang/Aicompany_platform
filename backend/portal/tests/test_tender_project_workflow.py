import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from portal.tender_grouping import assign_project_group, project_group_key
from portal.tender_models import TenderManualRefresh, TenderNotice, TenderOpportunity, TenderOpportunityUserState, TenderSource
from .base import PortalTestCase


class TenderProjectWorkflowTests(PortalTestCase):
    def setUp(self):
        self.user = self.create_user('project-workflow', 'product')
        self.client = Client()
        self.login(self.client, self.user)
        self.now = timezone.now()

    def opportunity(self, key, **values):
        defaults = dict(project_name='智能矿山建设招标公告', project_code='YL-2026-010',
                        purchaser='榆林测试煤业有限公司', region='陕西省榆林市',
                        classification_status='matched', notice_category='procurement',
                        publish_date=self.now.date(), publish_at=self.now, first_seen_at=self.now)
        defaults.update(values)
        opportunity = TenderOpportunity.objects.create(opportunity_key=key, **defaults)
        assign_project_group(opportunity)
        opportunity.save(update_fields=['project_group_key'])
        return opportunity

    def post(self, item, **payload):
        return self.client.post(f'/api/product/opportunities/{item.pk}/state/', json.dumps(payload), content_type='application/json')

    def test_same_number_and_purchaser_group_cross_site_but_keep_lots_and_buyers_separate(self):
        first = self.opportunity('source-a:1')
        same = self.opportunity('source-b:2', project_name='智能矿山建设延期公告', notice_category='change')
        other = self.opportunity('source-c:3', purchaser='陕西另一采购单位有限公司')
        lot1 = self.opportunity('source-a:4', project_name='智能矿山建设第1标段')
        lot2 = self.opportunity('source-a:5', project_name='智能矿山建设第2标段')
        self.assertEqual(first.project_group_key, same.project_group_key)
        self.assertEqual(len({first.project_group_key, other.project_group_key, lot1.project_group_key, lot2.project_group_key}), 4)
        payload = self.client.get('/api/product/opportunities/?notice_category=all').json()
        self.assertEqual(payload['total'], 4)
        self.assertEqual(TenderOpportunity.objects.count(), 5)

    def test_missing_code_never_merges_by_similar_title(self):
        first = self.opportunity('a', project_code='')
        second = self.opportunity('b', project_code='')
        self.assertNotEqual(project_group_key(first), project_group_key(second))

    def test_legacy_group_backfill_is_repeatable_and_preserves_records_and_marks(self):
        first = self.opportunity('legacy-a')
        self.opportunity('legacy-b')
        TenderOpportunity.objects.update(project_group_key='')
        TenderOpportunityUserState.objects.create(
            user=self.user, project_group_key=first.opportunity_key, is_read=True, is_favorite=True)
        before = list(TenderOpportunity.objects.order_by('pk').values_list('pk', 'first_seen_at', 'created_at', 'updated_at'))
        self.assertEqual(self.client.get('/api/product/opportunities/').json()['total'], 2)
        output = StringIO()
        call_command('group_tender_projects', stdout=output)
        self.assertIn('Updated 2 project grouping keys', output.getvalue())
        payload = self.client.get('/api/product/opportunities/').json()
        self.assertEqual(payload['total'], 1)
        self.assertTrue(payload['items'][0]['user_state']['is_read'])
        self.assertTrue(payload['items'][0]['user_state']['is_favorite'])
        self.assertEqual(list(TenderOpportunity.objects.order_by('pk').values_list(
            'pk', 'first_seen_at', 'created_at', 'updated_at')), before)
        repeated = StringIO()
        call_command('group_tender_projects', stdout=repeated)
        self.assertIn('Updated 0 project grouping keys', repeated.getvalue())

    def test_lots_and_second_tender_do_not_inherit_other_awards(self):
        first = self.opportunity('lot1', project_name='智能矿山建设第1包', notice_category='result', status='AWARDED')
        second = self.opportunity('lot2', project_name='智能矿山建设第2包', bid_deadline=self.now+timedelta(days=3))
        third = self.opportunity('lot-a', project_name='智能矿山建设A标段')
        fourth = self.opportunity('lot-b', project_name='智能矿山建设B标段')
        original = self.opportunity('original', notice_category='result', status='AWARDED')
        retry = self.opportunity('second-round', project_name='智能矿山建设二次招标公告', bid_deadline=self.now+timedelta(days=3))
        self.assertEqual(len({item.project_group_key for item in (first, second, third, fourth, original, retry)}), 6)
        self.assertEqual(self.client.get('/api/product/opportunities/?participation=open').json()['total'], 2)

    def test_user_state_shared_by_project_and_isolated_between_users(self):
        first = self.opportunity('source-a:1')
        same = self.opportunity('source-b:2')
        self.assertEqual(self.post(first, is_favorite=True, is_read=True).status_code, 200)
        detail = self.client.get(f'/api/product/opportunities/{same.pk}/').json()['opportunity']
        self.assertTrue(detail['user_state']['is_read'])
        self.assertTrue(detail['user_state']['is_favorite'])
        self.assertEqual(self.client.get('/api/product/opportunities/?user_state=unread').json()['total'], 0)
        self.assertEqual(self.post(first, is_irrelevant=True).status_code, 200)
        self.assertEqual(self.client.get('/api/product/opportunities/').json()['total'], 0)
        self.assertEqual(self.client.get('/api/product/opportunities/?user_state=irrelevant').json()['total'], 1)
        other = Client()
        self.login(other, self.create_user('other-workflow', 'product'))
        self.assertEqual(other.get('/api/product/opportunities/').json()['total'], 1)
        self.assertFalse(other.get(f'/api/product/opportunities/{same.pk}/').json()['opportunity']['user_state']['is_read'])
        self.post(first, is_irrelevant=False)
        self.assertEqual(self.client.get('/api/product/opportunities/?user_state=favorite').json()['total'], 1)

    def test_bad_state_and_unauthorized_requests_rejected(self):
        first = self.opportunity('first')
        for payload in ({'is_read': 'false'}, {'user_id': self.user.pk}, {}):
            self.assertEqual(self.post(first, **payload).status_code, 400)
        denied = Client()
        response = denied.post(f'/api/product/opportunities/{first.pk}/state/', '{}', content_type='application/json')
        self.assertIn(response.status_code, (401, 403, 404))

    def test_unknown_deadline_and_expired_and_relevance_sorting(self):
        mixed = self.opportunity('mixed', project_code='YL-2026-001', classification_evidence={'relevance_tier': 'related'})
        core = self.opportunity('core', project_code='YL-2026-002', classification_evidence={'relevance_tier': 'core'})
        expired = self.opportunity('expired', project_code='YL-2026-003', bid_deadline=self.now-timedelta(days=1))
        open_item = self.opportunity('open', project_code='YL-2026-004', bid_deadline=self.now+timedelta(days=1))
        rows = self.client.get('/api/product/opportunities/').json()['items']
        self.assertEqual([row['id'] for row in rows], [open_item.pk, core.pk, mixed.pk, expired.pk])
        self.assertEqual(rows[1]['participation_status'], 'unknown')
        self.assertEqual(rows[1]['status_label'], '截止时间待核实')
        self.assertEqual(self.client.get('/api/product/opportunities/?participation=open').json()['total'], 1)
        self.assertEqual(self.client.get('/api/product/opportunities/?participation=unknown').json()['total'], 2)

    def test_later_extension_controls_group_deadline_and_result_closes_group(self):
        first = self.opportunity('original', bid_deadline=self.now-timedelta(days=1), publish_at=self.now-timedelta(days=3), publish_date=(self.now-timedelta(days=3)).date())
        self.opportunity('extension', notice_category='change', bid_deadline=self.now+timedelta(days=5))
        row = self.client.get('/api/product/opportunities/').json()['items'][0]
        self.assertEqual(row['participation_status'], 'open')
        self.opportunity('result', notice_category='result', status='AWARDED')
        self.assertEqual(self.client.get('/api/product/opportunities/').json()['items'][0]['participation_status'], 'awarded')

    def test_latest_batch_counts_new_projects_once_and_not_new_notice_on_old_project(self):
        self.opportunity('old', first_seen_at=self.now-timedelta(days=3))
        self.opportunity('duplicate-new-notice')
        self.opportunity('fresh', project_code='YL-2026-999')
        TenderManualRefresh.objects.create(state='PARTIAL', requested_by=self.user,
                                          started_at=self.now-timedelta(minutes=5), finished_at=self.now+timedelta(seconds=1))
        payload = self.client.get('/api/product/opportunities/').json()
        self.assertEqual(payload['stats']['latest_batch_new'], 1)
        self.assertEqual(sum(item['latest_batch_new'] for item in payload['items']), 1)

    def test_running_batch_already_reports_its_new_projects(self):
        self.opportunity('fresh')
        TenderManualRefresh.objects.create(state='RUNNING', requested_by=self.user,
                                          started_at=self.now-timedelta(minutes=5))
        payload = self.client.get('/api/product/opportunities/').json()
        self.assertEqual(payload['stats']['latest_batch_new'], 1)
        self.assertTrue(payload['items'][0]['latest_batch_new'])

    def test_details_retain_every_official_notice_in_group(self):
        source = TenderSource.objects.create(code='ccgp_national', name='中国政府采购网')
        first = self.opportunity('a')
        second = self.opportunity('b', notice_category='change')
        for i, item in enumerate((first, second)):
            notice_id = f't20260928_{12345+i}'
            TenderNotice.objects.create(source=source, source_notice_id=notice_id,
                canonical_key=item.opportunity_key, title=item.project_name,
                original_url=f'https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202609/{notice_id}.htm',
                first_seen_at=self.now, last_seen_at=self.now)
        payload = self.client.get(f'/api/product/opportunities/{first.pk}/').json()['opportunity']
        self.assertEqual(payload['notice_count'], 2)
        self.assertEqual(len(payload['notices']), 2)
