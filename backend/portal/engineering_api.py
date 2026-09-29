import json
import math
import subprocess
from functools import wraps
from collections.abc import Mapping

from django.db import transaction
from django.http import FileResponse
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from .engineering_models import EngineeringJob
from . import engineering_knowledge as knowledge
from . import engineering_worker
from .engineering_storage import (StorageError, read_upload, remove_job, save_inputs,
                                  verified_result)
from .engineering_worker import WorkerUnavailable, max_attempts, runtime_state
from .security import audit, authorized_modules


class EngineeringError(Exception):
    def __init__(self, code, detail, status=400):
        self.code, self.detail, self.status = code, detail, status


def engineering_endpoint(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        try:
            response = function(request, *args, **kwargs)
        except ParseError:
            response = Response({"code": "invalid_request", "detail": "请求格式无效。"}, status=400)
        except EngineeringError as error:
            audit(request.user, "engineering_request", request.path[:150], result="denied",
                  changes=[error.code])
            response = Response({"code": error.code, "detail": error.detail}, status=error.status)
        except StorageError as error:
            audit(request.user, "engineering_request", request.path[:150], result="denied",
                  changes=[error.code])
            status = 400 if error.code in {
                "invalid_filename", "unsupported_file", "upload_too_large", "invalid_file",
            } else 503
            response = Response({"code": error.code, "detail": error.detail}, status=status)
        response["Cache-Control"] = "private, no-store"
        return response
    return wrapped


def _engineering_allowed(user):
    return (user.is_active and not user.must_change_password
            and user.roles.filter(code="engineering").exists()
            and authorized_modules(user).filter(code="cost", enabled=True).exists())


def _require_engineering(request):
    if not _engineering_allowed(request.user):
        raise EngineeringError("engineering_forbidden", "没有工程成本模块操作权限。", 403)


def _capabilities():
    state = runtime_state()
    return {
        "cost": {"status": state["status"], "detail": state["detail"]},
        "ragflow": {"status": "locked", "detail": "工程 RAGFlow 权限尚未解锁/未接入。"},
    }


def _job_data(job):
    error = None
    if job.error_code:
        error = {
            "code": job.error_code,
            "detail": job.error_detail,
            "retryable": job.status == EngineeringJob.Status.QUEUED,
        }
    result = {
        "classification": "internal_draft",
        "formal_pricing": False,
        "auto_imported": False,
        "download_available": job.status == EngineeringJob.Status.COMPLETED and bool(job.result_path),
        "filename": job.result_filename or None,
        "sha256": job.result_sha256 or None,
        "size": job.result_size,
        "summary": job.result or None,
    }
    return {
        "id": str(job.pk),
        "status": job.status,
        "region": job.region,
        "files": [{key: item[key] for key in ("name", "size", "sha256")} for item in job.inputs],
        "inspection": job.inspection or None,
        "result": result,
        "error": error,
        "attempt_count": job.attempt_count,
        "max_attempts": max_attempts(),
        "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _owned_job(user, job_id):
    try:
        return EngineeringJob.objects.get(pk=job_id, owner=user)
    except (EngineeringJob.DoesNotExist, ValueError, TypeError):
        raise EngineeringError("not_found", "对象不存在。", 404) from None


def _region(request):
    values = request.data.getlist("region") if hasattr(request.data, "getlist") else []
    if len(values) > 1:
        raise EngineeringError("invalid_request", "region 只能提供一次。")
    value = request.data.get("region", "陕西")
    if not isinstance(value, str) or not value.strip() or len(value) > 80 or any(ord(c) < 32 for c in value):
        raise EngineeringError("invalid_request", "region 格式无效。")
    return value.strip()


@api_view(["GET", "POST"])
@engineering_endpoint
def jobs(request):
    _require_engineering(request)
    if request.method == "GET":
        records = EngineeringJob.objects.filter(owner=request.user)
        return Response({"jobs": [_job_data(job) for job in records],
                         "capabilities": _capabilities()})
    if not isinstance(request.data, Mapping) or set(request.data.keys()) - {"files", "region"}:
        raise EngineeringError("invalid_request", "请求字段无效。")
    uploads = request.FILES.getlist("files")
    if not 1 <= len(uploads) <= 2:
        raise EngineeringError("invalid_request", "每个任务必须上传 1～2 份 .xlsx 工程清单。")
    files = [read_upload(upload) for upload in uploads]
    state = runtime_state()
    status = EngineeringJob.Status.QUEUED if state["status"] == "ready" else EngineeringJob.Status.BLOCKED
    error_code = "" if status == EngineeringJob.Status.QUEUED else state["error_code"]
    error_detail = "" if status == EngineeringJob.Status.QUEUED else state["detail"]
    job_id = None
    try:
        with transaction.atomic():
            job = EngineeringJob.objects.create(owner=request.user, region=_region(request),
                status=status, error_code=error_code, error_detail=error_detail)
            job_id = job.pk
            job.inputs = save_inputs(job.pk, files)
            job.save(update_fields=["inputs", "updated_at"])
            audit(request.user, "engineering_job_create", job.pk, changes=["inputs", "region"])
    except Exception as original:
        if job_id is not None:
            try:
                remove_job(job_id)
            except StorageError as cleanup_error:
                raise EngineeringError(cleanup_error.code, cleanup_error.detail, 503) from original
        raise
    return Response({"job": _job_data(job), "capabilities": _capabilities()}, status=201)


@api_view(["GET", "DELETE"])
@engineering_endpoint
def job_detail(request, job_id):
    _require_engineering(request)
    job = _owned_job(request.user, job_id)
    if request.method == "DELETE":
        # 仅允许删除终态任务（已完成/失败/阻塞），避免删除正在处理的任务
        # 造成 Worker 的输入文件被移除、产生悬空执行。
        if job.status in (EngineeringJob.Status.QUEUED, EngineeringJob.Status.RUNNING):
            raise EngineeringError("job_in_progress", "任务正在处理中，请等待结束后再删除。", 409)
        identifier = job.pk
        # 先清理私有存储，再删记录：文件清理失败时保留记录以便运维重试。
        try:
            remove_job(identifier)
        except StorageError as error:
            raise EngineeringError(error.code, error.detail, 503) from error
        job.delete()
        audit(request.user, "engineering_job_delete", identifier, changes=[job.status])
        return Response(status=204)
    return Response({"job": _job_data(job), "capabilities": _capabilities()})


@api_view(["GET"])
@engineering_endpoint
def download(request, job_id):
    _require_engineering(request)
    job = _owned_job(request.user, job_id)
    if job.status != EngineeringJob.Status.COMPLETED or not job.result_path:
        raise EngineeringError("result_not_ready", "内部草稿成果尚不可下载。", 409)
    try:
        target = verified_result(job)
    except StorageError as error:
        raise EngineeringError(error.code, error.detail, 409) from error
    response = FileResponse(target.open("rb"), as_attachment=True, filename=job.result_filename,
                            content_type=("application/zip" if target.suffix.lower() == ".zip"
                                          else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    response["Cache-Control"] = "private, no-store"
    audit(request.user, "engineering_result_download", job.pk, changes=[job.result_sha256])
    return response


def _knowledge_status(request):
    if not EngineeringJob.objects.filter(owner=request.user, inspection__ok=True).exists():
        return {"status": "locked", "detail": "请先完成至少一项工程清单任务并通过检查。"}
    try:
        config = knowledge.configuration()
        count = knowledge.dataset_document_count(config)
    except knowledge.KnowledgeError as error:
        if str(error) == "not_configured":
            return {"status": "not_configured", "detail": "工程专用知识库尚未配置。"}
        return {"status": "unavailable", "detail": "工程知识服务暂时不可用。"}
    if not count:
        return {"status": "locked", "detail": "工程专用知识库尚无已解析文档或片段。",
                "dataset_document_count": 0}
    return {"status": "ready", "detail": "工程知识库可检索。", "dataset_document_count": count}


def _quota_text(value, limit):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= limit
            or any(ord(character) < 32 for character in value)):
        raise ValueError
    return value.strip()


def _quota_candidates(payload):
    if (not isinstance(payload, dict) or payload.get("ok") is not True
            or payload.get("command") != "quota-candidates"
            or not isinstance(payload.get("candidates"), list)
            or len(payload["candidates"]) > 5):
        raise ValueError
    fields = {"code", "major", "name", "unit", "score", "original_source", "unit_compatible"}
    candidates = []
    for record in payload["candidates"]:
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError
        score = record["score"]
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not 0 <= score <= 2
                or not isinstance(record["unit_compatible"], bool)):
            raise ValueError
        candidates.append({
            "code": _quota_text(record["code"], 80),
            "major": _quota_text(record["major"], 120),
            "name": _quota_text(record["name"], 200),
            "unit": _quota_text(record["unit"], 80),
            "score": score,
            "source": _quota_text(record["original_source"], 200),
            "unit_compatible": record["unit_compatible"],
        })
    return candidates


@api_view(["GET"])
@engineering_endpoint
def quota_candidates(request):
    _require_engineering(request)
    query = request.query_params
    if set(query) != {"name", "unit"} or any(len(query.getlist(key)) != 1 for key in query):
        raise EngineeringError("invalid_request", "仅接受 name 和 unit 参数。")
    try:
        name, unit = _quota_text(query["name"], 100), _quota_text(query["unit"], 20)
    except ValueError:
        raise EngineeringError("invalid_request", "name 或 unit 格式无效。") from None
    if not EngineeringJob.objects.filter(owner=request.user, inspection__ok=True).exists():
        raise EngineeringError("quota_locked", "请先完成至少一项工程清单任务并通过检查。", 409)
    config = runtime_state()
    if config["status"] != "ready":
        raise EngineeringError("quota_unavailable", "工程定额候选服务暂时不可用。", 503)
    try:
        returncode, payload = engineering_worker._invoke(
            config, "quota-candidates", (), ("--name", name, "--unit", unit))
        if returncode != 0:
            raise ValueError
        candidates = _quota_candidates(payload)
    except subprocess.TimeoutExpired:
        raise EngineeringError("quota_unavailable", "工程定额候选服务暂时不可用。", 503) from None
    except (WorkerUnavailable, OSError, json.JSONDecodeError, ValueError):
        raise EngineeringError("quota_unavailable", "工程定额候选服务暂时不可用。", 503) from None
    _require_engineering(request)
    if not EngineeringJob.objects.filter(owner=request.user, inspection__ok=True).exists():
        raise EngineeringError("quota_locked", "工程任务检查权限已变更。", 409)
    audit(request.user, "engineering_quota_candidates", changes=[f"candidate_count:{len(candidates)}"])
    return Response({"status": "candidates", "classification": "internal_unapproved",
                     "candidates": candidates})


@api_view(["GET"])
@engineering_endpoint
def knowledge_status(request):
    _require_engineering(request)
    return Response(_knowledge_status(request))


@api_view(["POST"])
@engineering_endpoint
def knowledge_retrieve(request):
    _require_engineering(request)
    if not isinstance(request.data, dict) or set(request.data) != {"question"}:
        raise EngineeringError("invalid_request", "仅接受 question 字段。")
    question = request.data["question"]
    if (not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000
            or any(ord(character) < 32 and character not in "\n\t" for character in question)):
        raise EngineeringError("invalid_request", "question 格式无效。")
    state = _knowledge_status(request)
    if state["status"] != "ready":
        raise EngineeringError("knowledge_" + state["status"], state["detail"], 409)
    try:
        sources = knowledge.retrieve(question.strip(), knowledge.configuration())
    except knowledge.KnowledgeError:
        raise EngineeringError("knowledge_unavailable", "工程知识服务暂时不可用。", 503) from None
    _require_engineering(request)
    if not EngineeringJob.objects.filter(owner=request.user, inspection__ok=True).exists():
        raise EngineeringError("knowledge_locked", "工程任务检查权限已变更。", 409)
    audit(request.user, "engineering_knowledge_retrieve", changes=[f"result_count:{len(sources)}"])
    return Response({"status": "completed", "sources": sources})

urlpatterns = [
    path("quota/", quota_candidates),
    path("knowledge/", knowledge_status),
    path("knowledge/retrieve/", knowledge_retrieve),
    path("jobs/", jobs),
    path("jobs/<uuid:job_id>/", job_detail),
    path("jobs/<uuid:job_id>/download/", download),
]
