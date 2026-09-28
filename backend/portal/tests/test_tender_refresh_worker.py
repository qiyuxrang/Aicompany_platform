from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from portal.tender_manual_refresh import ActiveRefresh, claim, enqueue, execute_once, serialize
from portal.tender_models import TenderConsumerHeartbeat, TenderManualRefresh, TenderSource
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