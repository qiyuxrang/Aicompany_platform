"""Task-authorized endpoints for independent review-only outputs."""

import re

from django.db import transaction
from django.http import FileResponse
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .product_api import product_endpoint, _body, _expected, _task_detail
from .product_models import DocumentArtifact, DocumentRevision
from .product_pair import FAMILIES, effective_report_approval, latest_report_content, pair_snapshot
from .product_service import (ProductError, approved_blueprint, current_revision, input_authorized,
                              require_owner, require_version, task_for, source_ids_belong, validate_chapter, append_revision)
from .product_storage import StorageError, verified_artifact
from .security import audit


def _current(task, artifact):
    current_input = current_revision(task, DocumentRevision.Kind.INPUT)
    blueprint = approved_blueprint(task)
    latest = task.artifacts.filter(family=artifact.family).order_by("-version").first()
    if (not current_input or not blueprint or current_input.version != task.input_version or latest is None or latest.pk != artifact.pk
            or artifact.input_hash != current_input.sha256 or artifact.blueprint_hash != blueprint.sha256):
        return False
    if artifact.family == "presentation":
        try:
            return artifact.render_evidence.get("pair_hash") == pair_snapshot(task)["sha256"]
        except ProductError:
            return False
    if artifact.family == "feasibility":
        report = latest_report_content(task, "feasibility")
        return bool(report and artifact.render_evidence.get("report_id") == str(report.pk))
    return True


@api_view(["GET"])
@product_endpoint
def outputs(request, task_id):
    task = task_for(request.user, task_id)
    result = []
    for artifact in task.artifacts.order_by("version"):
        if artifact.family not in (*FAMILIES, "presentation"):
            continue
        revision = task.revisions.filter(kind="input", sha256=artifact.input_hash).first()
        if not input_authorized(task, revision):
            continue
        report = task.revisions.filter(pk=artifact.render_evidence.get("report_id"), kind=DocumentRevision.Kind.REPORT).first()
        result.append({"id": str(artifact.pk), "family": artifact.family, "version": artifact.version,
                       "sha256": artifact.sha256, "current": _current(task, artifact), "draft": True,
                       "engine": artifact.render_evidence.get("engine", "frozen-word"),
                       "content_version": report.version if report else None,
                       "content_sha256": report.sha256 if report else None,
                       "content_approved": effective_report_approval(report) is not None if report else None,
                       "source_versions": artifact.render_evidence.get("source_versions", [])})
    return Response({"outputs": result})


@api_view(["GET"])
@product_endpoint
def draft_download(request, artifact_id):
    try:
        artifact = DocumentArtifact.objects.select_related("task").get(pk=artifact_id)
    except (DocumentArtifact.DoesNotExist, ValueError, TypeError) as error:
        raise ProductError("not_found", "对象不存在。", 404) from error
    task = task_for(request.user, artifact.task_id)
    if artifact.family not in ("feasibility", "presentation"):
        raise ProductError("not_found", "对象不存在。", 404)
    if not input_authorized(task, task.revisions.filter(kind="input", sha256=artifact.input_hash).first()):
        raise ProductError("source_permission_changed", "来源授权已变化。", 404)
    if not _current(task, artifact):
        raise ProductError("stale_output", "此成果已过期，仅保留历史记录。", 409)
    try:
        target = verified_artifact(artifact)
    except StorageError as error:
        raise ProductError(error.code, error.detail, 409) from error
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", task.title).strip(" .") or "报告"
    response = FileResponse(target.open("rb"), as_attachment=True,
                            filename=f"草稿-{title}-{artifact.family}-v{artifact.version}{target.suffix}")
    audit(request.user, "product_artifact_download", f"{artifact.pk}:v{task.version}")
    return response


@api_view(["POST"])
@product_endpoint
def report_chapter(request, task_id):
    body = _body(request, {"expected_version", "family", "chapter_id", "title", "paragraphs", "source_ids"})
    if body["family"] != "feasibility":
        raise ProductError("invalid_family", "请使用独立的可研报告章节。")
    with transaction.atomic():
        task = task_for(request.user, task_id, write=True)
        require_owner(task, request.user)
        require_version(task, _expected(body["expected_version"]))
        if task.state in {"QUEUED", "RUNNING", "CANCELLED", "COMPLETED"}:
            raise ProductError("invalid_state", "任务当前不可修改。", 409)
        blueprint = approved_blueprint(task)
        if blueprint is None:
            raise ProductError("blueprint_approval_required", "蓝图尚未批准。", 409)
        payload = validate_chapter({key: body[key] for key in ("chapter_id", "title", "paragraphs", "source_ids")})
        spec = next((chapter for chapter in blueprint.payload["chapters"] if chapter["id"] == payload["chapter_id"]), None)
        if not spec or spec["title"] != payload["title"] or not source_ids_belong(task, payload["source_ids"]) or not set(payload["source_ids"]) <= set(spec["source_ids"]):
            raise ProductError("invalid_chapter", "章节或来源超出已批准蓝图。", 409)
        current = current_revision(task, "input")
        record = append_revision(task, "chapter", payload, input_hash=current.sha256, blueprint_hash=blueprint.sha256,
                                 actor=request.user, family="feasibility", reason="manual_chapter_edit")
        task.version += 1
        task.save(update_fields=["version", "updated_at"])
    audit(request.user, "product_chapter_create", f"{record.pk}:v{task.version}")
    return Response({"task": _task_detail(task, request.user)}, status=201)


urlpatterns = [path("tasks/<uuid:task_id>/outputs/", outputs),
               path("tasks/<uuid:task_id>/report-chapters/", report_chapter),
               path("outputs/<uuid:artifact_id>/download/", draft_download)]
