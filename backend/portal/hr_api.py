from collections.abc import Mapping
from functools import wraps

from django.core.exceptions import ValidationError

from django.db import transaction
from django.db.models import Q
from django.urls import path
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from .hr_retention import active_requests
from .hr_models import HrJobRevision, HrJobTask, ProbationCase, ProbationRevision, ProbationTransition
from .models import Module, User
from .security import audit, authorized_modules


class HrError(Exception):
    def __init__(self, code, detail, status=400, **extra):
        self.code, self.detail, self.status, self.extra = code, detail, status, extra


def hr_endpoint(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        request.hr_audit_action = "hr_request"
        try:
            return function(request, *args, **kwargs)
        except ParseError:
            error = HrError("invalid_json", "请求 JSON 格式无效。")
        except ValidationError:
            error = HrError('invalid_request', '请求字段格式无效。')
        except HrError as caught:
            error = caught
        audit(request.user, request.hr_audit_action, request.path[:150], result="denied", changes=[error.code])
        return Response({"detail": error.detail, "code": error.code, **error.extra}, status=error.status)
    return wrapped


def _body(request, required=(), optional=()):
    if not isinstance(request.data, Mapping):
        raise HrError("invalid_request", "请求必须是对象。")
    keys = set(request.data)
    if not set(required) <= keys or not keys <= set(required) | set(optional):
        raise HrError("invalid_request", "请求字段无效。")
    return request.data


def _text(value, field, *, required=False, maximum=12000):
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum or required and not value.strip():
        raise HrError("invalid_request", f"{field}格式无效。")
    return value.strip()


def _expected(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 10:
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2147483647:
        raise HrError("invalid_request", "expected_version 必须为正整数。")
    return value


def _is_hr(user):
    return (user.is_active and not user.must_change_password
            and user.roles.filter(code="hr").exists()
            and authorized_modules(user).filter(code="hr", enabled=True).exists())


def _require_hr(request):
    if not _is_hr(request.user):
        raise HrError("hr_forbidden", "没有人事业务操作权限。", 403)


def _manager_access(user):
    return (user.is_active and not user.must_change_password
            and Module.objects.filter(code="hr", enabled=True).exists())


def _job_for(request, task_id, *, write=False):
    _require_hr(request)
    queryset = HrJobTask.objects.select_for_update() if write else HrJobTask.objects.select_related("current_revision", "official_revision")
    try:
        return active_requests(queryset).get(pk=task_id, owner=request.user)
    except (HrJobTask.DoesNotExist, ValueError, TypeError):
        raise HrError("not_found", "对象不存在。", 404) from None


def _check_version(record, expected):
    if record.version != expected:
        raise HrError("version_conflict", "数据已更新，请刷新后重试。", 409)


def _missing(task):
    return [field for field in ("title", "objective", "responsibilities", "requirements") if not getattr(task, field).strip()]


def _revision_data(revision):
    if revision is None:
        return None
    return {
        "id": str(revision.pk), "version": revision.version, "input_version": revision.input_version,
        "kind": revision.kind, "body": revision.body, "parent_id": str(revision.parent_id) if revision.parent_id else None,
        "created_by_id": revision.created_by_id, "confirmed_by_id": revision.confirmed_by_id,
        "confirmed_at": revision.confirmed_at.isoformat() if revision.confirmed_at else None,
        "created_at": revision.created_at.isoformat(),
    }


def _job_data(task):
    current = task.current_revision
    return {
        "id": str(task.pk), "owner_id": task.owner_id, "title": task.title, "department": task.department,
        "objective": task.objective, "responsibilities": task.responsibilities, "requirements": task.requirements,
        "state": task.state, "version": task.version, "input_version": task.input_version,
        "missing_fields": _missing(task), "current_revision": _revision_data(current),
        "official_revision": _revision_data(task.official_revision),
        "current_revision_stale": bool(current and current.input_version != task.input_version),
        "revisions": [_revision_data(item) for item in task.revisions.select_related("parent").all()],
        "updated_at": task.updated_at.isoformat(),
    }


@api_view(["GET", "POST"])
@hr_endpoint
def jobs(request):
    request.hr_audit_action = "hr_job_create" if request.method == "POST" else "hr_job_list"
    _require_hr(request)
    if request.method == "GET":
        return Response([_job_data(task) for task in active_requests(HrJobTask.objects.filter(owner=request.user)).select_related("current_revision", "official_revision")])
    raise HrError("legacy_read_only", "旧版 JD 仅供历史查看，请使用招聘与 JD。", 405)


@api_view(["GET", "PATCH"])
@hr_endpoint
def job_detail(request, task_id):
    request.hr_audit_action = "hr_job_update" if request.method == "PATCH" else "hr_job_read"
    if request.method == "GET":
        return Response(_job_data(_job_for(request, task_id)))
    _job_for(request, task_id)
    raise HrError("legacy_read_only", "旧版 JD 仅供历史查看，请使用招聘与 JD。", 405)


@api_view(["POST"])
@hr_endpoint
def generate_job(request, task_id):
    request.hr_audit_action = "hr_job_generate"
    _job_for(request, task_id)
    raise HrError("legacy_read_only", "旧版 JD 仅供历史查看，请使用招聘与 JD。", 405)


@api_view(["POST"])
@hr_endpoint
def create_job_revision(request, task_id):
    request.hr_audit_action = "hr_job_revision"
    _job_for(request, task_id)
    raise HrError("legacy_read_only", "旧版 JD 仅供历史查看，请使用招聘与 JD。", 405)


@api_view(["POST"])
@hr_endpoint
def confirm_job(request, task_id):
    request.hr_audit_action = "hr_job_confirm"
    _job_for(request, task_id)
    raise HrError("legacy_read_only", "旧版 JD 仅供历史查看，请使用招聘与 JD。", 405)


def _materials(value):
    if (not isinstance(value, list) or len(value) > 50
            or any(not isinstance(item, str) or not item.strip() or len(item) > 500 or "\x00" in item for item in value)):
        raise HrError("invalid_request", "材料清单格式无效。")
    return [item.strip() for item in value]


def _case_for(request, case_id, *, write=False):
    queryset = ProbationCase.objects.select_related("owner", "assigned_manager")
    if write:
        queryset = queryset.select_for_update()
    try:
        case = queryset.get(pk=case_id)
    except (ProbationCase.DoesNotExist, ValueError, TypeError):
        raise HrError("not_found", "对象不存在。", 404) from None
    owner_access = case.owner_id == request.user.pk and _is_hr(request.user)
    manager_access = case.assigned_manager_id == request.user.pk and _manager_access(request.user)
    if not owner_access and not manager_access:
        raise HrError("not_found", "对象不存在。", 404)
    return case


def _case_actions(case, user):
    if user.pk == case.owner_id and _is_hr(user):
        return {ProbationCase.State.DRAFT: ["start_collecting"], ProbationCase.State.COLLECTING: ["submit_to_manager"], ProbationCase.State.HR_PENDING: ["hr_archive"]}.get(case.state, [])
    if (_manager_access(user) and user.pk == case.assigned_manager_id
            and case.state == ProbationCase.State.MANAGER_PENDING):
        return ["manager_approve"]
    return []


def _case_data(case, user):
    return {
        "id": str(case.pk), "owner_id": case.owner_id, "assigned_manager_id": case.assigned_manager_id,
        "employee_name": case.employee_name, "position": case.position, "materials": case.materials,
        "notes": case.notes, "manager_opinion": case.manager_opinion, "hr_conclusion": case.hr_conclusion,
        "state": case.state, "version": case.version, "actions": _case_actions(case, user),
        "assistant_enabled": case.assistant_mode != "manual",
        "assistant_mode": case.assistant_mode, "assistant_reason": case.assistant_reason,
        "updated_at": case.updated_at.isoformat(),
        "revisions": [{"before": item.before, "after": item.after, "changed_fields": item.changed_fields,
                       "actor_id": item.actor_id, "case_version": item.case_version,
                       "created_at": item.created_at.isoformat()} for item in case.revisions.all()],
        "transitions": [{"from_state": item.from_state, "to_state": item.to_state, "action": item.action,
                         "actor_id": item.actor_id, "comment": item.comment, "created_at": item.created_at.isoformat()}
                        for item in case.transitions.all()],
    }


@api_view(["GET", "POST"])
@hr_endpoint
def probations(request):
    request.hr_audit_action = "hr_probation_create" if request.method == "POST" else "hr_probation_list"
    if request.method == "GET":
        visible = Q(assigned_manager=request.user) if _manager_access(request.user) else Q(pk__in=[])
        if _is_hr(request.user):
            visible |= Q(owner=request.user)
        queryset = ProbationCase.objects.filter(visible).select_related("owner", "assigned_manager").distinct()
        return Response([_case_data(case, request.user) for case in queryset])
    _require_hr(request)
    body = _body(request, {"employee_name", "position", "assigned_manager_id", "materials", "notes"})
    try:
        manager = User.objects.get(pk=body["assigned_manager_id"], is_active=True)
    except (User.DoesNotExist, ValueError, TypeError):
        raise HrError("invalid_manager", "必须指定有效主管。") from None
    if manager.pk == request.user.pk:
        raise HrError("invalid_manager", "经办 HR 不能同时作为主管审批人。")
    with transaction.atomic():
        case = ProbationCase.objects.create(
            owner=request.user, assigned_manager=manager,
            employee_name=_text(body["employee_name"], "员工姓名", required=True, maximum=200),
            position=_text(body["position"], "转正岗位", required=True, maximum=200),
            materials=_materials(body["materials"]), notes=_text(body["notes"], "备注"),
        )
        audit(request.user, request.hr_audit_action, case.pk, changes=["case"])
    return Response(_case_data(case, request.user), status=201)


@api_view(["GET", "PATCH"])
@hr_endpoint
def probation_detail(request, case_id):
    request.hr_audit_action = "hr_probation_update" if request.method == "PATCH" else "hr_probation_read"
    if request.method == "GET":
        case = _case_for(request, case_id)
        return Response(_case_data(case, request.user))
    body = _body(request, {"expected_version"}, {"employee_name", "position", "assigned_manager_id", "materials", "notes"})
    with transaction.atomic():
        case = _case_for(request, case_id, write=True)
        if case.owner_id != request.user.pk or not _is_hr(request.user):
            raise HrError("not_found", "对象不存在。", 404)
        _check_version(case, _expected(body["expected_version"]))
        if case.state not in {ProbationCase.State.DRAFT, ProbationCase.State.COLLECTING}:
            raise HrError("invalid_state", "当前状态不能修改材料。", 409)
        changed = set(body) - {"expected_version"}
        if not changed:
            raise HrError("invalid_request", "没有可更新字段。")
        before = _probation_snapshot(case)
        if "employee_name" in body:
            case.employee_name = _text(body["employee_name"], "员工姓名", required=True, maximum=200)
        if "position" in body:
            case.position = _text(body["position"], "转正岗位", required=True, maximum=200)
        if "materials" in body:
            case.materials = _materials(body["materials"])
        if "notes" in body:
            case.notes = _text(body["notes"], "备注")
        if "assigned_manager_id" in body:
            try:
                manager = User.objects.get(pk=body["assigned_manager_id"], is_active=True)
            except (User.DoesNotExist, ValueError, TypeError):
                raise HrError("invalid_manager", "必须指定有效主管。") from None
            if manager.pk == request.user.pk:
                raise HrError("invalid_manager", "经办 HR 不能同时作为主管审批人。")
            case.assigned_manager = manager
        case.version += 1
        case.save()
        ProbationRevision.objects.create(
            case=case, before=before, after=_probation_snapshot(case), changed_fields=sorted(changed),
            actor=request.user, case_version=case.version,
        )
        audit(request.user, request.hr_audit_action, case.pk, changes=sorted(changed))
    return Response(_case_data(case, request.user))


TRANSITIONS = {
    "start_collecting": (ProbationCase.State.DRAFT, ProbationCase.State.COLLECTING, "hr"),
    "submit_to_manager": (ProbationCase.State.COLLECTING, ProbationCase.State.MANAGER_PENDING, "hr"),
    "manager_approve": (ProbationCase.State.MANAGER_PENDING, ProbationCase.State.HR_PENDING, "manager"),
    "hr_archive": (ProbationCase.State.HR_PENDING, ProbationCase.State.ARCHIVED, "hr"),
}


def _probation_snapshot(case):
    return {
        "employee_name": case.employee_name,
        "position": case.position,
        "assigned_manager_id": case.assigned_manager_id,
        "materials": case.materials,
        "notes": case.notes,
    }


@api_view(["POST"])
@hr_endpoint
def transition_probation(request, case_id):
    request.hr_audit_action = "hr_probation_transition"
    body = _body(request, {"expected_version", "action", "comment"})
    action = body["action"]
    if not isinstance(action, str) or action not in TRANSITIONS:
        raise HrError("invalid_transition", "不支持的状态操作。", 409)
    comment = _text(body["comment"], "处理意见", required=action in {"manager_approve", "hr_archive"}, maximum=12000)
    expected_state, target_state, actor_kind = TRANSITIONS[action]
    with transaction.atomic():
        case = _case_for(request, case_id, write=True)
        _check_version(case, _expected(body["expected_version"]))
        allowed_actor = (actor_kind == "hr" and case.owner_id == request.user.pk and _is_hr(request.user)) or (
            actor_kind == "manager" and _manager_access(request.user) and case.assigned_manager_id == request.user.pk)
        if not allowed_actor:
            raise HrError("not_found", "对象不存在。", 404)
        if case.state != expected_state:
            raise HrError("invalid_transition", "当前状态不能执行此操作。", 409)
        previous = case.state
        case.state = target_state
        case.version += 1
        if action == "manager_approve":
            case.manager_opinion = comment
        elif action == "hr_archive":
            case.hr_conclusion = comment
        case.save()
        ProbationTransition.objects.create(
            case=case, from_state=previous, to_state=target_state,
            action=action, actor=request.user, comment=comment,
        )
        audit(request.user, request.hr_audit_action, case.pk, changes=["state"])
    return Response(_case_data(case, request.user))


urlpatterns = [
    path("jobs/", jobs),
    path("jobs/<uuid:task_id>/", job_detail),
    path("jobs/<uuid:task_id>/generate/", generate_job),
    path("jobs/<uuid:task_id>/revisions/", create_job_revision),
    path("jobs/<uuid:task_id>/confirm/", confirm_job),
    path("probations/", probations),
    path("probations/<uuid:case_id>/", probation_detail),
    path("probations/<uuid:case_id>/transition/", transition_probation),
]
