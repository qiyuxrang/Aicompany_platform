"""Authorized, read-only document lineage for PRD-011."""

from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .product_api import product_endpoint
from .product_models import DocumentRevision
from .product_service import approval_current, input_authorized, task_for
from .security import audit


def _actor(user, task, *, initial=False):
    if user is None:
        return {"type": "ai_task", "id": None, "name": "system-worker"}
    role = "initiator" if initial and user.pk == task.owner_id else "editor"
    return {"type": role, "id": user.pk, "name": user.get_username()}


def _source_refs(revision):
    payload = revision.payload
    refs = set()
    for source in payload.get("sources", []) if isinstance(payload, dict) else []:
        if isinstance(source, dict) and source.get("id"):
            refs.add(str(source["id"]))
    records = []
    if revision.kind == DocumentRevision.Kind.BLUEPRINT:
        records = payload.get("chapters", [])
    elif revision.kind in {DocumentRevision.Kind.CHAPTER, DocumentRevision.Kind.REPORT}:
        records = [payload] if revision.kind == DocumentRevision.Kind.CHAPTER else payload.get("blocks", [])
    for record in records:
        if isinstance(record, dict):
            refs.update(str(value) for value in record.get("source_ids", []) if value not in (None, ""))
    return sorted(refs)


def _payload_diff(before, after, path="$", result=None):
    result = [] if result is None else result
    if before == after or len(result) >= 1000:
        return result
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}"
            if key not in before:
                result.append({"path": child, "change": "added", "before": None, "after": after[key]})
            elif key not in after:
                result.append({"path": child, "change": "removed", "before": before[key], "after": None})
            else:
                _payload_diff(before[key], after[key], child, result)
            if len(result) >= 1000:
                break
        return result
    if isinstance(before, list) and isinstance(after, list):
        maximum = max(len(before), len(after))
        for index in range(maximum):
            child = f"{path}[{index}]"
            if index >= len(before):
                result.append({"path": child, "change": "added", "before": None, "after": after[index]})
            elif index >= len(after):
                result.append({"path": child, "change": "removed", "before": before[index], "after": None})
            else:
                _payload_diff(before[index], after[index], child, result)
            if len(result) >= 1000:
                break
        return result
    result.append({"path": path, "change": "changed", "before": before, "after": after})
    return result


def _revision_record(task, revision, previous, access):
    initial = revision.kind == DocumentRevision.Kind.INPUT and revision.version == 1
    return {
        "event": "revision",
        "id": str(revision.pk),
        "kind": revision.kind,
        "family": revision.family,
        "version": revision.version,
        "sha256": revision.sha256,
        "parent_sha256": revision.parent_sha256,
        "reason": revision.change_reason,
        "actor": _actor(revision.created_by, task, initial=initial),
        "created_at": revision.created_at.isoformat(),
        "input_hash": revision.input_hash,
        "blueprint_hash": revision.blueprint_hash,
        "source_refs": _source_refs(revision),
        "source_authorized_current": access,
        "diff": _payload_diff(previous.payload, revision.payload) if previous else [],
    }


@api_view(["GET"])
@product_endpoint
def task_history(request, task_id):
    task = task_for(request.user, task_id)
    revisions = list(task.revisions.select_related("created_by").order_by("created_at", "pk"))
    input_access = {
        revision.sha256: input_authorized(task, revision)
        for revision in revisions if revision.kind == DocumentRevision.Kind.INPUT
    }
    previous_by_stream = {}
    timeline = []
    for revision in revisions:
        stream = (revision.kind, revision.family)
        access = input_access.get(revision.input_hash, True) if revision.input_hash else input_access.get(revision.sha256, True)
        timeline.append(_revision_record(task, revision, previous_by_stream.get(stream), access))
        previous_by_stream[stream] = revision
    for source in task.sources.select_related("uploaded_by").order_by("created_at", "pk"):
        timeline.append({
            "event": "external_upload",
            "id": str(source.pk),
            "sha256": source.sha256,
            "file_name": source.original_name,
            "size": source.size,
            "uploader": _actor(source.uploaded_by, task),
            "content_author": {"status": source.author_verification, "id": None, "name": None},
            "created_at": source.created_at.isoformat(),
        })
    for attempt in task.attempts.order_by("start_at", "pk"):
        timeline.append({
            "event": "ai_task",
            "id": str(attempt.pk),
            "action": attempt.action,
            "fence": attempt.fence,
            "status": attempt.status,
            "model_calls": attempt.model_calls,
            "error_code": attempt.error_code,
            "created_at": attempt.start_at.isoformat(),
        })
    current_input = task.revisions.filter(kind=DocumentRevision.Kind.INPUT, version=task.input_version).first()
    current_blueprint = task.revisions.filter(kind=DocumentRevision.Kind.BLUEPRINT, version=task.blueprint_version).first()
    for artifact in task.artifacts.order_by("created_at", "pk"):
        access = input_access.get(artifact.input_hash, False)
        latest = task.artifacts.filter(family=artifact.family).order_by("-version").first()
        current = bool(access and current_input and current_blueprint and latest and latest.pk == artifact.pk
                       and artifact.input_hash == current_input.sha256
                       and artifact.blueprint_hash == current_blueprint.sha256)
        timeline.append({
            "event": "artifact",
            "id": str(artifact.pk),
            "family": artifact.family,
            "version": artifact.version,
            "sha256": artifact.sha256,
            "input_hash": artifact.input_hash,
            "blueprint_hash": artifact.blueprint_hash,
            "generation_hash": artifact.generation_hash,
            "current": current,
            "stale": not current,
            "created_at": artifact.created_at.isoformat(),
        })
    for record in task.approvals.select_related("actor", "revision", "artifact").order_by("created_at", "pk"):
        target = record.revision or record.artifact
        access = input_access.get(target.input_hash, False)
        if not access:
            continue
        timeline.append({
            "event": "approval",
            "id": str(record.pk),
            "target_type": "revision" if record.revision_id else "artifact",
            "target_id": str(target.pk),
            "target_sha256": record.sha256,
            "decision": record.decision,
            "reason": record.comment,
            "actor": {"type": "approver", "id": record.actor_id, "name": record.actor.get_username()},
            "authorization_current": approval_current(task, record),
            "created_at": record.created_at.isoformat(),
        })
    timeline.sort(key=lambda item: (item["created_at"], item["event"], item["id"]))
    audit(request.user, "product_history_read", f"{task.pk}:v{task.version}")
    return Response({
        "task_id": str(task.pk),
        "task_version": task.version,
        "history_immutable": True,
        "tamper_claim": "application_read_only_not_forensic_immutability",
        "timeline": timeline,
    })


urlpatterns = [path("tasks/<uuid:task_id>/history/", task_history)]