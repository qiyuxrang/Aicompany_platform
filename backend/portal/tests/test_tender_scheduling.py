import hashlib
import threading
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from portal.tender_manual_refresh import ActiveRefresh, claim, enqueue, enqueue_due, execute_once, schedule_status
from portal.tender_models import TenderConsumerHeartbeat, TenderFetchRun, TenderManualRefresh, TenderOpportunity, TenderSource
from portal.tender_recovery import RecoveryRejected, confirm_batch, confirm_source
from portal.tender_runtime import LeaseHeartbeat, LeaseLost
from portal.tender_service import TenderIngestRejected, ingest_fetch_result
from portal.tender_sources.base import BlockReason, FetchResult, NoticeRef, SourceBlocked, SourcePreflight, SourceState
from portal.tender_window import classify_window_date, window_bounds, WindowCandidates
from portal.tender_worker import run_all, run_source, SourceRunResult
from .base import PortalTestCase

BEIJING = ZoneInfo('Asia/Shanghai')


class RollingWindowTests(SimpleTestCase):
    def test_default_thirty_days_move_at_beijing_midnight(self):
        moment = datetime(2027, 1, 1, 0, 1, tzinfo=BEIJING)
        start, end = window_bounds(moment)
        self.assertEqual(start, datetime(2026, 12, 3, tzinfo=BEIJING))
        self.assertEqual(end, datetime(2027, 1, 2, tzinfo=BEIJING))
        self.assertEqual(classify_window_date('2026-12-02', window=(start, end)), 'before')
        self.assertEqual(classify_window_date('2026-12-03', window=(start, end)), 'inside')
        self.assertEqual(classify_window_date('2027-01-02', window=(start, end)), 'after')

    @override_settings(TENDER_LOOKBACK_DAYS=3)
    def test_configured_window_is_bounded_and_rejects_naive_now(self):
        start, end = window_bounds(datetime(2027, 1, 1, tzinfo=BEIJING))
        self.assertEqual((end - start).days, 3)
        for invalid in (0, 31, -1, '7'):
            with self.assertRaises(ValueError):
                window_bounds(datetime(2027, 1, 1, tzinfo=BEIJING), days=invalid)
        with self.assertRaises(ValueError):
            window_bounds(datetime(2027, 1, 1))


@override_settings(PORTAL_TENDER_INGESTION_ENABLED=True, PORTAL_TENDER_SCHEDULE_ENABLED=True,
                   PORTAL_TENDER_MANUAL_REFRESH_ENABLED=True)
