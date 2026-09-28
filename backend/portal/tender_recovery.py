"""过期采集人工停机确认：只改数据库状态，绝不向外站出站。"""

from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .security import audit
from .product_service import product_user_allowed
from .tender_models import TenderFetchRun, TenderManualRefresh, TenderRecoveryAudit, TenderSource


class RecoveryRejected(ValueError):
    pass


def _authorize(actor, reason):
    operator_ids = getattr(settings, 'TENDER_OPERATOR_IDS', ())
    if not operator_ids or not product_user_allowed(actor) or actor.pk not in operator_ids:
        raise RecoveryRejected('操作员不在已配置的 Tender 权限名单内')
    if not isinstance(reason, str):
        raise RecoveryRejected('原因必须是文本')
    text = reason.strip()
    if not text or len(text) > 200 or any(mark in text.lower() for mark in ('://', 'token', 'secret', 'password', '\n', '\r')):
        raise RecoveryRejected('原因必须为不包含链接/凭据的 1–200 字摘要')
    return text


def confirm_source(run_id: int, actor, reason: str) -> TenderFetchRun:
    text = _authorize(actor, reason)
    now = timezone.now()
    with transaction.atomic():
        try:
            run = TenderFetchRun.objects.get(pk=run_id)
            TenderSource.objects.select_for_update().get(pk=run.source_id)
        except (TenderFetchRun.DoesNotExist, TenderSource.DoesNotExist, TypeError, ValueError):
            raise RecoveryRejected('来源运行 ID 无效') from None
        state = (run.State.WAITING_RETRY if run.attempt_count < getattr(settings, 'TENDER_MAX_ATTEMPTS', 3)
                 else run.State.FAILED)
        updated = TenderFetchRun.objects.filter(
            pk=run_id, state=run.State.RUNNING, fence=run.fence,
            lease_until__lte=now).update(state=state, fence=F('fence') + 1,
                                         lease_until=None, finished_at=now)
        if not updated:
            raise RecoveryRejected('来源不是租约已过期的 RUNNING，或被并发修改')
        TenderRecoveryAudit.objects.create(operator=actor, target_type='source',
                                           target_id=str(run_id), reason=text)
        audit(actor, 'tender_recovery_source', str(run_id), changes=['state', 'fence'])
        run.refresh_from_db()
        return run


def confirm_batch(batch_id: UUID, actor, reason: str) -> TenderManualRefresh:
    text = _authorize(actor, reason)
    now = timezone.now()
    with transaction.atomic():
        try:
            batch = TenderManualRefresh.objects.select_for_update().get(pk=batch_id)
        except (TenderManualRefresh.DoesNotExist, ValueError):
            raise RecoveryRejected('批次 ID 无效') from None
        if batch.state != batch.State.RUNNING or batch.lease_until is None or batch.lease_until > now:
            raise RecoveryRejected('批次不是租约已过期的 RUNNING')
        if TenderFetchRun.objects.filter(source__code__in=batch.source_codes, state=TenderFetchRun.State.RUNNING).exists():
            raise RecoveryRejected('冻结的来源仍有 RUNNING，须先确认所有旧来源执行节点')
        expired = batch.started_at is None or batch.started_at + timedelta(minutes=15) <= now
        state = batch.State.PARTIAL if expired else batch.State.QUEUED
        updated = TenderManualRefresh.objects.filter(
            pk=batch_id, state=batch.State.RUNNING, fence=batch.fence,
            lease_until__lte=now).update(state=state, fence=F('fence') + 1,
                lease_until=None, finished_at=now if expired else None,
                error_code='budget_exceeded' if expired else '')
        if not updated:
            raise RecoveryRejected('批次被并发修改')
        TenderRecoveryAudit.objects.create(operator=actor, target_type='batch',
                                           target_id=str(batch_id), reason=text)
        audit(actor, 'tender_recovery_batch', str(batch_id), changes=['state', 'fence'])
        batch.refresh_from_db()
        return batch
