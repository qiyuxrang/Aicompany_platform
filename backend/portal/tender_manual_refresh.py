"""人工与每小时调度共用单队列，来源顺序执行且持久保存进度。"""

from datetime import timedelta
import logging

from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from .security import audit
from .tender_models import TenderConsumerHeartbeat, TenderFetchRun, TenderManualRefresh, TenderSource
from .tender_worker import SourceRunResult, run_source
from .tender_runtime import LeaseHeartbeat, LeaseLost, database_retry
from .tender_window import BEIJING

VERIFIED_SOURCES = frozenset({'ccgp_national', 'sx_jk_ecai', 'shxjkjt', 'csg_bidding', 'qinyuan', 'zmzb', 'chnenergy', 'yuneng'})
logger = logging.getLogger(__name__)


class ActiveRefresh(Exception):
    def __init__(self, batch_id):
        self.batch_id = batch_id
        super().__init__('已有待执行或执行中的刷新批次')


def consumer_online(now=None):
    return TenderConsumerHeartbeat.objects.filter(
        slot=1, updated_at__gt=(now or timezone.now()) - timedelta(seconds=15)).exists()


def schedule_status():
    now = timezone.now()
    enabled = bool(getattr(settings, 'PORTAL_TENDER_INGESTION_ENABLED', False)
                   and getattr(settings, 'PORTAL_TENDER_SCHEDULE_ENABLED', False)
                   and TenderSource.objects.filter(code__in=VERIFIED_SOURCES, enabled=True).exists())
    slot = now.astimezone(BEIJING).replace(minute=0, second=0, microsecond=0)
    # At startup the current slot is due immediately; never replay old slots.
    next_run = (slot + timedelta(hours=1) if TenderManualRefresh.objects.filter(
        scheduled_for=slot).exists() else now) if enabled and consumer_online(now) else None
    return {'enabled': enabled, 'interval_minutes': 60,
            'lookback_days': getattr(settings, 'TENDER_LOOKBACK_DAYS', 30),
            'next_run_at': next_run.isoformat() if next_run else None}


def enqueue_due(*, now=None):
    """Create at most one scheduled batch for this hour; no catch-up backlog."""
    try:
        return _enqueue_due(now=now)
    except OperationalError:
        # SQLite contention is retried on the next consumer tick, never by
        # launching an unrecorded collection outside the queue.
        logger.exception('Tender scheduling database operation failed; retrying on next consumer tick')
        return None


def _enqueue_due(*, now=None):
    if not (getattr(settings, 'PORTAL_TENDER_INGESTION_ENABLED', False)
            and getattr(settings, 'PORTAL_TENDER_SCHEDULE_ENABLED', False)):
        return None
    moment = now or timezone.now()
    slot = moment.astimezone(BEIJING).replace(minute=0, second=0, microsecond=0)
    if TenderManualRefresh.objects.filter(state__in=['QUEUED', 'RUNNING']).exists():
        return None
    if TenderManualRefresh.objects.filter(scheduled_for=slot).exists():
        return None
    codes = list(TenderSource.objects.filter(code__in=VERIFIED_SOURCES, enabled=True)
                 .order_by('code').values_list('code', flat=True))
    if not codes:
        return None
    try:
        with transaction.atomic():
            return TenderManualRefresh.objects.create(
                trigger='scheduled', scheduled_for=slot, requested_by=None, source_codes=codes)
    except IntegrityError:
        # Both the hourly slot and the shared active slot have database uniqueness.
        return None


def enqueue(actor, source_codes: list[str]) -> TenderManualRefresh:
    if not (settings.PORTAL_TENDER_INGESTION_ENABLED and settings.PORTAL_TENDER_MANUAL_REFRESH_ENABLED):
        raise ValueError('刷新未启用')
    if not consumer_online():
        raise ValueError('后台消费者未运行')
    if not source_codes or len(set(source_codes)) != len(source_codes) or set(source_codes) - VERIFIED_SOURCES:
        raise ValueError('来源必须非空、唯一且均已验收')
    if TenderSource.objects.filter(code__in=source_codes, enabled=True).count() != len(source_codes):
        raise ValueError('来源未启用')
    try:
        with transaction.atomic():
            return TenderManualRefresh.objects.create(requested_by=actor, source_codes=source_codes)
    except IntegrityError:
        active = TenderManualRefresh.objects.filter(
            state__in=[TenderManualRefresh.State.QUEUED, TenderManualRefresh.State.RUNNING]).first()
        if active is None:
            raise
        raise ActiveRefresh(active.pk) from None


def serialize(batch: TenderManualRefresh) -> dict:
    state = batch.state
    if state == batch.State.RUNNING and batch.lease_until and batch.lease_until <= timezone.now():
        state = batch.State.INTERRUPTED
    safe_results = {code: {key: value for key, value in result.items()
                           if key in ('source_code', 'state', 'run_id', 'complete', 'listed',
                                      'ingested', 'new_notices', 'new_versions', 'events',
                                      'skipped', 'out_of_window', 'unverified', 'coverage_reason', 'error_code')}
                    for code, result in batch.results.items() if isinstance(result, dict)}
    return {'id': str(batch.pk), 'state': state, 'source_codes': batch.source_codes,
            'trigger': batch.trigger,
            'scheduled_for': batch.scheduled_for.isoformat() if batch.scheduled_for else None,
            'results': safe_results, 'created_at': batch.created_at.isoformat(),
            'started_at': batch.started_at.isoformat() if batch.started_at else None,
            'finished_at': batch.finished_at.isoformat() if batch.finished_at else None}


