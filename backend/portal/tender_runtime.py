"""Owner-only lease renewal while bounded remote requests are in flight."""

import threading
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import OperationalError, close_old_connections, connection
from django.utils import timezone

from .tender_models import TenderConsumerHeartbeat, TenderFetchRun, TenderManualRefresh

logger = logging.getLogger(__name__)


def database_retry(operation, *, label='tender database operation', attempts=4):
    """Retry a complete DB operation only for SQLite's transient writer contention.

    Callers must put a whole transaction inside operation, never retry a statement
    in an already broken transaction, and never include outbound requests.
    """
    for index in range(attempts):
        try:
            return operation()
        except OperationalError as error:
            locked = connection.vendor == 'sqlite' and any(
                value in str(error).lower() for value in ('database is locked', 'database table is locked', 'database is busy'))
            if not locked or connection.needs_rollback or index + 1 >= attempts:
                raise
            logger.warning('%s: transient SQLite write contention; retry %s/%s', label, index + 1, attempts - 1)
            time.sleep(min(0.1 * (2 ** index), 0.5))


class LeaseLost(RuntimeError):
    pass


class LeaseHeartbeat:
    """Never revive an expired lease or claim that another process has stopped.

    The renewal thread uses its own DB connection and performs no network fetches.
    The owning worker checks the lost flag before every adapter call and data write.
    """

    def __init__(self, *, batch=None, fence=None, clock=None, publish_consumer=False):
        self.batch = (batch.pk, fence) if batch is not None else None
        self.source = None
        self.clock = clock or timezone.now
        self.publish_consumer = publish_consumer
        self.stopped = threading.Event()
        self.lost = threading.Event()
        self.lock = threading.RLock()
        self.thread = None

    def attach_source(self, run, fence):
        with self.lock:
            self.source = (run.pk, fence)
        self.guard()

    def detach_source(self):
        with self.lock:
            self.source = None

    def pulse(self):
        return database_retry(self._pulse, label='tender lease renewal')

    def _pulse(self):
        with self.lock:
            if self.lost.is_set():
                return False
            moment = self.clock()
            deadline = moment + timedelta(seconds=getattr(settings, 'TENDER_LEASE_SECONDS', 180))
            if self.batch:
                pk, fence = self.batch
                if not TenderManualRefresh.objects.filter(
                    pk=pk, fence=fence, state='RUNNING', lease_until__gt=moment,
                ).update(lease_until=deadline):
                    self.lost.set()
                    return False
            if self.source:
                pk, fence = self.source
                if not TenderFetchRun.objects.filter(
                    pk=pk, fence=fence, state='RUNNING', lease_until__gt=moment,
                ).update(lease_until=deadline):
                    # The owner may have just completed this source between ticks.
                    finished = TenderFetchRun.objects.filter(
                        pk=pk, fence=fence, state__in=['SUCCESS', 'PARTIAL', 'WAITING_RETRY', 'FAILED', 'BLOCKED'],
                        finished_at__isnull=False,
                    ).exists()
                    if finished:
                        self.source = None
                    else:
                        self.lost.set()
                        return False
            if self.publish_consumer:
                TenderConsumerHeartbeat.objects.update_or_create(slot=1, defaults={'updated_at': moment})
            return True

    def guard(self):
        if self.lost.is_set() or not self.pulse():
            raise LeaseLost('采集租约失效，停止后续操作并等待停机确认')

    def _loop(self):
        close_old_connections()
        try:
            interval = getattr(settings, 'TENDER_HEARTBEAT_SECONDS', 5)
            while not self.stopped.wait(interval):
                try:
                    if not self.pulse():
                        return
                except OperationalError:
                    # A short SQLite writer lock is retried; an expired lease is
                    # rejected on the next pulse and can never be resurrected.
                    close_old_connections()
                    logger.exception('Tender heartbeat database renewal failed; batch=%s source=%s', self.batch, self.source)
                except Exception:
                    logger.exception('Tender heartbeat stopped unexpectedly; batch=%s source=%s', self.batch, self.source)
                    self.lost.set()
                    return
        finally:
            close_old_connections()

    def __enter__(self):
        self.guard()
        self.thread = threading.Thread(target=self._loop, name='tender-lease-heartbeat', daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
