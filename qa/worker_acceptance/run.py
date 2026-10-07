"""Owned PostgreSQL + actual HR/product Workers + deterministic loopback model gateway."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import importlib.metadata
import io
import json
import logging
import math
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import traceback
import uuid

from qa.release_acceptance.fixture_server import process_memory
from qa.run_portable_postgres import PortablePostgres
from .mock_gateway import MockGateway

ROOT = Path(__file__).resolve().parents[2]


def source_hashes():
    paths = list((ROOT / "qa/worker_acceptance").rglob("*.py")) + [ROOT / "qa/run_portable_postgres.py"]
    paths += list((ROOT / "backend/portal").glob("hr_*.py"))
    paths += list((ROOT / "backend/portal").glob("product_*.py"))
    paths += [ROOT / "qa/release_acceptance/fixture_server.py", ROOT / "backend/config/settings.py",
        ROOT / "backend/portal/model_gateway.py", ROOT / "backend/portal/model_config.py",
        ROOT / "backend/portal/management/commands/run_hr_worker.py",
        ROOT / "backend/portal/management/commands/run_product_worker.py", ROOT / "uv.lock"]
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def validate_args(args):
    if not 3 <= args.hr_tasks <= 100 or not 1 <= args.product_tasks <= 20 or args.hr_concurrency not in (1, 2):
        raise ValueError("bounded HR tasks 3..100, product 1..20, HR concurrency 1/2 required")
    if not all(math.isfinite(value) and value > 0 for value in (args.timeout, args.max_fixture_disk_mb)):
        raise ValueError("positive timeout and disk guard required")


def sample_queue_states(resume_artifacts, document_tasks, close_connections):
    """The observer releases its own checkout before disk sampling or idle waits."""
    try:
        return (Counter(resume_artifacts.values_list("processing_status", flat=True)),
                Counter(document_tasks.values_list("state", flat=True)))
    finally:
        close_connections()


def wait_without_controller_checkout(wait, close_connections, *, timeout):
    """Waiting controllers must not reserve a slot in the real worker pool."""
    close_connections()
    return wait(timeout=timeout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", required=True)
    parser.add_argument("--hr-tasks", type=int, default=10)
    parser.add_argument("--product-tasks", type=int, default=2)
    parser.add_argument("--hr-concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--max-fixture-disk-mb", type=float, default=1024)
    args = parser.parse_args(argv)
    validate_args(args)
    started_total = time.monotonic()
    token = uuid.uuid4().hex
    run_dir = ROOT / ".runtime" / "worker-acceptance" / token
    run_dir.mkdir(parents=True, exist_ok=False)
    report = {"fixture_id": token, "result": "FAIL", "model_mode": "deterministic_loopback_mockAI",
        "database_kind": "owned_portable_postgresql", "source_before": source_hashes(),
        "workload": vars(args) | {"postgres_bin": "explicit_existing_binaries"},
        "dependencies": {name: importlib.metadata.version(name) for name in ("Django", "psycopg", "langgraph", "waitress")},
        "coverage_limits": ["No actual model quality/token measurement", "No AG-15/16 closure or native Runtime",
            "Product source-only blueprint preview, not formal Word/PPT/50k/70k quality", "No cloud deployment proof",
            "Worker management commands execute in this owned Python process, not managed cloud processes",
            "Lease expiration fault is injected in own PG rows; no actual machine/process crash"]}
    failures = []
    pg = None
    try:
        report["stage"] = "isolated_pg_fixture_setup"
        with PortablePostgres(Path(args.postgres_bin), runtime_root=run_dir / "postgres") as pg:
            # Use only this context's freshly-created isolated config, never local.env or business DB.
            for key in list(os.environ):
                if key.startswith(("PORTAL_", "LANGSMITH_", "LANGCHAIN_", "LANGGRAPH_")) or key == "DJANGO_SETTINGS_MODULE":
                    del os.environ[key]
            os.environ.update(pg.env)
            os.environ["LANGSMITH_TRACING"] = "false"
            os.environ["LANGCHAIN_TRACING_V2"] = "false"
            sys.path.insert(0, str(ROOT / "backend"))
            import django
            django.setup()
            from django.conf import settings
            from django.core.management import call_command
            from django.db import close_old_connections, connections
            from django.utils import timezone
            from portal.models import User, Role, Module
            from portal.model_config import Provider, GatewayModel, ModelRoute, ModelCallLog
            from portal.hr_recruitment_models import RecruitmentRequest, JDVersion
            from portal.hr_screening_models import ResumeScreeningBatch, ResumeArtifact
            from portal.hr_resume_storage import save_file
            from portal.hr_screening_worker import claim_one, finish_one
            from portal.hr_screening_results import queue
            from portal.product_models import DocumentTask, DocumentRevision, DocumentArtifact, DocumentAttempt
            from portal.product_service import append_revision, digest
            from portal.product_api import cancel
            from rest_framework.test import APIRequestFactory, force_authenticate
            with io.StringIO() as sink:
                call_command("migrate", interactive=False, stdout=sink, stderr=sink)
                call_command("seed_portal", stdout=sink, stderr=sink)
            logging.getLogger("portal.hr_screening_worker").setLevel(logging.CRITICAL)
            settings.PRODUCT_P1_ENABLED = True
            settings.PRODUCT_MODEL_CALLS_ALLOWED = True
            settings.PRODUCT_BLUEPRINT_KNOWLEDGE_MODE = "source_only_preview"
            settings.AGENT_PLATFORM_ENABLED = False
            hr = User.objects.create_user(username="synthetic-worker-hr", password=secrets.token_urlsafe(32),
                department_code="hr", must_change_password=False)
            hr.roles.set(Role.objects.filter(code="hr"))
            product = User.objects.create_user(username="synthetic-worker-product", password=secrets.token_urlsafe(32),
                department_code="product", must_change_password=False)
            product.roles.set(Role.objects.filter(code="product"))
            request = RecruitmentRequest.objects.create(created_by=hr, updated_by=hr,
                position_name="隔离合成岗位", skill_requirements=["SQL"])
            jd = JDVersion.objects.create(request=request, version=1, input_version=1,
                state="confirmed", body="合成岗位需要SQL", created_by=hr)
            request.current_jd = request.official_jd = jd
            request.save()
            batch = ResumeScreeningBatch.objects.create(jd_version=jd, created_by=hr, input_version=1,
                requirements=request.structured_payload(), idempotency_key=token, status="queued")
            for index in range(args.hr_tasks):
                ResumeArtifact.objects.create(batch=batch, uploaded_by=hr, processing_status="queued",
                    **save_file(f"synthetic-{index}.txt", f"姓名：合成人\n技能：SQL\n样例编号：{index}".encode()))

            def create_product(key):
                payload = {"project": "隔离合成方案", "requirements": "不新增清单外设备", "background": "仅用于隔离压力验证",
                    "items": [{"row_id": "r1", "name": "测试设备", "quantity": "2", "unit": "台"}],
                    "conditions": ["不新增清单外设备"]}
                task = DocumentTask.objects.create(owner=product, title="隔离合成蓝图", idempotency_key=key,
                    payload_hash=digest(payload), state="QUEUED", stage="BLUEPRINT", pending_action="blueprint")
                revision = append_revision(task, "input", payload, actor=product)
                task.input_version = revision.version
                task.save(update_fields=["input_version"])
                return task

            product_tasks = [create_product(f"synthetic-product-{index}") for index in range(args.product_tasks)]
            report["database"] = {"name": pg.database_name, "port": pg.port,
                "version": pg.evidence.get("postgres", {}).get("version")}
            # Inject expired leases; late former holder must be rejected by the real guard.
            old = claim_one()
            if old is None:
                raise RuntimeError("lease_fixture_claim_failed")
            ResumeArtifact.objects.filter(pk=old[0]).update(lease_until=timezone.now() - timedelta(seconds=1))
            newer = claim_one()
            stale_rejected = not finish_one(*old, error="synthetic_late_holder")
            if not newer or newer[0] != old[0] or newer[1] <= old[1] or not stale_rejected:
                failures.append("hr_expired_lease_fence_failure")
            ResumeArtifact.objects.filter(pk=newer[0]).update(lease_until=timezone.now() - timedelta(seconds=1))
            report["lease_fault"] = {"stale_holder_rejected": stale_rejected,
                "same_artifact_reclaimed": newer[0] == old[0], "old_fence": old[1], "new_fence": newer[1]}
            api_factory = APIRequestFactory()

            def api_call(view, actor, data, **kwargs):
                req = api_factory.post("/api/synthetic-worker-validation/", data, format="json")
                force_authenticate(req, user=actor)
                return view(req, **kwargs)

            with MockGateway(secrets.token_urlsafe(48)) as gateway:
                report["stage"] = "loopback_gateway_and_routes"
                settings.MODEL_GATEWAY_URL = gateway.url
                settings.MODEL_GATEWAY_TOKEN = gateway.token
                settings.MODEL_GATEWAY_ALLOWED_URLS = [gateway.url]
                provider = Provider.objects.create(code="release-mock-only", name="Deterministic fixture, never forwarded",
                    base_url="https://synthetic.invalid/v1", api_key_env="PORTAL_MODEL_KEY_RELEASE_SYNTHETIC_NEVER_PROVIDER", enabled=True)
                for code, department in (("hr_resume_parse", "hr"), ("hr_match_summary", "hr"), ("product_blueprint", "product")):
                    model = GatewayModel.objects.create(name="Synthetic " + code, provider=provider,
                        model_name=code, enabled=True, timeout_seconds=15)
                    ModelRoute.objects.update_or_create(code=code, defaults={"name": "Synthetic " + code,
                        "module": Module.objects.get(code=department), "model": model, "enabled": True,
                        "max_calls_per_minute": 120})
                samples = []
                stop_sampler = threading.Event()
                sampler_errors = []
                load_started = time.monotonic()

                def sample():
                    close_old_connections()
                    try:
                        while not stop_sampler.is_set():
                            states, products = sample_queue_states(
                                ResumeArtifact.objects, DocumentTask.objects, connections.close_all)
                            size = 0
                            for path in run_dir.rglob("*"):
                                try:
                                    if path.is_file():
                                        size += path.stat().st_size
                                except FileNotFoundError:
                                    pass
                            samples.append({"elapsed_seconds": time.monotonic() - load_started, "cpu_seconds": time.process_time(),
                                "hr_queue": states["queued"], "hr_running": states["running"], "hr_completed": states["completed"],
                                "hr_failed": states["failed"], "product_queue": products["QUEUED"], "product_running": products["RUNNING"],
                                "fixture_disk_bytes": size, **process_memory()})
                            if size > args.max_fixture_disk_mb * 1024 * 1024:
                                sampler_errors.append("fixture_disk_guard")
                                stop_sampler.set()
                            stop_sampler.wait(.25)
                    except Exception as error:
                        sampler_errors.append(type(error).__name__)
                    finally:
                        connections.close_all()
                sampler = threading.Thread(target=sample, daemon=True)
                # Fixture preparation used ORM in this controller thread. It now waits
                # while independent HR/product command threads use the same bounded pool.
                connections.close_all()
                sampler.start()

                def command_once(name, **options):
                    close_old_connections()
                    try:
                        with io.StringIO() as sink:
                            call_command(name, once=True, stdout=sink, stderr=sink, **options)
                    finally:
                        connections.close_all()

                def hr_drain():
                    while ResumeArtifact.objects.filter(processing_status__in=("queued", "running")).exists():
                        if time.monotonic() - load_started > args.timeout or sampler_errors:
                            raise RuntimeError("bounded_hr_drain_failed")
                        command_once("run_hr_worker", concurrency=args.hr_concurrency)
                    connections.close_all()

                def product_drain():
                    for _ in range(args.product_tasks):
                        if time.monotonic() - load_started > args.timeout or sampler_errors:
                            raise RuntimeError("bounded_product_drain_failed")
                        command_once("run_product_worker")
                try:
                    report["stage"] = "actual_worker_queue_drain"
                    with ThreadPoolExecutor(max_workers=2) as workers:
                        hr_future = workers.submit(hr_drain)
                        product_future = workers.submit(product_drain)
                        wait_without_controller_checkout(
                            hr_future.result, connections.close_all, timeout=args.timeout + 30)
                        wait_without_controller_checkout(
                            product_future.result, connections.close_all, timeout=args.timeout + 30)
                    baseline_elapsed = time.monotonic() - load_started
                    failed_before_retry = list(ResumeArtifact.objects.filter(processing_status="failed").values_list("error_code", flat=True))
                    batch.refresh_from_db()
                    response = api_call(queue, hr, {"expected_version": batch.version}, batch_id=batch.pk, retry=True)
                    if response.status_code != 200 or failed_before_retry != ["timeout"]:
                        failures.append("real_hr_timeout_retry_api_failed")
                    else:
                        hr_drain()
                    drained_elapsed = time.monotonic() - load_started
                finally:
                    stop_sampler.set()
                    wait_without_controller_checkout(sampler.join, connections.close_all, timeout=10)
                report["timing"] = {"preparation_seconds": load_started - started_total,
                    "baseline_worker_seconds": baseline_elapsed, "worker_seconds_including_retry": drained_elapsed}
                report["timeout_retry"] = {"failed_before_retry": dict(Counter(failed_before_retry)),
                    "retry_api_status": response.status_code, "scope": "actual HTTP gateway 504, actual failed batch + retry API"}
                artifacts = list(ResumeArtifact.objects.all())
                batch.refresh_from_db()
                completions = sum(item.processing_status == "completed" for item in artifacts)
                if batch.status != "completed" or completions != args.hr_tasks or \
                        any(item.match.get("score", {}).get("total") != 1 for item in artifacts):
                    failures.append("hr_semantic_completion_failed")
                if any(task.state != "WAITING_REVIEW" for task in DocumentTask.objects.all()):
                    failures.append("product_blueprint_preview_failed")
                blueprint_count = DocumentRevision.objects.filter(kind="blueprint").count()
                if blueprint_count != args.product_tasks or DocumentArtifact.objects.exists():
                    failures.append("duplicate_or_unexpected_product_side_effect")
                # Real late-repeat calls must not produce new calls/revisions/artifact processing.
                before_calls = ModelCallLog.objects.count()
                before_attempts = DocumentAttempt.objects.count()
                command_once("run_hr_worker", concurrency=args.hr_concurrency)
                command_once("run_product_worker")
                if ModelCallLog.objects.count() != before_calls or DocumentAttempt.objects.count() != before_attempts or \
                        DocumentRevision.objects.filter(kind="blueprint").count() != blueprint_count:
                    failures.append("duplicate_side_effect_after_drain")
                report["queue_drain"] = {"hr_completed": completions, "hr_tasks": args.hr_tasks,
                    "hr_batch_state": batch.status,
                    "hr_execution_concurrency": args.hr_concurrency, "product_blueprints": blueprint_count,
                    "no_repeat_model_call_after_empty_poll": ModelCallLog.objects.count() == before_calls,
                    "successful_business_tasks_per_second": (completions + blueprint_count) / drained_elapsed,
                    "samples": samples}
                if args.hr_concurrency == 2 and gateway.max_hr_parse_active < 2:
                    failures.append("actual_hr_model_call_overlap_not_observed")
                if sampler_errors or len(samples) < 2:
                    failures.append("worker_resource_or_queue_samples_failed")
                # Cancellation while the actual product Worker awaits its loopback model response.
                report["stage"] = "actual_product_cancel_inflight"
                canceled = create_product("synthetic-cancel-in-flight")
                gateway.hold_product = True
                with ThreadPoolExecutor(max_workers=1) as worker:
                    future = worker.submit(command_once, "run_product_worker")
                    if not wait_without_controller_checkout(
                            gateway.product_entered.wait, connections.close_all, timeout=20):
                        failures.append("cancel_inflight_point_not_reached")
                    canceled.refresh_from_db()
                    response = api_call(cancel, product, {"expected_version": canceled.version}, task_id=canceled.pk)
                    gateway.product_release.set()
                    wait_without_controller_checkout(future.result, connections.close_all, timeout=30)
                canceled.refresh_from_db()
                late_count = DocumentRevision.objects.filter(task=canceled, kind="blueprint").count()
                if response.status_code != 200 or canceled.state != "CANCELLED" or late_count:
                    failures.append("actual_cancel_late_worker_commit_failed")
                report["cancel_inflight"] = {"api_status": response.status_code, "final_state": canceled.state,
                    "late_blueprints": late_count, "scope": "actual product Worker waits on authenticated synthetic HTTP response"}
                report["mock_gateway"] = {"calls": dict(gateway.calls), "max_active": gateway.max_active,
                    "max_hr_parse_active": gateway.max_hr_parse_active, "rejected": gateway.rejected, "errors": gateway.errors,
                    "never_forwarded": True, "token_usage": "unknown_not_fabricated"}
                report["model_call_logs"] = dict(Counter(ModelCallLog.objects.values_list("status", flat=True)))
                if ModelCallLog.objects.count() != sum(gateway.calls.values()):
                    failures.append("physical_gateway_call_and_audit_count_mismatch")
                if gateway.errors or gateway.rejected:
                    failures.append("mock_transport_fixture_error")
            connections.close_all()
            report["source_after"] = source_hashes()
            if report["source_after"] != report["source_before"]:
                failures.append("source_changed_during_acceptance")
            report["failures"] = failures
            report["result"] = "FAIL" if failures else "PASS"
        report["cleanup"] = {"gateway_stopped": True, "owned_pg_shutdown_verified": pg.evidence["cleanup"]["verified"],
                             "evidence_retained": True, "other_services_touched": False}
    except Exception as error:
        report.update(result="FAIL", error_class=type(error).__name__)
        report["failure_frames"] = [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                                    for frame in traceback.extract_tb(error.__traceback__)]
        if pg:
            report["pg_cleanup_verified"] = pg.evidence.get("cleanup", {}).get("verified", False)
    finally:
        report["total_seconds_including_preparation_and_cleanup"] = time.monotonic() - started_total
        with (run_dir / "report.json").open("x", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps({"result": report["result"], "report": str(run_dir / "report.json"),
                      "scope": "actual existing Workers, synthetic mockAI, isolated PG", "production_ready": False}))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
