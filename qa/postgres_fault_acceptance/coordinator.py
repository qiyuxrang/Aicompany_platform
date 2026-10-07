"""Self-join before importing/starting PG; all business inputs are synthetic."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
import traceback
import uuid

from .contracts import gate_valid, require, write_json, durability_valid, rollback_valid, redo_valid

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    directory = Path(args.run_dir).resolve()
    line = sys.stdin.readline(65537)
    require(len(line) <= 65536 and line.endswith("\n"), "bounded_stdin_gate_required")
    gate = gate_valid(json.loads(line), directory, "coordinator")
    from qa.browser_acceptance.process_job import join_owned_job, port_listener_identity
    join_owned_job(gate["job_name"])
    from .contracts import validate_budgets
    validate_budgets(gate["deadline"], gate["disk_mb"])
    from qa.release_pipeline.identity import process_identity
    write_json(directory / "coordinator-ready.json", {"fixture_id": gate["fixture_id"], "job_name": gate["job_name"],
        "identity": process_identity(os.getpid())})
    # These imports and every PG/Web child occur strictly after the outer Job join.
    from .process import Child, Membership
    from .postgres import CrashPostgres
    import httpx
    from psycopg import sql
    from psycopg.types.json import Jsonb
    started = time.monotonic()
    membership = Membership(gate["job_name"])
    report = {"result": "FAIL", "fixture_id": gate["fixture_id"], "production_ready": False,
        "scope": "synthetic Portal Windows PostgreSQL immediate crash and same-cluster WAL recovery",
        "model_calls": 0, "external_requests": 0,
        "not_verified": ["Native Runtime/AG15/16/Redis", "PITR/backup restore/offsite recovery", "power loss/storage-device durability",
            "Linux/cloud topology/capacity", "real models/real business quality/Office formal delivery"]}
    web = pg = pending = None
    clients = []
    events = (directory / "events.jsonl").open("x", encoding="utf-8")
    disk = {"max_bytes": 0, "last_scan": 0, "scans": 0, "scan_wall_seconds": 0}
    def event(phase):
        events.write(json.dumps({"phase": phase, "wall_time": time.time(), "elapsed_seconds": time.monotonic()-started}) + "\n")
        events.flush()
    def guard():
        require(time.monotonic()-started <= gate["deadline"], "overall_deadline_exceeded")
        require(len(membership.members()) <= 96, "owned_subtree_budget_exceeded")
        if web is not None:
            require(web.alive(), "normal_web_died_during_pg_outage")
        if time.monotonic()-disk["last_scan"] >= 5:
            scan = time.monotonic()
            total = 0
            for path in directory.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except FileNotFoundError:
                    pass
            disk.update(max_bytes=max(disk["max_bytes"], total), last_scan=time.monotonic(), scans=disk["scans"]+1,
                        scan_wall_seconds=disk["scan_wall_seconds"]+time.monotonic()-scan)
            require(total <= gate["disk_mb"]*1024**2, "private_fixture_disk_budget_exceeded")
    def request(client, method, path, *, expected=200, payload=None):
        guard()
        headers = {}
        if method != "GET":
            csrf = client.get("/api/csrf/")
            require(csrf.status_code == 200 and isinstance(csrf.json().get("csrfToken"), str), "actual_csrf_failed")
            headers["X-CSRFToken"] = csrf.json()["csrfToken"]
        response = client.request(method, path, headers=headers, json=payload) if payload is not None else client.request(method, path, headers=headers)
        require(response.status_code == expected, "normal_http_business_status_failed")
        return response.json()
    def login(client, username, password):
        data = request(client, "POST", "/api/login/", payload={"username": username, "password": password})
        require(data.get("username") == username, "real_login_identity_failed")
        require(request(client, "GET", "/api/me/").get("username") == username, "real_session_identity_failed")
    def durability():
        with pg.connect(pg.database_name) as connection:
            values = {key: connection.execute(sql.SQL("SHOW {}").format(sql.Identifier(key))).fetchone()[0]
                      for key in ("fsync", "full_page_writes", "synchronous_commit")}
            values["in_recovery"] = connection.execute("SELECT pg_is_in_recovery()").fetchone()[0]
            values["system_identifier"] = str(connection.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
            values["checkpoint_lsn"] = str(connection.execute("SELECT checkpoint_lsn FROM pg_control_checkpoint()").fetchone()[0])
            values["wal_flush_lsn"] = str(connection.execute("SELECT pg_current_wal_flush_lsn()").fetchone()[0])
        durability_valid(values)
        return values
    def snapshot(connection, table, pk):
        raw = connection.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t WHERE id=%s").format(sql.Identifier(table)), [pk]).fetchone()
        require(raw is not None, "committed_business_row_missing")
        value = json.loads(raw[0])
        return {"sha256": hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "input_version": value["input_version"]}
    try:
        event("owned_pg_initialize")
        with CrashPostgres(Path(gate["postgres_bin"]), runtime_root=directory / "postgres", membership=membership) as pg:
            pg.migrate()
            os.environ.clear()
            os.environ.update(pg.env)
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
            import django
            django.setup()
            from django.contrib.auth.password_validation import validate_password
            from django.core.management import call_command
            from django.db import connections
            from portal.models import User, Role
            from portal.hr_recruitment_models import RecruitmentRequest
            from portal.model_config import ModelCallLog
            from config.database import postgres_pool_evidence
            call_command("seed_portal", verbosity=0)
            password = secrets.token_urlsafe(32)
            user = User(username="synthetic-pg-fault", department_code="hr", must_change_password=False)
            validate_password(password, user)
            user.set_password(password)
            require(user.password.startswith("pbkdf2_sha256$"), "normal_pbkdf2_required")
            user.save()
            user.roles.add(Role.objects.get(code="hr"))
            pg._sensitive.append(password)
            web = Child("qa.postgres_fault_acceptance.web", directory, gate["fixture_id"], "web", pg.env)
            ready = json.loads((directory / "web-ready.json").read_text(encoding="utf-8"))
            require(port_listener_identity(ready["port"], web.job.members())["owned"], "normal_web_listener_not_owned")
            base = "http://127.0.0.1:" + str(ready["port"])
            existing = httpx.Client(base_url=base, trust_env=False, timeout=10)
            clients.append(existing)
            login(existing, user.username, password)
            with pg.connect(pg.database_name) as connection:
                connection.execute("CHECKPOINT")
            event("committed_business_created_after_checkpoint")
            baseline = request(existing, "POST", "/api/hr/recruitment/requests/", expected=201,
                               payload={"position_name": "合成已提交岗位", "headcount": 1, "skill_requirements": ["SQL"]})
            committed_id, pending_id = uuid.UUID(baseline["id"]), uuid.uuid4()
            table = RecruitmentRequest._meta.db_table
            with pg.connect(pg.database_name) as connection:
                before = snapshot(connection, table, committed_id)
            file = pg.run_dir / "storage/hr/synthetic-durability.txt"
            file.write_bytes(b"synthetic private immutable fixture; not real employee input\n")
            file_hash = hashlib.sha256(file.read_bytes()).hexdigest()
            report["durability_before"] = durability()
            report["web_before"] = ready
            report["driver_pool_before"] = postgres_pool_evidence(connections["default"])
            pending = pg.connect(pg.database_name)
            pending.execute("BEGIN")
            pending.execute("SET LOCAL lock_timeout = '5s'")
            pending.execute("SET LOCAL idle_in_transaction_session_timeout = '120s'")
            cursor = pending.execute(sql.SQL("SELECT * FROM {} WHERE id=%s").format(sql.Identifier(table)), [committed_id])
            names = [column.name for column in cursor.description]
            values = list(cursor.fetchone())
            for index, column in enumerate(cursor.description):
                if column.type_code in (114, 3802):
                    values[index] = Jsonb(values[index])
            values[names.index("id")] = pending_id
            values[names.index("position_name")] = "合成未提交岗位"
            pending.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, names)), sql.SQL(",").join(sql.Placeholder() for _ in names)), values)
            pending.execute(sql.SQL("UPDATE {} SET position_name=%s,input_version=input_version+1 WHERE id=%s").format(sql.Identifier(table)),
                            ["合成未提交修改", committed_id])
            report["uncommitted_transaction"] = {"backend_pid": pending.execute("SELECT pg_backend_pid()").fetchone()[0],
                "real_begin_insert_update": True, "commit_sent": False}
            with pg.connect(pg.database_name) as connection:
                rollback_valid(before, snapshot(connection, table, committed_id),
                    connection.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {} WHERE id=%s)").format(sql.Identifier(table)), [pending_id]).fetchone()[0])
            event("real_transaction_pending_then_immediate_crash")
            def on_stopped():
                guard()
                start = time.monotonic()
                event("controlled_outage_session_http_begin")
                try:
                    response = existing.get("/api/me/")
                    require(response.status_code in (500, 503), "unexpected_outage_http_contract")
                    outcome = {"http_status": response.status_code}
                except httpx.TransportError as error:
                    outcome = {"error_class": type(error).__name__, "http_status": None}
                report["controlled_outage_http"] = {**outcome, "expected_unavailability": True,
                    "seconds": time.monotonic()-start, "request_timeout_seconds": 10,
                    "scope": "one intentionally failed request while verified owned PG is stopped; no response body retained"}
                event("controlled_outage_session_http_end")
                require(web.alive(), "normal_web_died_while_pg_stopped")
            report["crash_restart"], segment = pg.crash_restart(guard, on_stopped)
            report["wal_redo"] = redo_valid(segment)
            event("wal_redo_complete_verify_transaction")
            with pg.connect(pg.database_name) as connection:
                after = snapshot(connection, table, committed_id)
                remains = connection.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {} WHERE id=%s)").format(sql.Identifier(table)), [pending_id]).fetchone()[0]
            rollback_valid(before, after, remains)
            require(hashlib.sha256(file.read_bytes()).hexdigest() == file_hash, "private_fixture_file_hash_changed")
            report["transaction_recovery"] = {"before": before, "after": after, "uncommitted_row_exists": remains,
                "private_file_sha256": file_hash, "sequence_equality_required": False}
            report["durability_after"] = durability()
            require(report["durability_before"]["system_identifier"] == report["durability_after"]["system_identifier"], "restarted_cluster_identity_changed")
            connections.close_all()  # driver intentionally uses a fresh ORM handle; surviving Web is not reset/restarted.
            require(RecruitmentRequest.objects.get(pk=committed_id).input_version == before["input_version"], "normal_orm_recovery_failed")
            require(web.alive() and web.identity == ready["identity"], "normal_web_generation_changed")
            event("healthy_existing_session_http_begin")
            require(request(existing, "GET", "/api/me/").get("username") == user.username, "old_session_not_preserved_after_recovery")
            event("healthy_existing_session_http_end")
            event("healthy_business_read_http_begin")
            require(request(existing, "GET", f"/api/hr/recruitment/requests/{committed_id}/")["input_version"] == before["input_version"], "recovered_http_read_failed")
            event("healthy_business_read_http_end")
            fresh = httpx.Client(base_url=base, trust_env=False, timeout=10)
            clients.append(fresh)
            event("healthy_new_login_http_begin")
            login(fresh, user.username, password)
            event("healthy_new_login_http_end")
            event("healthy_business_write_http_begin")
            changed = request(fresh, "PATCH", f"/api/hr/recruitment/requests/{committed_id}/", payload={
                "expected_version": before["input_version"], "position_name": "合成恢复后已提交更新"})
            require(changed["input_version"] == before["input_version"]+1, "recovered_http_write_version_failed")
            require(request(fresh, "GET", f"/api/hr/recruitment/requests/{committed_id}/")["input_version"] == changed["input_version"], "recovered_write_not_readable")
            event("healthy_business_write_http_end")
            report["http_recovery"] = {"same_web_pid_creation_and_job": True, "normal_csrf_login": True,
                "existing_session_survived": True, "new_pbkdf2_login": True, "business_read": True,
                "versioned_write_and_read": True, "blind_retries": 0, "request_timeout_seconds": 10}
            report["driver_pool_after"] = postgres_pool_evidence(connections["default"])
            require(ModelCallLog.objects.count() == 0, "unexpected_model_call_log")
            report["model_call_log_count"] = 0
            report["pg_generations"] = pg.generations
            guard()
            report["result"] = "PASS"
            connections.close_all()
            for client in clients:
                client.close()
            clients.clear()
            report["web_cleanup"] = web.close()
            event("normal_web_closed_before_pg_final_cleanup")
    except BaseException as error:
        frames = traceback.extract_tb(error.__traceback__)
        selected = frames if len(frames) <= 16 else frames[:4]+frames[-12:]
        report.update(result="FAIL", error_class=type(error).__name__, failure_frames=[
            {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
            for frame in selected], failure_frames_truncated=len(frames) > 16)
        if isinstance(error, ValueError) and str(error).replace("_", "").isalnum() and len(str(error)) <= 100:
            report["failure_code"] = str(error)
    finally:
        for client in clients:
            client.close()
        if pending is not None:
            pending.close()
        if web is not None:
            try:
                report["web_cleanup"] = web.close()
            except Exception as error:
                report["web_cleanup"] = {"verified": False, "error_class": type(error).__name__}
        report["postgres_cleanup"] = pg.evidence.get("cleanup", {}) if pg else {}
        summary_path = directory / "web-observer-summary.json"
        try:
            report["web_diagnostics"] = json.loads(summary_path.read_text(encoding="utf-8"))
            if report["web_diagnostics"].get("errors") or not report["web_diagnostics"].get("samples") or \
                    not 0 <= time.time()-report["web_diagnostics"].get("updated_wall_time", 0) <= 6:
                report["result"] = "FAIL"
        except (OSError, ValueError):
            report["web_diagnostics"] = {"verified": False, "reason": "observer_summary_missing_or_invalid"}
            report["result"] = "FAIL"
        report["disk_budget"] = disk
        report["elapsed_seconds"] = time.monotonic()-started
        if not report.get("web_cleanup", {}).get("verified") or not report["postgres_cleanup"].get("verified"):
            report["result"] = "FAIL"
        events.close()
        membership.close()
        write_json(directory / "coordinator-report.json", report)
    return 0 if report["result"] == "PASS" else 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"result": "FAIL", "error_class": type(error).__name__}), flush=True)
        raise SystemExit(1)
