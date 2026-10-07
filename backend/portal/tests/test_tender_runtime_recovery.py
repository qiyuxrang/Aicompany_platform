import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.db import OperationalError, close_old_connections, connection, transaction
from django.db.models.query import QuerySet
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from portal.tender_manual_refresh import execute_once, serialize
from portal.tender_models import TenderFetchRun, TenderManualRefresh, TenderSource
from portal.tender_runtime import LeaseHeartbeat, database_retry
from portal.tender_sources.base import FetchResult, NoticeRef, SourcePreflight, SourceState
from portal.tender_window import WindowCandidates
from portal.tender_worker import SourceRunResult, _recover_execution_failure, run_source


@override_settings(PORTAL_TENDER_INGESTION_ENABLED=True, PORTAL_TENDER_MANUAL_REFRESH_ENABLED=True,
                   TENDER_HEARTBEAT_SECONDS=600)
class RuntimeRecoveryTests(TransactionTestCase):
    def setUp(self):
        self.first = TenderSource.objects.create(code='ccgp_national', adapter_code='ccgp_national', name='First', enabled=True)
        self.second = TenderSource.objects.create(code='shxjkjt', adapter_code='shxjkjt', name='Second', enabled=True)
        self.calls = []

    def adapter(self, source, *, complete=False):
        owner = self
        class Adapter:
            def preflight(self):
                return SourcePreflight(source_code=source.code, state=SourceState.OK)

            def scan_window(self, **kwargs):
                refs = ([NoticeRef(source.code, str(index), '信息系统采购公告', 'https://www.ccgp.gov.cn/notice.htm')
                         for index in (1, 2)] if source.pk == owner.first.pk else [])
                return WindowCandidates(refs, complete, 'bounded_scan')

            def fetch_detail(self, ref):
                owner.calls.append((source.code, ref.source_notice_id))
                return FetchResult(source.code, ref.source_notice_id, ref.original_url,
                                   timezone.now().isoformat(), 200, 'text/html', b'<p>notice</p>', '')
        return Adapter()

    def ingest_result(self):
        return SimpleNamespace(notice_created=True, version_created=True, events=[])

    def health_lock(self, attempts):
        original = QuerySet.update
        remaining = [attempts]
        def update(queryset, **kwargs):
            if (queryset.model is TenderSource and 'last_success_at' in kwargs and
                    kwargs.get('health_state') == TenderSource.Health.DEGRADED and remaining[0]):
                remaining[0] -= 1
                raise OperationalError('database is locked diagnostic-only-token')
            return original(queryset, **kwargs)
        return update

    def test_exhausted_writer_lock_finishes_owned_source_retains_progress_and_runs_next_source(self):
        batch = TenderManualRefresh.objects.create(source_codes=[self.first.code, self.second.code])
        with patch('django.db.models.query.QuerySet.update', new=self.health_lock(4)), \
                patch('portal.tender_runtime.time.sleep'), \
                patch('portal.tender_worker.tender_service.ingest_fetch_result', return_value=self.ingest_result()), \
                self.assertLogs('portal.tender_worker', level='ERROR') as logs:
            execute_once(adapter_factory=self.adapter)
        batch.refresh_from_db()
        first, second = batch.results[self.first.code], batch.results[self.second.code]
        self.assertEqual(first['state'], 'WAITING_RETRY')
        self.assertEqual(first['ingested'], 1)
        self.assertEqual(first['listed'], 2)
        self.assertIsNotNone(first['run_id'])
        persisted = TenderFetchRun.objects.get(pk=first['run_id'])
        self.assertEqual(persisted.stats['ingested'], 1)
        self.assertEqual(persisted.state, 'WAITING_RETRY')
        self.assertEqual(persisted.error_code, 'execution_failed')
        self.assertIsNone(persisted.lease_until)
        self.assertEqual(second['state'], 'PARTIAL')
        self.assertIsNotNone(second['run_id'])
        self.assertFalse(TenderFetchRun.objects.filter(state='RUNNING').exists())
        self.assertNotIn('diagnostic-only-token', str(serialize(batch)))
        self.assertIn('run=', '\n'.join(logs.output))

    def test_brief_write_lock_retries_only_db_write_not_detail_fetch_or_ingest(self):
        if connection.vendor != 'sqlite':
            self.skipTest('Only SQLite transient writer locks are retried by database_retry')
        with patch('django.db.models.query.QuerySet.update', new=self.health_lock(2)), \
                patch('portal.tender_runtime.time.sleep'), \
                patch('portal.tender_worker.tender_service.ingest_fetch_result', return_value=self.ingest_result()) as ingest:
            result = run_source(self.first, adapter=self.adapter(self.first))
        self.assertEqual(result.state, 'PARTIAL')
        self.assertEqual(result.ingested, 2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(ingest.call_count, 2)

    def test_postgresql_sqlite_lock_text_is_not_retried_and_worker_preserves_progress(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL must not interpret SQLite lock text as retry authorization')
        with patch('django.db.models.query.QuerySet.update', new=self.health_lock(1)), \
                patch('portal.tender_runtime.time.sleep') as sleep, \
                patch('portal.tender_worker.tender_service.ingest_fetch_result', return_value=self.ingest_result()) as ingest, \
                self.assertLogs('portal.tender_worker', level='ERROR'):
            result = run_source(self.first, adapter=self.adapter(self.first))
        self.assertEqual(result.state, 'WAITING_RETRY')
        self.assertEqual(result.error_code, 'execution_failed')
        self.assertEqual(result.ingested, 1)
        self.assertEqual(len(self.calls), 1)
        ingest.assert_called_once()
        sleep.assert_not_called()
        persisted = TenderFetchRun.objects.get(pk=result.run_id)
        self.assertEqual(persisted.state, 'WAITING_RETRY')
        self.assertEqual(persisted.stats['ingested'], 1)
        self.assertIsNone(persisted.lease_until)
        self.assertNotIn('diagnostic-only-token', str(result.to_dict()))

    def test_postgresql_actual_nowait_lock_error_is_not_silently_retried(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Actual PostgreSQL row-lock SQLSTATE regression')
        # A separate connection holds a real row lock. Each attempted operation
        # owns its whole transaction, so the error cannot poison the caller.
        locker = connection.copy(alias='tender_lock_probe')
        locker_pool = locker.pool
        raw_locker = None
        table = connection.ops.quote_name(TenderSource._meta.db_table)
        try:
            locker.set_autocommit(False)
            raw_locker = locker.connection
            with locker.cursor() as cursor:
                cursor.execute(f'SELECT id FROM {table} WHERE id = %s FOR UPDATE', [self.first.pk])
            def operation():
                with transaction.atomic():
                    TenderSource.objects.select_for_update(nowait=True).get(pk=self.first.pk)
            attempted = Mock(side_effect=operation)
            with patch('portal.tender_runtime.time.sleep') as sleep:
                with self.assertRaises(OperationalError) as failed:
                    database_retry(attempted)
            self.assertEqual(failed.exception.__cause__.sqlstate, '55P03')
            attempted.assert_called_once()
            sleep.assert_not_called()
            self.assertFalse(connection.needs_rollback)
        finally:
            try:
                locker.rollback()
            finally:
                try:
                    locker.close()
                finally:
                    # copy() is not registered with Django's connections.
                    # close() returns its checkout; this private alias's pool
                    # must also close before normal test database destruction.
                    if locker_pool is not None:
                        locker.close_pool()
        self.assertIsNone(locker.connection)
        self.assertTrue(raw_locker.closed)
        if locker_pool is not None:
            self.assertTrue(locker_pool.closed)
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            self.assertEqual(cursor.fetchone(), (1,))
        # The failed operation did not leave a broken transaction or row change.
        with transaction.atomic():
            row = TenderSource.objects.select_for_update(nowait=True).get(pk=self.first.pk)
        self.assertEqual(row.health_state, self.first.health_state)

    def test_persistent_failure_to_finish_stops_batch_without_fabricating_later_source_failures(self):
        batch = TenderManualRefresh.objects.create(source_codes=[self.first.code, self.second.code])
        with patch('portal.tender_worker._finish', side_effect=OperationalError('database is locked')), \
                patch('portal.tender_worker.tender_service.ingest_fetch_result', return_value=self.ingest_result()), \
                patch('portal.tender_runtime.time.sleep'), self.assertLogs('portal.tender_worker', level='ERROR'):
            execute_once(adapter_factory=self.adapter)
        batch.refresh_from_db()
        self.assertEqual(batch.state, 'RUNNING')
        self.assertEqual(batch.results[self.first.code]['error_code'], 'recovery_required')
        self.assertIsNotNone(batch.results[self.first.code]['run_id'])
        self.assertNotIn(self.second.code, batch.results)
        self.assertFalse(TenderFetchRun.objects.filter(source=self.second).exists())

    def test_health_failure_after_completion_preserves_committed_outcome(self):
        with patch('portal.tender_worker.tender_service.record_source_health', side_effect=RuntimeError('diagnostic-only-token')), \
                self.assertLogs('portal.tender_worker', level='ERROR'):
            result = run_source(self.second, adapter=self.adapter(self.second, complete=True))
        self.assertEqual(result.state, 'SUCCESS')
        self.assertTrue(result.complete)
        self.assertEqual(TenderFetchRun.objects.get(pk=result.run_id).state, 'SUCCESS')
        self.assertNotIn('diagnostic-only-token', str(result.to_dict()))

    def test_failure_cleanup_cannot_overwrite_a_new_fence_or_an_expired_lease(self):
        for changed_fence, expired in ((True, False), (False, True)):
            with self.subTest(changed_fence=changed_fence):
                run = TenderFetchRun.objects.create(source=self.first, state='RUNNING', fence=2 if changed_fence else 1,
                                                    lease_until=timezone.now() + timedelta(seconds=-1 if expired else 120))
                result = _recover_execution_failure(self.first, {'run': run, 'fence': 1,
                                                     'result': SourceRunResult(self.first.code, 'RUNNING', run_id=run.pk)})
                self.assertEqual(result.error_code, 'lease_lost')
                run.refresh_from_db()
                self.assertEqual(run.state, 'RUNNING')
                run.delete()

    def test_non_lock_database_errors_are_never_silently_retried(self):
        operation = Mock(side_effect=OperationalError('no such table: corruption'))
        with self.assertRaises(OperationalError):
            database_retry(operation)
        operation.assert_called_once()

    def test_foreground_heartbeat_survives_actual_other_connection_writer_lock(self):
        if connection.vendor != 'sqlite':
            self.skipTest('SQLite-specific concurrent writer regression')
        run = TenderFetchRun.objects.create(source=self.first, state='RUNNING', fence=1,
                                            lease_until=timezone.now() + timedelta(seconds=120))
        heartbeat = LeaseHeartbeat()
        heartbeat.attach_source(run, 1)
        writer_ready = threading.Event()
        errors = []
        def writer():
            close_old_connections()
            try:
                with transaction.atomic():
                    TenderFetchRun.objects.filter(pk=run.pk).update(stats={'maintenance': 'completed'})
                    writer_ready.set()
                    threading.Event().wait(0.2)
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()
        thread = threading.Thread(target=writer)
        thread.start()
        self.assertTrue(writer_ready.wait(3))
        try:
            heartbeat.guard()
        finally:
            thread.join(timeout=3)
            heartbeat.detach_source()
        self.assertFalse(errors)
        self.assertFalse(thread.is_alive())
        self.assertFalse(heartbeat.lost.is_set())
        self.assertIsNone(heartbeat.source)
        run.refresh_from_db()
        self.assertEqual(run.stats, {'maintenance': 'completed'})
