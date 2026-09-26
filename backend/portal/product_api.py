import re
import uuid
from collections.abc import Mapping
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.urls import path
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from .product_models import (DocumentApproval, DocumentArtifact, DocumentAttempt,
                             DocumentRevision, DocumentTask)
from .product_service import (ProductError, append_revision, approved_blueprint,
                              artifact_releasable, attach_import_path, attach_source, digest,
                              effective_artifact_approval, eligible_reviewer,
                              invalidate_generation, product_user_allowed,
                              preserve_input_provenance, require_editable, require_owner, require_version, reviewer_allowed,
                              source_ids_belong, task_for, validate_blueprint, resolve_input_issues, input_authorized, approval_authorization, approval_current,
                              validate_chapter, validate_input)
from .product_storage import StorageError, remove_relative, verified_artifact
from .product_release import CONTENT_CHECKS, candidate_current, evidence_file, public_evidence
from .product_pair import latest_report_content, output_current, pair_snapshot, report_current
from .security import audit
from .models import User
from .product_intake import summary as extraction_summary
from .product_service import require_project_materials


def product_endpoint(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        request.product_request_id = request.headers.get("X-Request-ID", "")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", request.product_request_id):
            request.product_request_id = uuid.uuid4().hex
        if not getattr(settings, "PRODUCT_P1_ENABLED", False):
            return _error(request, ProductError("product_disabled", "产品方案 P1 当前未启用。", 503))
        try:
            response = function(request, *args, **kwargs)
        except ProductError as error:
            response = _error(request, error)
        except ParseError:
            response = _error(request, ProductError("invalid_json", "请求 JSON 格式无效。"))
        except StorageError as error:
            response = _error(request, ProductError(error.code, error.detail, 400))
        response["X-Request-ID"] = request.product_request_id
        response["Cache-Control"] = "private, no-store"
        return response
    return wrapped


def _error(request, error):
    return Response({"detail": error.detail, "code": error.code, "request_id": request.product_request_id}, status=error.status)


def _body(request, required, optional=()):
    if not isinstance(request.data, Mapping):
        raise ProductError("invalid_request", "请求必须是对象。")
    keys = set(request.data)
    if not set(required) <= keys or not keys <= set(required) | set(optional):
        raise ProductError("invalid_request", "请求字段无效。")
    return request.data


def _expected(value):
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductError("invalid_request", "expected_version 必须是整数。")
    return value


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or "\x00" in value:
        raise ProductError("invalid_request", "标题格式无效。")
    return value


def _conversation_intent(message, requested_outputs, paths_supplied, paths):
    allowed = {"technical_solution", "feasibility", "presentation"}
    if requested_outputs is not None:
        if (not isinstance(requested_outputs, list) or len(requested_outputs) > 3
                or any(not isinstance(value, str) or value not in allowed for value in requested_outputs)):
            raise ProductError("invalid_request", "requested_outputs 格式无效。")
        outputs = list(dict.fromkeys(requested_outputs))
    else:
        outputs = ["technical_solution", "feasibility", "presentation"]
    if "technical_solution" not in outputs:
        outputs.insert(0, "technical_solution")
    detected_paths = paths if paths_supplied else _paths_from_message(message)
    if (not isinstance(detected_paths, list) or len(detected_paths) > 20
            or any(not isinstance(value, str) or not value or len(value) > 2000 for value in detected_paths)):
        raise ProductError("invalid_request", "paths 格式无效。")
    return outputs, list(dict.fromkeys(detected_paths))


def _paths_from_message(message):
    quoted = re.findall(r'["“]((?:[A-Za-z]:\\|\\\\)[^"”\r\n]+)["”]', message)
    inline = re.findall(
        r'((?:[A-Za-z]:\\|\\\\)[^\r\n"“”]*?\.(?:csv|txt|pdf|docx|xlsx|xls|png|jpe?g|webp|bmp|tiff?))(?=$|[\s，。；;、）)])',
        message,
        flags=re.IGNORECASE,
    )
    lines = [line.strip() for line in message.splitlines()]
    standalone = [line for line in lines if re.fullmatch(r"(?:[A-Za-z]:\\|\\\\).+", line)]
    return list(dict.fromkeys([*quoted, *inline, *standalone]))


def _requested_output_states(outputs):
    result = []
    for value in outputs:
        if value == "technical_solution":
            result.append({"type": value, "status": "blocked", "code": "model_not_authorized"})
        else:
            result.append({"type": value, "status": "not_started", "code": "approved_content_required"})
    return result


def _audit(request, action, task, result="success", changes=None, target=None):
    object_id = target or task.pk
    audit(request.user, action, f"{object_id}:v{task.version}:r{request.product_request_id}", result=result, changes=changes)


def _input_revision(task):
    return task.revisions.filter(kind=DocumentRevision.Kind.INPUT, version=task.input_version).first()


def _blueprint_revision(task):
    if not task.blueprint_version:
        return None
    return task.revisions.filter(kind=DocumentRevision.Kind.BLUEPRINT, version=task.blueprint_version).first()


def _revision_data(revision):
    if revision is None:
        return None
    return {"id": str(revision.pk), "version": revision.version, "sha256": revision.sha256, "payload": revision.payload}


def _task_summary(task):
    return {
        "id": str(task.pk), "title": task.title, "state": task.state, "stage": task.stage,
        "version": task.version, "owner_id": task.owner_id, "reviewer_id": task.reviewer_id,
        "updated_at": task.updated_at.isoformat(),
    }


def _chapters_complete(task, input_revision, blueprint):
    if input_revision is None or blueprint is None:
        return False
    current = {
        chapter.payload.get("chapter_id")
        for chapter in task.revisions.filter(
            kind=DocumentRevision.Kind.CHAPTER,
            input_hash=input_revision.sha256,
            blueprint_hash=blueprint.sha256,
        )
        if chapter.payload.get("paragraphs")
    }
    required = {chapter["id"] for chapter in blueprint.payload["chapters"]}
    return bool(required and required <= current)


def _task_actions(task, user, input_revision, blueprint):
    actions = []
    ended = task.state in {DocumentTask.State.CANCELLED, DocumentTask.State.COMPLETED}
    busy = task.state in {DocumentTask.State.QUEUED, DocumentTask.State.RUNNING}
    queueable = not (busy or ended)
    approved = approved_blueprint(task)

    if task.owner_id == user.pk:
        if not ended:
            actions.append("cancel")
            if not busy:
                actions.extend(["edit", "save_blueprint", "add_source"])
        if queueable and input_revision is not None:
            actions.extend(["queue_retrieve", "queue_blueprint"])
        if (queueable and task.state == DocumentTask.State.WAITING_REVIEW
                and task.stage == DocumentTask.Stage.BLUEPRINT and blueprint is not None):
            actions.append("confirm_blueprint")
        if (task.state in {DocumentTask.State.FAILED, DocumentTask.State.WAITING_INPUT}
                and task.attempt_count < int(getattr(settings, "PRODUCT_MAX_ATTEMPTS", 8))
                and task.pending_action in {"blueprint", "generate_outputs"}):
            actions.append("retry")
        if not (task.state in {DocumentTask.State.QUEUED, DocumentTask.State.RUNNING} or ended):
            actions.append("add_statement")

    # The owner confirms the blueprint in the single-user workflow. Input/source
    # checks remain explicit; legacy assigned reviewers may still resolve these.
    input_reviewer = task.owner_id == user.pk or (
        task.reviewer_id == user.pk and reviewer_allowed(user)
    )
    if queueable and input_reviewer and input_revision is not None and input_revision.payload.get("issues"):
        actions.append("review_input")
    return actions


def _task_blockers(actions):
    blockers = {}
    if "queue_retrieve" in actions and (
            not getattr(settings, "PRODUCT_RETRIEVAL_ENABLED", False)
            or not getattr(settings, "PRODUCT_RETRIEVAL_AUTHORIZATIONS", {})):
        blockers["queue_retrieve"] = {
            "code": "retrieval_authorization_required",
            "detail": "D-01/D-08 尚未批准真实资料检索与身份映射，当前不能发起检索。",
        }
    for action in ("queue_blueprint", "queue_write"):
        if action not in actions:
            continue
        if not getattr(settings, "PRODUCT_MODEL_CALLS_ALLOWED", False):
            blockers[action] = {
                "code": "model_authorization_required",
                "detail": "D-01 尚未批准真实模型调用与资料外发，当前不能执行模型生成。",
            }
    if "queue_render" in actions and not getattr(settings, "PRODUCT_TEMPLATE_APPROVAL", {}):
        blockers["queue_render"] = {
            "code": "template_approval_required",
            "detail": "D-02 正式母版与目标 Office 环境尚未签认，当前不生成交付稿。",
        }
    if "queue_candidate" in actions:
        if not getattr(settings, "PRODUCT_TEMPLATE_APPROVAL", {}):
            blockers["queue_candidate"] = {
                "code": "template_approval_required",
                "detail": "D-02 正式母版尚未签认，当前不能生成正式候选。",
            }
        elif not getattr(settings, "PRODUCT_OFFICE_RENDER_ENABLED", False):
            blockers["queue_candidate"] = {
                "code": "office_render_disabled",
                "detail": "目标 Office 渲染环境未获准启用，当前不能生成正式候选。",
            }
    return blockers


def _task_detail(task, user):
    input_revision = _input_revision(task)
    if input_revision and not input_authorized(task, input_revision):
        raise ProductError("source_permission_changed", "资料授权已变化，当前内容不可读取。", 404)
    input_access = {revision.sha256: input_authorized(task, revision) for revision in task.revisions.filter(kind="input")}
    blueprint = _blueprint_revision(task)
    chapters = [{
        "id": str(record.pk), "version": record.version, "sha256": record.sha256,
        "family": record.family, "payload": record.payload, "input_hash": record.input_hash,
        "blueprint_hash": record.blueprint_hash, "created_at": record.created_at.isoformat(),
    } for record in task.revisions.filter(kind=DocumentRevision.Kind.CHAPTER).order_by("version") if input_access.get(record.input_hash, False)]
    reports = [{
        "id": str(record.pk), "family": record.family, "version": record.version, "sha256": record.sha256,
        "input_hash": record.input_hash, "blueprint_hash": record.blueprint_hash,
        "current": report_current(task, record),
        "approved": False, "created_at": record.created_at.isoformat(),
    } for record in task.revisions.filter(kind=DocumentRevision.Kind.REPORT).order_by("family", "version")
        if input_access.get(record.input_hash, False)]
    artifacts = []
    for artifact in task.artifacts.order_by("version"):
        if not input_access.get(artifact.input_hash, False):
            continue
        approved = effective_artifact_approval(artifact) is not None
        artifacts.append({
            "id": str(artifact.pk), "family": artifact.family, "version": artifact.version, "sha256": artifact.sha256,
            "blueprint_hash": artifact.blueprint_hash, "input_hash": artifact.input_hash,
            "review_id": str(artifact.review_id) if artifact.review_id else None,
            "render_evidence": public_evidence(artifact.render_evidence), "template_hash": artifact.template_hash,
            "approved": approved, "draft": not approved, "created_at": artifact.created_at.isoformat(),
        })
    sources = [{
        "id": str(source.pk), "original_name": source.original_name, "media_type": source.media_type, "purpose": source.purpose,
        "sha256": source.sha256, "size": source.size, "parsed": extraction_summary(source.parsed),
        "warnings": source.warnings, "created_at": source.created_at.isoformat(),
    } for source in task.sources.order_by("created_at")]
    approvals = [{
        "id": str(record.pk), "target": ("blueprint" if record.revision_id and record.revision.kind == DocumentRevision.Kind.BLUEPRINT else "report" if record.revision_id else "artifact"),
        "target_id": str(record.revision_id or record.artifact_id), "actor_id": record.actor_id,
        "decision": record.decision, "comment": record.comment, "sha256": record.sha256,
        "created_at": record.created_at.isoformat(),
    } for record in task.approvals.select_related("revision", "artifact").order_by("created_at")
        if input_access.get(record.revision.input_hash if record.revision_id else record.artifact.input_hash, False)]
    issues = []
    if blueprint:
        issues.extend({"code": "blueprint_missing", "text": value} for value in blueprint.payload.get("missing", []))
        issues.extend({"code": "blueprint_conflict", "text": value} for value in blueprint.payload.get("conflicts", []))
    if input_revision:
        issues.extend(input_revision.payload.get("issues", []))
    review = task.revisions.filter(
        kind=DocumentRevision.Kind.REVIEW,
        input_hash=input_revision.sha256 if input_revision else "",
        blueprint_hash=blueprint.sha256 if blueprint else "",
    ).order_by("-version").first()
    if review:
        issues.extend(review.payload.get("issues", []))
        issues.extend({"code": "model_review_issue", "text": value} for value in review.payload.get("model_review", {}).get("issues", []))
    if not getattr(settings, "PRODUCT_FORMAL_RELEASE_ENABLED", False):
        issues.append({"code": "formal_release_blocked", "text": "D-02 格式与发布基线未批准，成果仅可作为草稿。"})
    actions = _task_actions(task, user, input_revision, blueprint)
    return {
        "id": str(task.pk), "title": task.title, "state": task.state, "stage": task.stage,
        "version": task.version, "input_version": task.input_version,
        "blueprint_version": task.blueprint_version,
        "blueprint_approved": approved_blueprint(task) is not None,
        "pending_action": task.pending_action,
        "created_at": task.created_at.isoformat(), "updated_at": task.updated_at.isoformat(),
        "input": input_revision.payload if input_revision else None,
        "blueprint": _revision_data(blueprint), "chapters": chapters, "reports": reports, "artifacts": artifacts,
        "sources": sources, "approvals": approvals, "issues": issues,
        "error_code": task.error_code,
        "actions": actions, "blockers": _task_blockers(actions),
        "reviewer_id": task.reviewer_id, "owner_id": task.owner_id,
        "input_issues": [{**issue, "issue_hash": digest(issue)} for issue in (input_revision.payload.get("issues", []) if input_revision else [])],
        "impact": task.checkpoint.get("impact", {}),
        "intake_mode": task.checkpoint.get("intake_mode", "manual"),
        "analysis_progress": task.checkpoint.get("analysis_progress", {}),
    }


@api_view(["POST"])
@product_endpoint
def assign_reviewer(request, task_id):
    body = _body(request, {"expected_version", "reviewer_id", "reason"})
    if not isinstance(body["reason"], str) or not body["reason"].strip() or len(body["reason"]) > 2000:
        raise ProductError("invalid_request", "请填写指定或改派原因。")
    with transaction.atomic():
        task = task_for(request.user, task_id, write=True)
        require_owner(task, request.user)
        require_version(task, _expected(body["expected_version"]))
        if task.state in {"QUEUED", "RUNNING", "CANCELLED", "COMPLETED"}:
            raise ProductError("invalid_state", "当前状态不能改派审核人。", 409)
        reviewer = eligible_reviewer(body["reviewer_id"], request.user)
        if reviewer is None:
            raise ProductError("invalid_reviewer", "必须指定有效审核人。")
        if reviewer.pk != task.reviewer_id:
            previous = task.reviewer_id
            blueprint = _blueprint_revision(task)
            current_input = _input_revision(task)
            if current_input and current_input.payload.get("issue_resolutions"):
                payload = dict(current_input.payload)
                payload["issues"] = [*payload.get("issues", []), *[record["issue"] for record in payload["issue_resolutions"]]]
                payload["issue_resolutions"] = []
                current_input = append_revision(task, "input", payload, actor=request.user, reason="reviewer_changed")
                task.input_version = current_input.version
            task.reviewer = reviewer
            if current_input and not input_authorized(task, current_input):
                raise ProductError("source_permission_changed", "当前知识来源绑定原审核人；请先按新权限建立任务，不能自动转移资料授权。", 409)
            task.fence += 1
            task.lease_until = None
            task.pending_action = ""
            task.error_code = ""
            task.state = "WAITING_REVIEW" if blueprint else "DRAFT"
            task.stage = "BLUEPRINT" if blueprint else "INTAKE"
            if blueprint:
                new_revision = append_revision(task, "blueprint", blueprint.payload, input_hash=current_input.sha256, actor=request.user, reason="reviewer_changed")
                task.blueprint_version = new_revision.version
            task.checkpoint = {**task.checkpoint, "reviewer_changes": [*task.checkpoint.get("reviewer_changes", []),
                {"from": previous, "to": reviewer.pk, "actor_id": request.user.pk, "reason": body["reason"], "version": task.version}]}
            task.version += 1
            task.save()
    _audit(request, "product_reviewer_change", task, changes=["reviewer_id"])
    return Response(_task_detail(task, request.user))


@api_view(["POST"])
@product_endpoint
def input_review(request, task_id):
    body = _body(request, {"expected_version", "resolutions"})
    with transaction.atomic():
        task = task_for(request.user, task_id, write=True)
        # task_for permits only the owner or the task's still-authorized legacy reviewer.
        require_version(task, _expected(body["expected_version"]))
        if task.state in {"QUEUED", "RUNNING", "CANCELLED", "COMPLETED"}:
            raise ProductError("invalid_state", "当前状态不能核对输入。", 409)
        resolve_input_issues(task, request.user, body["resolutions"])
    _audit(request, "product_input_review", task, changes=["input"])
    return Response(_task_detail(task, request.user))


@api_view(["POST"])
@product_endpoint
def input_statement(request, task_id):
    body = _body(request, {"expected_version", "category", "text", "source_ids"})
    if not isinstance(body["category"], str) or body["category"] not in {"fact", "inference", "conflict", "missing"} or not isinstance(body["text"], str) or not body["text"].strip() or len(body["text"]) > 4000:
        raise ProductError("invalid_request", "请填写事实、推断、冲突或缺项说明。")
    with transaction.atomic():
        task = task_for(request.user, task_id, write=True)
        require_owner(task, request.user)
        require_version(task, _expected(body["expected_version"]))
        if task.state in {"QUEUED", "RUNNING", "CANCELLED", "COMPLETED"}:
            raise ProductError("invalid_state", "当前状态不能补充资料判断。", 409)
        if not isinstance(body["source_ids"], list) or not body["source_ids"] or len(body["source_ids"]) > 100 or not source_ids_belong(task, body["source_ids"]):
            raise ProductError("invalid_source", "必须关联当前任务来源。")
        previous = _input_revision(task)
        payload = dict(previous.payload)
        statement = {"category": body["category"], "text": body["text"], "source_ids": body["source_ids"], "actor_id": request.user.pk}
        payload["statements"] = [*payload.get("statements", []), statement]
        payload["issues"] = [*payload.get("issues", []), {"code": "statement_review_required", **statement}]
        revision = append_revision(task, "input", payload, actor=request.user, reason="manual_statement")
        invalidate_generation(task)
        task.input_version = revision.version
        task.version += 1
        task.save()
    _audit(request, "product_input_statement", task, changes=["input"])
    return Response(_task_detail(task, request.user))


@api_view(["POST"])
@product_endpoint
def conversations(request):
    if not product_user_allowed(request.user):
        raise ProductError("not_found", "对象不存在。", 404)
    body = _body(request, {"message"}, {"paths", "requested_outputs"})
    message = body["message"]
    if not isinstance(message, str) or not message.strip() or len(message) > 10000 or "\x00" in message:
        raise ProductError("invalid_request", "message 格式无效。")
    outputs, detected_paths = _conversation_intent(
        message, body.get("requested_outputs"), "paths" in body, body.get("paths"),
    )
    key = request.headers.get("Idempotency-Key", "")
    if not key or len(key) > 128 or any(ord(character) < 33 for character in key):
        raise ProductError("invalid_idempotency_key", "Idempotency-Key 无效。")
    output_states = _requested_output_states(outputs)
    title_text = next((line.strip() for line in message.splitlines() if line.strip()), "技术方案任务")
    title = _title(title_text[:200])
    input_payload = validate_input({
        "project": title,
        "requirements": message,
        "items": [],
        "background": "",
        "conditions": [],
    })
    conversation_record = {
        "message": message,
        "intent_mode": "deterministic_fallback",
        "requested_outputs": output_states,
        "detected_paths": detected_paths,
        "path_scope": "shared_authorized_roots",
    }
    input_payload["conversation_request"] = conversation_record
    input_payload["conversation_history"] = [{**conversation_record, "actor_id": request.user.pk}]
    payload_hash = digest({"message": message, "paths": detected_paths, "requested_outputs": outputs})
    created = False
    written_paths = []
    try:
        with transaction.atomic():
            owner = User.objects.select_for_update().get(pk=request.user.pk)
            task = DocumentTask.objects.filter(owner=owner, idempotency_key=key).first()
            if task:
                if task.payload_hash != payload_hash:
                    raise ProductError("idempotency_conflict", "同一幂等键已用于不同请求。", 409)
            else:
                task = DocumentTask.objects.create(
                    owner=owner, title=title, idempotency_key=key, payload_hash=payload_hash,
                    checkpoint={"conversation_intent": {
                        "mode": "deterministic_fallback", "requested_outputs": output_states,
                    }},
                )
                revision = append_revision(task, DocumentRevision.Kind.INPUT, input_payload, actor=owner,
                                           reason="conversation_created")
                task.input_version = revision.version
                task.save(update_fields=["input_version", "updated_at"])
                created = True
                for source_path in detected_paths:
                    task, source = attach_import_path(owner, task.pk, task.version, source_path)
                    written_paths.append(source.path)
    except Exception:
        for written_path in written_paths:
            remove_relative(written_path)
        raise
    _audit(
        request, "product_conversation_create" if created else "product_conversation_create_replay", task,
        changes=["title", "input", "sources"] if created else [],
    )
    return Response({
        "task": _task_detail(task, request.user),
        "intent": {
            "mode": "deterministic_fallback",
            "model_called": False,
            "requested_outputs": output_states,
            "detected_paths": detected_paths,
        },
        "blockers": {
            "technical_solution": "model_not_authorized",
            "feasibility": "approved_content_required",
            "presentation": "approved_content_required",
        },
    }, status=201 if created else 200)


@api_view(["POST"])
@product_endpoint
def task_conversation(request, task_id):
    body = _body(request, {"expected_version", "message"}, {"paths"})
    message = body["message"]
    if not isinstance(message, str) or not message.strip() or len(message) > 10000 or "\x00" in message:
        raise ProductError("invalid_request", "message 格式无效。")
    _, detected_paths = _conversation_intent(
        message, ["technical_solution"], "paths" in body, body.get("paths"),
    )
    written_paths = []
    try:
        with transaction.atomic():
            task = task_for(request.user, task_id, write=True)
            require_owner(task, request.user)
            require_version(task, _expected(body["expected_version"]))
            require_editable(task)
            previous = _input_revision(task)
            if previous is None:
                raise ProductError("input_required", "请先保存输入。", 409)
            payload = dict(previous.payload)
            separator = "\n\n" if payload.get("requirements") else ""
            payload["requirements"] = payload.get("requirements", "") + separator + message
            record = {
                "message": message,
                "intent_mode": "deterministic_fallback",
                "detected_paths": detected_paths,
                "path_scope": "shared_authorized_roots",
                "actor_id": request.user.pk,
            }
            payload.setdefault("conversation_request", {key: value for key, value in record.items() if key != "actor_id"})
            payload["conversation_history"] = [*payload.get("conversation_history", []), record]
            revision = append_revision(task, DocumentRevision.Kind.INPUT, payload, actor=request.user,
                                       reason="conversation_updated")
            invalidate_generation(task)
            task.input_version = revision.version
            task.version += 1
            task.save()
            for source_path in detected_paths:
                task, source = attach_import_path(request.user, task.pk, task.version, source_path)
                written_paths.append(source.path)
    except Exception:
        for written_path in written_paths:
            remove_relative(written_path)
        raise
    _audit(request, "product_conversation_append", task, changes=["input", "sources"])
    requested_outputs = payload.get("conversation_request", {}).get("requested_outputs") or _requested_output_states(["technical_solution"])
    return Response({
        "task": _task_detail(task, request.user),
        "intent": {
            "mode": "deterministic_fallback", "model_called": False,
            "requested_outputs": requested_outputs, "detected_paths": detected_paths,
        },
        "blockers": {
            "technical_solution": "model_not_authorized",
            "feasibility": "approved_content_required",
            "presentation": "approved_content_required",
        },
    })


@api_view(["GET", "POST"])
@product_endpoint
def tasks(request):
    if not product_user_allowed(request.user):
        raise ProductError("not_found", "对象不存在。", 404)
    if request.method == "GET":
        scope = Q(owner=request.user)
        if reviewer_allowed(request.user):
            scope |= Q(reviewer=request.user)
        queryset = DocumentTask.objects.filter(scope).order_by("-updated_at")
        audit(request.user, "product_task_list", f"list:r{request.product_request_id}")
        return Response([_task_summary(task) for task in queryset])
    body = _body(request, {"title", "input"}, {"reviewer_id", "intake_mode"})
    intake_mode = body.get("intake_mode", "manual")
    if not isinstance(intake_mode, str) or intake_mode not in {"manual", "equipment_background"}:
        raise ProductError("invalid_intake_mode", "项目资料模式无效。")
    key = request.headers.get("Idempotency-Key", "")
    if not key or len(key) > 128 or any(ord(character) < 33 for character in key):
        raise ProductError("invalid_idempotency_key", "Idempotency-Key 无效。")
    title = _title(body["title"])
    input_payload = validate_input(body["input"])
    reviewer_id = body.get("reviewer_id")
    payload_hash = digest({"title": title, "input": input_payload, "reviewer_id": reviewer_id})
    if intake_mode != "manual":
        payload_hash = digest({"base": payload_hash, "intake_mode": intake_mode})
    created = False
    with transaction.atomic():
        owner = User.objects.select_for_update().get(pk=request.user.pk)
        existing = DocumentTask.objects.filter(owner=owner, idempotency_key=key).first()
        if existing:
            if existing.payload_hash != payload_hash:
                raise ProductError("idempotency_conflict", "同一幂等键已用于不同请求。", 409)
            task = existing
        else:
            reviewer = eligible_reviewer(reviewer_id, owner)
            task = DocumentTask.objects.create(
                owner=owner, reviewer=reviewer, title=title,
                idempotency_key=key, payload_hash=payload_hash, checkpoint={"intake_mode": intake_mode},
            )
            revision = append_revision(task, DocumentRevision.Kind.INPUT, input_payload, actor=owner,
                                       reason="task_created")
            task.input_version = revision.version
            task.save(update_fields=["input_version", "updated_at"])
            created = True
    _audit(request, "product_task_create" if created else "product_task_create_replay", task, changes=["title", "input", "reviewer_id"] if created else [])
    return Response(_task_detail(task, request.user), status=201 if created else 200)


@api_view(["GET", "PATCH"])
@product_endpoint
def task_detail(request, task_id):
    if request.method == "GET":
        task = task_for(request.user, task_id)
        _audit(request, "product_task_read", task)
        return Response(_task_detail(task, request.user))
    body = _body(request, {"expected_version"}, {"title", "input"})
    if not ({"title", "input"} & set(body)):
        raise ProductError("invalid_request", "至少提供 title 或 input。")
    with transaction.atomic():
        task = task_for(request.user, task_id, write=True)
        require_owner(task, request.user)
        require_version(task, _expected(body["expected_version"]))
        require_editable(task)
        changes = []
        if "title" in body:
            new_title = _title(body["title"])
            if task.title != new_title:
                invalidate_generation(task)
            task.title = new_title
            changes.append("title")
        if "input" in body:
            input_payload = validate_input(body["input"])
            previous_input = _input_revision(task)
            if previous_input:
                input_payload = preserve_input_provenance(previous_input.payload, input_payload)
            previous_blueprint = _blueprint_revision(task)
            task.checkpoint = {**task.checkpoint, "impact": {
                "reason": "input_changed", "scope": "all", "status": "requires_blueprint_review",
                "chapters": [chapter["id"] for chapter in previous_blueprint.payload["chapters"]] if previous_blueprint else [],
                "tables": ["INPUT_TABLE"], "previous_input_hash": previous_input.sha256 if previous_input else "",
            }}
            revision = append_revision(task, DocumentRevision.Kind.INPUT, input_payload, actor=request.user,
                                       reason="manual_input_edit")
            invalidate_generation(task)
            task.input_version = revision.version
            changes.append("input")
        task.version += 1
        task.save()
    _audit(request, "product_task_update", task, changes=changes)
    return Response(_task_detail(task, request.user))


@api_view(["PATCH"])
@product_endpoint
def blueprint(request, task_id):
    body = _body(request, {"expected_version", "payload"})
    with transaction.atomic():
        task = task_for(request.user, task_id, write=True)
        require_owner(task, request.user)
        require_version(task, _expected(body["expected_version"]))
        require_editable(task)
        input_revision = _input_revision(task)
        if input_revision is None:
            raise ProductError("input_required", "请先保存输入。", 409)
        payload = validate_blueprint(body["payload"], input_revision.payload)
        source_ids = [source_id for chapter in payload["chapters"] for source_id in chapter["source_ids"]]
        if not source_ids_belong(task, source_ids):
            raise ProductError("invalid_source", "蓝图引用了无权来源。")
        revision = append_revision(task, DocumentRevision.Kind.BLUEPRINT, payload, input_hash=input_revision.sha256,
                                   actor=request.user, reason="manual_blueprint_edit")
        task.blueprint_version = revision.version
        task.state = DocumentTask.State.WAITING_REVIEW
        task.stage = DocumentTask.Stage.BLUEPRINT
        task.pending_action = ""
        task.error_code = ""
        task.lease_until = None
        task.fence += 1
        task.version += 1
        task.save()
    _audit(request, "product_blueprint_update", task, changes=["blueprint"])
    return Response(_task_detail(task, request.user))


def _queue(task, action):
    if not isinstance(action, str):
        raise ProductError("invalid_action", "任务动作无效。")
    if task.state in {DocumentTask.State.QUEUED, DocumentTask.State.RUNNING, DocumentTask.State.CANCELLED, DocumentTask.State.COMPLETED}:
        raise ProductError("invalid_state", "当前状态不能排队。", 409)
    if action in {"blueprint", "retrieve"}:
        if _input_revision(task) is None:
            raise ProductError("input_required", "请先保存输入。", 409)
        task.stage = DocumentTask.Stage.BLUEPRINT if action == "blueprint" else DocumentTask.Stage.INTAKE
        if action == "blueprint":
            require_project_materials(task)
            task.checkpoint = {**task.checkpoint, "analysis_progress": {}}
    elif action in {"write", "render", "candidate", "three_drafts", "presentation", "generate_outputs"}:
        blueprint_revision = approved_blueprint(task)
        if blueprint_revision is None:
            raise ProductError("blueprint_approval_required", "当前蓝图尚未获有效批准。", 409)
        task.stage = DocumentTask.Stage.WRITING if action == "write" else DocumentTask.Stage.RENDER
        if action in {"render", "candidate"}:
            input_revision = _input_revision(task)
            if not _chapters_complete(task, input_revision, blueprint_revision):
                raise ProductError("chapters_incomplete", "当前蓝图章节尚未完整保存。", 409)
        if action == "three_drafts":
            current = _input_revision(task)
            required = {chapter["id"] for chapter in blueprint_revision.payload["chapters"]}
            for family in ("technical-solution", "feasibility"):
                found = {record.payload.get("chapter_id") for record in task.revisions.filter(
                    kind="chapter", family=family, input_hash=current.sha256, blueprint_hash=blueprint_revision.sha256)
                    if record.payload.get("paragraphs")}
                if not required <= found:
                    raise ProductError("chapters_incomplete", f"{family} 报告章节尚未完整保存。", 409)
        if action == "presentation":
            pair_snapshot(task)
    else:
        raise ProductError("invalid_action", "任务动作无效。")
    task.pending_action = action
    blocked = action in {"blueprint", "write", "generate_outputs"} and not getattr(settings, "PRODUCT_MODEL_CALLS_ALLOWED", False)
    task.state = DocumentTask.State.WAITING_INPUT if blocked else DocumentTask.State.QUEUED
    task.error_code = "model_authorization_required" if blocked else ""
    task.version += 1
    task.save()
    return blocked


@api_view(["POST"])
@product_endpoint
def queue_task(request, task_id):
    body = _body(request, {"expected_version", "action"})
    with transaction.atomic():
        task = task_for(request.user, task_id, write=True)
        require_owner(task, request.user)
        require_version(task, _expected(body["expected_version"]))
        blocked = _queue(task, body["action"])
    _audit(request, "product_task_queue", task, result="blocked" if blocked else "success", changes=["state", "pending_action"])
    if blocked:
        return _error(request, ProductError("model_authorization_required", "模型外发未获授权，任务已转为等待输入。", 409))
    return Response(_task_detail(task, request.user))


def _decision_target(task, target, target_id):
    try:
        if target == "blueprint":
            record = task.revisions.get(pk=target_id, kind=DocumentRevision.Kind.BLUEPRINT, version=task.blueprint_version)
            if record.input_hash != _input_revision(task).sha256:
                raise DocumentRevision.DoesNotExist
            return record
        if target == "report":
            record = task.revisions.get(pk=target_id, kind=DocumentRevision.Kind.REPORT)
            current = latest_report_content(task, record.family)
            blueprint = _blueprint_revision(task)
            if (current is None or current.pk != record.pk or record.input_hash != _input_revision(task).sha256
                    or blueprint is None or record.blueprint_hash != blueprint.sha256):
                raise DocumentRevision.DoesNotExist
            return record
        if target == "artifact":
            return task.artifacts.get(pk=target_id)
    except (ValueError, TypeError, ValidationError, DocumentRevision.DoesNotExist, DocumentArtifact.DoesNotExist) as error:
        raise ProductError("not_found", "对象不存在。", 404) from error
    raise ProductError("invalid_target", "审核目标无效。")


@api_view(["POST"])
@product_endpoint
def decisions(request, task_id):
    body = _body(request, {"expected_version", "target", "target_id", "sha256", "decision", "comment"}, {"scope"})
    if body["decision"] not in DocumentApproval.Decision.values or not isinstance(body["comment"], str) or len(body["comment"]) > 10000:
        raise ProductError("invalid_request", "审核决定格式无效。")
    with transaction.atomic():
        if body["target"] == "blueprint":
            task = task_for(request.user, task_id, write=True)
            require_owner(task, request.user)
        else:
            task = task_for(request.user, task_id, write=True, review=True)
        target = _decision_target(task, body["target"], body["target_id"])
        if not isinstance(body["sha256"], str) or target.sha256 != body["sha256"]:
            raise ProductError("stale_target", "审核目标版本或哈希已变化。", 409)
        lookup = {"revision": target} if body["target"] in {"blueprint", "report"} else {"artifact": target}
        existing = DocumentApproval.objects.filter(actor=request.user, decision=body["decision"], **lookup).first()
        if existing:
            latest = target.approvals.filter(actor=request.user).order_by("-created_at").first()
            if latest and latest.pk != existing.pk:
                raise ProductError("decision_superseded", "该审核决定已被后续决定取代，必须提交新版本。", 409)
            if existing.decision == "approve" and not approval_current(task, existing):
                raise ProductError("approval_authorization_changed", "授权已变化，请提交新版本后重新批准。", 409)
            _audit(request, "product_decision_replay", task, target=existing.pk)
            return Response({"approval_id": str(existing.pk), "task": _task_detail(task, request.user)})
        require_version(task, _expected(body["expected_version"]))
        expected_stage = DocumentTask.Stage.BLUEPRINT if body["target"] == "blueprint" else DocumentTask.Stage.FINAL_REVIEW
        if task.state != DocumentTask.State.WAITING_REVIEW or task.stage != expected_stage or task.lease_until is not None:
            raise ProductError("invalid_state", "任务当前不处于可审核状态。", 409)
        if body["target"] == "artifact" and body["decision"] == DocumentApproval.Decision.APPROVE and not artifact_releasable(target):
            raise ProductError("formal_release_blocked", "缺少正式发布许可、渲染证据或通过的内容审查。", 409)
        approval = DocumentApproval.objects.create(
            task=task, actor=request.user, decision=body["decision"], comment=body["comment"],
            sha256=target.sha256, authorization=approval_authorization(task, request.user), **lookup,
        )
        if body["decision"] == DocumentApproval.Decision.REVISE:
            if not body["comment"].strip():
                raise ProductError("invalid_request", "退回必须说明修改依据。")
            blueprint = _blueprint_revision(task)
            all_chapters = [chapter["id"] for chapter in blueprint.payload["chapters"]] if blueprint else []
            scope = body.get("scope", {"chapters": all_chapters, "tables": ["INPUT_TABLE"]})
            if (not isinstance(scope, dict) or set(scope) != {"chapters", "tables"}
                    or not isinstance(scope["chapters"], list) or not isinstance(scope["tables"], list)
                    or any(not isinstance(value, str) or value not in all_chapters for value in scope["chapters"])
                    or any(value != "INPUT_TABLE" for value in scope["tables"])
                    or not (scope["chapters"] or scope["tables"])):
                raise ProductError("invalid_request", "退回修改范围无效。")
            task.checkpoint = {**task.checkpoint, "revision_requests": [*task.checkpoint.get("revision_requests", [])[-19:],
                {"target_id": str(target.pk), "sha256": target.sha256, "actor_id": request.user.pk,
                 "comment": body["comment"], "scope": scope}]}
            task.fence += 1
            task.lease_until = None
            task.state = DocumentTask.State.WAITING_INPUT
            task.pending_action = ""
            task.error_code = "revision_requested"
            if body["target"] == "blueprint":
                require_project_materials(task)
                blocked = not getattr(settings, "PRODUCT_MODEL_CALLS_ALLOWED", False)
                task.state = DocumentTask.State.WAITING_INPUT if blocked else DocumentTask.State.QUEUED
                task.pending_action = "blueprint"
                task.error_code = "model_authorization_required" if blocked else ""
                task.checkpoint = {**task.checkpoint, "analysis_progress": {}}
        elif body["target"] == "blueprint":
            require_project_materials(task)
            task.pending_action = "generate_outputs"
            task.stage = DocumentTask.Stage.WRITING
            blocked = not getattr(settings, "PRODUCT_MODEL_CALLS_ALLOWED", False)
            task.state = DocumentTask.State.WAITING_INPUT if blocked else DocumentTask.State.QUEUED
            task.error_code = "model_authorization_required" if blocked else ""
            task.fence += 1
        elif body["target"] == "report":
            task.state = DocumentTask.State.WAITING_REVIEW
            task.stage = DocumentTask.Stage.FINAL_REVIEW
            task.pending_action = ""
            task.error_code = ""
        else:
            task.state = DocumentTask.State.COMPLETED
            task.stage = DocumentTask.Stage.FINAL_REVIEW
            task.pending_action = ""
            task.error_code = ""
        task.version += 1
        task.save()
    _audit(request, "product_decision", task, changes=["decision", "state"], target=approval.pk)
    return Response({"approval_id": str(approval.pk), "task": _task_detail(task, request.user)}, status=201)


@api_view(["POST"])
@product_endpoint
def cancel(request, task_id):
    body = _body(request, {"expected_version"})
    with transaction.atomic():
        task = task_for(request.user, task_id, write=True)
        require_owner(task, request.user)
        require_version(task, _expected(body["expected_version"]))
        if task.state == DocumentTask.State.COMPLETED:
            raise ProductError("invalid_state", "已完成任务不能取消。", 409)
        task.fence += 1
        task.lease_until = None
        task.state = DocumentTask.State.CANCELLED
        task.pending_action = ""
        task.version += 1
        task.save()
        DocumentAttempt.objects.filter(task=task, status=DocumentAttempt.Status.RUNNING).update(
            status=DocumentAttempt.Status.CANCELLED, finished_at=timezone.now(), error_code="cancelled",
        )
    _audit(request, "product_task_cancel", task, changes=["state", "fence", "lease_until"])
    return Response(_task_detail(task, request.user))


@api_view(["POST"])
@product_endpoint
def retry(request, task_id):
    body = _body(request, {"expected_version"})
    with transaction.atomic():
        task = task_for(request.user, task_id, write=True)
        require_owner(task, request.user)
        require_version(task, _expected(body["expected_version"]))
        if task.state not in {DocumentTask.State.FAILED, DocumentTask.State.WAITING_INPUT}:
            raise ProductError("invalid_state", "当前状态不能重试。", 409)
        if task.attempt_count >= int(getattr(settings, "PRODUCT_MAX_ATTEMPTS", 8)):
            raise ProductError("attempt_limit", "已达到隔离环境安全重试上限。", 409)
        if task.pending_action not in {"blueprint", "write", "render", "three_drafts", "presentation", "generate_outputs"}:
            raise ProductError("invalid_action", "没有可重试的持久动作。", 409)
        blocked = task.pending_action in {"blueprint", "write", "generate_outputs"} and not getattr(settings, "PRODUCT_MODEL_CALLS_ALLOWED", False)
        task.state = DocumentTask.State.WAITING_INPUT if blocked else DocumentTask.State.QUEUED
        task.error_code = "model_authorization_required" if blocked else ""
        task.version += 1
        task.save()
    _audit(request, "product_task_retry", task, result="blocked" if blocked else "success", changes=["state"])
    if blocked:
        return _error(request, ProductError("model_authorization_required", "模型外发未获授权，任务仍在等待输入。", 409))
    return Response(_task_detail(task, request.user))


@api_view(["POST"])
@product_endpoint
def sources(request, task_id):
    body = _body(request, {"expected_version", "file"}, {"purpose"})
    upload = body["file"]
    if not hasattr(upload, "read"):
        raise ProductError("invalid_file", "请选择上传文件。")
    task, source = attach_source(request.user, task_id, _expected(body["expected_version"]), upload, purpose=body.get("purpose"))
    _audit(request, "product_source_create", task, changes=["source", "input"], target=source.pk)
    return Response({"source_id": str(source.pk), "task": _task_detail(task, request.user)}, status=201)


@api_view(["POST"])
@product_endpoint
def chapters(request, task_id):
    body = _body(request, {"expected_version", "chapter_id", "title", "paragraphs", "source_ids"}, {"family"})
    with transaction.atomic():
        task = task_for(request.user, task_id, write=True)
        require_owner(task, request.user)
        require_version(task, _expected(body["expected_version"]))
        if task.state in {DocumentTask.State.QUEUED, DocumentTask.State.RUNNING, DocumentTask.State.CANCELLED, DocumentTask.State.COMPLETED}:
            raise ProductError("invalid_state", "当前状态不能手工修改章节。", 409)
        blueprint_revision = approved_blueprint(task)
        if blueprint_revision is None:
            raise ProductError("blueprint_approval_required", "当前蓝图尚未获有效批准。", 409)
        payload = validate_chapter({key: body[key] for key in ("chapter_id", "title", "paragraphs", "source_ids")})
        blueprint_chapter = next((chapter for chapter in blueprint_revision.payload["chapters"] if chapter["id"] == payload["chapter_id"]), None)
        if blueprint_chapter is None or blueprint_chapter["title"] != payload["title"]:
            raise ProductError("invalid_chapter", "章节不属于当前批准蓝图。", 409)
        if not source_ids_belong(task, payload["source_ids"]):
            raise ProductError("invalid_source", "章节引用了无权来源。")
        if not set(payload["source_ids"]).issubset(set(blueprint_chapter["source_ids"])):
            raise ProductError("invalid_source_scope", "章节引用超出当前批准蓝图范围。")
        input_revision = _input_revision(task)
        family = body.get("family", "technical-solution")
        if family not in {"technical-solution", "feasibility"}:
            raise ProductError("invalid_family", "报告类型无效。")
        revision = append_revision(
            task, DocumentRevision.Kind.CHAPTER, payload,
            input_hash=input_revision.sha256, blueprint_hash=blueprint_revision.sha256, actor=request.user, family=family,
            reason="manual_chapter_edit",
        )
        task.checkpoint = {**task.checkpoint, "impact": {
            "reason": "chapter_changed", "scope": "all_checks", "edited_chapter": payload["chapter_id"],
            "chapters": [chapter["id"] for chapter in blueprint_revision.payload["chapters"]],
            "tables": ["INPUT_TABLE"], "status": "requires_content_review", "revision_id": str(revision.pk),
        }}
        task.state = DocumentTask.State.DRAFT
        task.stage = DocumentTask.Stage.WRITING
        task.pending_action = ""
        task.error_code = ""
        task.version += 1
        task.save()
    _audit(request, "product_chapter_create", task, changes=["chapter"], target=revision.pk)
    return Response({"chapter": _revision_data(revision), "task": _task_detail(task, request.user)}, status=201)


@api_view(["GET"])
@product_endpoint
def download(request, artifact_id):
    try:
        artifact = DocumentArtifact.objects.select_related("task").get(pk=artifact_id)
    except (DocumentArtifact.DoesNotExist, ValueError, TypeError) as error:
        raise ProductError("not_found", "对象不存在。", 404) from error
    task = task_for(request.user, artifact.task_id)
    if not input_authorized(task, task.revisions.filter(kind="input", sha256=artifact.input_hash).first()):
        raise ProductError("source_permission_changed", "资料授权已变化，成果不可下载。", 404)
    current = output_current(task, artifact)
    history = request.query_params.get("history") == "1"
    if not current and not history:
        raise ProductError("stale_output", "此成果已过期；如需追溯，请显式下载历史版本。", 409)
    approved = effective_artifact_approval(artifact) is not None
    try:
        if artifact.render_evidence.get("kind") == "candidate" and not approved:
            target = evidence_file(artifact.render_evidence.get("draft_fallback"))
        else:
            target = verified_artifact(artifact)
    except StorageError as error:
        raise ProductError(error.code, error.detail, 409) from error
    document_title = artifact.render_evidence.get("document_title", task.title)
    safe_title = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", document_title).strip(" .") or "技术方案"
    prefix = "" if approved else "草稿-"
    if not current:
        prefix = "历史草稿-已过期-"
    filename = f"{prefix}{safe_title}-v{artifact.version}{target.suffix or '.docx'}"
    stream = target.open("rb")
    response = FileResponse(stream, as_attachment=True, filename=filename)
    response["X-Product-Artifact-Current"] = "true" if current else "false"
    response["Cache-Control"] = "no-store"
    _audit(request, "product_artifact_download", task, target=artifact.pk,
           changes=["current" if current else "history", artifact.family, artifact.sha256])
    return response


@api_view(["GET"])
@product_endpoint
def artifact_preview(request, artifact_id):
    try:
        artifact = DocumentArtifact.objects.select_related("task").get(pk=artifact_id)
    except DocumentArtifact.DoesNotExist:
        raise ProductError("not_found", "对象不存在。", 404) from None
    task = task_for(request.user, artifact.task_id, review=True)
    if not candidate_current(artifact):
        raise ProductError("stale_evidence", "候选或渲染证据已变化。", 409)
    page = request.query_params.get("page", "")
    if not page.isdigit() or len(page) > 6 or not 1 <= int(page) <= len(artifact.render_evidence["pages"]):
        raise ProductError("invalid_page", "页面编号无效。")
    target = evidence_file(artifact.render_evidence["pages"][int(page) - 1])
    with transaction.atomic():
        task = task_for(request.user, artifact.task_id, write=True, review=True)
        artifact = DocumentArtifact.objects.select_for_update().get(pk=artifact.pk)
        artifact.task = task
        if not candidate_current(artifact):
            raise ProductError("stale_evidence", "候选或渲染证据已变化。", 409)
        record = artifact.render_evidence["pages"][int(page) - 1]
        views = dict(artifact.render_evidence.get("page_views", {}))
        views[f"{request.user.pk}:{page}"] = {"actor_id": request.user.pk, "artifact_sha256": artifact.sha256,
            "page_sha256": record["sha256"], "authorization": approval_authorization(task, request.user), "served_at": timezone.now().isoformat()}
        artifact.render_evidence = {**artifact.render_evidence, "page_views": views}
        artifact.save(update_fields=["render_evidence"])
    _audit(request, "product_page_preview", task, target=artifact.pk)
    response = FileResponse(target.open("rb"), content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@api_view(["POST"])
@product_endpoint
def artifact_verification(request, artifact_id):
    body = _body(request, {"expected_version", "sha256", "pages", "content_checks", "comment"})
    with transaction.atomic():
        try:
            artifact = DocumentArtifact.objects.select_related("task").get(pk=artifact_id)
        except DocumentArtifact.DoesNotExist:
            raise ProductError("not_found", "对象不存在。", 404) from None
        task = task_for(request.user, artifact.task_id, write=True, review=True)
        artifact.task = task
        require_version(task, _expected(body["expected_version"]))
        if task.state != "WAITING_REVIEW" or task.stage != "FINAL_REVIEW" or task.lease_until is not None:
            raise ProductError("invalid_state", "任务当前不能登记成稿核对。", 409)
        if body["sha256"] != artifact.sha256 or not candidate_current(artifact):
            raise ProductError("stale_evidence", "工件版本或检查证据已变化。", 409)
        checks = body["content_checks"]
        if (not isinstance(checks, dict) or set(checks) != CONTENT_CHECKS or any(type(value) is not bool for value in checks.values())
                or not isinstance(body["comment"], str) or not body["comment"].strip() or len(body["comment"]) > 4000):
            raise ProductError("invalid_request", "请逐项核对内容并填写依据。")
        pages = artifact.render_evidence["pages"]
        for page in pages:
            receipt = artifact.render_evidence.get("page_views", {}).get(f"{request.user.pk}:{page['page']}", {})
            if (receipt.get("artifact_sha256") != artifact.sha256 or receipt.get("page_sha256") != page["sha256"]
                    or receipt.get("authorization") != approval_authorization(task, request.user)):
                raise ProductError("page_preview_required", "请先逐页打开当前版本的鉴权预览。", 409)
        if not isinstance(body["pages"], list) or len(body["pages"]) != len(pages):
            raise ProductError("invalid_pages", "必须逐页核对当前全部页面。")
        passed = all(checks.values())
        for expected, provided in zip(pages, body["pages"]):
            if (not isinstance(provided, dict) or set(provided) != {"page", "sha256", "passed", "comment"}
                    or type(provided["page"]) is not int or provided["page"] != expected["page"]
                    or provided["sha256"] != expected["sha256"] or type(provided["passed"]) is not bool
                    or not isinstance(provided["comment"], str) or len(provided["comment"]) > 2000):
                raise ProductError("invalid_pages", "页面顺序、哈希或核对内容无效。")
            passed = passed and provided["passed"]
        check = {"actor_id": request.user.pk, "sha256": artifact.sha256, "review_hash": artifact.review.sha256,
                 "authorization": approval_authorization(task, request.user),
                 "pages_hash": digest([{key: page[key] for key in ("page", "sha256")} for page in pages]),
                 "pages": body["pages"], "content_checks": checks, "comment": body["comment"], "passed": passed,
                 "recorded_at": timezone.now().isoformat()}
        artifact.render_evidence = {**artifact.render_evidence, "status": "verified" if passed else "rendered", "verified": passed,
            "visual_review": check, "verification_history": [*artifact.render_evidence.get("verification_history", []), check]}
        artifact.save(update_fields=["render_evidence"])
        task.version += 1
        task.save(update_fields=["version", "updated_at"])
    _audit(request, "product_artifact_verify", task, result="success" if passed else "failed", target=artifact.pk)
    return Response(_task_detail(task, request.user))


urlpatterns = [
    path("conversations/", conversations),
    path("tasks/", tasks),
    path("tasks/<uuid:task_id>/conversation/", task_conversation),
    path("tasks/<uuid:task_id>/", task_detail),
    path("tasks/<uuid:task_id>/blueprint/", blueprint),
    path("tasks/<uuid:task_id>/queue/", queue_task),
    path("tasks/<uuid:task_id>/decisions/", decisions),
    path("tasks/<uuid:task_id>/cancel/", cancel),
    path("tasks/<uuid:task_id>/retry/", retry),
    path("tasks/<uuid:task_id>/sources/", sources),
    path("tasks/<uuid:task_id>/chapters/", chapters),
    path("tasks/<uuid:task_id>/input-review/", input_review),
    path("tasks/<uuid:task_id>/statements/", input_statement),
    path("artifacts/<uuid:artifact_id>/download/", download),
    path("artifacts/<uuid:artifact_id>/preview/", artifact_preview),
    path("artifacts/<uuid:artifact_id>/verification/", artifact_verification),
]
