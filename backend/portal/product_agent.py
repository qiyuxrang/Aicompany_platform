from django.db import transaction
from django.utils import timezone

from .agent_models import AgentBusinessReference, AgentRequirement, AgentRun, AgentWorkTask
from .product_models import DocumentAttempt, DocumentTask
from .product_service import ProductError, product_user_allowed, require_version


def _active_scope(root, work, owner, requirement_version, grant_version, session_version, root_fence,
                  department="product"):
    identity = root.policy.get("identity", {}) if isinstance(root.policy, dict) else {}
    if department == "product":
        authorized = product_user_allowed(owner)
    elif department == "hr":
        from .hr_api import _is_hr
        authorized = _is_hr(owner)
    elif department == "finance":
        from .business_boards import _grant
        authorized = _grant(owner, "finance") is not None
    else:
        authorized = False
    if (root.parent_id is not None or root.root_run_id != root.pk
            or root.work_id != work.pk or root.conversation_id != work.conversation_id
            or root.conversation.root_run_id != root.pk
            or root.conversation.owner_id != owner.pk or work.owner_id != owner.pk
            or not authorized or owner.department_code != department
            or root.conversation.department_code != department or work.department_code != department
            or owner.grant_version != grant_version or owner.session_version != session_version
            or identity != {"owner_id": owner.pk, "grant_version": grant_version,
                            "session_version": session_version}
            or root.policy.get("fence") != root_fence
            or root.state in {"stopping", "cancelled", "terminated", "failed", "completed"}
            or work.state in {"stopping", "cancelled", "terminated", "failed", "completed"}
            or root.deadline_at is None or timezone.now() >= root.deadline_at
            or work.current_requirement_version != requirement_version
            or not root.requirement_id
            or not AgentRequirement.objects.filter(pk=root.requirement_id, work=work,
                                                   version=requirement_version).exists()):
        raise ProductError("agent_binding_stale", "Agent 任务关联已失效。", 409)


def lock_scope(root_id, work_id, owner, requirement_version, grant_version, session_version, root_fence,
               department="product"):
    if not all((root_id, work_id, requirement_version, grant_version, session_version, root_fence)):
        raise ProductError("agent_binding_required", "业务写入需要有效工作关联。", 409)
    try:
        root = AgentRun.objects.select_for_update().select_related("conversation").get(
            pk=root_id, root_run_id=root_id, parent__isnull=True)
        work = AgentWorkTask.objects.select_for_update().get(pk=work_id)
    except (AgentRun.DoesNotExist, AgentWorkTask.DoesNotExist, ValueError, TypeError):
        raise ProductError("agent_binding_stale", "Agent 任务关联已失效。", 409) from None
    _active_scope(root, work, owner, requirement_version, grant_version, session_version, root_fence,
                  department=department)
    return root, work


def lock_task_scope(task_id):
    binding = DocumentTask.objects.filter(pk=task_id).values(
        "agent_root_id", "agent_work_id", "agent_requirement_version", "agent_grant_version",
        "agent_session_version", "agent_root_fence", "owner_id").first()
    if binding is None:
        raise ProductError("not_found", "对象不存在。", 404)
    if not any(binding[key] is not None for key in (
            "agent_root_id", "agent_work_id", "agent_requirement_version", "agent_grant_version",
            "agent_session_version", "agent_root_fence")):
        return None
    from .models import User
    owner = User.objects.get(pk=binding["owner_id"])
    return lock_scope(binding["agent_root_id"], binding["agent_work_id"], owner,
                      binding["agent_requirement_version"], binding["agent_grant_version"],
                      binding["agent_session_version"], binding["agent_root_fence"])


def check_task_scope(task, scope):
    if scope is None:
        if any(getattr(task, field) is not None for field in (
                "agent_root_id", "agent_work_id", "agent_requirement_version", "agent_grant_version",
                "agent_session_version", "agent_root_fence")):
            raise ProductError("agent_binding_stale", "Agent 任务关联已失效。", 409)
        return
    root, work = scope
    if (task.agent_root_id != str(root.pk) or task.agent_work_id != str(work.pk)
            or task.owner_id != work.owner_id):
        raise ProductError("agent_binding_stale", "Agent 任务关联已失效。", 409)
    _active_scope(root, work, task.owner, task.agent_requirement_version, task.agent_grant_version,
                  task.agent_session_version, task.agent_root_fence)


def runtime_guard_for_task(task):
    from .agent_runtime import RunBinding, RuntimeGuard
    return RuntimeGuard(RunBinding(task.owner_id, task.agent_root_id, task.agent_root_id,
                                   task.agent_grant_version, task.agent_session_version,
                                   task.agent_root_fence))


def requirement_current(task):
    if not task.agent_root_id:
        return True
    return AgentWorkTask.objects.filter(pk=task.agent_work_id,
                                       current_requirement_version=task.agent_requirement_version).exists()


