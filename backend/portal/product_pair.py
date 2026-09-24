"""Persisted report content and its exact two-family provenance."""

from .product_models import DocumentApproval, DocumentRevision
from .product_service import (ProductError, append_revision, approval_current,
                              current_revision, digest, reviewer_allowed)

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


def effective_report_approval(report):
    task = report.task
    if not task.reviewer_id or task.owner_id == task.reviewer_id:
        return None
    latest = report.approvals.filter(actor_id=task.reviewer_id).select_related("actor").order_by("-created_at").first()
    if (latest and latest.decision == DocumentApproval.Decision.APPROVE
            and latest.sha256 == report.sha256 and reviewer_allowed(latest.actor)
            and approval_current(task, latest)):
        return latest
    return None


def pair_snapshot(task):
    current_input = current_revision(task, DocumentRevision.Kind.INPUT)
    blueprint = current_revision(task, DocumentRevision.Kind.BLUEPRINT)
    if current_input is None or blueprint is None or current_input.version != task.input_version or blueprint.version != task.blueprint_version:
        raise ProductError("stale_pair", "项目输入或蓝图已变化。", 409)
    reports = [latest_report_content(task, family) for family in FAMILIES]
    if any(report is None or report.input_hash != current_input.sha256 or report.blueprint_hash != blueprint.sha256 for report in reports):
        raise ProductError("stale_pair", "两份报告尚未生成或来源已变化。", 409)
    approvals = [effective_report_approval(report) for report in reports]
    if any(record is None for record in approvals):
        raise ProductError("report_approval_required", "两份结构化报告内容须分别人工批准后才能生成 PPT。", 409)
    blocks = [{"ref": f"{report.family}:{block['id']}", "text": block.get("text", ""),
               "type": block.get("type", "paragraph"), "source_ids": block.get("source_ids", [])}
              for report in reports for block in report.payload["blocks"]]
    sources = [{"family": report.family, "id": str(report.pk), "version": report.version, "sha256": report.sha256,
                "approval_id": str(approval.pk), "approval_sha256": approval.sha256, "approved_by": approval.actor_id}
               for report, approval in zip(reports, approvals)]
    return {"input_hash": current_input.sha256, "blueprint_hash": blueprint.sha256,
            "sources": sources, "blocks": blocks, "pending": _pending_items(reports),
            "approval_inherited": False, "sha256": digest({"sources": sources, "input_hash": current_input.sha256, "blueprint_hash": blueprint.sha256})}