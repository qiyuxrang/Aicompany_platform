import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q

from .models import Module, User
from .product_models import (DocumentApproval, DocumentArtifact, DocumentRevision,
                             DocumentSource, DocumentTask, DocumentReviewPolicy)
from .product_storage import parse_upload, read_import_path, remove_relative, write_source


class ProductError(Exception):
    def __init__(self, code, detail, status=400):
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


def digest(payload):
    try:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ProductError("invalid_request", "请求包含不可序列化的值。") from error
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def current_revision(task, kind):
    return task.revisions.filter(kind=kind).order_by("-version").first()


@transaction.atomic
def append_revision(task, kind, payload, input_hash="", blueprint_hash="", actor=None,
                    family="technical-solution", reason="unspecified"):
    locked = DocumentTask.objects.select_for_update().get(pk=task.pk)
    previous = locked.revisions.filter(kind=kind, family=family).order_by("-version").first()
    version = previous.version if previous else 0
    return DocumentRevision.objects.create(
        task=locked,
        kind=kind,
        family=family,
        version=version + 1,
        payload=payload,
        sha256=digest(payload),
        parent_sha256=previous.sha256 if previous else "",
        change_reason=reason,
        input_hash=input_hash,
        blueprint_hash=blueprint_hash,
        created_by=actor,
    )


def validate_input(payload):
    _object(payload, {"project", "requirements", "items", "background", "conditions"})
    result = {
        "project": _text(payload["project"], "project", 300),
        "requirements": _text(payload["requirements"], "requirements", 10000),
        "background": _text(payload["background"], "background", 50000),
        "conditions": _text_list(payload["conditions"], "conditions", 200, 1000),
        "items": [],
        "sources": [],
        "issues": [],
    }
    if not isinstance(payload["items"], list) or len(payload["items"]) > 5000:
        raise ProductError("invalid_input", "设备清单格式无效。")
    seen_row_ids = set()
    for item_index, item in enumerate(payload["items"], start=1):
        _object(item, {"row_id", "name", "quantity", "unit"})
        row_id = item["row_id"]
        quantity = item["quantity"]
        if isinstance(row_id, bool) or not isinstance(row_id, (str, int)):
            raise ProductError("invalid_input", "设备行号格式无效。")
        if (isinstance(quantity, bool) or not isinstance(quantity, (str, int, float))
                or isinstance(quantity, float) and not math.isfinite(quantity)):
            raise ProductError("invalid_input", "设备数量格式无效。")
        normalized_row_id = str(row_id)
        result["items"].append({
            "row_id": normalized_row_id,
            "name": _text(item["name"], "name", 500),
            "quantity": quantity,
            "unit": _text(item["unit"], "unit", 100),
        })
        if normalized_row_id and normalized_row_id in seen_row_ids:
            result["issues"].append({"code": "duplicate_row_id", "item_index": item_index})
        if normalized_row_id:
            seen_row_ids.add(normalized_row_id)
        for key in ("name", "unit"):
            if not item[key].strip():
                result["issues"].append({"code": "missing_" + key, "item_index": item_index})
        try:
            amount = Decimal(str(quantity))
            valid_quantity = amount.is_finite() and amount >= 0
        except InvalidOperation:
            valid_quantity = False
        if not valid_quantity:
            result["issues"].append({"code": "invalid_quantity", "item_index": item_index})
    return result


