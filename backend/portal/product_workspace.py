"""Read models for the product division. Existing task/approval services stay authoritative."""
from math import ceil

from django.conf import settings
from django.db.models import F, Prefetch, Q
from django.http import FileResponse
from django.urls import path
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import User
from .product_api import product_endpoint, _task_summary
from .product_models import DocumentArtifact, DocumentRevision, DocumentSource, DocumentTask
from .product_pair import output_current
from .product_service import (ProductError, input_authorized,
                              product_user_allowed, reviewer_allowed, task_for)
from .product_storage import StorageError, verified_artifact
from .security import audit
from .source_parsers.core import EXTENSIONS


FILTERS = {"all", "active", "review", "generation", "completed", "completed_month", "attention"}
OUTPUT_FAMILIES = {"technical-solution", "feasibility", "presentation"}
GENERATION_STAGES = {"WRITING", "CONTENT_CHECK", "RENDER"}
ENDED = {"COMPLETED", "CANCELLED"}


def _matches(task, category):
    if category == "completed_month":
        now, updated = timezone.localtime(), timezone.localtime(task.updated_at)
        return task.state == "COMPLETED" and (now.year, now.month) == (updated.year, updated.month)
    if category == "active":
        return task.state not in ENDED
    if category == "review":
        return task.state == "WAITING_REVIEW"
    if category == "generation":
        return task.stage in GENERATION_STAGES and task.state not in {*ENDED, "WAITING_REVIEW"}
    if category == "completed":
        return task.state == "COMPLETED"
    if category == "attention":
        return task.state in {"FAILED", "WAITING_INPUT"}
    return True


def _number(request, key, default, maximum):
    raw = request.query_params.get(key, str(default))
    if len(raw) > len(str(maximum)) or not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= maximum:
        raise ProductError("invalid_filter", f"{key} 超出有效范围。")
    return int(raw)


def _project(task):
    return {**_task_summary(task), "created_at": task.created_at.isoformat(),
            "owner_name": task.owner.display_name or task.owner.get_username(),
            "reviewer_name": (task.reviewer.display_name or task.reviewer.get_username()) if task.reviewer else None,
            "error_code": task.error_code, "pending_action": task.pending_action}


def _authorized_tasks(user):
    if not product_user_allowed(user):
        raise ProductError("not_found", "对象不存在。", 404)
    scope = Q(owner=user)
    if reviewer_allowed(user):
        scope |= Q(reviewer=user)
    current_inputs = DocumentRevision.objects.filter(kind="input", version=F("task__input_version"))
    queryset = (DocumentTask.objects.filter(scope).select_related("owner", "reviewer")
                .prefetch_related(Prefetch("revisions", queryset=current_inputs, to_attr="workspace_inputs"))
                .order_by("-updated_at", "id"))
    # Counts must use the same source-authorization scope as detail/downloads.
    return [task for task in queryset if not task.workspace_inputs or input_authorized(task, task.workspace_inputs[0])]


def _recent_outputs(tasks):
    task_map = {task.pk: task for task in tasks}
    result = []
    # Bound expensive lineage checks. This panel is explicitly a recent window,
    # not a total; independent project output/history endpoints are the full record.
    artifacts = DocumentArtifact.objects.filter(task_id__in=task_map).order_by("-created_at", "-version")[:30]
    for artifact in artifacts:
        task = task_map[artifact.task_id]
        revision = task.revisions.filter(kind="input", sha256=artifact.input_hash).first()
        if artifact.family not in OUTPUT_FAMILIES or not input_authorized(task, revision):
            continue
        current = output_current(task, artifact)
        result.append({"id": str(artifact.pk), "task_id": str(task.pk), "title": task.title,
                       "family": artifact.family, "version": artifact.version,
                       "created_at": artifact.created_at.isoformat(), "current": current,
                       "review_status": "pending_review" if current else "stale"})
        if len(result) == 6:
            break
    return result


def _file_metadata(path_value, sha256):
    return (isinstance(path_value, str) and bool(path_value) and isinstance(sha256, str) and len(sha256) == 64
            and all(character in "0123456789abcdef" for character in sha256))


def _output_download_url(artifact, current):
    if not _file_metadata(artifact.path, artifact.sha256):
        return None
    if artifact.family == "technical-solution":
        if artifact.render_evidence.get("kind") == "candidate":
            fallback = artifact.render_evidence.get("draft_fallback")
            if not isinstance(fallback, dict) or not _file_metadata(fallback.get("path"), fallback.get("sha256")):
                return None
        url = f"/api/product/artifacts/{artifact.pk}/download/"
    else:
        url = f"/api/product/outputs/{artifact.pk}/download/"
    return url if current else f"{url}?history=1"


