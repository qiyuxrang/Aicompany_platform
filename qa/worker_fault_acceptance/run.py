"""Real owned Windows Worker process crashes; actual business leases are never edited."""
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import secrets
import sys
import time
import traceback
import uuid

from .contracts import ROOT, RUNTIME, expiry_valid, hr_lease_contract, require, source_manifest, validate_options, write_json


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--postgres-bin", required=True)
    p.add_argument("--deadline", type=float, default=1200)
    p.add_argument("--phase-timeout", type=float, default=420)
    p.add_argument("--max-fixture-disk-mb", type=float, default=1024)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    validate_options(args)
    require(os.name == "nt", "owned_windows_job_required")
    from qa.run_portable_postgres import PortablePostgres
    from qa.release_pipeline.identity import identity_alive
    from .fixtures import Fixtures
    from .gateway import Gateway
    from .process import Worker, resources
    token = uuid.uuid4().hex
    directory = RUNTIME / token
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {"fixture_id": token, "result": "FAIL", "production_ready": False,
        "source_before": source_manifest(), "cases": [], "workers": [],
        "options": {"deadline": args.deadline, "phase_timeout": args.phase_timeout, "max_fixture_disk_mb": args.max_fixture_disk_mb},
        "model_mode": "authenticated_deterministic_loopback_mockAI",
        "postgres_fault": {"result": "NOT_EXECUTED", "scope": "Worker crashes only; PG is kept alive, no stop/restart was performed"},
        "coverage_limits": ["No Native license/Runtime/AG15/16", "No actual model quality or paid-model calls",
            "Product source-only blueprint preview, not 50k/70k formal documents", "No upstream exactly-once/charging guarantee",
            "No PG crash/restart/PITR/Redis/full-stack/cloud recovery proof"],
        "dependencies": {name: importlib.metadata.version(name) for name in ("Django", "psycopg", "langgraph", "httpx")}}
    workers, control, pg, gateway = [], None, None, None
    disk = {"last_scan": 0, "max_bytes": 0, "scan_count": 0}
    resource_file = (directory / "resources.jsonl").open("x", encoding="utf-8")
    observed = {"last": 0, "samples": 0, "worker_rss_max_bytes": 0,
                "scope": "owned OS Worker/control processes; driver CPU separately; never Web/cloud metrics"}
    def guard():
        require(time.monotonic() - started <= args.deadline, "overall_deadline_exceeded")
        if time.monotonic() - disk["last_scan"] >= 10:
            size = 0
            for path in directory.rglob("*"):
                try:
                    if path.is_file():
                        size += path.stat().st_size
                except FileNotFoundError:
                    pass
            disk.update(last_scan=time.monotonic(), max_bytes=max(size, disk["max_bytes"]), scan_count=disk["scan_count"]+1)
            require(size <= args.max_fixture_disk_mb*1024**2, "fixture_disk_budget_exceeded")
        if control is not None:
            require(control.alive(), "unrelated_control_process_was_lost")
        if time.monotonic() - observed["last"] >= 1:
            rows = []
            for worker in workers:
                if worker.cleanup is None:
                    require(len(worker.job.members()) <= 16, "owned_worker_subtree_budget")
                    if worker.alive():
                        try:
                            value = resources(worker.identity)
                            rows.append({"label": worker.label, **value})
                            observed["worker_rss_max_bytes"] = max(value["rss_bytes"], observed["worker_rss_max_bytes"])
                        except (OSError, ValueError) as error:
                            rows.append({"label": worker.label, "resource_error_class": type(error).__name__})
            resource_file.write(json.dumps({"wall_time": time.time(), "elapsed_seconds": time.monotonic()-started,
                "driver_cpu_seconds": time.process_time(), "processes": rows}) + "\n")
            resource_file.flush()
            observed.update(last=time.monotonic(), samples=observed["samples"]+1)
    def launch(kind, label, *, once):
        guard()
        worker = Worker(directory, token, kind, label, child_env, once=once)
        workers.append(worker)
        report["workers"].append({"label": label, "kind": kind, "once": once,
            "launcher_identity": worker.launcher_identity, "worker_identity": worker.identity,
            "job_name": worker.job.name})
        return worker
    def await_condition(predicate, timeout):
        end = time.monotonic() + timeout
        while True:
            guard()
            if predicate():
                return
            require(time.monotonic() < end, "bounded_condition_timeout")
            time.sleep(.25)
    def survivor_check(gateway):
        require(control.alive(), "control_died_during_worker_crash")
        require(pg.process.poll() is None and gateway.thread.is_alive(), "pg_or_gateway_died_during_worker_crash")
        with pg.connect() as probe:
            require(probe.execute("SELECT 1").fetchone() == (1,), "owned_pg_not_responsive")
        import httpx
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(gateway.url + "/__fault__/health", headers={"Authorization": "Bearer " + gateway.token})
            require(response.status_code == 200 and response.json() == {"synthetic": True, "status": "ready"}, "owned_gateway_not_responsive")
        return {"control_alive": True, "postgres_alive_and_sql": True, "gateway_alive_authenticated_http": True}
    try:
        with PortablePostgres(Path(args.postgres_bin), runtime_root=directory / "postgres") as pg:
            pg.migrate()
            with Gateway(secrets.token_urlsafe(48)) as gateway:
                child_env = pg.env.copy()
                child_env.update(PYTHONPATH=str(ROOT / "backend") + os.pathsep + str(ROOT), PYTHONUNBUFFERED="1",
                    PORTAL_MODEL_GATEWAY_URL=gateway.url, PORTAL_MODEL_GATEWAY_TOKEN=gateway.token,
                    PORTAL_MODEL_GATEWAY_ALLOWED_URLS=gateway.url, PORTAL_PRODUCT_P1_ENABLED="1",
                    PORTAL_PRODUCT_MODEL_CALLS_ALLOWED="1", PORTAL_PRODUCT_BLUEPRINT_KNOWLEDGE_MODE="source_only_preview",
                    PORTAL_AGENT_ENABLED="0", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
                os.environ.clear()
                os.environ.update(child_env)
                sys.path.insert(0, str(ROOT / "backend"))
                import django
                django.setup()
                from django.db import connections
                from django.utils import timezone
                from portal.model_config import ModelCallLog
                from portal.product_models import DocumentAttempt
                from django.conf import settings
                from config.database import postgres_pool_evidence
                require(settings.PRODUCT_LEASE_SECONDS == 180, "product_lease_contract_changed")
                report["hr_lease_contract"] = hr_lease_contract((ROOT / "backend/portal/hr_screening_worker.py").read_text(encoding="utf-8"))
                fixtures = Fixtures(token, gateway)
                report["driver_postgres_pool"] = postgres_pool_evidence(connections["default"])
                control = Worker(directory, token, "control", "unrelated-control", child_env, once=False)
                workers.append(control)
                report["control"] = {"identity": control.identity, "job_name": control.job.name}
                for kind in ("hr", "product"):
                    # Actual OS process death while a real authenticated model HTTP call waits.
                    row = fixtures.create(kind, kind + "-precommit-" + token)
                    case = {"name": kind + "-before-commit", "result": "FAIL"}
                    report["cases"].append(case)
                    gateway.arm("hr_resume_parse" if kind == "hr" else "product_blueprint")
                    worker = launch(kind, kind + "-precommit-crash", once=False)
                    await_condition(gateway.entered.is_set, 20)
                    row.refresh_from_db()
                    old_fence, lease = row.fence, row.lease_until
                    killed_wall = timezone.now()
                    require(lease is not None and lease > killed_wall, "live_business_lease_required_at_crash")
                    require((row.processing_status if kind == "hr" else row.state) == ("running" if kind == "hr" else "RUNNING"), "worker_not_running_before_commit")
                    if kind == "product":
                        require(not row.revisions.filter(kind="blueprint").exists(), "blueprint_committed_before_fault")
                    else:
                        require(not row.match, "hr_score_committed_before_fault")
                    case["process_resources_at_crash"] = resources(worker.identity)
                    case["crash_cleanup"] = worker.crash()
                    require(case["crash_cleanup"].get("verified") is True, "crashed_worker_tree_not_empty")
                    gateway.release.set()
                    case["survivors_after_crash"] = survivor_check(gateway)
                    before_wait = time.monotonic()
                    def expired():
                        row.refresh_from_db()
                        require(row.lease_until == lease and row.fence == old_fence, "dead_worker_lease_or_fence_changed")
                        return expiry_valid(lease, timezone.now(), elapsed_seconds=time.monotonic()-before_wait)
                    await_condition(expired, args.phase_timeout)
                    case["lease_wait"] = {"lease_until": lease.isoformat(), "crashed_at": killed_wall.isoformat(),
                        "expired_observed_at": timezone.now().isoformat(), "actual_wait_seconds": time.monotonic()-before_wait,
                        "actual_remaining_lease_seconds_at_crash": (lease-killed_wall).total_seconds(),
                        "injected_expiry": False, "expected_production_seconds": 300 if kind == "hr" else 180}
                    gateway.arm("hr_resume_parse" if kind == "hr" else "product_blueprint")
                    restarted = launch(kind, kind + "-precommit-restart", once=True)
                    await_condition(gateway.entered.is_set, 20)
                    row.refresh_from_db()
                    require(row.fence > old_fence and row.lease_until > timezone.now(), "restart_did_not_reclaim_live_lease")
                    require((row.processing_status if kind == "hr" else row.state) == ("running" if kind == "hr" else "RUNNING"), "new_holder_not_running_for_fence_probe")
                    reclaimed_fence = row.fence
                    before = fixtures.snapshot(kind, row)
                    require(fixtures.reject_stale(kind, row, old_fence), "old_fence_commit_was_not_rejected")
                    require(fixtures.snapshot(kind, row) == before, "stale_fence_changed_business_side_effects")
                    gateway.release.set()
                    case["restart_cleanup"] = restarted.wait(90)
                    require(case["restart_cleanup"].get("verified") is True and fixtures.committed(kind, row), "restart_did_not_semantically_complete")
                    case.update(old_fence=old_fence, new_fence=reclaimed_fence, stale_holder_rejected=True,
                                stale_probe_scope="new holder RUNNING with unexpired real lease, before its model response",
                                business_snapshot=fixtures.snapshot(kind, row), result="PASS")
                    # The genuine long-poll management command remains alive after the DB commit.
                    committed = fixtures.create(kind, kind + "-postcommit-" + token)
                    after_case = {"name": kind + "-after-commit", "result": "FAIL"}
                    report["cases"].append(after_case)
                    live = launch(kind, kind + "-postcommit-crash", once=False)
                    await_condition(lambda: fixtures.committed(kind, committed), 90)
                    require(live.alive(), "long_poll_worker_not_live_after_commit")
                    snapshot = fixtures.snapshot(kind, committed)
                    calls_before = ModelCallLog.objects.count()
                    after_case["process_resources_at_crash"] = resources(live.identity)
                    after_case["crash_cleanup"] = live.crash()
                    require(after_case["crash_cleanup"].get("verified") is True, "postcommit_worker_tree_not_empty")
                    after_case["survivors_after_crash"] = survivor_check(gateway)
                    empty = launch(kind, kind + "-postcommit-empty-restart", once=True)
                    after_case["restart_cleanup"] = empty.wait(60)
                    require(after_case["restart_cleanup"].get("verified") is True, "empty_restart_cleanup_failed")
                    require(fixtures.snapshot(kind, committed) == snapshot and ModelCallLog.objects.count() == calls_before,
                            "duplicate_committed_business_or_model_poll_side_effect")
                    after_case.update(business_snapshot=snapshot, no_duplicate_after_restart=True, result="PASS")
                guard()
                require(not gateway.errors and gateway.rejected == 0, "unexpected_mock_gateway_error")
                report["gateway"] = {"calls": dict(gateway.calls), "disconnects_after_owned_crash": gateway.disconnected,
                    "upstream_exactly_once_claim": False, "forwarded_requests": 0}
                report["model_call_logs"] = dict(Counter(ModelCallLog.objects.values_list("status", flat=True)))
                report["worker_postgres_pools"] = []
                for worker in workers:
                    if worker is control:
                        continue
                    proof = json.loads((directory / (worker.label + "-pool.json")).read_text(encoding="utf-8"))
                    require(proof.get("fixture_id") == token and proof.get("label") == worker.label
                            and proof.get("postgres_pool", {}).get("enabled") is True, "actual_worker_pool_evidence_missing")
                    report["worker_postgres_pools"].append(proof)
                report["result"] = "PASS"
                connections.close_all()
            report["gateway_shutdown_verified"] = not gateway.thread.is_alive()
            control.process.stdin.write("stop\n")
            control.process.stdin.flush()
            control.process.wait(10)
            control.close()
    except BaseException as error:
        report.update(result="FAIL", error_class=type(error).__name__,
            failure_frames=[{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                            for frame in traceback.extract_tb(error.__traceback__)[-8:]])
    finally:
        report["gateway_shutdown_verified"] = gateway is not None and not gateway.thread.is_alive() and gateway.active_handlers == 0
        if gateway is not None and (gateway.errors or gateway.rejected):
            report["result"] = "FAIL"
        cleanup = []
        for worker in workers:
            try:
                clean = worker.close()
            except Exception as error:
                clean = {"verified": False, "error_class": type(error).__name__}
            cleanup.append({"label": worker.label, **clean})
        report["cleanup"] = {"workers": cleanup, "all_worker_jobs_empty": all(v.get("verified") is True for v in cleanup),
            "postgres": pg.evidence.get("cleanup", {}) if pg else {}, "evidence_retained": True, "foreign_processes_touched": False}
        if not cleanup or not report["cleanup"]["all_worker_jobs_empty"] or not report["cleanup"]["postgres"].get("verified") or not report.get("gateway_shutdown_verified"):
            report["result"] = "FAIL"
        resource_file.close()
        report["resource_evidence"] = observed
        report["disk_budget"] = disk
        report["elapsed_seconds_including_real_lease_wait"] = time.monotonic()-started
        report["source_after"] = source_manifest()
        if report["source_before"] != report["source_after"]:
            report.update(result="FAIL", source_changed=True)
        write_json(directory / "report.json", report)
    print(json.dumps({"result": report["result"], "report": str(directory / "report.json"), "production_ready": False}))
    return 0 if report["result"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
