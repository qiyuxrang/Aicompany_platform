"""Object-authorized extraction viewing, correction, retry and raster preview."""
import copy
import re
from collections import defaultdict
from pathlib import Path

from django.db import transaction
from django.http import HttpResponse
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .product_api import product_endpoint, _body, _expected, _task_detail
from .product_intake import extraction_hash, run_parser, summary
from .product_models import DocumentSource, DocumentRevision
from .product_service import ProductError, require_editable, require_owner, require_version, task_for, digest, input_authorized
from .product_source_service import save_extraction
from .product_storage import parse_source_content, verified_artifact
from .security import audit
from .source_parsers.core import table_items, Result


def source_for(user, source_id, write=False):
    try: source = DocumentSource.objects.get(pk=source_id)
    except DocumentSource.DoesNotExist: raise ProductError("not_found", "资料不存在或无权访问。", 404) from None
    task = task_for(user, source.task_id, write=write)
    if write:
        require_owner(task, user)
        require_editable(task)
        source = DocumentSource.objects.select_for_update().get(pk=source_id)
    return task, source


def verify_version(task, source, body):
    require_version(task, _expected(body["expected_version"]))
    if body["source_sha256"] != source.sha256 or body["extraction_hash"] != extraction_hash(source.parsed):
        raise ProductError("stale_extraction", "资料或解析版本已变化，请重新载入后核对。", 409)


def blocks_for(source):
    if "blocks" in source.parsed: return source.parsed["blocks"]
    if source.parsed.get("background"):
        return [{"id": "legacy-text", "text": source.parsed["background"], "kind": "paragraph", "location": {}, "location_label": "原始文本"}]
    return [{"id": f"legacy-row-{index}", "kind": "table_row", "text": "\t".join(str(item.get(key, "")) for key in ("row_id", "name", "quantity", "unit")),
             "cells": [str(item.get(key, "")) for key in ("row_id", "name", "quantity", "unit")],
             "location": {"row": item.get("source_row", index)}, "location_label": f"原文件第 {item.get('source_row', index)} 行"}
            for index, item in enumerate(source.parsed.get("items", []), 1)]


@api_view(["GET"])
@product_endpoint
def detail(request, source_id):
    task, source = source_for(request.user, source_id)
    raw = request.query_params.get("page", "1")
    query = request.query_params.get("q", "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,5}", raw) or len(query) > 200:
        raise ProductError("invalid_filter", "资料查询参数无效。")
    version = request.query_params.get("revision", "")
    parsed = source.parsed
    if version:
        if not re.fullmatch(r"[1-9][0-9]{0,5}", version):
            raise ProductError("invalid_filter", "解析版本无效。")
        snapshot = task.revisions.filter(kind="extraction", family=source.pk.hex, version=int(version)).first()
        original_input = task.revisions.filter(kind="input", sha256=snapshot.input_hash).first() if snapshot else None
        if not snapshot or not input_authorized(task, original_input):
            raise ProductError("not_found", "历史解析不存在或来源授权已变化。", 404)
        parsed = snapshot.payload["extraction"]
    view = copy.copy(source)
    view.parsed = parsed
    blocks = blocks_for(view)
    matches = [block for block in blocks if not query or query.casefold() in block["text"].casefold()]
    start = (int(raw) - 1) * 40
    current = task.revisions.get(kind="input", version=task.input_version)
    issues = [{**issue, "issue_hash": digest(issue)} for issue in current.payload.get("issues", []) if issue.get("source_id") == str(source.pk)]
    revisions = [{"version": row.version, "sha256": row.sha256, "created_at": row.created_at.isoformat(), "reason": row.change_reason}
                 for row in task.revisions.filter(kind="extraction", family=source.pk.hex).order_by("-version")[:20]]
    can_edit = not version and task.owner_id == request.user.pk and task.state not in {"QUEUED", "RUNNING", "CANCELLED", "COMPLETED"}
    response = {"id": str(source.pk), "task_id": str(task.pk), "task_version": task.version,
                "name": source.original_name, "sha256": source.sha256, "size": source.size, "media_type": source.media_type,
                "summary": summary(parsed), "blocks": matches[start:start + 40], "warnings": parsed.get("warnings", source.warnings),
                "issues": [] if version else issues, "revisions": revisions, "pagination": {"page": int(raw), "page_size": 40, "total": len(matches)},
                "can_reparse": can_edit and bool(source.parsed.get("schema_version")),
                "can_correct": can_edit and bool(source.parsed.get("schema_version")),
                "can_preview": source.media_type == "application/pdf" or source.media_type.startswith("image/")}
    audit(request.user, "product_source_read", f"{source.pk}:v{task.version}")
    return Response(response)


