"""Persist independent report drafts and derive an editable presentation."""

from django.db import transaction
from django.db.models import Max

from .product_documents import content_document, render_report_draft
from .product_models import DocumentArtifact
from .product_pair import pair_snapshot, save_report_content
from .product_service import ProductError, digest
from .product_storage import verified_artifact


def generate_report_drafts(task, fence, attempt_id, input_revision, blueprint):
    from .product_worker import _guard, _renew, current_chapters

    artifacts = []
    for family in ("technical-solution", "feasibility"):
        chapters = current_chapters(task, input_revision.sha256, blueprint.sha256, family)
        ordered = [chapters.get(chapter["id"]) for chapter in blueprint.payload["chapters"]]
        if any(chapter is None for chapter in ordered):
            raise ProductError("chapters_incomplete", f"{family} 报告内容不完整。", 409)
        _renew(task.pk, fence)
        content = content_document(task, input_revision, blueprint, ordered, family)
        report_hash = digest({"family": family, "input": input_revision.sha256, "blueprint": blueprint.sha256,
                              "chapters": [chapter.sha256 for chapter in ordered], "title": task.title})
        existing = DocumentArtifact.objects.filter(task=task, generation_hash=report_hash).first()
        if existing:
            verified_artifact(existing)
            artifacts.append(existing)
            continue
        generated = render_report_draft(task, input_revision, blueprint, ordered, family)
        with transaction.atomic():
            fresh = _guard(task.pk, fence)
            report = save_report_content(fresh, family, content, input_hash=input_revision.sha256,
                                         blueprint_hash=blueprint.sha256, actor=fresh.owner)
            version = (fresh.artifacts.aggregate(value=Max("version"))["value"] or 0) + 1
            artifact = DocumentArtifact.objects.create(
                task=fresh, family=family, version=version, path=generated["path"], sha256=generated["sha256"],
                input_hash=input_revision.sha256, blueprint_hash=blueprint.sha256,
                template_hash=generated["template_hash"], generation_hash=report_hash,
                render_evidence={**generated["render_evidence"], "report_id": str(report.pk), "kind": "draft",
                                 "attempt_id": str(attempt_id)},
            )
            artifacts.append(artifact)
    return artifacts


def generate_presentation_artifact(task, fence, attempt_id, input_revision, blueprint):
    from .product_presentation import render_presentation_draft
    from .product_worker import _guard, _renew

    _renew(task.pk, fence)
    pair = pair_snapshot(task)
    generation_hash = digest({"kind": "presentation", "pair": pair["sha256"], "title": task.title})
    existing = DocumentArtifact.objects.filter(task=task, generation_hash=generation_hash).first()
    if existing:
        verified_artifact(existing)
        return existing
    generated = render_presentation_draft(task, pair)
    with transaction.atomic():
        fresh = _guard(task.pk, fence)
        if pair_snapshot(fresh)["sha256"] != pair["sha256"]:
            raise ProductError("stale_pair", "报告来源已变化。", 409)
        version = (fresh.artifacts.aggregate(value=Max("version"))["value"] or 0) + 1
        return DocumentArtifact.objects.create(
            task=fresh, family="presentation", version=version, path=generated["path"],
            sha256=generated["sha256"], input_hash=input_revision.sha256,
            blueprint_hash=blueprint.sha256, template_hash=generated["template_hash"],
            generation_hash=generation_hash,
            render_evidence={**generated["render_evidence"], "attempt_id": str(attempt_id)},
        )


def generate_three_drafts(task, fence, attempt_id, input_revision, blueprint):
    from .product_worker import _finish

    generate_report_drafts(task, fence, attempt_id, input_revision, blueprint)
    return _finish(task.pk, fence, attempt_id, "WAITING_REVIEW", "FINAL_REVIEW")


def generate_presentation(task, fence, attempt_id, input_revision, blueprint):
    from .product_worker import _finish

    generate_presentation_artifact(task, fence, attempt_id, input_revision, blueprint)
    return _finish(task.pk, fence, attempt_id, "WAITING_REVIEW", "FINAL_REVIEW")