def claim() -> tuple[TenderManualRefresh, int] | None:
    try:
        return database_retry(_claim, label='claim tender refresh batch')
    except OperationalError:
        logger.exception('Tender batch claim database operation failed; retrying on next consumer tick')
        return None


def _claim():
    now = timezone.now()
    with transaction.atomic():
        if TenderManualRefresh.objects.filter(state=TenderManualRefresh.State.RUNNING).exists():
            return None
        if TenderFetchRun.objects.filter(state=TenderFetchRun.State.RUNNING).exists():
            return None
        batch = (TenderManualRefresh.objects.select_for_update()
                 .filter(state=TenderManualRefresh.State.QUEUED)
                 .order_by('created_at').first())
        if batch is None:
            return None
        deadline = now + timedelta(seconds=getattr(settings, 'TENDER_LEASE_SECONDS', 180))
        updated = TenderManualRefresh.objects.filter(pk=batch.pk, fence=batch.fence,
                                                       state=batch.state).update(
            state=batch.State.RUNNING, fence=batch.fence + 1,
            started_at=batch.started_at or now, lease_until=deadline)
        if not updated:
            return None
        batch.refresh_from_db()
        return batch, batch.fence


def _owned(batch, fence):
    return TenderManualRefresh.objects.filter(
        pk=batch.pk, state=batch.State.RUNNING, fence=fence, lease_until__gt=timezone.now())


def execute_once(*, adapter_factory=None) -> TenderManualRefresh | None:
    if not (getattr(settings, 'PORTAL_TENDER_INGESTION_ENABLED', False) and
            (getattr(settings, 'PORTAL_TENDER_MANUAL_REFRESH_ENABLED', False)
             or getattr(settings, 'PORTAL_TENDER_SCHEDULE_ENABLED', False))):
        return None
    claimed = claim()
    if claimed is None:
        return None
    batch, fence = claimed
    try:
        with LeaseHeartbeat(batch=batch, fence=fence, publish_consumer=True) as heartbeat:
            return _execute_claimed(batch, fence, adapter_factory, heartbeat)
    except LeaseLost:
        logger.warning('Tender refresh ownership lost; batch=%s fence=%s', batch.pk, fence)
        _refresh_safely(batch)
        return batch
    except Exception:
        logger.exception('Tender refresh execution failed; batch=%s fence=%s', batch.pk, fence)
        try:
            active = database_retry(lambda: TenderFetchRun.objects.filter(
                source__code__in=batch.source_codes, state=TenderFetchRun.State.RUNNING).exists(),
                label='check interrupted tender source ownership')
            if not active:
                database_retry(lambda: _owned(batch, fence).update(
                    state=batch.State.PARTIAL, finished_at=timezone.now(), lease_until=None,
                    results=batch.results), label='finish interrupted tender refresh')
            else:
                logger.error('Tender refresh needs explicit recovery; unfinished source remains; batch=%s', batch.pk)
        except Exception:
            logger.exception('Tender refresh failure could not be persisted; batch=%s fence=%s', batch.pk, fence)
        _refresh_safely(batch)
        return batch


def _refresh_safely(batch):
    try:
        database_retry(batch.refresh_from_db, label='read tender refresh state')
    except Exception:
        logger.exception('Cannot reload tender refresh state; batch=%s', batch.pk)


def _execute_claimed(batch, fence, adapter_factory, heartbeat):
    results = dict(batch.results)
    for code in batch.source_codes:
        heartbeat.guard()
        if not database_retry(lambda: _owned(batch, fence).exists(), label='check tender refresh ownership'):
            return batch
        if results.get(code, {}).get('state') == 'SUCCESS' and results[code].get('complete'):
            continue
        source = database_retry(lambda: TenderSource.objects.filter(code=code, enabled=True).first(),
                                label='read tender refresh source')
        if source is None or code not in VERIFIED_SOURCES:
            result = SourceRunResult(code, 'FAILED', error_code='source_disabled')
        else:
            try:
                adapter = adapter_factory(source) if adapter_factory else None
                result = run_source(source, adapter=adapter, actor=batch.requested_by, heartbeat=heartbeat)
            except LeaseLost:
                raise
            except Exception:  # worker 兜底，异常不得输出到页面/阻断后续来源
                logger.exception('Tender source dispatch failed; batch=%s source=%s', batch.pk, code)
                unfinished = database_retry(lambda: TenderFetchRun.objects.filter(
                    source=source, state=TenderFetchRun.State.RUNNING).order_by('-created_at').first(),
                    label='read interrupted tender source')
                result = (SourceRunResult(code, 'SKIPPED', run_id=unfinished.pk, error_code='recovery_required')
                          if unfinished else SourceRunResult(code, 'FAILED', error_code='execution_failed'))
        results[code] = {key: value for key, value in result.to_dict().items()
                         if key not in ('error_detail', 'notes')}
        batch.results = results
        if not database_retry(lambda: _owned(batch, fence).update(results=results),
                              label='persist tender refresh progress'):
            return batch
        if result.error_code in ('recovery_required', 'lease_lost'):
            _refresh_safely(batch)
            return batch
    complete = all(results.get(code, {}).get('state') == 'SUCCESS' and
                   results[code].get('complete') for code in batch.source_codes)
    state = batch.State.SUCCESS if complete else batch.State.PARTIAL
    if database_retry(lambda: _owned(batch, fence).update(state=state, finished_at=timezone.now(), lease_until=None,
                                                        results=results), label='complete tender refresh'):
        database_retry(lambda: audit(batch.requested_by, 'tender_manual_refresh', str(batch.pk), changes=['state']),
                       label='audit tender refresh completion')
    _refresh_safely(batch)
    return batch