@api_view(["GET"])
@product_endpoint
def outputs(request):
    page = _number(request, "page", 1, 1000000)
    page_size = _number(request, "page_size", 12, 50)
    family = request.query_params.get("family", "all")
    query = request.query_params.get("q", "").strip()
    if family not in {*OUTPUT_FAMILIES, "all"} or len(query) > 200:
        raise ProductError("invalid_filter", "筛选条件无效。")
    tasks = _authorized_tasks(request.user)
    if query:
        tasks = [task for task in tasks if query.casefold() in task.title.casefold()]
    task_map = {task.pk: task for task in tasks}
    input_revisions = {
        (revision.task_id, revision.sha256): revision
        for revision in DocumentRevision.objects.filter(task_id__in=task_map, kind=DocumentRevision.Kind.INPUT)
    }
    artifacts = DocumentArtifact.objects.filter(task_id__in=task_map, family__in=OUTPUT_FAMILIES)
    if family != "all":
        artifacts = artifacts.filter(family=family)
    result = []
    for artifact in artifacts.select_related("review").order_by("-created_at", "-version", "-pk"):
        task = task_map[artifact.task_id]
        revision = input_revisions.get((artifact.task_id, artifact.input_hash))
        artifact.task = task
        try:
            if not input_authorized(task, revision):
                continue
            current = output_current(task, artifact)
            download_url = _output_download_url(artifact, current)
            if download_url is None:
                continue
            task = task_for(request.user, artifact.task_id)
            if not input_authorized(task, revision):
                continue
        except ProductError:
            continue
        result.append({"id": str(artifact.pk), "task_id": str(task.pk), "title": task.title,
                       "family": artifact.family, "version": artifact.version,
                       "created_at": artifact.created_at.isoformat(), "current": current,
                       "review_status": "pending_review" if current else "stale",
                       "download_url": download_url})
    start = (page - 1) * page_size
    response = Response({"outputs": result[start:start + page_size],
                         "pagination": {"page": page, "page_size": page_size, "total": len(result),
                                        "pages": ceil(len(result) / page_size)}})
    response["Cache-Control"] = "private, no-store"
    audit(request.user, "product_workspace_outputs_read", f"workspace_outputs:r{request.product_request_id}")
    return response


@api_view(["GET"])
@product_endpoint
def workspace(request):
    page = _number(request, "page", 1, 1000000)
    page_size = _number(request, "page_size", 12, 50)
    category = request.query_params.get("filter", "all")
    query = request.query_params.get("q", "").strip()
    if category not in FILTERS or len(query) > 200:
        raise ProductError("invalid_filter", "筛选条件无效。")
    tasks = _authorized_tasks(request.user)
    now = timezone.localtime()
    metrics = {"all": len(tasks),
               "active": sum(_matches(task, "active") for task in tasks),
               "review": sum(_matches(task, "review") for task in tasks),
               "generation": sum(_matches(task, "generation") for task in tasks),
               "completed": sum(_matches(task, "completed") for task in tasks),
               "completed_month": sum(task.state == "COMPLETED" and
                    (timezone.localtime(task.updated_at).year, timezone.localtime(task.updated_at).month) == (now.year, now.month)
                    for task in tasks)}
    filtered = [task for task in tasks if _matches(task, category) and
                (not query or query.casefold() in task.title.casefold())]
    start = (page - 1) * page_size
    reviewer_ids = getattr(settings, "PRODUCT_REVIEWER_IDS", ())
    reviewers = [{"id": user.pk, "name": user.display_name or user.get_username()}
                 for user in User.objects.filter(pk__in=reviewer_ids, is_active=True).exclude(pk=request.user.pk).order_by("pk")
                 if product_user_allowed(user)]
    response = Response({
        "as_of": now.isoformat(), "scope": "authorized_projects", "metrics": metrics,
        "projects": [_project(task) for task in filtered[start:start + page_size]],
        "pagination": {"page": page, "page_size": page_size, "total": len(filtered), "pages": ceil(len(filtered) / page_size)},
        "recent_projects": [_project(task) for task in tasks[:5]],
        "todos": [_project(task) for task in tasks if task.state in {"WAITING_REVIEW", "WAITING_INPUT", "FAILED"}][:5],
        "recent_outputs": _recent_outputs(tasks), "reviewers": reviewers,
        "capabilities": {"model_generation": bool(settings.PRODUCT_MODEL_CALLS_ALLOWED),
                         "retrieval": bool(settings.PRODUCT_RETRIEVAL_ENABLED and settings.PRODUCT_RETRIEVAL_AUTHORIZATIONS),
                         "formal_release": bool(settings.PRODUCT_FORMAL_RELEASE_ENABLED),
                         "office_preview": bool(settings.PRODUCT_OFFICE_RENDER_ENABLED),
                         "upload_extensions": list(EXTENSIONS), "upload_max_bytes": settings.PRODUCT_UPLOAD_MAX_BYTES,
                         "local_ocr": settings.PRODUCT_OCR_ENABLED,
                         "parser_ready": settings.PRODUCT_PARSER_PYTHON.is_file()},
        "templates": [{"id": "frozen-original-v1", "name": "产品项目文档母版", "version": "frozen-original-v1",
                       "families": ["technical-solution", "feasibility", "presentation"],
                       "approved": bool(settings.PRODUCT_TEMPLATE_APPROVAL),
                       "description": "确认蓝图后自动生成技术方案、可研报告与汇报 PPT 草稿，保留独立成果版本和来源链。"}],
    })
    response["Cache-Control"] = "private, no-store"
    audit(request.user, "product_workspace_read", f"workspace:r{request.product_request_id}")
    return response


@api_view(["GET"])
@product_endpoint
def source_download(request, source_id):
    try:
        source = DocumentSource.objects.get(pk=source_id)
    except DocumentSource.DoesNotExist:
        raise ProductError("not_found", "对象不存在。", 404) from None
    task = task_for(request.user, source.task_id)
    try:
        target = verified_artifact(source)
    except StorageError as error:
        raise ProductError(error.code, "原始资料缺失或完整性校验失败。", 409) from error
    task = task_for(request.user, source.task_id)
    response = FileResponse(target.open("rb"), as_attachment=True, filename=source.original_name,
                            content_type="application/octet-stream")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    audit(request.user, "product_source_download", f"{source.pk}:v{task.version}:{source.sha256}")
    return response


urlpatterns = [path("workspace/", workspace), path("workspace/outputs/", outputs),
               path("sources/<uuid:source_id>/download/", source_download)]

# Independent RAGFlow knowledge Q&A; does not reuse blueprint retrieval configuration.
from .product_knowledge_api import urlpatterns as knowledge_urlpatterns
urlpatterns += knowledge_urlpatterns
