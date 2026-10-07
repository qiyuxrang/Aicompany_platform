"""HR screening guards shared by the API and asynchronous worker."""

import contextvars
from contextlib import contextmanager
from uuid import uuid4

from django.db import transaction
from django.db.models.signals import pre_save
from django.utils import timezone

from .agent_runtime import AgentDenied, RunBinding, RuntimeGuard
from .hr_retention import batch_expired
from .hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from .product_agent import lock_scope
from .product_service import ProductError
from .product_storage import StorageError


BINDING_FIELDS = (
    "agent_root_id", "agent_work_id", "agent_requirement_version", "agent_grant_version",
    "agent_session_version", "agent_root_fence",
)
_model_call = contextvars.ContextVar("hr_agent_model_call", default=None)


def binding_values(batch):
    return tuple(getattr(batch, field) for field in BINDING_FIELDS)


def lock_batch_scope(batch, owner=None):
    values = binding_values(batch)
    if all(value is None for value in values):
        return None
    if (any(value is None or value == "" for value in values[:2])
            or any(type(value) is not int or value <= 0 for value in values[2:])):
        raise ProductError("agent_binding_stale", "HR 批次的 Agent 关联不完整。", 409)
    root_id, work_id, requirement_version, grant_version, session_version, root_fence = values
    try:
        root, work = lock_scope(root_id, work_id, owner or batch.created_by, requirement_version,
                                grant_version, session_version, root_fence, department="hr")
        guard = RuntimeGuard(RunBinding(work.owner_id, str(root.pk), str(root.pk), grant_version,
                                        session_version, root_fence))
        if (not root.requirement_id or root.requirement.version != requirement_version):
            raise ProductError("agent_binding_stale", "HR 批次要求版本已变化。", 409)
        guard._authorize(root, root)
        return root, work, guard
    except AgentDenied as error:
        raise ProductError("agent_binding_stale", "HR 批次的 Agent 运行已失效。", 409) from error


def ensure_binding_unchanged(snapshot, batch):
    if binding_values(batch) != snapshot:
        raise ProductError("agent_binding_stale", "HR 批次的 Agent 关联已变化。", 409)


def lock_artifact(item_id, fence=None, *, allow_stale_scope=False):
    reference = ResumeArtifact.objects.select_related(
        "batch__jd_version__request", "batch__created_by").filter(pk=item_id).first()
    if reference is None:
        return None
    snapshot = binding_values(reference.batch)
    owner_id, jd_version_id = reference.batch.created_by_id, reference.batch.jd_version_id
    scope_error = None
    try:
        scope = lock_batch_scope(reference.batch, reference.batch.created_by)
    except ProductError as error:
        scope, scope_error = None, error
    batch = ResumeScreeningBatch.objects.select_for_update(of=("self",)).select_related(
        "jd_version__request", "created_by").filter(pk=reference.batch_id).first()
    if batch is None:
        return None
    try:
        ensure_binding_unchanged(snapshot, batch)
    except ProductError as error:
        scope, scope_error = None, error
    if batch.created_by_id != owner_id or batch.jd_version_id != jd_version_id:
        scope = None
        scope_error = ProductError("agent_binding_stale", "HR 批次归属或 JD 版本已变化。", 409)
    item = ResumeArtifact.objects.select_for_update(of=("self",)).select_related(
        "batch__jd_version__request", "batch__created_by").filter(pk=item_id, batch=batch).first()
    if item is None:
        return None
    if scope_error and not allow_stale_scope:
        raise scope_error
    return item, batch, batch.created_by, scope, scope_error


def check_artifact(item, batch, owner, fence=None):
    if item.archive_state != "active" or batch_expired(batch):
        raise StorageError("expired", "招聘记录已失效。")
    if fence is not None and item.fence != fence:
        raise StorageError("lease_lost", "处理租约已失效。")
    from .hr_api import _is_hr
    if not _is_hr(owner):
        raise StorageError("permission_changed", "人事授权已变化。")
    if batch.stale:
        raise StorageError("stale_revision", "招聘需求已变化。")


def current_artifact(item_id, fence=None):
    with transaction.atomic():
        locked = lock_artifact(item_id, fence)
        if locked is None:
            raise StorageError("lease_lost", "处理记录已清理。")
        item, batch, owner, scope, _ = locked
        check_artifact(item, batch, owner, fence)
        return item, owner, scope


def read_authorized_file(item_id, fence):
    from .hr_resume_storage import read_file

    with transaction.atomic():
        locked = lock_artifact(item_id, fence)
        if locked is None:
            raise StorageError("lease_lost", "处理记录已清理。")
        item, batch, owner, _, _ = locked
        check_artifact(item, batch, owner, fence)
        if (item.processing_status != "running" or not item.lease_until
                or item.lease_until <= timezone.now()):
            raise StorageError("lease_lost", "处理租约已失效。")
        return item, read_file(item.file_id, item.sha256)


@contextmanager
def model_action(item_id, fence):
    _, owner, scope = current_artifact(item_id, fence)
    if scope is None:
        error = None
        try:
            yield owner
        except BaseException as failure:
            error = failure
        try:
            current_artifact(item_id, fence)
        except BaseException as failure:
            if error is None:
                error = failure
        if error is not None:
            raise error
        return

    guard = scope[2]
    action_key = str(uuid4())
    guard.admit("model", action_key)
    status = "error"
    try:
        run = guard.check()
        _, owner, current_scope = current_artifact(item_id, fence)
        if current_scope is None or current_scope[2].binding != guard.binding:
            raise AgentDenied("agent_binding_stale")
        context = {
            "owner_id": owner.pk,
            "root_id": guard.binding.root_id,
            "run_id": guard.binding.root_id,
            "conversation_id": run.conversation_id,
            "work_id": run.work_id,
            "requirement_id": run.requirement_id,
            "physical_call_id": action_key,
        }
        token = _model_call.set(context)
        error = None
        try:
            yield owner
        except BaseException as failure:
            error = failure
        finally:
            _model_call.reset(token)
        try:
            guard.check()
            current_artifact(item_id, fence)
        except BaseException as failure:
            if error is None:
                error = failure
        if error is not None:
            raise error
        status = "finished"
    finally:
        guard.finish(action_key, status)


def _associate_model_call(sender, instance, **kwargs):
    context = _model_call.get()
    if context and instance._state.adding and instance.actor_id == context["owner_id"]:
        instance.root_id = context["root_id"]
        instance.run_id = context["run_id"]
        instance.conversation_id = context["conversation_id"]
        instance.work_id = context["work_id"]
        instance.requirement_id = context["requirement_id"]
        instance.physical_call_id = context["physical_call_id"]


from .model_config import ModelCallLog

pre_save.connect(_associate_model_call, sender=ModelCallLog, dispatch_uid="hr-agent-model-call")