def preserve_input_provenance(previous, updated):
    updated["sources"] = json.loads(json.dumps(previous.get("sources", []), ensure_ascii=False))
    issues = json.loads(json.dumps(previous.get("issues", []), ensure_ascii=False)) + updated.get("issues", [])
    fields = ("row_id", "name", "quantity", "unit")
    def facts(payload):
        return {**{key: payload.get(key) for key in ("project", "requirements", "background", "conditions")},
                "items": [{key: item.get(key) for key in fields} for item in payload.get("items", [])]}
    changed = digest(facts(previous)) != digest(facts(updated))
    updated["issue_history"] = json.loads(json.dumps(previous.get("issue_history", []), ensure_ascii=False))
    updated["statements"] = json.loads(json.dumps(previous.get("statements", []), ensure_ascii=False))
    for key in ("conversation_request", "conversation_history"):
        if key in previous:
            updated[key] = json.loads(json.dumps(previous[key], ensure_ascii=False))
    dependencies = list(previous.get("authorization_dependencies", []))
    if previous.get("retrieval"):
        dependencies.append(previous["retrieval"])
    updated["authorization_dependencies"] = list({snapshot["scope_hash"]: snapshot for snapshot in dependencies}.values())
    if changed:
        issues.extend(record["issue"] for record in previous.get("issue_resolutions", []))
        updated["issue_resolutions"] = []
    else:
        updated["issue_resolutions"] = json.loads(json.dumps(previous.get("issue_resolutions", []), ensure_ascii=False))
        resolved = {digest(record["issue"]) for record in updated["issue_resolutions"]}
        issues = [issue for issue in issues if digest(issue) not in resolved]
        for key in ("knowledge_sources", "retrieval"):
            if key in previous:
                updated[key] = previous[key]
    old_items = previous.get("items", [])
    for index, item in enumerate(updated["items"]):
        if index >= len(old_items):
            continue
        old = old_items[index]
        if "source_id" not in old:
            continue
        item["source_id"] = old["source_id"]
        item["source_row"] = old.get("source_row")
        if any(item[field] != old.get(field) for field in fields):
            issues.append({
                "code": "manual_source_row_revision",
                "item_index": index + 1,
                "source_id": old["source_id"],
                "source_row": old.get("source_row"),
            })
    if updated["sources"] and len(updated["items"]) != len(old_items):
        issues.append({"code": "source_recheck_required"})
    unique = {}
    for issue in issues:
        unique[digest(issue)] = issue
    updated["issues"] = list(unique.values())
    return updated


def resolve_input_issues(task, user, resolutions):
    previous = current_revision(task, "input")
    if not previous or not isinstance(resolutions, list) or not resolutions or len(resolutions) > 100:
        raise ProductError("invalid_request", "请逐项提供问题及核对依据。")
    payload = json.loads(json.dumps(previous.payload, ensure_ascii=False))
    issues = {digest(issue): issue for issue in payload.get("issues", [])}
    seen = set()
    for resolution in resolutions:
        _object(resolution, {"issue_hash", "category", "reason", "source_ids"})
        issue_hash = resolution["issue_hash"]
        if not isinstance(issue_hash, str) or issue_hash not in issues or issue_hash in seen:
            raise ProductError("stale_issue", "问题已变化，请刷新后核对。", 409)
        if not isinstance(resolution["category"], str) or resolution["category"] not in {"fact", "inference", "conflict", "missing"}:
            raise ProductError("invalid_request", "问题分类无效。")
        reason = _text(resolution["reason"], "核对依据", 4000, required=True)
        sources = _text_list(resolution["source_ids"], "来源", 100, 64)
        if not sources or not source_ids_belong(task, sources):
            raise ProductError("invalid_source", "必须引用当前任务的有效来源。")
        record = {**resolution, "reason": reason, "issue": issues[issue_hash], "actor_id": user.pk,
                  "input_hash": previous.sha256, "input_version": previous.version}
        payload.setdefault("issue_resolutions", []).append(record)
        payload.setdefault("issue_history", []).append(record)
        seen.add(issue_hash)
    payload["issues"] = [issue for issue_hash, issue in issues.items() if issue_hash not in seen]
    revision = append_revision(task, "input", payload, actor=user, reason="input_issue_review")
    invalidate_generation(task)
    task.input_version = revision.version
    task.version += 1
    task.save()
    return revision


