"""Run one isolated long-form product document acceptance sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import traceback
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "qa"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_run_directory() -> Path:
    parent = ROOT / ".runtime" / "preproduction-20260929"
    parent.mkdir(parents=True, exist_ok=True)
    while True:
        directory = parent / f"document-acceptance-{uuid.uuid4().hex}"
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        for name in ("storage", "hr-storage", "downloads"):
            (directory / name).mkdir()
        (directory / "database.sqlite3").touch()
        return directory


def office_probe(runtime: Path, timeout: int) -> tuple[bool, str]:
    if not runtime.is_file():
        return False, "document_runtime_missing"
    try:
        result = subprocess.run(
            [str(runtime), "-c", "import win32com.client"],
            capture_output=True,
            text=True,
            timeout=min(timeout, 60),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "document_runtime_import_timeout"
    except OSError as error:
        return False, f"document_runtime_import_error:{type(error).__name__}"
    return (True, "document_runtime_and_pywin32_available") if result.returncode == 0 else (
        False, "document_runtime_pywin32_unavailable"
    )


def configure_environment(run_directory: Path, timeout: int, office: bool) -> dict:
    for key in list(os.environ):
        if key.startswith("PORTAL_") or key == "DJANGO_SETTINGS_MODULE":
            del os.environ[key]
    runtime = ROOT / ".runtime" / "product-documents-python" / "Scripts" / "python.exe"
    storage = run_directory / "storage"
    database = run_directory / "database.sqlite3"
    os.environ.update({
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "PORTAL_SECRET_KEY": secrets.token_urlsafe(48),
        "PORTAL_DEBUG": "1",
        "PORTAL_HTTPS": "0",
        "PORTAL_ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
        "PORTAL_SQLITE_PATH": str(database),
        "PORTAL_PRODUCT_STORAGE_ROOT": str(storage),
        "PORTAL_HR_STORAGE_ROOT": str(run_directory / "hr-storage"),
        "PORTAL_PRODUCT_P1_ENABLED": "1",
        "PORTAL_PRODUCT_MODEL_CALLS_ALLOWED": "0",
        "PORTAL_MODEL_GATEWAY_URL": "",
        "PORTAL_MODEL_GATEWAY_TOKEN": "",
        "PORTAL_MODEL_GATEWAY_ALLOWED_URLS": "",
        "PORTAL_PRODUCT_RETRIEVAL_ENABLED": "0",
        "PORTAL_PRODUCT_RETRIEVAL_URL": "",
        "PORTAL_PRODUCT_RETRIEVAL_ALLOWED_URLS": "",
        "PORTAL_PRODUCT_RETRIEVAL_AUTHORIZATIONS": "{}",
        "PORTAL_PRODUCT_KNOWLEDGE_ENABLED": "0",
        "PORTAL_PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED": "0",
        "PORTAL_PRODUCT_KNOWLEDGE_URL": "",
        "PORTAL_PRODUCT_KNOWLEDGE_ALLOWED_URLS": "",
        "PORTAL_PRODUCT_KNOWLEDGE_AUTHORIZATIONS": "{}",
        "PORTAL_PRODUCT_BLUEPRINT_KNOWLEDGE_MODE": "source_only_preview",
        "PORTAL_PRODUCT_DOCUMENT_RENDER_TIMEOUT_SECONDS": str(timeout),
        "PORTAL_PRODUCT_OFFICE_RENDER_TIMEOUT_SECONDS": str(timeout),
        "PORTAL_PRODUCT_OFFICE_RENDER_ENABLED": "1" if office else "0",
    })
    if runtime.is_file():
        os.environ["PORTAL_PRODUCT_DOCUMENT_PYTHON"] = str(runtime)
    return {
        "database": str(database),
        "storage": str(storage),
        "model_calls_allowed": False,
        "retrieval_enabled": False,
        "knowledge_ai_calls_allowed": False,
        "blueprint_knowledge_mode": "source_only_preview",
        "word_office_render_enabled": office,
        "render_timeout_seconds": timeout,
        "document_runtime": str(runtime),
    }


def setup_django(run_directory: Path) -> None:
    import django
    from django.conf import settings
    from django.core.management import call_command

    django.setup()
    database = Path(settings.DATABASES["default"]["NAME"]).resolve()
    storage = Path(settings.PRODUCT_STORAGE_ROOT).resolve()
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
        raise RuntimeError("isolated acceptance requires SQLite")
    if database != (run_directory / "database.sqlite3").resolve():
        raise RuntimeError("database escaped acceptance directory")
    if storage != (run_directory / "storage").resolve():
        raise RuntimeError("product storage escaped acceptance directory")
    call_command("migrate", "--noinput", verbosity=0)
    call_command("seed_portal", verbosity=0)


def login(client, user, password: str) -> None:
    response = client.post(
        "/api/login/",
        json.dumps({"username": user.username, "password": password}),
        content_type="application/json",
    )
    if response.status_code != 200:
        raise RuntimeError(f"login failed: {response.status_code} {response.content!r}")


def require(response, status: int, label: str):
    if response.status_code != status:
        raise RuntimeError(f"{label}: expected {status}, got {response.status_code}: {response.content!r}")
    return response.json()


def synthetic_input() -> dict:
    return {
        "project": "[SYNTHETIC_PREPRODUCTION] 长文三件套隔离验收项目",
        "requirements": "[SYNTHETIC_PREPRODUCTION] 只验证章节、渲染、Worker、下载和哈希链路",
        "items": [{"row_id": "1", "name": "合成设备", "quantity": 2, "unit": "台"}],
        "background": "[SYNTHETIC_PREPRODUCTION] 本输入不代表真实项目、企业资料或经营数据。",
        "conditions": ["不得新增合成输入范围外的设备"],
    }


def create_task(client, run_directory: Path) -> dict:
    payload = synthetic_input()
    (run_directory / "synthetic-input.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return require(client.post(
        "/api/product/tasks/",
        json.dumps({"title": "[SYNTHETIC_PREPRODUCTION] 三件套长文验收", "input": payload}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="preproduction-document-acceptance",
    ), 201, "create task")


def approve_blueprint(client, task: dict) -> dict:
    chapters = [
        {"id": "architecture", "title": "总体设计", "scope": "说明合成平台的总体设计边界。", "source_ids": ["1"]},
        {"id": "implementation", "title": "实施方案", "scope": "说明合成项目的实施、校验和交付方法。", "source_ids": ["1"]},
        {"id": "operations", "title": "运行保障", "scope": "说明合成项目的运行、监控、审计和恢复要求。", "source_ids": ["1"]},
    ]
    payload = {
        "purpose": "形成合成项目的可追溯长文三件套",
        "audience": "隔离验收人员",
        "chapters": chapters,
        "conditions": [{"text": "不得新增合成输入范围外的设备", "type": "program"}],
        "missing": [],
        "conflicts": [],
        "template_version": "frozen-original-v1",
    }
    task = require(client.patch(
        f"/api/product/tasks/{task['id']}/blueprint/",
        json.dumps({"expected_version": task["version"], "payload": payload}),
        content_type="application/json",
    ), 200, "save blueprint")
    blueprint = task["blueprint"]
    return require(client.post(
        f"/api/product/tasks/{task['id']}/decisions/",
        json.dumps({
            "expected_version": task["version"],
            "target": "blueprint",
            "target_id": blueprint["id"],
            "sha256": blueprint["sha256"],
            "decision": "approve",
            "comment": "合成隔离验收预置蓝图已确认。",
        }),
        content_type="application/json",
    ), 201, "approve blueprint")["task"]


def long_paragraph(family: str, title: str, length: int, segment_number: int) -> str:
    label = "技术方案" if family == "technical-solution" else "可行性研究报告"
    seed = (
        f"合成验收标记。{label}的{title}章节第{segment_number}段仅用于隔离长文渲染校验。"
        "本段内容不代表真实项目事实，不代表企业资料，不形成正式业务结论。"
        "正文用于验证章节来源、篇幅边界、文档排版、图示嵌入、任务保存和下载哈希。"
        "所有事实均以当前合成输入为边界，缺少真实依据的部分仍需人工核对。"
    )
    return (seed * ((length // len(seed)) + 1))[:length]


def seed_verified_chapters(task_id, owner):
    from django.conf import settings
    from portal.product_models import DocumentTask
    from portal.product_service import append_revision, current_revision, validate_chapter

    task = DocumentTask.objects.get(pk=task_id)
    input_revision = current_revision(task, "input")
    blueprint = current_revision(task, "blueprint")
    if input_revision is None or blueprint is None:
        raise RuntimeError("missing input or approved blueprint")
    entries = []
    for family, target in (
        ("technical-solution", settings.PRODUCT_TECHNICAL_TARGET_CHARACTERS),
        ("feasibility", settings.PRODUCT_FEASIBILITY_TARGET_CHARACTERS),
    ):
        chapter_length = (target + 2) // 3
        for chapter in blueprint.payload["chapters"]:
            remaining = chapter_length
            paragraphs = []
            segment_number = 1
            while remaining:
                segment_length = min(9000, remaining)
                paragraphs.append(long_paragraph(family, chapter["title"], segment_length, segment_number))
                remaining -= segment_length
                segment_number += 1
            payload = {
                "chapter_id": chapter["id"],
                "title": chapter["title"],
                "paragraphs": paragraphs,
                "source_ids": ["1"],
            }
            payload = validate_chapter(payload)
            revision = append_revision(
                task, "chapter", payload,
                input_hash=input_revision.sha256,
                blueprint_hash=blueprint.sha256,
                actor=owner,
                family=family,
                reason="preproduction_verified_synthetic_chapter",
            )
            entries.append({"family": family, "chapter_id": chapter["id"], "sha256": revision.sha256,
                            "characters": sum(len(value) for value in payload["paragraphs"])})
    task.refresh_from_db()
    task.state = DocumentTask.State.QUEUED
    task.stage = DocumentTask.Stage.WRITING
    task.pending_action = "generate_outputs"
    task.error_code = ""
    task.lease_until = None
    task.version += 1
    task.save(update_fields=["state", "stage", "pending_action", "error_code", "lease_until", "version", "updated_at"])
    return task, entries


def download_and_hash(client, artifacts, storage: Path, run_directory: Path) -> list[dict]:
    downloads = run_directory / "downloads"
    result = []
    for artifact in artifacts:
        source = (storage / artifact.path).resolve()
        if not source.is_relative_to(storage.resolve()) or not source.is_file():
            raise RuntimeError(f"artifact path invalid: {artifact.path}")
        source_hash = sha256(source)
        url = (
            f"/api/product/artifacts/{artifact.pk}/download/"
            if artifact.family == "technical-solution"
            else f"/api/product/outputs/{artifact.pk}/download/"
        )
        response = client.get(url)
        if response.status_code != 200:
            raise RuntimeError(f"download {artifact.family} {url}: {response.status_code} {response.content!r}")
        content = b"".join(response.streaming_content)
        target = downloads / f"{artifact.family}-v{artifact.version}{source.suffix}"
        target.write_bytes(content)
        downloaded_hash = hashlib.sha256(content).hexdigest()
        result.append({
            "id": str(artifact.pk),
            "family": artifact.family,
            "version": artifact.version,
            "storage_path": str(source.relative_to(run_directory).as_posix()),
            "download_path": str(target.relative_to(run_directory).as_posix()),
            "database_sha256": artifact.sha256,
            "storage_sha256": source_hash,
            "download_sha256": downloaded_hash,
            "hash_match": artifact.sha256 == source_hash == downloaded_hash,
            "http_status": response.status_code,
            "download_url": url,
            "office_render": artifact.render_evidence.get("office_render"),
            "word_field_cache": artifact.render_evidence.get("word_field_cache"),
        })
    return result


def run_acceptance(run_directory: Path, config: dict) -> dict:
    from django.conf import settings
    from django.test import Client
    from portal.models import Role, User
    from portal.product_models import DocumentArtifact, DocumentAttempt, DocumentTask
    from portal.product_service import current_revision
    import portal.model_gateway as model_gateway
    import portal.product_worker as product_worker

    password = "Preproduction!Document9274-Qx"
    owner = User.objects.create_user(
        username="preproduction-document-owner", password=password, must_change_password=False,
    )
    owner.roles.add(Role.objects.get(code="product"))
    client = Client()
    login(client, owner, password)
    task = create_task(client, run_directory)
    task = approve_blueprint(client, task)
    task, chapter_entries = seed_verified_chapters(task["id"], owner)

    review_calls = []

    def forbidden_model(*args, **kwargs):
        raise AssertionError("network model call is forbidden in document acceptance")

    def review_stub(task_id, fence, attempt_id, route, payload):
        if payload.get("action") != "independent_review":
            raise AssertionError(f"unexpected model action: {payload.get('action')}")
        if route != settings.PRODUCT_REVIEW_ROUTE:
            raise AssertionError(f"unexpected review route: {route}")
        review_calls.append({"route": route, "family": payload.get("family"),
                             "scope": payload.get("review_scope", "full"), "mocked": True})
        return {"passed": True, "issues": []}

    with patch.object(product_worker, "_model", side_effect=review_stub), \
         patch.object(product_worker, "generate_for_use", side_effect=forbidden_model), \
         patch.object(model_gateway, "generate_for_use", side_effect=forbidden_model):
        worker_claimed = product_worker.run_once()

    task.refresh_from_db()
    artifacts = list(DocumentArtifact.objects.filter(task=task).order_by("family"))
    if not worker_claimed or task.state != DocumentTask.State.COMPLETED:
        raise RuntimeError(f"worker did not complete: claimed={worker_claimed}, state={task.state}, error={task.error_code}")
    if len(artifacts) != 3 or {artifact.family for artifact in artifacts} != {"technical-solution", "feasibility", "presentation"}:
        raise RuntimeError("worker did not create exactly one three-piece artifact set")
    if len(review_calls) != 2 or {call["family"] for call in review_calls} != {"technical-solution", "feasibility"}:
        raise RuntimeError(f"review mock boundary mismatch: {review_calls!r}")

    downloads = download_and_hash(client, artifacts, Path(settings.PRODUCT_STORAGE_ROOT), run_directory)
    paths = {item["family"]: run_directory / item["download_path"] for item in downloads}
    verification_output = run_directory / "deliverable-verification.json"
    verifier = subprocess.run(
        [
            config["document_runtime"], "-B", str(ROOT / "qa" / "verify_product_deliverables.py"),
            "--technical", str(paths["technical-solution"]),
            "--feasibility", str(paths["feasibility"]),
            "--presentation", str(paths["presentation"]),
            "--output", str(verification_output),
        ],
        capture_output=True,
        text=True,
        timeout=config["render_timeout_seconds"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if verifier.returncode not in (0, 1):
        raise RuntimeError(f"deliverable verifier failed: {verifier.returncode}: {verifier.stderr[-2000:]}")
    if not verification_output.is_file():
        raise RuntimeError("deliverable verifier did not write JSON output")
    verification = json.loads(verification_output.read_text(encoding="utf-8"))
    word_statuses = [item["office_render"] for item in downloads if item["family"] in {"technical-solution", "feasibility"}]
    word_office_status = "completed" if word_statuses == ["completed", "completed"] else (
        "notrun" if not config["word_office_render_enabled"] else "failed"
    )
    attempts = list(DocumentAttempt.objects.filter(task=task).values("action", "status", "model_calls", "error_code"))
    return {
        "schema": "PREPRODUCTION_DOCUMENT_ACCEPTANCE_V1",
        "passed": all(item["hash_match"] for item in downloads)
        and all(verification[family]["passed"] for family in ("technical_solution", "feasibility", "presentation"))
        and verifier.returncode == 0
        and verification.get("passed") is True
        and word_office_status == "completed",
        "scope": "one synthetic task with technical solution, feasibility report, and presentation",
        "synthetic_input": True,
        "external_model_calls": "forbidden",
        "ragflow_calls": "forbidden by source_only_preview configuration",
        "review_mock": {"boundary": "portal.product_worker._model only; independent_review only",
                        "calls": review_calls, "full_document_quality_claim": False},
        "worker": {"claimed": worker_claimed, "state": task.state, "stage": task.stage,
                    "attempts": attempts, "preseeded_chapters": chapter_entries},
        "downloads": downloads,
        "ooXML_verification": verification,
        "ooXML_verifier": {"python": config["document_runtime"], "returncode": verifier.returncode,
                           "output": str(verification_output)},
        "word_office_render": {"status": word_office_status, "ppt_office_render": "not_run_by_scope",
                               "configured": config["word_office_render_enabled"],
                               "preflight": config["word_office_probe"],
                               "timeout_seconds": config["render_timeout_seconds"]},
        "isolation": config,
        "paths": {"run_directory": str(run_directory), "report": str(run_directory / "acceptance-result.json")},
    }


def write_result(run_directory: Path, result: dict) -> None:
    (run_directory / "acceptance-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not 30 <= args.timeout <= 1200:
        parser.error("--timeout must be between 30 and 1200 seconds")
    run_directory = new_run_directory()
    runtime = ROOT / ".runtime" / "product-documents-python" / "Scripts" / "python.exe"
    office, office_reason = office_probe(runtime, args.timeout)
    config = configure_environment(run_directory, args.timeout, office)
    config["word_office_probe"] = office_reason
    result = {"schema": "PREPRODUCTION_DOCUMENT_ACCEPTANCE_V1", "passed": False,
              "paths": {"run_directory": str(run_directory), "report": str(run_directory / "acceptance-result.json")}}
    try:
        setup_django(run_directory)
        result = run_acceptance(run_directory, config)
    except Exception as error:
        result.update({"error": f"{type(error).__name__}: {error}",
                       "traceback": traceback.format_exc(), "isolation": config})
    write_result(run_directory, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