def _record_reference(root, work, kind, object_id, revision, checksum, summary, operation_key, *, result=False):
    reference = AgentBusinessReference.objects.filter(root=root, operation_key=operation_key).first()
    if reference is None:
        reference = AgentBusinessReference.objects.create(
            root=root, work=work, requirement=root.requirement, domain_type=kind,
            object_id=str(object_id), revision=str(revision), digest=checksum,
            operation_key=operation_key, public_summary=summary[:300])
    elif (reference.work_id != work.pk or reference.requirement_id != root.requirement_id
          or reference.domain_type != kind or reference.object_id != str(object_id)
          or reference.revision != str(revision) or reference.digest != checksum):
        raise ProductError("reference_conflict", "业务引用版本已变化。", 409)
    if result:
        projection = {"domain_type": kind, "object_id": str(object_id), "revision": str(revision),
                      "digest": checksum, "public_summary": summary[:300], "reference_id": str(reference.pk),
                      "current": True}
        previous = [item for item in work.result_references
                    if not (item.get("domain_type") == kind and item.get("object_id") == str(object_id))]
        work.result_references = [*previous, projection]
        work.save(update_fields=["result_references", "updated_at"])
    return reference


def record_product_sources(task, root, work):
    from .product_service import approved_blueprint, current_revision, input_authorized
    from .product_storage import StorageError, verified_artifact

    check_task_scope(task, (root, work))
    revision = current_revision(task, "input")
    if approved_blueprint(task) is None or not input_authorized(task, revision):
        raise ProductError("blueprint_approval_required", "来源版本尚未获所有者确认。", 409)
    sources = {str(item.get("id")): item for item in revision.payload.get("sources", [])}
    for source in task.sources.order_by("created_at"):
        snapshot = sources.get(str(source.pk))
        if snapshot is None or snapshot.get("sha256") != source.sha256:
            continue
        try:
            verified_artifact(source)
        except StorageError as error:
            raise ProductError(error.code, "原始资料完整性校验失败。", 409) from error
        _record_reference(root, work, "document_source", source.pk, source.sha256, source.sha256,
                          f"原始资料：{source.original_name}（真实性待核）",
                          f"product:source:{source.pk}:{source.sha256}")


def record_product_artifacts(task, root, work):
    from .product_pair import output_current
    from .product_storage import StorageError, verified_artifact

    check_task_scope(task, (root, work))
    if task.state != DocumentTask.State.COMPLETED:
        raise ProductError("invalid_state", "成果尚未完成。", 409)
    for family, label in (("technical-solution", "技术方案"), ("feasibility", "可行性研究报告"),
                          ("presentation", "汇报PPT")):
        artifact = task.artifacts.filter(family=family).order_by("-version").first()
        if artifact is None or not output_current(task, artifact):
            raise ProductError("stale_output", "成果版本未通过当前来源与蓝图核对。", 409)
        try:
            verified_artifact(artifact)
        except StorageError as error:
            raise ProductError(error.code, "成果文件完整性校验失败。", 409) from error
        _record_reference(root, work, "document_artifact", artifact.pk, artifact.version,
                          artifact.sha256, f"{task.title} · {label} v{artifact.version}",
                          f"product:artifact:{artifact.pk}:v{artifact.version}", result=True)


@transaction.atomic
def bind_task(guard, task_id, expected_version):
    run = guard.check(write=True)
    owner = run.conversation.owner
    root, work = lock_scope(guard.binding.root_id, run.work_id, owner,
                            run.requirement.version, guard.binding.grant_version,
                            guard.binding.session_version, guard.binding.fence)
    task = DocumentTask.objects.select_for_update().get(pk=task_id)
    if task.owner_id != owner.pk:
        raise ProductError("not_found", "对象不存在。", 404)
    require_version(task, expected_version)
    if task.agent_root_id:
        check_task_scope(task, (root, work))
        return task
    if task.state in {"QUEUED", "RUNNING", "CANCELLED", "COMPLETED"}:
        raise ProductError("invalid_state", "当前任务不能关联 Agent 工作。", 409)
    task.agent_root_id = str(root.pk)
    task.agent_work_id = str(work.pk)
    task.agent_requirement_version = run.requirement.version
    task.agent_grant_version = owner.grant_version
    task.agent_session_version = owner.session_version
    task.agent_root_fence = guard.binding.fence
    task.save(update_fields=["agent_root_id", "agent_work_id", "agent_requirement_version",
                             "agent_grant_version", "agent_session_version", "agent_root_fence", "updated_at"])
    return task


@transaction.atomic
def invalidate_tasks_for_work(work, *, cancelled=False):
    if work.result_references:
        work.result_references = [{**item, "current": False, "stale": True}
                                  for item in work.result_references]
        work.save(update_fields=["result_references", "updated_at"])
    affected = DocumentTask.objects.select_for_update().filter(agent_work_id=str(work.pk)).exclude(
        state__in=["CANCELLED", "COMPLETED"])
    if not cancelled:
        affected = affected.exclude(agent_requirement_version=work.current_requirement_version)
    for task in affected:
        for attempt in task.attempts.filter(status="running"):
            if attempt.agent_action_key:
                runtime_guard_for_task(task).finish(attempt.agent_action_key, "cancelled")
            attempt.status = "cancelled"
            attempt.error_code = "agent_cancelled" if cancelled else "agent_binding_stale"
            attempt.finished_at = timezone.now()
            attempt.save(update_fields=["status", "error_code", "finished_at"])
        task.state = "CANCELLED" if cancelled else "WAITING_INPUT"
        task.error_code = "agent_cancelled" if cancelled else "agent_binding_stale"
        task.pending_action = ""
        task.blueprint_version = 0 if not cancelled else task.blueprint_version
        task.lease_until = None
        task.fence += 1
        task.version += 1
        task.save(update_fields=["state", "error_code", "pending_action", "blueprint_version",
                                 "lease_until", "fence", "version", "updated_at"])
