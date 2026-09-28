from datetime import timedelta
from hashlib import sha256
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from portal.tender_manual_refresh import ActiveRefresh, claim, enqueue, execute_once, serialize
from portal.tender_models import TenderConsumerHeartbeat, TenderManualRefresh, TenderOpportunity, TenderSource
from portal.tender_sources.base import FetchResult, NoticeRef, SourcePreflight, SourceState
from portal.tender_worker import SourceRunResult


@override_settings(PORTAL_TENDER_INGESTION_ENABLED=True,
                   PORTAL_TENDER_MANUAL_REFRESH_ENABLED=True)
class RefreshWorkerTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='refresh-worker', password='test-only-password')
        self.sources = [TenderSource.objects.create(code=code, name=code, adapter_code=code, enabled=True)
                        for code in ('ccgp_national', 'shxjkjt')]
        TenderConsumerHeartbeat.objects.create(slot=1, updated_at=timezone.now())

    def test_second_active_batch_returns_existing_batch_id(self):
        first = enqueue(self.actor, [self.sources[0].code])
        with self.assertRaises(ActiveRefresh) as caught:
            enqueue(self.actor, [self.sources[1].code])
        self.assertEqual(caught.exception.batch_id, first.pk)
        self.assertEqual(TenderManualRefresh.objects.count(), 1)

    def test_no_new_batch_when_consumer_is_stale(self):
        TenderConsumerHeartbeat.objects.update(updated_at=timezone.now() - timedelta(minutes=5))
        with self.assertRaisesRegex(ValueError, '后台消费者未运行'):
            enqueue(self.actor, [self.sources[0].code])
        self.assertFalse(TenderManualRefresh.objects.exists())

    def test_expired_batch_stays_running_without_automatic_takeover(self):
        batch = enqueue(self.actor, [self.sources[0].code])
        claimed, fence = claim()
        self.assertEqual(claimed.pk, batch.pk)
        TenderManualRefresh.objects.filter(pk=batch.pk, fence=fence).update(
            lease_until=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(claim())
        batch.refresh_from_db()
        self.assertEqual(batch.state, batch.State.RUNNING)
        self.assertEqual(serialize(batch)['state'], batch.State.INTERRUPTED)

    def test_offline_click_queue_consume_and_list_data(self):
        reference = NoticeRef(source_code='ccgp_national', source_notice_id='t20260926_10000001',
                              title='Road monitoring public tender',
                              original_url='https://www.ccgp.gov.cn/cggg/zygg/gkzb/202609/t20260926_10000001.htm',
                              published_at='2026-09-26')
        html = '<html><title>Road monitoring public tender</title><p>发布时间：2026-09-26 10:30</p><p>项目编号：YL-1</p></html>'.encode('utf8')

        class OfflineAdapter:
            def preflight(self):
                return SourcePreflight(source_code='ccgp_national', state=SourceState.OK)

            def list_notices(self, *, page, page_size):
                return [reference] if page == 1 else []

            def fetch_detail(self, ref):
                return FetchResult(source_code=ref.source_code, source_notice_id=ref.source_notice_id,
                                   original_url=ref.original_url, fetched_at=timezone.now().isoformat(),
                                   http_status=200, content_type='text/html', raw_bytes=html,
                                   sha256=sha256(html).hexdigest(), source_metadata={'list_published_at': ref.published_at,
                                                                                      'title_from_list': ref.title})

        with TemporaryDirectory() as directory, override_settings(TENDER_STORAGE_ROOT=directory):
            batch = enqueue(self.actor, ['ccgp_national'])
            execute_once(adapter_factory=lambda source: OfflineAdapter())
            batch.refresh_from_db()
            self.assertEqual(batch.state, batch.State.PARTIAL)
            self.assertEqual(TenderOpportunity.objects.count(), 1)
            self.assertEqual(batch.results['ccgp_national']['new_notices'], 1)
            self.assertFalse(batch.results['ccgp_national']['complete'])

    def test_single_page_public_source_ingests_without_claiming_complete_coverage(self):
        source = TenderSource.objects.create(code='sx_jk_ecai', name='sx_jk_ecai',
                                             adapter_code='sx_jk_ecai', enabled=True)
        notice_id = 'a' * 32
        reference = NoticeRef(source_code=source.code, source_notice_id=notice_id,
                              title='Public opportunity',
                              original_url=f'https://www.sxjkjcpt.com/portal/detail?chnlcode=tender&docid={notice_id}',
                              published_at=None)
        html = '<html><title>Public opportunity</title><p>发布时间：2026-09-27 10:30</p></html>'.encode('utf8')

        class SinglePageAdapter:
            def preflight(self):
                return SourcePreflight(source_code=source.code, state=SourceState.OK)

            def list_notices(self, *, page, page_size):
                if page != 1:
                    raise AssertionError('public source has no second page')
                return [reference]

            def fetch_detail(self, ref):
                return FetchResult(source_code=ref.source_code, source_notice_id=ref.source_notice_id,
                                   original_url=ref.original_url, fetched_at=timezone.now().isoformat(),
                                   http_status=200, content_type='text/html', raw_bytes=html,
                                   sha256=sha256(html).hexdigest(), source_metadata={'title_from_list': ref.title})

        with TemporaryDirectory() as directory, override_settings(TENDER_STORAGE_ROOT=directory):
            batch = enqueue(self.actor, [source.code])
            execute_once(adapter_factory=lambda item: SinglePageAdapter())
            batch.refresh_from_db()
            self.assertEqual(batch.state, batch.State.PARTIAL)
            self.assertEqual(TenderOpportunity.objects.filter(source=source).count(), 1)
            self.assertFalse(batch.results[source.code]['complete'])

    def test_progress_persists_per_source_and_partial_is_not_success(self):
        batch = enqueue(self.actor, [source.code for source in self.sources])

        def fake_run(source, **kwargs):
            if source.code == 'shxjkjt':
                self.assertEqual(TenderManualRefresh.objects.get(pk=batch.pk).results['ccgp_national']['state'], 'SUCCESS')
                return SourceRunResult(source.code, 'BLOCKED', run_id=13)
            return SourceRunResult(source.code, 'SUCCESS', run_id=12, complete=True)

        with patch('portal.tender_manual_refresh.run_source', side_effect=fake_run):
            execute_once()
        batch.refresh_from_db()
        self.assertEqual(batch.state, batch.State.PARTIAL)
        self.assertEqual(batch.results['shxjkjt']['state'], 'BLOCKED')