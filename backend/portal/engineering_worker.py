"""Controlled local cost worker.

Deployment must set absolute trusted paths in PORTAL_ENGINEERING_PYTHON and
PORTAL_ENGINEERING_COST_CLI. Neither path is accepted from API clients.
Optional limits use PORTAL_ENGINEERING_TIMEOUT_SECONDS and
PORTAL_ENGINEERING_MAX_ATTEMPTS. Online fallback is disabled unless
PORTAL_ENGINEERING_ALLOW_ONLINE=1 and PORTAL_ENGINEERING_WEBPRICE_KEY is set.
"""
import hashlib
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from .engineering_models import EngineeringJob
from .engineering_storage import (StorageError, persist_result, verified_cli_output,
                                  verified_input, work_directory)
from .security import audit, authorized_modules


SAFE_ENVIRONMENT = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
                    "LOCALAPPDATA", "PROGRAMDATA")


class WorkerUnavailable(Exception):
    pass


def _configured(name, environment, default=""):
    value = getattr(settings, name, None)
    return value if value is not None else os.environ.get(environment, default)


def _bounded_integer(name, environment, default, lower, upper):
    try:
        value = int(_configured(name, environment, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, lower), upper)


def timeout_seconds():
    return _bounded_integer("ENGINEERING_TIMEOUT_SECONDS", "PORTAL_ENGINEERING_TIMEOUT_SECONDS",
                            600, 10, 3600)


def max_attempts():
    return _bounded_integer("ENGINEERING_MAX_ATTEMPTS", "PORTAL_ENGINEERING_MAX_ATTEMPTS",
                            3, 1, 5)


def online_webprice_key():
    if os.environ.get("PORTAL_ENGINEERING_ALLOW_ONLINE") != "1":
        return ""
    return os.environ.get("PORTAL_ENGINEERING_WEBPRICE_KEY", "").strip()


def runtime_state():
    python_value = _configured("ENGINEERING_PYTHON", "PORTAL_ENGINEERING_PYTHON")
    cli_value = _configured("ENGINEERING_COST_CLI", "PORTAL_ENGINEERING_COST_CLI")
    if not python_value or not cli_value:
        return {"status": "not_configured", "error_code": "worker_not_configured",
                "detail": "成本测算 Python/CLI 绝对路径尚未配置。", "python": None, "cli": None}
    python_source = Path(python_value).expanduser()
    cli_source = Path(cli_value).expanduser()
    if not python_source.is_absolute() or not cli_source.is_absolute():
        return {"status": "unavailable", "error_code": "worker_unavailable",
                "detail": "成本测算 Python/CLI 必须配置为绝对路径。",
                "python": python_source, "cli": cli_source}
    python = python_source.resolve()
    cli = cli_source.resolve()
    if not python.is_file() or not cli.is_file():
        return {"status": "unavailable", "error_code": "worker_unavailable",
                "detail": "成本测算运行时未部署或不可读取。", "python": python, "cli": cli}
    return {"status": "ready", "error_code": "", "detail": "成本测算 Worker 已配置。",
            "python": python, "cli": cli}


def _engineering_allowed(user):
    return (user.is_active and not user.must_change_password
            and user.roles.filter(code="engineering").exists()
            and authorized_modules(user).filter(code="cost", enabled=True).exists())