class SchedulingTests(TestCase):
    def setUp(self):
        self.now = datetime(2027, 1, 1, 14, 35, tzinfo=BEIJING)
        self.source = TenderSource.objects.create(code='ccgp_national', name='全国', adapter_code='ccgp_national', enabled=True)
        TenderConsumerHeartbeat.objects.create(slot=1, updated_at=self.now)

    def test_one_slot_per_hour_including_partial_and_no_old_slot_backlog(self):
        batch = enqueue_due(now=self.now)
        self.assertEqual(batch.trigger, 'scheduled')
        self.assertIsNone(batch.requested_by_id)
        self.assertEqual(batch.scheduled_for, self.now.replace(minute=0))
        self.assertIsNone(enqueue_due(now=self.now))
        batch.state = 'PARTIAL'
        batch.save()
        self.assertIsNone(enqueue_due(now=self.now + timedelta(minutes=10)))
        later = enqueue_due(now=self.now + timedelta(hours=5))
        self.assertEqual(later.scheduled_for.hour, 19)
        self.assertEqual(TenderManualRefresh.objects.count(), 2)

    def test_manual_and_scheduled_requests_share_one_active_slot(self):
        scheduled = enqueue_due(now=self.now)
        with patch('portal.tender_manual_refresh.timezone.now', return_value=self.now):
            with self.assertRaises(ActiveRefresh):
                enqueue(None, [self.source.code])
            scheduled.state = 'PARTIAL'
            scheduled.save()
            manual = enqueue(None, [self.source.code])
        self.assertEqual(manual.trigger, 'manual')
        self.assertIsNone(enqueue_due(now=self.now + timedelta(hours=1)))

    def test_disabled_schedule_and_offline_status_never_imply_a_running_plan(self):
        with patch('portal.tender_manual_refresh.timezone.now', return_value=self.now):
            status = schedule_status()
            self.assertTrue(status['enabled'])
            self.assertEqual(status['interval_minutes'], 60)
            self.assertEqual(status['lookback_days'], 30)
            self.assertEqual(status['next_run_at'], self.now.isoformat())
            enqueue_due(now=self.now)
            self.assertEqual(schedule_status()['next_run_at'], self.now.replace(hour=15, minute=0).isoformat())
            TenderConsumerHeartbeat.objects.update(updated_at=self.now - timedelta(minutes=1))
            self.assertTrue(schedule_status()['enabled'])
            self.assertIsNone(schedule_status()['next_run_at'])
        with override_settings(PORTAL_TENDER_SCHEDULE_ENABLED=False):
            self.assertIsNone(enqueue_due(now=self.now + timedelta(hours=1)))

    def test_expired_source_run_blocks_queue_until_confirmed_stopped(self):
        enqueue_due(now=self.now)
        TenderFetchRun.objects.create(source=self.source, state='RUNNING', fence=1,
                                      lease_until=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(claim())

    def test_run_all_does_not_inject_a_frozen_time_into_production_workers(self):
        with patch('portal.tender_worker.run_source', return_value=SourceRunResult(self.source.code, 'PARTIAL')) as worker:
            run_all(sources=[self.source])
        self.assertIsNone(worker.call_args.kwargs['now'])


@override_settings(PORTAL_TENDER_INGESTION_ENABLED=True)
class WorkerProgressTests(TestCase):
    def setUp(self):
        self.now = datetime(2027, 1, 1, 14, 35, tzinfo=BEIJING)
        self.source = TenderSource.objects.create(code='ccgp_national', name='全国', adapter_code='ccgp_national', enabled=True)
        self.directory = TemporaryDirectory(prefix='tender-progress-')
        self.addCleanup(self.directory.cleanup)
        config = override_settings(TENDER_STORAGE_ROOT=Path(self.directory.name))
        config.enable()
        self.addCleanup(config.disable)

    def reference(self, identifier):
        return NoticeRef(source_code=self.source.code, source_notice_id=identifier, title='煤矿数字化建设招标公告',
                         original_url=f'https://www.ccgp.gov.cn/cggg/zygg/gkzb/202701/{identifier}.htm',
                         published_at='2027-01-01')

    def fetched(self, ref, *, published='2027-01-01', fetched_at=None):
        raw = (f'<title>{ref.title}</title><p>发布时间：{published} 10:00</p>'
               '<p>建设内容：建设信息系统。</p>').encode()
        return FetchResult(source_code=ref.source_code, source_notice_id=ref.source_notice_id,
                           original_url=ref.original_url, fetched_at=(fetched_at or self.now).isoformat(),
                           raw_bytes=raw, http_status=200, content_type='text/html', sha256=hashlib.sha256(raw).hexdigest())

    def test_partial_success_records_stats_and_success_time_without_full_coverage(self):
        owner = self
        ref = self.reference('t20270101_1')

        class Adapter:
            def preflight(self):
                return SourcePreflight(source_code=owner.source.code, state=SourceState.OK)

            def fetch_detail(self, item):
                return owner.fetched(item)

        listing = WindowCandidates([ref], False, 'candidate_limit')
        with patch('portal.tender_worker.list_window_candidates', return_value=listing):
            result = run_source(self.source, adapter=Adapter(), now=self.now)
        self.assertEqual(result.state, 'PARTIAL')
        self.assertEqual(result.error_code, 'coverage_partial')
        self.assertFalse(result.complete)
        run = TenderFetchRun.objects.get(pk=result.run_id)
        self.assertEqual(run.stats['ingested'], 1)
        self.assertEqual(run.stats['new_notices'], 1)
        self.assertFalse(run.stats['complete'])
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_success_at, self.now)
        self.assertEqual(self.source.health_state, 'degraded')
        self.assertEqual(TenderOpportunity.objects.count(), 1)
        # Later empty scans preserve historical opportunities.
        with patch('portal.tender_worker.list_window_candidates', return_value=WindowCandidates([], False, 'empty_page')):
            later = run_source(self.source, adapter=Adapter(), now=self.now + timedelta(days=20))
        self.assertNotEqual(later.run_id, result.run_id)
        self.assertEqual(TenderOpportunity.objects.count(), 1)

    def test_source_protection_stops_remaining_details_and_retains_committed_progress(self):
        owner = self
        refs = [self.reference(f't20270101_{number}') for number in (1, 2, 3)]
        requested = []

        class Adapter:
            def preflight(self):
                return SourcePreflight(source_code=owner.source.code, state=SourceState.OK)

            def fetch_detail(self, ref):
                requested.append(ref.source_notice_id)
                if ref == refs[1]:
                    raise SourceBlocked(owner.source.code, [BlockReason.CAPTCHA], '验证阻断')
                return owner.fetched(ref)

        with patch('portal.tender_worker.list_window_candidates', return_value=WindowCandidates(refs, False, 'page_limit')):
            result = run_source(self.source, adapter=Adapter(), now=self.now)
        self.assertEqual(requested, [refs[0].source_notice_id, refs[1].source_notice_id])
        self.assertEqual(result.state, 'BLOCKED')
        self.assertEqual(result.error_code, 'source_blocked')
        self.assertEqual(result.ingested, 1)
        self.assertEqual(TenderFetchRun.objects.get(pk=result.run_id).stats['ingested'], 1)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_success_at, self.now)
        self.assertEqual(self.source.health_state, 'blocked')

    def test_ingest_honors_frozen_window_even_if_fetch_crosses_midnight(self):
        ref = self.reference('t20270101_4')
        frozen = window_bounds(self.now)
        fetched = self.fetched(ref, published='2026-12-03', fetched_at=self.now + timedelta(days=1))
        self.source.initial_coverage_complete = True
        self.source.save()
        with self.assertRaises(TenderIngestRejected):
            ingest_fetch_result(fetched, source=self.source)
        outcome = ingest_fetch_result(fetched, source=self.source, window=frozen)
        self.assertTrue(outcome.notice_created)