@api_view(["GET"])
@product_endpoint
def preview(request, source_id):
    task, source = source_for(request.user, source_id)
    page = request.query_params.get("page", "1")
    if not re.fullmatch(r"[1-9][0-9]{0,2}", page): raise ProductError("invalid_page", "预览页码无效。")
    if not (source.media_type == "application/pdf" or source.media_type.startswith("image/")):
        raise ProductError("preview_unavailable", "此类资料请查看文字与表格位置，或下载原文档。", 400)
    if source.media_type.startswith("image/") and page != "1":
        raise ProductError("invalid_page", "图片仅提供第一帧预览。")
    content = verified_artifact(source).read_bytes()
    png = run_parser(source.original_name, content, int(page))
    # Recheck access after expensive rendering, before serving the derived image.
    task_for(request.user, task.pk)
    audit(request.user, "product_source_preview", f"{source.pk}:page{page}:{source.sha256}")
    response = HttpResponse(png, content_type="image/png")
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@api_view(["POST"])
@product_endpoint
def reparse(request, source_id):
    body = _body(request, {"expected_version", "source_sha256", "extraction_hash"})
    task, source = source_for(request.user, source_id)
    require_owner(task, request.user)
    require_editable(task)
    verify_version(task, source, body)
    if not source.parsed.get("schema_version"): raise ProductError("legacy_source", "旧文本资料请在项目底稿中修订。")
    parsed = parse_source_content(source.original_name, verified_artifact(source).read_bytes())[3]
    with transaction.atomic():
        task, source = source_for(request.user, source_id, write=True)
        verify_version(task, source, body)
        save_extraction(task, source, parsed, request.user, "source_reparsed")
    audit(request.user, "product_source_reparse", f"{source.pk}:v{task.version}")
    return Response({"task": _task_detail(task, request.user)})


@api_view(["PATCH"])
@product_endpoint
def correct(request, source_id):
    body = _body(request, {"expected_version", "source_sha256", "extraction_hash", "block_id", "text", "reason"}, {"cells"})
    if not isinstance(body["text"], str) or len(body["text"]) > 16000 or "\x00" in body["text"] or not body["text"].strip() or not isinstance(body["reason"], str) or not body["reason"].strip() or len(body["reason"]) > 2000:
        raise ProductError("invalid_correction", "请填写校正文字与依据，并控制在文本长度上限内。")
    with transaction.atomic():
        task, source = source_for(request.user, source_id, write=True)
        verify_version(task, source, body)
        parsed = copy.deepcopy(source.parsed)
        if not parsed.get("schema_version"): raise ProductError("legacy_source", "旧文本资料请在项目底稿中修订。")
        block = next((item for item in parsed["blocks"] if item["id"] == body["block_id"]), None)
        if not block and not parsed["blocks"] and body["block_id"] == "manual1":
            block = {"id": "manual1", "text": "", "kind": "paragraph", "location": {}, "location_label": "人工补录（原文件）"}
            parsed["blocks"].append(block)
        if not block: raise ProductError("not_found", "解析内容块不存在。", 404)
        block.setdefault("original_text", block["text"])
        if block["kind"] == "table_row":
            cells = body.get("cells")
            if not isinstance(cells, list) or len(cells) != len(block["cells"]) or any(not isinstance(value, str) or len(value) > 2000 or "\x00" in value for value in cells):
                raise ProductError("invalid_correction", "请逐格校正表格，不得改变列数。")
            block.setdefault("original_cells", block["cells"])
            block["cells"] = cells
            block["text"] = "\t".join(cells)
            if len(block['text']) > 16000:
                raise ProductError('invalid_correction', '单行校正文本超过 1.6 万字，请拆分原始资料。')
        else: block["text"] = body["text"].strip()
        block["correction"] = {"actor_id": request.user.pk, "reason": body["reason"].strip(), "previous_extraction_hash": body["extraction_hash"]}
        parsed["character_count"] = sum(len(item["text"]) for item in parsed["blocks"])
        if parsed["character_count"] > 200000: raise ProductError("text_limit", "校正后内容超过解析上限。")
        parsed["block_count"] = len(parsed["blocks"])
        parsed["status"] = "partial" if parsed.get("truncated") or any(w.get("severity") == "partial" for w in parsed["warnings"]) else "needs_review"
        # Rebuild only native table facts. OCR text remains evidence, never guessed quantities.
        result = Result()
        groups = defaultdict(list)
        for item in parsed["blocks"]:
            if item["kind"] == "table_row":
                loc = item["location"]
                groups[(loc.get("sheet"), loc.get("part"), loc.get("table"))].append(item)
        for rows in groups.values(): table_items(result, rows)
        for warning in result.warnings:
            if warning not in parsed["warnings"] and len(parsed["warnings"]) < 100:
                parsed["warnings"].append(warning)
        # XLS cached values must remain unpromoted even after a text-only correction.
        parsed["items"] = result.items if not source.original_name.lower().endswith(".xls") else []
        if not any(item.get("code") == "manual_extraction_correction" for item in parsed["warnings"]):
            parsed["warnings"].append({"code": "manual_extraction_correction", "detail": "解析文字已人工校正，请审核人结合原文件核对。", "severity": "review", "location": block["location"]})
        save_extraction(task, source, parsed, request.user, "source_text_corrected")
    audit(request.user, "product_source_correct", f"{source.pk}:v{task.version}")
    return Response({"task": _task_detail(task, request.user)})


urlpatterns = [path("sources/<uuid:source_id>/", detail), path("sources/<uuid:source_id>/preview/", preview),
               path("sources/<uuid:source_id>/reparse/", reparse), path("sources/<uuid:source_id>/correction/", correct)]
