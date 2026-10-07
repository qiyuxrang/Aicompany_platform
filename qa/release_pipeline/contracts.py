"""Pure fixed-plan and evidence gates. No service, credential, or network access."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime/release-pipeline"
GUARDS = {"test_parallel_admission_reserves_exact_limit_without_masking_database_errors",
          "test_restored_guard_keeps_unknown_reservation_and_cumulative_limit"}
PLAN = (
    {"name": "renewal-preflight", "mode": "target", "concurrency": 8, "target_rps": 4,
     "duration": 180, "renewal_interval": 60},
    {"name": "step", "mode": "step", "concurrency": 100, "target_rps": 20, "duration": 180,
     "renewal_interval": 14400},
    {"name": "burst", "mode": "burst", "concurrency": 100, "target_rps": 20, "duration": 180,
     "renewal_interval": 14400},
    {"name": "soak-2h", "mode": "soak", "concurrency": 100, "target_rps": 20, "duration": 7200,
     "renewal_interval": 14400},
    {"name": "soak-24h", "mode": "soak", "concurrency": 100, "target_rps": 20, "duration": 86400,
     "renewal_interval": 14400},
)
FIXED = {"threads": 4, "connection_limit": 512, "max_memory_mb": 512,
         "max_fixture_disk_mb": 1024, "max_queue": 128, "health_window_seconds": 300,
         "memory_warmup_seconds": 900, "memory_growth_window_seconds": 3600,
         "max_memory_growth_mib_hour": 32, "read_p95_ms": 1000, "write_p95_ms": 2000,
         "min_samples": 20, "max_error_rate": .001, "min_target_rate_ratio": .95,
         "rows": 20, "admission_limit": 2, "burst_duration": 60, "timeout": 10,
         "setup_timeout": 30, "setup_deadline": 600, "startup_deadline": 600,
         "disk_interval": 5, "disk_stale_seconds": 15, "min_success_rps": 0,
         "fault_mode": "timeout"}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def identifier(value):
    result = uuid.UUID(str(value)).hex
    require(str(value) == result, "canonical_uuid_required")
    return result


def owned_run(value):
    path = Path(value).resolve()
    require(path.parent == RUNTIME.resolve() and identifier(path.name) == path.name,
            "owned_pipeline_directory_required")
    return path


def read_json(path, maximum=16*1024*1024):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= maximum, "missing_or_oversized_report")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "object_report_required")
    return value


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def candidate_asset(name):
    return bool(re.fullmatch(r"\.runtime/cloud-readiness-evidence/frontend-secured-[a-f0-9]{32}/dist/[A-Za-z0-9_./-]+", name)
                and ".." not in Path(name).parts
                and Path(name).suffix.lower() in {".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp",
                                                       ".woff", ".woff2", ".map", ".ico"})


def manifest(extra_files=()):
    from qa.run_portable_postgres import source_snapshot, source_files
    files = dict(source_snapshot()["files"])
    for name in ("qa/release_pipeline", "qa/release_acceptance", "qa/browser_acceptance"):
        for path in source_files(ROOT / name, ".py"):
            files[path.relative_to(ROOT).as_posix()] = digest(path)
    for name in ("frontend/src", "frontend/dist"):
        for path in source_files(ROOT / name):
            files[path.relative_to(ROOT).as_posix()] = digest(path)
    for name in ("frontend/package.json", "frontend/pnpm-lock.yaml", "qa/release_pipeline/README.md"):
        files[name] = digest(ROOT / name)
    for name in extra_files:
        require(candidate_asset(name), "unsafe_candidate_asset")
        path = (ROOT / name).resolve()
        require(path.is_relative_to(ROOT.resolve()), "candidate_asset_escape")
        files[name] = digest(path)
    return {"files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def current_snapshot(snapshot):
    require(isinstance(snapshot, dict), "source_snapshot_missing")
    files = snapshot.get("files")
    require(isinstance(files, dict) and bool(files), "source_files_missing")
    require(snapshot.get("sha256") == hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            "source_manifest_digest_invalid")
    for name, expected in files.items():
        require(isinstance(name, str) and isinstance(expected, str), "invalid_source_entry")
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts
                and (".runtime" not in relative.parts or candidate_asset(name)),
                "unsafe_source_path")
        path = (ROOT / relative).resolve()
        require(path.is_relative_to(ROOT.resolve()) and path.is_file() and digest(path) == expected,
                "prerequisite_source_not_current")


def backend_gate(report):
    require(report.get("outcome") in ("PASS", "PASS_WITH_SKIPS"), "backend_not_passed")
    run = report.get("test_run", {})
    require(type(run.get("tests_run")) is int and run["tests_run"] >= 1000 and run.get("exit_code") == 0
            and run.get("failures") == 0 and run.get("errors") == 0
            and isinstance(run.get("summary"), str) and run["summary"].startswith("OK"), "backend_test_gate_failed")
    guards = report.get("postgres_root_guard", {})
    require(set(guards.get("required", [])) == GUARDS and set(guards.get("passed", {})) == GUARDS
            and all(value is True for value in guards["passed"].values()), "backend_root_guards_missing")
    require(report.get("cleanup", {}).get("verified") is True and not report.get("source_changes_during_run"),
            "backend_cleanup_or_source_failed")
    require(report.get("source_before") == report.get("source_after"), "backend_source_changed")
    current_snapshot(report.get("source_after"))
    from qa.run_portable_postgres import source_snapshot
    require(report["source_after"] == source_snapshot(), "backend_snapshot_scope_not_current")


def browser_gate(report, report_path):
    require(report.get("outcome") == "PASS" and report.get("exit_code") == 0
            and report.get("browser_exit_code") == 0, "browser_not_passed")
    cleanup = report.get("cleanup", {})
    require(all(cleanup.get(key) is True for key in
                ("browser_worker_stopped", "server_process_stopped", "server_port_closed")), "browser_cleanup_missing")
    require(all(cleanup.get(key, {}).get("verified") is True for key in
                ("postgres", "browser_process_tree", "server_process_tree")), "browser_tree_cleanup_missing")
    require(report.get("source_before") == report.get("source_after"), "browser_source_changed")
    current_snapshot(report.get("source_after"))
    # Use the existing semantic/browser-artifact gate, not the aggregate summary alone.
    from qa.browser_acceptance.run import validate_browser_result
    directory = Path(report_path).resolve().parent
    require(directory == (ROOT / ".runtime/browser-acceptance" / identifier(report.get("uuid"))).resolve(),
            "browser_report_directory_mismatch")
    validate_browser_result(read_json(directory / "browser-result.json"), directory)


def expected_phases(spec):
    n, rate, seconds = spec["concurrency"], spec["target_rps"], spec["duration"]
    if spec["mode"] == "step":
        return [("target", n, seconds, rate), ("step-2x", 2*n, seconds, 2*rate)]
    if spec["mode"] == "burst":
        return [("target-before", n, seconds, rate), ("burst-5x", 5*n, 60, 5*rate),
                ("target-recovery", n, seconds, rate)]
    return [(spec["mode"], n, seconds, rate)]


def phase_arguments(spec, fixture_id, postgres_bin):
    result = ["--postgres-bin", str(postgres_bin), "--fixture-id", identifier(fixture_id)]
    for key, value in {**FIXED, **{k: v for k, v in spec.items() if k != "name"}}.items():
        result.extend(["--" + key.replace("_", "-"), str(value)])
    return result


def phase_gate(report, spec, fixture_id, exit_code, pg_report):
    require(exit_code == 0 and report.get("result") == "PASS", "phase_not_passed")
    require(report.get("fixture_id") == fixture_id, "phase_uuid_mismatch")
    require(report.get("source_before") == report.get("source_after")
            and not report.get("source_changed_during_run"), "phase_source_changed")
    source = report.get("source_before", {})
    files = source.get("source_sha256")
    require(isinstance(files, dict) and bool(files)
            and source.get("source_manifest_sha256") == hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            "phase_source_manifest_missing")
    require(report.get("fixture", {}).get("database_kind") == "postgresql"
            and report.get("fixture", {}).get("agent_mode") == "disabled"
            and report.get("fixture", {}).get("model_mode") == "no_model_calls", "phase_scope_invalid")
    from qa.release_acceptance.run import validate_database_pool_identity
    validate_database_pool_identity(report["fixture"])
    require(report.get("database_pool") == {"identity_verified": True, **report["fixture"]["database_pool"]},
            "phase_database_pool_unverified")
    workload = report.get("workload", {})
    for key, expected in {**FIXED, **{k: v for k, v in spec.items() if k != "name"}}.items():
        require(workload.get(key) == expected, "phase_parameters_changed:" + key)
    clean = report.get("cleanup", {})
    require(all(clean.get(key) is True for key in
                ("owned_process_stopped", "owned_port_closed", "owned_capacity_monitor_stopped")), "phase_cleanup_missing")
    require(report.get("portable_postgres", {}).get("context_closed") is True
            and pg_report.get("cleanup", {}).get("verified") is True, "phase_pg_cleanup_missing")
    require(pg_report.get("postgres", {}).get("database") == "release_acceptance_" + fixture_id,
            "phase_pg_identity_mismatch")
    measures = report.get("measurements", {})
    require(measures.get("result") == "PASS" and measures.get("failures") == [], "phase_measurements_failed")
    drain = measures.get("renewal_drain", {})
    require(drain.get("result") == "PASS" and drain.get("timeout_seconds") == FIXED["timeout"] * 6
            and drain.get("snapshot_scope") == "workload_boundary"
            and drain.get("new_attempts_during_drain") == 0 and drain.get("failures_during_drain") == 0,
            "phase_renewal_drain_failed")
    phases = measures.get("phases", [])
    planned = expected_phases(spec)
    require(len(phases) == len(planned), "phase_sequence_incomplete")
    for actual, (name, concurrency, seconds, rate) in zip(phases, planned):
        require(actual.get("name") == name and actual.get("concurrency") == concurrency
                and actual.get("requested_seconds") == seconds
                and actual.get("offered_logical_operations_per_second") == rate, "phase_plan_mismatch")
        elapsed = actual.get("elapsed_seconds")
        require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= seconds,
                "phase_not_elapsed")
        require(actual.get("result") == "PASS" and actual.get("failures") == [], "phase_semantics_or_timing_failed")
        windows = actual.get("health_windows", {})
        require(windows.get("result") == "PASS", "phase_health_failed")
        complete = [w for w in windows.get("retained_windows", []) if w.get("complete_window") is True]
        require(len(complete) >= seconds // 300 and all(w.get("result") == "PASS" and w.get("failures") == []
                for w in complete), "phase_complete_windows_missing")
        require(all(w.get("result") in ("PASS", "PARTIAL") and w.get("failures") == []
                    for w in windows.get("retained_windows", [])), "phase_tail_window_failed")
    renewals = measures.get("session_renewal", {})
    require(renewals.get("failures") == 0, "renewal_failure")
    if spec["name"] in ("renewal-preflight", "soak-24h"):
        require(renewals.get("schema_version") == 2, "per_client_renewal_schema_missing")
        clients = renewals.get("per_client", {})
        count = spec["concurrency"]
        expected = {str(index) for index in range(count)} | {"denied"}
        minimum = 1 if spec["name"] == "renewal-preflight" else 5
        require(set(clients) == expected, "per_client_renewal_population_missing")
        coverage = renewals.get("coverage", {})
        require(coverage.get("result") == "PASS" and coverage.get("failures") == []
                and coverage.get("expected_clients") == len(expected)
                and coverage.get("observed_clients") == len(expected)
                and coverage.get("requested_workload_seconds") == spec["duration"], "renewal_coverage_failed")
        observed_end = coverage.get("workload_end_monotonic")
        require(type(observed_end) in (int, float) and math.isfinite(observed_end), "renewal_end_time_missing")
        for key, value in clients.items():
            require(value.get("client_id") == ("denied" if key == "denied" else int(key)), "renewal_client_id_mismatch")
            require(value.get("interval_seconds") == spec["renewal_interval"], "renewal_interval_mismatch")
            first = value.get("first_issued_monotonic")
            observed = value.get("observed_seconds_since_first_issue")
            require(type(first) in (int, float) and math.isfinite(first)
                    and type(observed) in (int, float) and math.isfinite(observed) and observed >= 0,
                    "renewal_actual_issue_time_missing")
            require(math.isclose(observed, max(0, observed_end-first), abs_tol=.01), "renewal_observed_span_mismatch")
            require(int(observed // spec["renewal_interval"]) >= minimum,
                    "renewal_required_duration_not_elapsed")
            require(value.get("successes", 0) >= minimum and value.get("failures") == 0
                    and value.get("early_losses") == 0 and value.get("missed") == 0
                    and value.get("overdue_at_workload_end") is False and value.get("coverage_result") == "PASS",
                    "per_client_renewal_failed")
            required = max(0, int(observed // spec["renewal_interval"])-1,
                           int(spec["duration"] // spec["renewal_interval"])-1)
            require(value.get("required_successes") == required and required >= minimum
                    and value.get("successes", 0) >= value["required_successes"], "required_renewals_missing")
    if spec["mode"] == "soak":
        growth = measures.get("memory_growth", {})
        require(growth.get("result") == "PASS" and growth.get("windows")
                and all(w.get("result") == "PASS" and w.get("observed_seconds", 0) >= 3600
                        and w.get("samples", 0) >= 30 for w in growth["windows"]), "growth_evidence_missing")


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