def validate_blueprint(payload, input_payload):
    _object(payload, {"purpose", "audience", "chapters", "conditions", "missing", "conflicts", "template_version"})
    conditions = payload["conditions"]
    chapters = payload["chapters"]
    if (not isinstance(conditions, list) or len(conditions) > 500
            or not isinstance(chapters, list) or not chapters or len(chapters) > 200):
        raise ProductError("invalid_blueprint", "蓝图格式无效。")
    normalized_conditions = []
    for condition in conditions:
        _object(condition, {"text", "type"})
        condition_type = condition["type"]
        if condition_type not in {"program", "model", "human"}:
            raise ProductError("invalid_blueprint", "蓝图条件类型无效。")
        normalized_conditions.append({"text": _text(condition["text"], "text", 1000, required=True), "type": condition_type})
    texts = {condition["text"] for condition in normalized_conditions}
    if any(condition not in texts for condition in input_payload.get("conditions", [])):
        raise ProductError("condition_missing", "蓝图不得移除用户输入条件。", 409)
    normalized_chapters = []
    chapter_ids = set()
    for chapter in chapters:
        _object(chapter, {"id", "title", "scope", "source_ids"})
        chapter_id = _text(chapter["id"], "id", 100, required=True)
        if chapter_id in chapter_ids:
            raise ProductError("invalid_blueprint", "章节标识不得重复。")
        chapter_ids.add(chapter_id)
        normalized_chapters.append({
            "id": chapter_id,
            "title": _text(chapter["title"], "title", 160, required=True),
            "scope": _text(chapter["scope"], "scope", 5000),
            "source_ids": _text_list(chapter["source_ids"], "source_ids", 500, 64),
        })
    return {
        "purpose": _text(payload["purpose"], "purpose", 5000, required=True),
        "audience": _text(payload["audience"], "audience", 1000, required=True),
        "chapters": normalized_chapters,
        "conditions": normalized_conditions,
        "missing": _text_list(payload["missing"], "missing", 500, 2000),
        "conflicts": _text_list(payload["conflicts"], "conflicts", 500, 2000),
        "template_version": _template_version(payload["template_version"]),
    }


def validate_chapter(payload):
    _object(payload, {"chapter_id", "title", "paragraphs", "source_ids"})
    paragraphs = _text_list(payload["paragraphs"], "paragraphs", 2000, 20000)
    if not paragraphs or not any(paragraph.strip() for paragraph in paragraphs):
        raise ProductError("invalid_chapter", "章节正文不能为空。")
    return {
        "chapter_id": _text(payload["chapter_id"], "chapter_id", 100, required=True),
        "title": _text(payload["title"], "title", 300, required=True),
        "paragraphs": paragraphs,
        "source_ids": _text_list(payload["source_ids"], "source_ids", 500, 64),
    }


def product_user_allowed(user):
    return bool(user and user.is_authenticated and user.is_active and not user.must_change_password
                and Module.objects.filter(code="product", enabled=True, role__user=user).exists())


def reviewer_allowed(user):
    review_policy_version()
    reviewer_ids = {int(value) for value in getattr(settings, "PRODUCT_REVIEWER_IDS", [])}
    return product_user_allowed(user) and user.pk in reviewer_ids


@transaction.atomic
def review_policy_version():
    fingerprint = digest({"reviewer_ids": sorted(getattr(settings, "PRODUCT_REVIEWER_IDS", [])),
                          "revision": getattr(settings, "PRODUCT_REVIEW_POLICY_REVISION", "")})
    policy, created = DocumentReviewPolicy.objects.select_for_update().get_or_create(pk=1, defaults={"fingerprint": fingerprint})
    if not created and policy.fingerprint != fingerprint:
        policy.fingerprint = fingerprint
        policy.version += 1
        policy.save(update_fields=["fingerprint", "version"])
    return policy.version


def task_for(user, task_id, write=False, review=False):
    if not product_user_allowed(user):
        raise ProductError("not_found", "对象不存在。", 404)
    queryset = DocumentTask.objects.select_related("owner", "reviewer")
    if write:
        queryset = queryset.select_for_update(of=("self",))
    try:
        task = queryset.get(pk=task_id)
    except (DocumentTask.DoesNotExist, ValueError, TypeError) as error:
        raise ProductError("not_found", "对象不存在。", 404) from error
    if user.pk != task.owner_id and (task.reviewer_id != user.pk or not reviewer_allowed(user)):
        raise ProductError("not_found", "对象不存在。", 404)
    if review and (task.reviewer_id != user.pk or task.owner_id == user.pk or not reviewer_allowed(user)):
        raise ProductError("not_found", "对象不存在。", 404)
    current_input = task.revisions.filter(kind="input", version=task.input_version).first()
    if current_input and not input_authorized(task, current_input):
        raise ProductError("source_permission_changed", "资料授权已变化，任务不可访问。", 404)
    return task


def eligible_reviewer(reviewer_id, owner):
    if reviewer_id in (None, ""):
        return None
    if isinstance(reviewer_id, bool) or not isinstance(reviewer_id, int) or reviewer_id == owner.pk:
        raise ProductError("invalid_reviewer", "审核人无效。")
    try:
        reviewer = User.objects.get(pk=reviewer_id)
    except User.DoesNotExist as error:
        raise ProductError("invalid_reviewer", "审核人无效。") from error
    if not reviewer_allowed(reviewer):
        raise ProductError("invalid_reviewer", "审核人无效。")
    return reviewer