@transaction.atomic
def claim_job():
    if runtime_state()["status"] != "ready":
        return None
    now = timezone.now()
    EngineeringJob.objects.filter(status=EngineeringJob.Status.RUNNING,
        lease_until__lte=now, attempt_count__gte=max_attempts()).update(
            status=EngineeringJob.Status.FAILED, lease_until=None, next_retry_at=None,
            error_code="attempt_limit", error_detail="已达到工程任务重试上限。",
            completed_at=now, updated_at=now)
    eligible = (
        Q(status=EngineeringJob.Status.QUEUED, attempt_count__lt=max_attempts())
        & (Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
    ) | Q(status=EngineeringJob.Status.RUNNING, lease_until__lte=now,
          attempt_count__lt=max_attempts()) | Q(
              status=EngineeringJob.Status.BLOCKED,
              error_code__in=["worker_not_configured", "worker_unavailable"])
    locks = {"skip_locked": True} if connection.features.has_select_for_update_skip_locked else {}
    job = EngineeringJob.objects.select_for_update(**locks).filter(eligible).order_by("created_at").first()
    if job is None:
        return None
    job.fence += 1
    job.attempt_count += 1
    job.status = EngineeringJob.Status.RUNNING
    job.lease_until = now + timedelta(seconds=timeout_seconds() * 2 + 120)
    job.next_retry_at = None
    job.error_code = ""
    job.error_detail = ""
    job.save()
    return job.pk, job.fence


def _invoke(config, command, paths, extra=(), webprice_key=""):
    environment = {key: os.environ[key] for key in SAFE_ENVIRONMENT if key in os.environ}
    environment.update({"DISABLE_WEB_LOOKUP": "1", "PYTHONIOENCODING": "utf-8",
                        "PYTHONDONTWRITEBYTECODE": "1"})
    if webprice_key:
        environment["DASHSCOPE_API_KEY"] = webprice_key
    arguments = [str(config["python"]), str(config["cli"]), command,
                 *(str(path) for path in paths), *extra]
    command_timeout = min(timeout_seconds(), 15) if command == "quota-candidates" else timeout_seconds()
    completed = subprocess.run(arguments, shell=False, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=command_timeout, cwd=str(config["cli"].parent.parent), env=environment)
    if len(completed.stdout) > 2 * 1024 * 1024:
        raise ValueError("CLI output too large")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        if completed.returncode != 0:
            raise WorkerUnavailable from None
        raise
    if not isinstance(payload, dict):
        raise ValueError("CLI output is not an object")
    return completed.returncode, payload


def _inspection(payload, inputs):
    records = payload.get("files") if isinstance(payload.get("files"), list) else []
    return {
        "ok": bool(payload.get("ok")),
        "files": [{**{key: value for key, value in record.items() if key != "input"},
                   "name": inputs[index]["name"] if index < len(inputs) else ""}
                  for index, record in enumerate(records) if isinstance(record, dict)],
    }


def _without_paths(value, path_names):
    if isinstance(value, list):
        return [_without_paths(item, path_names) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned = {}
    for key, item in value.items():
        if key in ("output_dir", "output_file"):
            continue
        if key == "input":
            if str(item) in path_names:
                cleaned["name"] = path_names[str(item)]
            continue
        cleaned[key] = _without_paths(item, path_names)
    return cleaned


def _result(payload, inputs, paths):
    records = payload.get("files") if isinstance(payload.get("files"), list) else []
    allowed = ("input_sha256", "preflight_issues", "status", "validation_passed",
               "validation_issues", "source_health", "pending_confirmations", "internal_draft",
               "output_sha256")
    aggregate = ("online_allowed", "input_hash", "output_hash", "validation_issues",
                 "source_health", "pending_confirmations", "result_type")
    path_names = {str(path): inputs[index]["name"] for index, path in enumerate(paths)}
    return {
        **{key: _without_paths(payload[key], path_names) for key in aggregate if key in payload},
        "files": [_without_paths({key: record[key] for key in allowed if key in record}, path_names)
                  for record in records if isinstance(record, dict)],
    }


def _combined_hash(values):
    return hashlib.sha256("".join(values).encode("ascii")).hexdigest()


@transaction.atomic
def _finalize(job_id, fence, *, status, error_code="", error_detail="", inspection=None,
              result=None, artifact=None, retry=False):
    job = EngineeringJob.objects.select_for_update().select_related("owner").filter(pk=job_id).first()
    now = timezone.now()
    if (job is None or job.status != EngineeringJob.Status.RUNNING or job.fence != fence
            or not job.lease_until or job.lease_until <= now):
        return False
    if status == EngineeringJob.Status.COMPLETED and not _engineering_allowed(job.owner):
        status = EngineeringJob.Status.BLOCKED
        error_code = "permission_changed"
        error_detail = "工程成本模块授权已变化。"
        artifact = None
    if retry and job.attempt_count < max_attempts():
        status = EngineeringJob.Status.QUEUED
        job.next_retry_at = now + timedelta(seconds=min(60 * (2 ** (job.attempt_count - 1)), 300))
    else:
        job.next_retry_at = None
    job.status = status
    job.error_code = error_code
    job.error_detail = error_detail[:300]
    job.lease_until = None
    if inspection is not None:
        job.inspection = inspection
    if result is not None:
        job.result = result
    if artifact is not None and status == EngineeringJob.Status.COMPLETED:
        job.result_path = artifact["path"]
        job.result_filename = artifact["filename"]
        job.result_sha256 = artifact["sha256"]
        job.result_size = artifact["size"]
    if status in (EngineeringJob.Status.COMPLETED, EngineeringJob.Status.FAILED):
        job.completed_at = now
    job.save()
    audit(job.owner, "engineering_job_process", job.pk,
          result="success" if status == EngineeringJob.Status.COMPLETED else status,
          changes=[error_code] if error_code else ["internal_draft"])
    return True


def process_job(job_id, fence):
    job = EngineeringJob.objects.select_related("owner").filter(pk=job_id).first()
    if job is None or job.status != EngineeringJob.Status.RUNNING or job.fence != fence:
        return
    if not _engineering_allowed(job.owner):
        _finalize(job_id, fence, status=EngineeringJob.Status.BLOCKED,
                  error_code="permission_changed", error_detail="工程成本模块授权已变化。")
        return
    config = runtime_state()
    if config["status"] != "ready":
        _finalize(job_id, fence, status=EngineeringJob.Status.BLOCKED,
                  error_code=config["error_code"], error_detail=config["detail"])
        return
    inspection = None
    result = None
    try:
        paths = [verified_input(item) for item in job.inputs]
        inspect_code, inspect_payload = _invoke(config, "inspect", paths)
        inspection = _inspection(inspect_payload, job.inputs)
        if inspect_code == 2 and isinstance(inspect_payload.get("files"), list):
            _finalize(job_id, fence, status=EngineeringJob.Status.FAILED,
                      error_code="preflight_failed", error_detail="工程清单预检未通过，未执行测算。",
                      inspection=inspection)
            return
        if inspect_code != 0 or not inspect_payload.get("ok"):
            _finalize(job_id, fence, status=EngineeringJob.Status.FAILED,
                      error_code="inspect_failed", error_detail="工程清单预检程序执行失败。",
                      inspection=inspection, retry=True)
            return
        work = work_directory(job_id, fence)
        webprice_key = online_webprice_key()
        allow_online = bool(webprice_key)
        run_options = ("--region", job.region, "--output-dir", str(work))
        if allow_online:
            run_options += ("--allow-online",)
        run_code, run_payload = _invoke(config, "run", paths, run_options,
                                        webprice_key=webprice_key)
        result = _result(run_payload, job.inputs, paths)
        if run_code == 2 and run_payload.get("stage") == "preflight":
            _finalize(job_id, fence, status=EngineeringJob.Status.FAILED,
                      error_code="preflight_failed", error_detail="工程清单预检未通过，未执行测算。",
                      inspection=inspection, result=result)
            return
        records = run_payload.get("files")
        if run_code != 0 or not run_payload.get("ok"):
            _finalize(job_id, fence, status=EngineeringJob.Status.FAILED,
                      error_code="cost_run_failed", error_detail="成本测算程序执行失败。",
                      inspection=inspection, result=result, retry=True)
            return
        if (run_payload.get("online_allowed") is not allow_online
                or run_payload.get("result_type") != "internal_draft"
                or any(not isinstance(run_payload.get(key), str)
                       or len(run_payload[key]) != 64
                       or any(char not in "0123456789abcdef" for char in run_payload[key])
                       for key in ("input_hash", "output_hash"))
                or run_payload["input_hash"] != _combined_hash(
                    item["sha256"] for item in job.inputs)):
            raise StorageError("invalid_result", "测算程序未返回受控内部草稿成果。")
        if not isinstance(records, list) or len(records) != len(paths):
            raise StorageError("invalid_result", "测算成果数量与输入不一致。")
        outputs = []
        output_hashes = []
        for index, record in enumerate(records):
            if (not isinstance(record, dict) or record.get("status") != "completed"
                    or record.get("internal_draft") is not True
                    or record.get("input_sha256") != job.inputs[index]["sha256"]
                    or not isinstance(record.get("output_sha256"), str)):
                raise StorageError("invalid_result", "测算程序未返回受控内部草稿成果。")
            outputs.append(verified_cli_output(record.get("output_file"),
                                                record["output_sha256"], work))
            output_hashes.append(record["output_sha256"])
        if run_payload["output_hash"] != _combined_hash(output_hashes):
            raise StorageError("invalid_result", "测算程序未返回受控内部草稿成果。")
        artifact = persist_result(job_id, fence, outputs)
        _finalize(job_id, fence, status=EngineeringJob.Status.COMPLETED,
                  inspection=inspection, result=result, artifact=artifact)
    except subprocess.TimeoutExpired:
        _finalize(job_id, fence, status=EngineeringJob.Status.FAILED,
                  error_code="worker_timeout", error_detail="成本测算处理超时。",
                  inspection=inspection, result=result, retry=True)
    except StorageError as error:
        _finalize(job_id, fence, status=EngineeringJob.Status.FAILED,
                  error_code=error.code, error_detail=error.detail,
                  inspection=inspection, result=result)
    except OSError:
        _finalize(job_id, fence, status=EngineeringJob.Status.BLOCKED,
                  error_code="worker_unavailable", error_detail="成本测算运行时未部署或不可执行。",
                  inspection=inspection, result=result)
    except WorkerUnavailable:
        _finalize(job_id, fence, status=EngineeringJob.Status.BLOCKED,
                  error_code="worker_unavailable", error_detail="成本测算运行时未部署或不可执行。",
                  inspection=inspection, result=result)
    except (json.JSONDecodeError, ValueError):
        _finalize(job_id, fence, status=EngineeringJob.Status.FAILED,
                  error_code="invalid_worker_output", error_detail="成本测算程序返回格式无效。",
                  inspection=inspection, result=result, retry=True)
    except Exception:
        _finalize(job_id, fence, status=EngineeringJob.Status.FAILED,
                  error_code="worker_error", error_detail="工程成本 Worker 执行异常。",
                  inspection=inspection, result=result, retry=True)


def run_once():
    claim = claim_job()
    if claim is None:
        return False
    process_job(*claim)
    return True