@override_settings(PORTAL_TENDER_INGESTION_ENABLED=True, PORTAL_TENDER_SCHEDULE_ENABLED=True,
                   TENDER_HEARTBEAT_SECONDS=0.02, TENDER_LEASE_SECONDS=180)
class RuntimeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.source = TenderSource.objects.create(code='ccgp_national', name='全国', adapter_code='ccgp_national', enabled=True)
        self.now = timezone.now()

    def owned(self):
        batch = TenderManualRefresh.objects.create(state='RUNNING', fence=1, trigger='scheduled',
                                                   lease_until=self.now + timedelta(seconds=180))
        run = TenderFetchRun.objects.create(source=self.source, state='RUNNING', fence=1,
                                           lease_until=self.now + timedelta(seconds=180))
        return batch, run

    def test_background_heartbeat_renews_during_long_blocking_work_and_cannot_revive_expired_owner(self):
        batch, run = self.owned()
        current = [self.now]
        with LeaseHeartbeat(batch=batch, fence=1, clock=lambda: current[0], publish_consumer=True) as heartbeat:
            heartbeat.attach_source(run, 1)
            current[0] += timedelta(seconds=120)
            threading.Event().wait(0.15)
            batch.refresh_from_db()
            run.refresh_from_db()
            self.assertEqual(batch.lease_until, current[0] + timedelta(seconds=180))
            self.assertEqual(run.lease_until, batch.lease_until)
            self.assertEqual(TenderConsumerHeartbeat.objects.get().updated_at, current[0])
            current[0] += timedelta(seconds=181)
            with self.assertRaises(LeaseLost):
                heartbeat.guard()
            run.refresh_from_db()
            self.assertLess(run.lease_until, current[0])

    def test_heartbeat_survives_brief_sqlite_write_contention(self):
        batch, run = self.owned()
        current = [self.now]
        with LeaseHeartbeat(batch=batch, fence=1, clock=lambda: current[0], publish_consumer=True) as heartbeat:
            heartbeat.attach_source(run, 1)
            with transaction.atomic():
                TenderFetchRun.objects.filter(pk=run.pk).update(stats={'writer': 'busy'})
                current[0] += timedelta(seconds=60)
                threading.Event().wait(0.08)
            threading.Event().wait(0.15)
            self.assertFalse(heartbeat.lost.is_set())
            run.refresh_from_db()
            self.assertEqual(run.lease_until, current[0] + timedelta(seconds=180))
            self.assertEqual(run.stats, {'writer': 'busy'})

    def race(self, operation):
        barrier = threading.Barrier(2)
        results, errors = [], []

        def participant():
            close_old_connections()
            try:
                barrier.wait(timeout=3)
                results.append(operation())
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=participant) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(errors, errors)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        return results

    def test_concurrent_schedulers_and_consumers_have_one_winner(self):
        self.race(lambda: enqueue_due(now=self.now))
        self.assertEqual(TenderManualRefresh.objects.count(), 1)
        results = self.race(claim)
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(TenderManualRefresh.objects.get().state, 'RUNNING')