def approved_blueprint(task):
    if not task.blueprint_version:
        return None
    revision = task.revisions.filter(kind=DocumentRevision.Kind.BLUEPRINT, version=task.blueprint_version).first()
    if not revision:
        return None
    current_input = task.revisions.filter(kind=DocumentRevision.Kind.INPUT, version=task.input_version).first()
    if not current_input or revision.input_hash != current_input.sha256:
        return None
    latest = DocumentApproval.objects.filter(
        task=task,
        revision=revision,
        actor_id=task.reviewer_id,
    ).select_related("actor").order_by("-created_at").first()
    return revision if (latest and latest.decision == DocumentApproval.Decision.APPROVE
                        and task.owner_id != latest.actor_id and reviewer_allowed(latest.actor)
                        and approval_current(task, latest)) else None


def approval_authorization(task, actor):
    from .product_rules import ProductRulesError, rules_hash

    try:
        current_rules = rules_hash()
    except ProductRulesError:
        raise ProductError("product_rules_unavailable", "文档规则包不可用，暂停批准。", 409) from None
    versions = dict(User.objects.filter(pk__in=[task.owner_id, actor.pk]).values_list("pk", "grant_version"))
    return {"owner_grant_version": versions.get(task.owner_id), "actor_grant_version": versions.get(actor.pk), "policy_version": review_policy_version(), "rules_hash": current_rules}


def approval_current(task, approval):
    return bool(approval.authorization and approval.authorization == approval_authorization(task, approval.actor))


def artifact_releasable(artifact):
    from .product_release import candidate_current

    if not candidate_current(artifact, visual=True):
        return False
    task = artifact.task
    blueprint = approved_blueprint(task)
    input_revision = task.revisions.filter(kind=DocumentRevision.Kind.INPUT, version=task.input_version).first()
    review = artifact.review
    if not (getattr(settings, "PRODUCT_FORMAL_RELEASE_ENABLED", False)
            and artifact.render_evidence.get("status") == "verified"
            and blueprint and input_revision
            and review is not None and review.kind == DocumentRevision.Kind.REVIEW
            and review.payload.get("passed") is True
            and review.input_hash == artifact.input_hash == input_revision.sha256
            and review.blueprint_hash == artifact.blueprint_hash == blueprint.sha256
            and task.artifacts.filter(family=artifact.family).order_by("-version").values_list("pk", flat=True).first() == artifact.pk):
        return False
    current_hashes = {}
    for chapter in task.revisions.filter(
        kind=DocumentRevision.Kind.CHAPTER,
        input_hash=input_revision.sha256,
        blueprint_hash=blueprint.sha256,
    ).order_by("version"):
        current_hashes[chapter.payload.get("chapter_id")] = chapter.sha256
    required = {chapter["id"] for chapter in blueprint.payload.get("chapters", [])}
    return bool(required and set(current_hashes) == required and review.payload.get("chapter_hashes") == current_hashes)


def effective_artifact_approval(artifact):
    task = artifact.task
    if not artifact_releasable(artifact) or not task.reviewer_id or task.owner_id == task.reviewer_id:
        return None
    latest = artifact.approvals.filter(actor_id=task.reviewer_id).select_related("actor").order_by("-created_at").first()
    if (latest and latest.decision == DocumentApproval.Decision.APPROVE
            and latest.sha256 == artifact.sha256 and reviewer_allowed(latest.actor) and approval_current(task, latest)):
        return latest
    return None


def source_ids_belong(task, source_ids):
    input_revision = task.revisions.filter(kind=DocumentRevision.Kind.INPUT, version=task.input_version).first()
    allowed = {str(item.get("row_id")) for item in (input_revision.payload.get("items", []) if input_revision else []) if item.get("row_id") not in (None, "")}
    allowed.update(str(value) for value in task.sources.values_list("pk", flat=True))
    allowed.update(source["id"] for source in (input_revision.payload.get("knowledge_sources", []) if input_revision else []))
    return all(isinstance(value, str) and value in allowed for value in source_ids)


