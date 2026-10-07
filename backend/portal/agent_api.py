import uuid
from functools import wraps

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404
from django.urls import include, path
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .agent_models import (AgentAttachment, AgentBusinessReference, AgentConversation, AgentEvent, AgentMessage,
                           AgentProject, AgentRequirement, AgentRun, AgentSkillInstallation,
                           AgentWorkTask, append_public_event)
from .agent_skills import reviewed_catalog
from .business_models import BusinessLedgerGrant
from .models import Module, User
from .model_config import ModelCallLog
from .product_storage import StorageError, parse_upload, remove_relative, verified_artifact, write_source
from .security import authorized_modules


GM_REFERENCE_TYPES = frozenset({"document_task", "document_source", "document_artifact",
                                "resume_batch", "resume_artifact", "business_revision"})
GM_DOWNLOAD_TYPES = frozenset({"document_source", "document_artifact", "resume_artifact"})


class AgentApiError(Exception):
    def __init__(self, code, detail, status):
        self.code, self.detail, self.status = code, detail, status


def fail(code="invalid_request", detail="请求无效。", status=400):
    raise AgentApiError(code, detail, status)


def endpoint(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        try:
            if not getattr(settings, "AGENT_PLATFORM_ENABLED", False):
                fail("unavailable", "Agent 功能未启用。", 503)
            user = User.objects.filter(pk=getattr(request.user, "pk", None), is_active=True,
                                       must_change_password=False).first()
            if user is None:
                fail("forbidden", "无权访问。", 403)
            request.agent_user = user
            result = function(request, *args, **kwargs)
        except AgentApiError as error:
            result = Response({"code": error.code, "detail": error.detail}, status=error.status)
        except StorageError as error:
            result = Response({"code": error.code, "detail": error.detail}, status=400)
        result["Cache-Control"] = "private, no-store"
        return result
    return wrapped


def actor(request, *, manager=False):
    user = request.agent_user
    if manager:
        from .business_boards import allowed

        if not allowed(user):
            fail("forbidden", "无权访问管理摘要。", 403)
        return user
    if user.is_staff or user.is_superuser or user.is_platform_admin:
        fail("forbidden", "无权使用员工助手。", 403)
    from .business_boards import allowed

    if allowed(user):
        return user
    department = user.department_code
    if department not in ("product", "hr", "finance", "engineering"):
        fail("department_unassigned", "请先分配所属部门。", 403)
    if department == "finance":
        allowed = BusinessLedgerGrant.objects.filter(user=user, department="finance").exists()
        module_code = "business"
    else:
        allowed = user.roles.filter(code=department).exists()
        module_code = "cost" if department == "engineering" else department
        allowed = allowed and authorized_modules(user).filter(code=module_code, enabled=True).exists()
    if not allowed or not Module.objects.filter(code=module_code, enabled=True).exists():
        fail("forbidden", "当前部门授权已失效。", 403)
    return user


def department(user):
    return "" if user.roles.filter(code="general_manager").exists() else user.department_code


def body(request, required, optional=()):
    data = request.data
    if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
        fail()
    return data


def string(data, key, maximum, *, blank=False):
    value = data.get(key)
    if not isinstance(value, str) or len(value) > maximum or (not blank and not value.strip()):
        fail()
    return value.strip()


def version(data, key="expected_version"):
    value = data.get(key)
    if type(value) is not int or value < 0:
        fail()
    return value


def uid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        fail()


def page(request, allowed=()):
    if set(request.query_params) - set(allowed) - {"page", "page_size"} or any(
            len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        fail()
    values = []
    for key, default, maximum in (("page", 1, 100000), ("page_size", 20, 50)):
        raw = request.query_params.get(key, str(default))
        if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= maximum:
            fail()
        values.append(int(raw))
    return (values[0] - 1) * values[1], values[1]


def listing(queryset, offset, size, formatter):
    return {"items": [formatter(item) for item in queryset[offset:offset + size]],
            "total": queryset.count()}


def owned(model, user, object_id):
    item = model.objects.filter(pk=uid(object_id), owner=user).first()
    if item is None:
        raise Http404
    if getattr(item, "department_code", department(user)) != department(user):
        fail("forbidden", "所属部门已变更。", 403)
    return item


def project_data(item):
    return {"id": str(item.pk), "name": item.name, "department_code": item.department_code,
            "created_at": item.created_at.isoformat()}


def conversation_data(item):
    return {"id": str(item.pk), "project_id": str(item.project_id) if item.project_id else None,
            "root_run_id": str(item.root_run_id), "created_at": item.created_at.isoformat()}


def message_data(item):
    return {"id": str(item.pk), "role": item.role, "content": item.content,
            "attachment_references": item.attachment_references,
            "work_id": str(item.work_id) if item.work_id else None,
            "conversation_id": str(item.conversation_id), "received": True,
            "applied": item.execution_state == "submitted", "execution_state": item.execution_state,
            "execution_reason": item.execution_reason or None,
            "created_at": item.created_at.isoformat()}


def dispatch_saved_message(message_id):
    from .agent_execution import dispatch_message

    result = dispatch_message(message_id)
    state = result.get("state", "dispatch_unknown")
    reason = result.get("reason", "")
    AgentMessage.objects.filter(pk=message_id).update(execution_state=state,
        execution_reason=str(reason)[:100])
    if state == "submitted":
        work_id = AgentMessage.objects.filter(pk=message_id).values_list("work_id", flat=True).first()
        if work_id:
            AgentWorkTask.objects.filter(pk=work_id, state="queued").update(state="running")
        requirement = AgentRequirement.objects.filter(user_message_id=message_id).select_related("work").first()
        if requirement and requirement.work.current_requirement_version == requirement.version:
            AgentRequirement.objects.filter(pk=requirement.pk, applied_at__isnull=True).update(
                applied_at=timezone.now())
    return result


def dispatch_native_cancel(root_id):
    from . import agent_execution

    canceller = getattr(agent_execution, "cancel_root", None)
    if canceller is None:
        return {"state": "stopping", "reason": "native_cancel_not_connected"}
    return canceller(root_id)


def work_data(item):
    return {"id": str(item.pk), "project_id": str(item.project_id) if item.project_id else None,
            "conversation_id": str(item.conversation_id), "goal": item.goal, "state": item.state,
            "public_summary": item.public_summary,
            "current_requirement_version": item.current_requirement_version,
            "result_references": item.result_references, "stop_reason": item.stop_reason,
            "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()}


def attachment_data(item):
    return {"type": "agent_attachment", "id": str(item.pk), "sha256": item.sha256,
            "name": item.name, "content_type": item.content_type, "size": item.size,
            "created_at": item.created_at.isoformat()}


def management_references(work):
    references = []
    for reference in AgentBusinessReference.objects.filter(work=work):
        if reference.domain_type not in GM_REFERENCE_TYPES:
            continue
        url = f"/api/agent/management/references/{reference.pk}/"
        item = {"reference_id": str(reference.pk), "domain_type": reference.domain_type,
                "object_id": reference.object_id, "revision": reference.revision,
                "digest": reference.digest, "public_summary": reference.public_summary, "url": url}
        if reference.domain_type in GM_DOWNLOAD_TYPES:
            item["download_url"] = url + "download/"
        references.append(item)
    return references


@api_view(["GET", "POST"])
@endpoint
def attachments(request):
    user = actor(request)
    if request.method == "GET":
        offset, size = page(request)
        return Response(listing(AgentAttachment.objects.filter(owner=user, department_code=department(user),
            deleted_at__isnull=True).order_by(
            "-created_at"), offset, size, attachment_data))
    if set(request.data) != {"file"} or "file" not in request.FILES:
        fail()
    name, content_type, content, _, _ = parse_upload(request.FILES["file"])
    item = AgentAttachment(owner=user, department_code=department(user), name=name,
                           content_type=content_type, size=len(content))
    _, relative, digest = write_source(f"agent/{user.pk}/{item.pk}", name, content)
    try:
        item.path, item.sha256 = relative, digest
        item.save()
    except Exception:
        remove_relative(relative)
        raise
    return Response(attachment_data(item), status=201)


@api_view(["GET"])
@endpoint
def attachment_detail(request, attachment_id):
    user = actor(request)
    item = owned(AgentAttachment, user, attachment_id)
    if item.deleted_at:
        raise Http404
    verified_artifact(item)
    return Response(attachment_data(item))


@api_view(["GET"])
@endpoint
def attachment_download(request, attachment_id):
    user = actor(request)
    item = owned(AgentAttachment, user, attachment_id)
    if item.deleted_at:
        raise Http404
    target = verified_artifact(item)
    return FileResponse(target.open("rb"), as_attachment=True, filename=item.name,
                        content_type=item.content_type)


@api_view(["GET", "POST"])
@endpoint
def projects(request):
    user = actor(request)
    if request.method == "GET":
        offset, size = page(request)
        return Response(listing(AgentProject.objects.filter(owner=user,
            department_code=department(user)).order_by("-created_at"),
                                offset, size, project_data))
    data = body(request, {"name"})
    if not department(user):
        fail("department_unassigned", "项目需要明确所属部门。", 403)
    name = string(data, "name", 120)
    item = AgentProject.objects.create(owner=user, department_code=department(user), name=name)
    return Response(project_data(item), status=201)


@api_view(["GET", "POST"])
@endpoint
def conversations(request):
    user = actor(request)
    if request.method == "GET":
        offset, size = page(request)
        return Response(listing(AgentConversation.objects.filter(owner=user,
            department_code=department(user)).order_by("-created_at"),
                                offset, size, conversation_data))
    data = body(request, set(), {"project_id", "client_request_id"})
    project = owned(AgentProject, user, data["project_id"]) if data.get("project_id") else None
    create_key = string(data, "client_request_id", 100) if "client_request_id" in data else ""
    with transaction.atomic():
        existing = AgentConversation.objects.filter(owner=user, create_key=create_key).first() if create_key else None
        if existing:
            if existing.project_id != (project.pk if project else None):
                fail("conflict", "请求键已用于其他会话。", 409)
            return Response(conversation_data(existing))
        item = AgentConversation.objects.create(owner=user, department_code=department(user),
                                                project=project, create_key=create_key)
        AgentRun.objects.create(id=item.root_run_id, root_run_id=item.root_run_id, conversation=item,
                                state="pending")
    return Response(conversation_data(item), status=201)


@api_view(["GET", "POST"])
@endpoint
def messages(request, conversation_id):
    user = actor(request)
    conversation = owned(AgentConversation, user, conversation_id)
    if request.method == "GET":
        from .agent_execution import reconcile_conversation

        try:
            reconciliation = reconcile_conversation(conversation.pk, user.pk)
        except Exception:
            reconciliation = {"state": "reconcile_unknown"}
        offset, size = page(request)
        return Response({**listing(AgentMessage.objects.filter(conversation=conversation).order_by(
            "created_at", "pk"), offset, size, message_data), "reconciliation": reconciliation})
    data = body(request, {"text", "client_request_id"},
                {"attachment_references", "work_id", "defer_dispatch"})
    content = string(data, "text", 12000)
    key = string(data, "client_request_id", 100)
    attachments = data.get("attachment_references", [])
    if not isinstance(attachments, list) or len(attachments) > 20:
        fail()
    verified = []
    for reference in attachments:
        if not isinstance(reference, dict) or set(reference) != {"type", "id", "sha256"} or reference["type"] != "agent_attachment":
            fail()
        item = owned(AgentAttachment, user, reference["id"])
        if item.deleted_at or item.sha256 != reference["sha256"]:
            fail("forbidden", "附件不可访问。", 403)
        verified_artifact(item)
        verified.append({"type": "agent_attachment", "id": str(item.pk), "sha256": item.sha256})
    work = owned(AgentWorkTask, user, data["work_id"]) if data.get("work_id") else None
    if work and work.conversation_id != conversation.pk:
        fail("forbidden", "工作不属于当前会话。", 403)
    defer_dispatch = data.get("defer_dispatch", False)
    if type(defer_dispatch) is not bool or (defer_dispatch and work is None):
        fail()
    with transaction.atomic():
        existing = AgentMessage.objects.filter(conversation=conversation, client_request_id=key).first()
        if existing:
            if (existing.content, existing.attachment_references, existing.work_id) != (
                    content, verified, work.pk if work else None):
                fail("conflict", "请求键已用于其他消息。", 409)
            return Response(message_data(existing))
        item = AgentMessage.objects.create(conversation=conversation, work=work, role="user",
            content=content, attachment_references=verified, client_request_id=key)
        dispatch = {"state": "pending"}
        if not defer_dispatch:
            transaction.on_commit(lambda: dispatch.update(dispatch_saved_message(item.pk)), robust=True)
    if dispatch["state"] != "pending":
        item.refresh_from_db()
    return Response(message_data(item), status=201)


@api_view(["GET", "POST"])
@endpoint
def work_list(request):
    user = actor(request)
    if request.method == "GET":
        offset, size = page(request, {"project_id"})
        query = AgentWorkTask.objects.filter(owner=user, department_code=department(user))
        if request.query_params.get("project_id"):
            project = owned(AgentProject, user, request.query_params["project_id"])
            query = query.filter(project=project)
        return Response(listing(query.order_by("-created_at"), offset, size, work_data))
    data = body(request, {"conversation_id", "message_id", "goal"}, {"project_id"})
    if not department(user):
        fail("department_unassigned", "工作需要明确所属部门。", 403)
    conversation = owned(AgentConversation, user, data["conversation_id"])
    message = AgentMessage.objects.filter(pk=uid(data["message_id"]), conversation=conversation,
                                          role="user").first()
    if message is None or message.work_id:
        fail("conflict", "消息不可用于新工作。", 409)
    project = owned(AgentProject, user, data["project_id"]) if data.get("project_id") else conversation.project
    goal = string(data, "goal", 12000)
    with transaction.atomic():
        message = AgentMessage.objects.select_for_update().get(pk=message.pk)
        if message.work_id:
            fail("conflict", "消息已绑定工作。", 409)
        item = AgentWorkTask.objects.create(owner=user, department_code=department(user),
            conversation=conversation, project=project, goal=goal,
            public_summary="工作已创建，等待执行摘要。",
            current_requirement_version=1)
        requirement = AgentRequirement.objects.create(work=item, version=1, user_message=message, content=goal)
        message.work = item
        message.save(update_fields=["work"])
        AgentRun.objects.filter(pk=conversation.root_run_id, conversation=conversation).update(work=item,
                                                                                               requirement=requirement)
    return Response(work_data(item), status=201)


@api_view(["GET"])
@endpoint
def work_detail(request, work_id):
    user = actor(request)
    item = owned(AgentWorkTask, user, work_id)
    if item.department_code == "finance":
        from .agent_finance import reconcile_finance_work
        if reconcile_finance_work(item.pk):
            item.refresh_from_db()
    data = work_data(item)
    data["requirements"] = [{"version": row.version, "message_id": str(row.user_message_id),
        "content": row.content, "source_versions": row.source_versions, "source_hash": row.source_hash,
        "received_at": row.received_at.isoformat(), "applied_at": row.applied_at.isoformat() if row.applied_at else None}
        for row in AgentRequirement.objects.filter(work=item).order_by("version")]
    data["business_references"] = [{"domain_type": row.domain_type, "object_id": row.object_id,
        "revision": row.revision, "digest": row.digest, "public_summary": row.public_summary}
        for row in AgentBusinessReference.objects.filter(work=item).order_by("created_at")]
    return Response(data)


@api_view(["POST"])
@endpoint
def requirements(request, work_id):
    user = actor(request)
    data = body(request, {"expected_version", "message_id", "content"}, {"source_versions", "source_hash"})
    expected = version(data)
    content = string(data, "content", 12000)
    sources = data.get("source_versions", [])
    digest = data.get("source_hash", "")
    if not isinstance(sources, list) or len(sources) > 100 or not isinstance(digest, str) or len(digest) not in (0, 64):
        fail()
    with transaction.atomic():
        root_id = AgentWorkTask.objects.filter(pk=uid(work_id), owner=user).values_list(
            "conversation__root_run_id", flat=True).first()
        if root_id is None:
            raise Http404
        root = AgentRun.objects.select_for_update().get(pk=root_id)
        item = AgentWorkTask.objects.select_for_update().filter(pk=uid(work_id), owner=user).first()
        if item is None:
            raise Http404
        if item.current_requirement_version != expected:
            fail("conflict", f"当前要求版本为 {item.current_requirement_version}。", 409)
        if item.state in ("stopping", "cancelled", "terminated", "completed"):
            fail("conflict", "当前工作不能更正。", 409)
        message = AgentMessage.objects.filter(pk=uid(data["message_id"]), conversation=item.conversation,
                                              role="user").first()
        if message is None or message.work_id not in (None, item.pk):
            fail("conflict", "消息不属于当前工作。", 409)
        requirement = AgentRequirement.objects.create(work=item, version=expected + 1,
            user_message=message, content=content, source_versions=sources, source_hash=digest)
        item.current_requirement_version = requirement.version
        item.save(update_fields=["current_requirement_version", "updated_at"])
        message.work = item
        message.save(update_fields=["work"])
        from .product_agent import invalidate_tasks_for_work

        invalidate_tasks_for_work(item)
        if "identity" in root.policy:
            root.policy = {**root.policy, "fence": root.policy.get("fence", 0) + 1}
        root.requirement = requirement
        root.save(update_fields=["policy", "requirement", "updated_at"])
        append_public_event(item.conversation.root_run_id, item.conversation.root_run_id,
                            f"requirement:{item.pk}:{requirement.version}", "requirement_received",
                            {"work_id": str(item.pk), "version": requirement.version}, work=item)
        dispatch = {"state": "pending"}
        transaction.on_commit(lambda: dispatch.update(dispatch_saved_message(message.pk)), robust=True)
    return Response({"version": requirement.version, "status": "received",
        "applied": AgentRequirement.objects.filter(pk=requirement.pk, applied_at__isnull=False).exists(),
        "execution_state": dispatch["state"]}, status=201)


@api_view(["POST"])
@endpoint
def cancel_work(request, work_id):
    user = actor(request)
    data = body(request, {"expected_version", "request_id"})
    expected = version(data)
    key = string(data, "request_id", 90)
    with transaction.atomic():
        root_id = AgentWorkTask.objects.filter(pk=uid(work_id), owner=user).values_list(
            "conversation__root_run_id", flat=True).first()
        if root_id is None:
            raise Http404
        root = AgentRun.objects.select_for_update().get(pk=root_id)
        item = AgentWorkTask.objects.select_for_update().filter(pk=uid(work_id), owner=user).first()
        if item is None:
            raise Http404
        if item.current_requirement_version != expected:
            fail("conflict", f"当前要求版本为 {item.current_requirement_version}。", 409)
        event, created = append_public_event(root.pk, root.pk, f"cancel:{item.pk}:{key}",
            "cancel_requested", {"work_id": str(item.pk)}, work=item)
        if created:
            root.state = "stopping"
            root.stop_reason = "user_cancelled"
            if "identity" in root.policy:
                root.policy = {**root.policy, "fence": root.policy.get("fence", 0) + 1}
            root.save(update_fields=["state", "stop_reason", "policy", "updated_at"])
            item.state = "stopping"
            item.stop_reason = "user_cancelled"
            item.save(update_fields=["state", "stop_reason", "updated_at"])
            from .product_agent import invalidate_tasks_for_work

            invalidate_tasks_for_work(item, cancelled=True)
        cancellation = {"state": "stopping"}
        if item.state == "stopping":
            transaction.on_commit(lambda: cancellation.update(dispatch_native_cancel(root.pk)), robust=True)
    item.refresh_from_db()
    return Response({"state": item.state, "event_seq": event.seq,
                     "native_cancel_state": cancellation["state"]})


@api_view(["POST"])
@endpoint
def retry_work(request, work_id):
    user = actor(request)
    data = body(request, {"expected_version", "request_id"})
    expected = version(data)
    key = string(data, "request_id", 90)
    original = owned(AgentWorkTask, user, work_id)
    if original.current_requirement_version != expected or original.state not in ("failed", "cancelled", "terminated"):
        fail("conflict", "当前工作不能重试。", 409)
    with transaction.atomic():
        existing = AgentConversation.objects.filter(owner=user, create_key=f"retry:{key}").first()
        if existing:
            item = AgentWorkTask.objects.filter(conversation=existing, predecessor=original).first()
            if item is None:
                fail("conflict", "请求键已用于其他工作。", 409)
            return Response(work_data(item))
        conversation = AgentConversation.objects.create(owner=user, department_code=department(user),
            project=original.project, create_key=f"retry:{key}")
        message = AgentMessage.objects.create(conversation=conversation, role="user", content=original.goal)
        item = AgentWorkTask.objects.create(owner=user, department_code=department(user),
            conversation=conversation, project=original.project, predecessor=original, goal=original.goal,
            public_summary=original.public_summary,
            current_requirement_version=1)
        message.work = item
        message.save(update_fields=["work"])
        requirement = AgentRequirement.objects.create(work=item, version=1, user_message=message,
            content=original.goal)
        AgentRun.objects.create(id=conversation.root_run_id, root_run_id=conversation.root_run_id,
            conversation=conversation, work=item, requirement=requirement, state="pending")
        transaction.on_commit(lambda: dispatch_saved_message(message.pk), robust=True)
    item.refresh_from_db()
    return Response(work_data(item), status=201)


@api_view(["GET"])
@endpoint
def events(request, work_id):
    user = actor(request)
    item = owned(AgentWorkTask, user, work_id)
    offset, size = page(request, {"after_seq"})
    raw = request.query_params.get("after_seq", "0")
    if not raw.isascii() or not raw.isdigit():
        fail()
    query = AgentEvent.objects.filter(root_id=item.conversation.root_run_id, work=item,
                                      seq__gt=int(raw)).order_by("seq")
    return Response(listing(query, offset, size, lambda event: {"id": str(event.pk), "seq": event.seq,
        "type": event.type, "payload": event.payload, "created_at": event.created_at.isoformat()}))


@api_view(["GET"])
@endpoint
def skills(request):
    actor(request)
    return Response({"items": list(reviewed_catalog().values())})


@api_view(["GET", "POST"])
@endpoint
def installations(request):
    user = actor(request)
    if request.method == "GET":
        return Response({"items": [{"skill_id": item.skill_id, "version": item.version,
            "digest": item.digest, "enabled": item.enabled} for item in
            AgentSkillInstallation.objects.filter(owner=user).order_by("skill_id")]})
    data = body(request, {"skill_id", "enabled"})
    skill_id = string(data, "skill_id", 100)
    if type(data["enabled"]) is not bool:
        fail()
    catalog = reviewed_catalog().get(skill_id)
    if catalog is None:
        fail("not_found", "技能不在审核目录中。", 404)
    item, _ = AgentSkillInstallation.objects.update_or_create(owner=user, skill_id=skill_id,
        defaults={"version": catalog["version"], "digest": catalog["digest"], "enabled": data["enabled"]})
    return Response({"skill_id": item.skill_id, "version": item.version,
                     "digest": item.digest, "enabled": item.enabled})


@api_view(["DELETE"])
@endpoint
def installation_detail(request, skill_id):
    user = actor(request)
    AgentSkillInstallation.objects.filter(owner=user, skill_id=skill_id).delete()
    return Response(status=204)


@api_view(["GET"])
@endpoint
def management_work(request):
    actor(request, manager=True)
    offset, size = page(request, {"department_code", "owner_id", "start", "end"})
    department_code = request.query_params.get("department_code")
    if department_code and department_code not in ("product", "hr", "finance", "engineering"):
        fail()
    query = AgentWorkTask.objects.all()
    if department_code:
        query = query.filter(department_code=department_code)
    owner_id = request.query_params.get("owner_id")
    if owner_id:
        if not owner_id.isascii() or not owner_id.isdigit():
            fail()
        query = query.filter(owner_id=int(owner_id))
    for key, lookup in (("start", "created_at__gte"), ("end", "created_at__lte")):
        value = request.query_params.get(key)
        if value:
            parsed = parse_datetime(value)
            if parsed is None or parsed.tzinfo is None:
                fail()
            query = query.filter(**{lookup: parsed})
    return Response(listing(query.order_by("-created_at"), offset, size,
        lambda item: {"id": str(item.pk), "owner_id": item.owner_id,
            "department_code": item.department_code, "summary": item.public_summary,
            "state": item.state, "updated_at": item.updated_at.isoformat(),
            "business_references": management_references(item)}))


@api_view(["GET"])
@endpoint
def management_usage(request):
    actor(request, manager=True)
    page(request, {"department_code", "owner_id", "start", "end"})
    query = ModelCallLog.objects.filter(root__isnull=False, physical_call_id__isnull=False).exclude(
        physical_call_id="")
    department_code = request.query_params.get("department_code")
    if department_code:
        if department_code not in ("product", "hr", "finance", "engineering"):
            fail()
        query = query.filter(root__conversation__department_code=department_code)
    owner_id = request.query_params.get("owner_id")
    if owner_id:
        if not owner_id.isascii() or not owner_id.isdigit():
            fail()
        query = query.filter(root__conversation__owner_id=int(owner_id))
    for key, lookup in (("start", "created_at__gte"), ("end", "created_at__lte")):
        value = request.query_params.get(key)
        if value:
            parsed = parse_datetime(value)
            if parsed is None or parsed.tzinfo is None:
                fail()
            query = query.filter(**{lookup: parsed})
    totals = query.aggregate(calls=Count("id"), prompt_total=Sum("prompt_tokens"),
        completion_total=Sum("completion_tokens"), unknown_usage_calls=Count("id", filter=Q(
            prompt_tokens__isnull=True) | Q(completion_tokens__isnull=True)))
    known = not totals["unknown_usage_calls"]
    return Response({"calls": totals["calls"], "unknown_usage_calls": totals["unknown_usage_calls"],
        "prompt_tokens": totals["prompt_total"] if known else None,
        "completion_tokens": totals["completion_total"] if known else None})


urlpatterns = [
    path("attachments/", attachments),
    path("attachments/<uuid:attachment_id>/", attachment_detail),
    path("attachments/<uuid:attachment_id>/download/", attachment_download),
    path("projects/", projects),
    path("conversations/", conversations),
    path("conversations/<uuid:conversation_id>/messages/", messages),
    path("work/", work_list),
    path("work/<uuid:work_id>/", work_detail),
    path("work/<uuid:work_id>/requirements/", requirements),
    path("work/<uuid:work_id>/cancel/", cancel_work),
    path("work/<uuid:work_id>/retry/", retry_work),
    path("work/<uuid:work_id>/events/", events),
    path("skills/", skills),
    path("installations/", installations),
    path("installations/<slug:skill_id>/", installation_detail),
    path("management/work/", management_work),
    path("management/usage/", management_usage),
    path("", include("portal.agent_employees")),
]
