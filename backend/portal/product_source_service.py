"""Versioned extraction contributions, independent from immutable uploaded bytes."""
import copy

from .product_intake import extraction_hash, source_text
from .product_models import DocumentRevision
from .product_service import ProductError, append_revision, invalidate_generation


def add_contribution(payload, source, parsed, *, replacing=False):
    source_id = str(source.pk)
    if replacing:
        if any(item.get("source_id") == source_id and item.get("source_edited") for item in payload.get("items", [])):
            raise ProductError("source_manually_edited", "此资料的设备行已人工修改，不能用重新解析覆盖；请保留现有版本并另传新资料对照。", 409)
        payload["items"] = [item for item in payload.get("items", []) if item.get("source_id") != source_id]
        payload["source_materials"] = [item for item in payload.get("source_materials", []) if item.get("source_id") != source_id]
        payload["issues"] = [item for item in payload.get("issues", []) if item.get("source_id") != source_id]
    payload.setdefault("items", []).extend({
        **{key: item[key] for key in ("row_id", "name", "quantity", "unit")},
        "source_id": source_id, "source_row": item.get("source_row"),
        "source_item_id": f"{source_id}:{item.get('block_id', index)}", "source_location": item.get("source_location", {"row": item.get("source_row", 0)}),
    } for index, item in enumerate(parsed.get("items", []), 1))
    checksum = extraction_hash(parsed)
    if parsed.get("schema_version"):
        payload.setdefault("source_materials", []).append({"source_id": source_id, "extraction_hash": checksum,
            "parser_version": parsed["parser_version"], "status": parsed["status"], "text": source_text(parsed)})
    elif "background" in parsed:
        # Legacy TXT background behavior is retained for old clients and histories.
        payload["background"] += ("\n\n" if payload["background"] else "") + parsed["background"]
    payload.setdefault("issues", []).extend({**warning, "source_id": source_id} for warning in parsed.get("warnings", source.warnings) if warning.get("severity") != "info")
    for record in payload.get("sources", []):
        if record["id"] == source_id:
            record["extraction_hash"] = checksum
    if len(payload.get('background', '')) > 50000:
        raise ProductError('background_limit', 'TXT 项目背景累计超过 5 万字，请拆分项目或使用有独立解析快照的 PDF/DOCX 资料。')
    if len(payload["items"]) > 5000 or sum(len(item["text"]) for item in payload.get("source_materials", [])) > 1000000:
        raise ProductError("project_intake_limit", "项目清单或资料文字已超过安全上限，请拆分项目。")
    return payload


def save_extraction(task, source, parsed, user, reason, *, initial=False):
    previous = task.revisions.get(kind="input", version=task.input_version)
    payload = copy.deepcopy(previous.payload)
    if initial:
        payload.setdefault("sources", []).append({"id": str(source.pk), "original_name": source.original_name,
            "media_type": source.media_type, "sha256": source.sha256})
    add_contribution(payload, source, parsed, replacing=not initial)
    revision = append_revision(task, "input", payload, actor=user, reason=reason)
    if parsed.get("schema_version"):
        append_revision(task, DocumentRevision.Kind.EXTRACTION, {"source_id": str(source.pk), "source_name": source.original_name,
            "source_sha256": source.sha256, "extraction": parsed}, input_hash=revision.sha256,
            actor=user, family=source.pk.hex, reason=reason)
    source.parsed = parsed
    source.warnings = parsed.get("warnings", source.warnings)
    source.save(update_fields=["parsed", "warnings"])
    invalidate_generation(task)
    task.input_version = revision.version
    task.version += 1
    task.save()
    return task
