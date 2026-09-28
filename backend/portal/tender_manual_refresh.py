"""人工刷新批次：Web 只入队，显式启动的命令才消费。"""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .security import audit
from .tender_models import TenderConsumerHeartbeat, TenderManualRefresh, TenderSource
from .tender_worker import SourceRunResult, run_source

VERIFIED_SOURCES = frozenset({'ccgp_national', 'sx_jk_ecai', 'shxjkjt', 'csg_bidding'})


class ActiveRefresh(Exception):
    def __init__(self, batch_id):
        self.batch_id = batch_id
        super().__init__('已有待执行或执行中的刷新批次')


def enqueue(actor, source_codes: list[str]) -> TenderManualRefresh:
    if not (settings.PORTAL_TENDER_INGESTION_ENABLED and settings.PORTAL_TENDER_MANUAL_REFRESH_ENABLED):
        raise ValueError('刷新未启用')
    heartbeat = TenderConsumerHeartbeat.objects.filter(slot=1, updated_at__gt=timezone.now() - timedelta(seconds=60)).exists()
    if not heartbeat:
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
                                      'skipped', 'error_code')}
                    for code, result in batch.results.items() if isinstance(result, dict)}
    return {'id': str(batch.pk), 'state': state, 'source_codes': batch.source_codes,
            'results': safe_results, 'created_at': batch.created_at.isoformat(),
            'started_at': batch.started_at.isoformat() if batch.started_at else None,
            'finished_at': batch.finished_at.isoformat() if batch.finished_at else None}


def claim() -> tuple[TenderManualRefresh, int] | None:
    now = timezone.now()
    with transaction.atomic():
        if TenderManualRefresh.objects.filter(state=TenderManualRefresh.State.RUNNING).exists():
            return None
        batch = (TenderManualRefresh.objects.select_for_update()
                 .filter(state=TenderManualRefresh.State.QUEUED)
                 .order_by('created_at').first())
        if batch is None:
            return None
        deadline = (batch.started_at or now) + timedelta(minutes=15)
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
            getattr(settings, 'PORTAL_TENDER_MANUAL_REFRESH_ENABLED', False)):
        return None
    claimed = claim()
    if claimed is None:
        return None
    batch, fence = claimed
    results = dict(batch.results)
    for code in batch.source_codes:
        if not _owned(batch, fence).exists():
            return batch
        if results.get(code, {}).get('state') == 'SUCCESS' and results[code].get('complete'):
            continue
        source = TenderSource.objects.filter(code=code, enabled=True).first()
        if source is None or code not in VERIFIED_SOURCES:
            result = SourceRunResult(code, 'FAILED', error_code='source_disabled')
        else:
            try:
                adapter = adapter_factory(source) if adapter_factory else None
                result = run_source(source, adapter=adapter, actor=batch.requested_by)
            except Exception:  # worker 兜底，异常不得输出到页面/阻断后续来源
                result = SourceRunResult(code, 'FAILED', error_code='execution_failed')
        results[code] = {key: value for key, value in result.to_dict().items()
                         if key not in ('error_detail', 'notes')}
        if not _owned(batch, fence).update(results=results):
            return batch
        if result.error_code == 'recovery_required':
            batch.refresh_from_db()
            return batch
    complete = all(results.get(code, {}).get('state') == 'SUCCESS' and
                   results[code].get('complete') for code in batch.source_codes)
    state = batch.State.SUCCESS if complete else batch.State.PARTIAL
    if _owned(batch, fence).update(state=state, finished_at=timezone.now(), lease_until=None,
                                   results=results):
        audit(batch.requested_by, 'tender_manual_refresh', str(batch.pk), changes=['state'])
    batch.refresh_from_db()
    return batch
