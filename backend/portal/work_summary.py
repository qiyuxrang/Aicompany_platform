from django.db.models import Q
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .hr_models import HrJobTask, ProbationCase
from .hr_retention import cutoff
from .models import Module
from .product_models import DocumentApproval, DocumentArtifact, DocumentTask
from .product_service import ProductError, effective_artifact_approval, reviewer_allowed
from .product_storage import StorageError
from .security import authorized_modules


MODULE_REASONS = {
    "product": "未获授权访问产品模块。",
    "hr": "未获授权访问人事模块。",
}


def _module_allowed(user, code):
    if not user.is_active or user.must_change_password:
        return False
    business_roles = user.roles.exclude(code="platform_admin")
    return (business_roles.filter(modules__code=code, modules__enabled=True).exists()
            and authorized_modules(user).filter(code=code, enabled=True).exists())


def _hr_probation_allowed(user):
    return (user.is_active and not user.must_change_password
            and Module.objects.filter(code="hr", enabled=True).exists()
            and ProbationCase.objects.filter(assigned_manager=user).exists())


def _item(record_id, title, status, href, updated_at, kind):
    return {
        "id": str(record_id),
        "title": title,
        "status": status,
        "href": href,
        "updated_at": updated_at.isoformat(),
        "kind": kind,
    }


def _section(items, available, partial):
    if not available:
        return {"available": False, "count": None, "items": [], "reason": "未获授权访问产品或人事业务模块。"}
    items.sort(key=lambda item: item[0], reverse=True)
    result = {"available": True, "count": len(items), "items": [item[1] for item in items[:3]]}
    if partial:
        result["reason"] = "仅汇总已授权模块；未授权模块不计入数量。"
    return result


def _product_items(user):
    visible = Q(owner=user)
    can_review = reviewer_allowed(user)
    if can_review:
        visible |= Q(reviewer=user)
    tasks = DocumentTask.objects.filter(visible).select_related("owner", "reviewer")
    mine = [
        (task.updated_at, _item(task.pk, task.title, task.state,
         f"/centers/product/documents?task={task.pk}", task.updated_at, "product_task"))
        for task in tasks if task.owner_id == user.pk
    ]
    reviews = [
        (task.updated_at, _item(task.pk, task.title, task.state,
         f"/centers/product/documents?task={task.pk}", task.updated_at, "product_task"))
        for task in tasks
        if can_review and task.reviewer_id == user.pk and task.owner_id != user.pk
        and task.state == DocumentTask.State.WAITING_REVIEW
    ]
    results = []
    artifacts = DocumentArtifact.objects.filter(
        task__in=tasks, approvals__decision=DocumentApproval.Decision.APPROVE,
    ).select_related("task", "review", "task__reviewer").distinct()
    for artifact in artifacts:
        try:
            approval = effective_artifact_approval(artifact)
        except (ProductError, StorageError, OSError):
            approval = None
        if approval:
            title = f"{artifact.task.title} · 成果 v{artifact.version}"
            results.append((approval.created_at, _item(
                artifact.pk, title, "approved",
                f"/centers/product/documents?task={artifact.task_id}&artifact={artifact.pk}",
                approval.created_at, "product_artifact",
            )))
    return mine, reviews, results


def _hr_items(user, owner_access):
    jobs = HrJobTask.objects.filter(owner=user, created_at__gt=cutoff()) if owner_access else HrJobTask.objects.none()
    owned_cases = ProbationCase.objects.filter(owner=user) if owner_access else ProbationCase.objects.none()
    case_scope = Q(assigned_manager=user)
    if owner_access:
        case_scope |= Q(owner=user)
    visible_cases = ProbationCase.objects.filter(case_scope).distinct()
    mine = [
        (job.updated_at, _item(job.pk, job.title, job.state,
         f"/centers/hr/job?task={job.pk}", job.updated_at, "hr_job"))
        for job in jobs
    ] + [
        (case.updated_at, _item(case.pk, f"{case.employee_name} · 转正", case.state,
         f"/centers/hr/probation?case={case.pk}", case.updated_at, "hr_probation"))
        for case in owned_cases
    ]
    reviews = [
        (case.updated_at, _item(case.pk, f"{case.employee_name} · 转正", case.state,
         f"/centers/hr/probation?case={case.pk}", case.updated_at, "hr_probation"))
        for case in visible_cases
        if ((case.state == ProbationCase.State.MANAGER_PENDING and case.assigned_manager_id == user.pk)
            or (case.state == ProbationCase.State.HR_PENDING and case.owner_id == user.pk))
    ]
    results = [
        (job.updated_at, _item(job.pk, job.title, job.state,
         f"/centers/hr/job?task={job.pk}", job.updated_at, "hr_job"))
        for job in jobs if job.state == HrJobTask.State.CONFIRMED
    ] + [
        (case.updated_at, _item(case.pk, f"{case.employee_name} · 转正", case.state,
         f"/centers/hr/probation?case={case.pk}", case.updated_at, "hr_probation"))
        for case in visible_cases if case.state == ProbationCase.State.ARCHIVED
    ]
    return mine, reviews, results


@api_view(["GET"])
def summary(request):
    access = {code: _module_allowed(request.user, code) for code in MODULE_REASONS}
    hr_owner_access = access["hr"]
    access["hr"] = access["hr"] or _hr_probation_allowed(request.user)
    modules = {
        code: {"available": True} if allowed else {"available": False, "reason": MODULE_REASONS[code]}
        for code, allowed in access.items()
    }
    groups = [[], [], []]
    if access["product"]:
        for target, values in zip(groups, _product_items(request.user)):
            target.extend(values)
    if access["hr"]:
        for target, values in zip(groups, _hr_items(request.user, hr_owner_access)):
            target.extend(values)
    available = any(access.values())
    partial = available and not all(access.values())
    return Response({
        "modules": modules,
        "sections": {
            "my_tasks": _section(groups[0], available, partial),
            "pending_reviews": _section(groups[1], available, partial),
            "recent_results": _section(groups[2], available, partial),
        },
    })
