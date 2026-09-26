"""Persisted report content and its exact two-family provenance."""

from .product_models import DocumentRevision
from .product_service import (ProductError, append_revision, approved_blueprint,
                              current_revision, digest, input_authorized)

FAMILIES = ("technical-solution", "feasibility")


def _pending_items(reports):
    items = {}
    for report in reports:
        for item in report.payload["pending"]:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            value = items.setdefault(text, {"text": text, "refs": [], "families": []})
            value["refs"].append(f"{report.family}:{item.get('id', 'PENDING')}")
            if report.family not in value["families"]:
                value["families"].append(report.family)
    return list(items.values())

def save_report_content(task, family, payload, *, input_hash, blueprint_hash, actor):
    if family not in FAMILIES or not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list) or not isinstance(payload.get("pending"), list):
        raise ProductError("invalid_report", "报告内容无效。")
    identifiers = [block.get("id") for block in payload["blocks"] if isinstance(block, dict)]
    if (len(identifiers) != len(payload["blocks"]) or len(set(identifiers)) != len(identifiers)
            or any(not isinstance(value, str) or not value or len(value) > 100 for value in identifiers)):
        raise ProductError("invalid_report", "报告内容块标识无效。")
    return append_revision(task, DocumentRevision.Kind.REPORT, payload, input_hash=input_hash,
                           blueprint_hash=blueprint_hash, actor=actor, family=family, reason="report_generated")


def latest_report_content(task, family):
    if family not in FAMILIES:
        raise ProductError("invalid_report", "报告类型无效。")
    return task.revisions.filter(kind=DocumentRevision.Kind.REPORT, family=family).order_by("-version").first()


def _chapter_hashes(task, family, current_input, blueprint):
    found = {}
    for revision in task.revisions.filter(kind=DocumentRevision.Kind.CHAPTER, family=family,
                                          input_hash=current_input.sha256, blueprint_hash=blueprint.sha256).order_by("version"):
        found[revision.payload.get("chapter_id")] = revision.sha256
    required = [chapter["id"] for chapter in blueprint.payload["chapters"]]
    return {chapter_id: found[chapter_id] for chapter_id in required} if set(found) == set(required) else None


def _report_generation_hash(task, family, current_input, blueprint):
    chapters = _chapter_hashes(task, family, current_input, blueprint)
    if chapters is None:
        return None
    return digest({"family": family, "input": current_input.sha256, "blueprint": blueprint.sha256,
                   "chapters": list(chapters.values()), "title": task.title})


def report_current(task, report):
    current_input = current_revision(task, DocumentRevision.Kind.INPUT)
    blueprint = current_revision(task, DocumentRevision.Kind.BLUEPRINT)
    latest = latest_report_content(task, report.family)
    if (not current_input or not blueprint or not input_authorized(task, current_input) or not latest or latest.pk != report.pk
            or report.input_hash != current_input.sha256 or report.blueprint_hash != blueprint.sha256):
        return False
    artifact = next((item for item in task.artifacts.filter(family=report.family)
                     if item.render_evidence.get("report_id") == str(report.pk)), None)
    if artifact is None:
        return False
    expected = _report_generation_hash(task, report.family, current_input, blueprint)
    return bool(expected and artifact and artifact.generation_hash == expected)


def output_current(task, artifact):
    current_input = current_revision(task, DocumentRevision.Kind.INPUT)
    blueprint = approved_blueprint(task)
    latest = task.artifacts.filter(family=artifact.family).order_by("-version").first()
    if (not current_input or not blueprint or not input_authorized(task, current_input) or not latest or latest.pk != artifact.pk
            or artifact.input_hash != current_input.sha256 or artifact.blueprint_hash != blueprint.sha256):
        return False
    if not artifact.generation_hash:
        return False
    if artifact.family == "presentation":
        try:
            return artifact.render_evidence.get("pair_hash") == pair_snapshot(task)["sha256"]
        except ProductError:
            return False
    report_id = artifact.render_evidence.get("report_id")
    if report_id:
        report = task.revisions.filter(pk=report_id, kind=DocumentRevision.Kind.REPORT).first()
        return bool(report and report_current(task, report))
    chapters = _chapter_hashes(task, artifact.family, current_input, blueprint)
    return bool(chapters and artifact.review and artifact.review.payload.get("chapter_hashes") == chapters
                and artifact.render_evidence.get("document_title", task.title) == task.title)


def pair_snapshot(task):
    current_input = current_revision(task, DocumentRevision.Kind.INPUT)
    blueprint = current_revision(task, DocumentRevision.Kind.BLUEPRINT)
    if current_input is None or blueprint is None or current_input.version != task.input_version or blueprint.version != task.blueprint_version:
        raise ProductError("stale_pair", "项目输入或蓝图已变化。", 409)
    reports = [latest_report_content(task, family) for family in FAMILIES]
    if any(report is None or report.input_hash != current_input.sha256 or report.blueprint_hash != blueprint.sha256
           or not report_current(task, report) for report in reports):
        raise ProductError("stale_pair", "两份报告尚未生成或来源已变化。", 409)
    blocks = [{"ref": f"{report.family}:{block['id']}", "text": block.get("text", ""),
               "type": block.get("type", "paragraph"), "source_ids": block.get("source_ids", [])}
              for report in reports for block in report.payload["blocks"]]
    sources = [{"family": report.family, "id": str(report.pk), "version": report.version,
                "sha256": report.sha256, "created_by": report.created_by_id,
                "created_at": report.created_at.isoformat()} for report in reports]
    return {"input_hash": current_input.sha256, "blueprint_hash": blueprint.sha256,
            "sources": sources, "blocks": blocks, "pending": _pending_items(reports),
            "approval_inherited": False, "sha256": digest({"sources": sources, "input_hash": current_input.sha256, "blueprint_hash": blueprint.sha256})}
