"""RAGFlow gate and adapters for blueprint generation.

Preview mode is deliberately source-only and records no synthetic retrieval
hits. Formal mode calls the existing allow-listed RAGFlow retrieval service and
fails closed on configuration, authorization, transport, or response errors.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .product_service import ProductError, digest


PREVIEW = "source_only_preview"
RAGFLOW_REQUIRED = "ragflow_required"


def _ragflow_snapshot(input_payload):
    value = input_payload.get("blueprint_knowledge") if isinstance(input_payload, Mapping) else None
    if not isinstance(value, Mapping):
        return None
    sources = value.get("sources")
    scope_hash = value.get("scope_hash")
    if (value.get("provider") != "ragflow" or value.get("status") != "completed"
            or not isinstance(sources, list) or not isinstance(scope_hash, str)
            or len(scope_hash) != 64):
        return None
    return value


def blueprint_knowledge_status(input_payload):
    mode = getattr(settings, "PRODUCT_BLUEPRINT_KNOWLEDGE_MODE", PREVIEW)
    snapshot = _ragflow_snapshot(input_payload)
    if snapshot is not None:
        return {
            "mode": mode,
            "required": mode == RAGFLOW_REQUIRED,
            "status": "ready",
            "ragflow_used": True,
            "source_count": len(snapshot["sources"]),
            "query_hash": snapshot.get("query_hash", ""),
            "retrieval_outcome": snapshot.get("retrieval_outcome", "hits" if snapshot["sources"] else "no_hits"),
            "detail": "当前输入版本已完成 RAGFlow 授权检索。",
        }
    if mode == RAGFLOW_REQUIRED:
        return {
            "mode": mode,
            "required": True,
            "status": "waiting_for_ragflow",
            "ragflow_used": False,
            "source_count": 0,
            "query_hash": "",
            "retrieval_outcome": "not_run",
            "detail": "正式模式必须先完成 RAGFlow 授权检索；服务不可用时不会绕过该步骤。",
        }
    return {
        "mode": PREVIEW,
        "required": False,
        "status": "source_only_preview",
        "ragflow_used": False,
        "source_count": 0,
        "query_hash": "",
        "retrieval_outcome": "not_run",
        "detail": "当前为资料直生成预览，仅使用本项目上传资料；没有伪造 RAGFlow 命中。",
    }


def require_blueprint_knowledge(input_payload):
    status = blueprint_knowledge_status(input_payload)
    if status["required"] and status["status"] != "ready":
        raise ProductError("ragflow_required", status["detail"], 409)
    return status


def _query(payload):
    value = "\n".join(str(payload.get(key, "")).strip()
                      for key in ("project", "requirements", "background"))
    return value[-6000:] or str(payload.get("project", ""))


def _preview_checkpoint(task, fence):
    with transaction.atomic():
        from .product_models import DocumentTask

        locked = DocumentTask.objects.select_for_update().get(pk=task.pk)
        if locked.state != DocumentTask.State.RUNNING or locked.fence != fence:
            raise ProductError("lease_lost", "任务租约已失效。", 409)
        locked.checkpoint = {
            **locked.checkpoint,
            "blueprint_knowledge": {
                "adapter": "source_only_preview",
                "status": "skipped",
                "ragflow_used": False,
                "source_count": 0,
                "updated_at": timezone.now().isoformat(),
            },
        }
        locked.save(update_fields=["checkpoint", "updated_at"])


def prepare_blueprint_knowledge(task_id, fence):
    """Idempotently prepare knowledge evidence for the current input version."""
    from .product_knowledge_service import authorize, configuration, retrieve
    from .product_models import DocumentTask
    from .product_service import append_revision, current_revision

    task = DocumentTask.objects.select_related("owner").get(pk=task_id)
    if task.state != DocumentTask.State.RUNNING or task.fence != fence:
        raise ProductError("lease_lost", "任务租约已失效。", 409)
    revision = current_revision(task, "input")
    if revision is None:
        raise ProductError("input_required", "请先保存输入。", 409)
    mode = getattr(settings, "PRODUCT_BLUEPRINT_KNOWLEDGE_MODE", PREVIEW)
    if mode == PREVIEW:
        _preview_checkpoint(task, fence)
        return blueprint_knowledge_status(revision.payload)

    existing = _ragflow_snapshot(revision.payload)
    scope = authorize(task.owner)
    scope_hash = digest(scope)
    if (existing is not None and existing.get("authorization") == scope
            and existing.get("scope_hash") == scope_hash):
        return blueprint_knowledge_status(revision.payload)

    config = configuration()
    query = _query(revision.payload)
    hits = retrieve(query, [], scope, config)
    knowledge_sources = [{
        "id": f"ragflow:{source['id']}",
        "provider": "ragflow",
        "chunk_id": source["chunk_id"],
        "dataset_id": source["dataset_id"],
        "document_id": source["document_id"],
        "title": source["title"],
        "text": source["content"],
    } for source in hits]
    snapshot = {
        "provider": "ragflow",
        "status": "completed",
        "ragflow_used": True,
        "query_hash": digest({"query": query}),
        "scope_hash": scope_hash,
        "retrieval_outcome": "hits" if knowledge_sources else "no_hits",
        "authorization": scope,
        "sources": [{key: source[key] for key in (
            "id", "chunk_id", "dataset_id", "document_id", "title"
        )} for source in knowledge_sources],
        "retrieved_at": timezone.now().isoformat(),
    }
    with transaction.atomic():
        locked = DocumentTask.objects.select_for_update().get(pk=task_id)
        if locked.state != DocumentTask.State.RUNNING or locked.fence != fence:
            raise ProductError("lease_lost", "任务租约已失效。", 409)
        current = current_revision(locked, "input")
        if current.pk != revision.pk:
            raise ProductError("source_snapshot_changed", "输入版本已变化，请重试。", 409)
        payload = json.loads(json.dumps(current.payload, ensure_ascii=False))
        payload["blueprint_knowledge"] = snapshot
        payload["knowledge_sources"] = knowledge_sources
        prepared = append_revision(
            locked,
            "input",
            payload,
            actor=locked.owner,
            reason="ragflow_blueprint_retrieval",
        )
        locked.input_version = prepared.version
        locked.blueprint_version = 0
        locked.checkpoint = {
            **locked.checkpoint,
            "blueprint_knowledge": {
                "adapter": "ragflow",
                "status": "completed",
                "ragflow_used": True,
                "source_count": len(knowledge_sources),
                "query_hash": snapshot["query_hash"],
                "updated_at": snapshot["retrieved_at"],
            },
        }
        locked.save(update_fields=[
            "input_version", "blueprint_version", "checkpoint", "updated_at"
        ])
    return blueprint_knowledge_status(payload)