class RecoveryTests(PortalTestCase):
    def test_recovery_requires_operator_and_expired_leases_then_preserves_evidence(self):
        actor = self.create_user('tender-operator', 'product')
        source = TenderSource.objects.create(code='ccgp_national', adapter_code='ccgp_national', name='全国')
        now = timezone.now()
        run = TenderFetchRun.objects.create(source=source, state='RUNNING', fence=3, attempt_count=1,
                                           lease_until=now + timedelta(seconds=30), stats={'ingested': 2})
        batch = TenderManualRefresh.objects.create(state='RUNNING', fence=4, source_codes=[source.code],
                                                   lease_until=now - timedelta(seconds=1), results={'ccgp_national': {'ingested': 2}})
        with override_settings(TENDER_RECOVERY_OPERATOR_IDS=[actor.pk]):
            with self.assertRaises(RecoveryRejected):
                confirm_source(run.pk, actor, '已确认旧进程停止')
            with self.assertRaises(RecoveryRejected):
                confirm_batch(batch.pk, actor, '已确认旧进程停止')
            TenderFetchRun.objects.filter(pk=run.pk).update(lease_until=now - timedelta(seconds=1))
            recovered = confirm_source(run.pk, actor, '已确认旧进程停止')
            self.assertEqual(recovered.fence, 4)
            self.assertEqual(recovered.stats, {'ingested': 2})
            finished = confirm_batch(batch.pk, actor, '已确认旧进程停止')
            self.assertEqual(finished.state, 'PARTIAL')
            self.assertEqual(finished.fence, 5)
            self.assertEqual(finished.results['ccgp_national']['ingested'], 2)
        with self.assertRaises(CommandError):
            call_command('confirm_tender_stopped', operator_id=actor.pk, reason='旧进程停止',
                         source_run=[run.pk], stdout=StringIO())

    def test_recovery_command_requires_confirmation_and_configured_operator(self):
        actor = self.create_user('recovery-command-user', 'product')
        source = TenderSource.objects.create(code='ccgp_national', adapter_code='ccgp_national', name='全国')
        run = TenderFetchRun.objects.create(source=source, state='RUNNING', fence=2,
                                           lease_until=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(CommandError):
            call_command('recover_tender_run', operator_id=actor.pk, reason='已确认旧进程停止',
                         source_run=[run.pk], stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('recover_tender_run', operator_id=actor.pk, reason='已确认旧进程停止',
                         confirm_process_stopped=True, source_run=[run.pk], stdout=StringIO())
        run.refresh_from_db()
        self.assertEqual(run.state, 'RUNNING')
        with override_settings(TENDER_RECOVERY_OPERATOR_IDS=[actor.pk]):
            with self.assertRaises(CommandError):
                call_command('recover_tender_run', operator_id=actor.pk, reason='已确认旧进程停止',
                             confirm_process_stopped=True, batch='invalid-uuid', stdout=StringIO())
            call_command('recover_tender_run', '--confirm-stopped', operator_id=actor.pk,
                         reason='已确认旧进程停止', source_run=[run.pk], stdout=StringIO())
        run.refresh_from_db()
        self.assertEqual(run.state, 'WAITING_RETRY')
        self.assertEqual(run.fence, 3)