def input_authorized(task, revision):
    if revision is None:
        return False
    snapshots = list(revision.payload.get("authorization_dependencies", []))
    if revision.payload.get("retrieval"):
        snapshots.append(revision.payload["retrieval"])
    from .product_retrieval import authorization_current
    return all(authorization_current(task, snapshot)["current"] for snapshot in snapshots)


@transaction.atomic
def attach_source(user, task_id, expected_version, upload):
    task = task_for(user, task_id, write=True)
    require_owner(task, user)
    require_version(task, expected_version)
    require_editable(task)
    original_name, media_type, content, parsed, warnings = parse_upload(upload)
    return _attach_parsed_source(task, user, original_name, media_type, content, parsed, warnings)


@transaction.atomic
def attach_import_path(user, task_id, expected_version, path):
    task = task_for(user, task_id, write=True)
    require_owner(task, user)
    require_version(task, expected_version)
    require_editable(task)
    parsed_source = read_import_path(path)
    return _attach_parsed_source(task, user, *parsed_source)


def _attach_parsed_source(task, user, original_name, media_type, content, parsed, warnings):
    source_id = relative = None
    try:
        source_id, relative, checksum = write_source(task.id, original_name, content)
        source = DocumentSource.objects.create(
            id=source_id, task=task, original_name=original_name, media_type=media_type,
            path=relative, sha256=checksum, size=len(content), parsed=parsed, warnings=warnings,
            uploaded_by=user, author_verification="unverified",
        )
        previous = task.revisions.get(kind=DocumentRevision.Kind.INPUT, version=task.input_version)
        input_payload = json.loads(json.dumps(previous.payload, ensure_ascii=False))
        input_payload.setdefault("sources", []).append({
            "id": str(source.pk),
            "original_name": source.original_name,
            "media_type": source.media_type,
            "sha256": source.sha256,
        })
        input_payload.setdefault("issues", []).extend({**warning, "source_id": str(source.pk)} for warning in warnings)
        if "items" in parsed:
            input_payload["items"].extend({
                **{key: item[key] for key in ("row_id", "name", "quantity", "unit")},
                "source_id": str(source.pk),
                "source_row": item["source_row"],
            } for item in parsed["items"])
        else:
            separator = "\n\n" if input_payload["background"] else ""
            input_payload["background"] += separator + parsed["background"]
        revision = append_revision(task, DocumentRevision.Kind.INPUT, input_payload, actor=user,
                                   reason="external_source_upload")
        invalidate_generation(task)
        task.input_version = revision.version
        task.version += 1
        task.save()
        return task, source
    except Exception:
        if relative:
            remove_relative(relative)
        raise


def invalidate_generation(task):
    task.state = DocumentTask.State.DRAFT
    task.stage = DocumentTask.Stage.INTAKE
    task.blueprint_version = 0
    task.pending_action = ""
    task.error_code = ""
    task.lease_until = None
    task.fence += 1


def require_owner(task, user):
    if task.owner_id != user.pk:
        raise ProductError("not_found", "对象不存在。", 404)


def require_version(task, expected_version):
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise ProductError("invalid_request", "expected_version 必须是整数。")
    if task.version != expected_version:
        raise ProductError("stale_version", "任务版本已更新，请刷新后重试。", 409)


def require_editable(task):
    if task.state in {DocumentTask.State.QUEUED, DocumentTask.State.RUNNING}:
        raise ProductError("invalid_state", "任务正在排队或执行，请等待完成或先取消任务。", 409)
    if task.state in {DocumentTask.State.CANCELLED, DocumentTask.State.COMPLETED}:
        raise ProductError("invalid_state", "已结束任务不能修改。", 409)


def _object(value, exact_keys):
    if not isinstance(value, Mapping) or set(value) != exact_keys:
        raise ProductError("invalid_request", "请求字段无效。")


def _text(value, field, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()) or "\x00" in value:
        raise ProductError("invalid_request", f"{field} 格式无效。")
    return value


def _text_list(value, field, count_limit, text_limit):
    if not isinstance(value, list) or len(value) > count_limit:
        raise ProductError("invalid_request", f"{field} 格式无效。")
    return [_text(item, field, text_limit) for item in value]


def _template_version(value):
    value = _text(value, "template_version", 100, required=True)
    if value != "frozen-original-v1":
        raise ProductError("invalid_template_version", "模板版本未获批准。")
    return value
