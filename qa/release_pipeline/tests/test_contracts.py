import copy
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qa.release_pipeline.contracts import (FIXED, GUARDS, PLAN, atomic_json, backend_gate, expected_phases,
                                         identifier, phase_arguments, phase_gate)
from qa.release_pipeline.observer import numeric_tail
from qa.release_pipeline.status import inspect


UUID = "0123456789abcdef0123456789abcdef"


def valid_report(spec):
    source = {"source_sha256": {"backend/example.py": "0"*64}}
    source["source_manifest_sha256"] = hashlib.sha256(json.dumps(source["source_sha256"], sort_keys=True).encode()).hexdigest()
    pool = {"enabled": True, "scope": "per_process", "implementation": "django_psycopg3",
            "version": version("psycopg-pool"), "min_size": 1, "max_size": 4,
            "timeout_seconds": 5.0, "max_waiting": 16, "conn_max_age": 0, "health_checks": True,
            "connect_timeout_seconds": 3,
            "stats": {"pool_min": 1, "pool_max": 4, "pool_size": 1, "pool_available": 1, "requests_waiting": 0}}
    report = {"fixture_id": UUID, "result": "PASS", "source_before": source, "source_after": copy.deepcopy(source),
              "fixture": {"database_kind": "postgresql", "agent_mode": "disabled", "model_mode": "no_model_calls",
                          "database_pool": pool},
              "database_pool": {"identity_verified": True, **pool},
              "workload": {**FIXED, **{k: v for k, v in spec.items() if k != "name"}},
              "cleanup": dict.fromkeys(("owned_process_stopped", "owned_port_closed", "owned_capacity_monitor_stopped"), True),
              "portable_postgres": {"context_closed": True},
              "measurements": {"result": "PASS", "failures": [], "phases": [],
                  "renewal_drain": {"result": "PASS", "timeout_seconds": 60,
                      "snapshot_scope": "workload_boundary", "new_attempts_during_drain": 0, "failures_during_drain": 0},
                  "memory_growth": {"result": "PASS", "windows": [{"result": "PASS", "observed_seconds": 3600, "samples": 3600}]},
                  "session_renewal": {"failures": 0, "successes": 505}}}
    for name, count, seconds, rate in expected_phases(spec):
        report["measurements"]["phases"].append({"name": name, "concurrency": count, "requested_seconds": seconds,
            "elapsed_seconds": seconds, "offered_logical_operations_per_second": rate, "result": "PASS", "failures": [],
            "health_windows": {"result": "PASS", "retained_windows":
                [{"complete_window": True, "result": "PASS", "failures": []} for _ in range(seconds//300)]}})
    if spec["name"] in ("renewal-preflight", "soak-24h"):
        minimum = 2 if spec["name"] == "renewal-preflight" else 5
        names = [str(index) for index in range(spec["concurrency"])] + ["denied"]
        renewal = report["measurements"]["session_renewal"]
        renewal["schema_version"] = 2
        renewal["per_client"] = {name: {"client_id": "denied" if name == "denied" else int(name),
            "interval_seconds": spec["renewal_interval"], "first_issued_monotonic": 100.,
            "observed_seconds_since_first_issue": spec["duration"], "successes": minimum, "failures": 0,
            "missed": 0, "early_losses": 0, "overdue_at_workload_end": False, "coverage_result": "PASS",
            "required_successes": minimum} for name in names}
        renewal["coverage"] = {"result": "PASS", "failures": [], "expected_clients": len(names), "observed_clients": len(names),
                               "workload_end_monotonic": 100.+spec["duration"], "requested_workload_seconds": spec["duration"]}
    return report


PG = {"cleanup": {"verified": True}, "postgres": {"database": "release_acceptance_" + UUID}}


class GateTests(unittest.TestCase):
    def test_actual_bounded_pool_and_matching_verified_report_are_required(self):
        for mutation in (lambda r: r["fixture"].pop("database_pool"),
                         lambda r: r["fixture"]["database_pool"].update(enabled=False),
                         lambda r: r["fixture"]["database_pool"].update(max_size=8),
                         lambda r: r["fixture"]["database_pool"].pop("connect_timeout_seconds"),
                         lambda r: r["fixture"]["database_pool"].update(connect_timeout_seconds=0),
                         lambda r: r["fixture"]["database_pool"].update(connect_timeout_seconds=30),
                         lambda r: r["database_pool"].update(identity_verified=False)):
            report = valid_report(PLAN[1])
            mutation(report)
            with self.assertRaises(ValueError):
                phase_gate(report, PLAN[1], UUID, 0, PG)

    def test_drain_cannot_start_new_rounds_or_hide_a_failed_real_renewal(self):
        for mutation in (lambda r: r["measurements"].pop("renewal_drain"),
                         lambda r: r["measurements"]["renewal_drain"].update(result="FAIL"),
                         lambda r: r["measurements"]["renewal_drain"].update(new_attempts_during_drain=1),
                         lambda r: r["measurements"]["renewal_drain"].update(failures_during_drain=1)):
            report = valid_report(PLAN[1])
            mutation(report)
            with self.assertRaises(ValueError):
                phase_gate(report, PLAN[1], UUID, 0, PG)

    def test_fixed_plan_cli_is_accepted_by_actual_pressure_parser_without_running(self):
        from qa.release_acceptance.run import parser, validate, workload_plan
        for spec in PLAN:
            arguments = phase_arguments(spec, UUID, "explicit-postgres-bin")
            args = parser().parse_args(arguments)
            validate(args)
            self.assertEqual(workload_plan(args), expected_phases(spec))
            self.assertEqual(args.fixture_id, UUID)
        self.assertEqual(expected_phases(PLAN[2])[1], ("burst-5x", 500, 60, 100))

    def test_complete_fixed_evidence_passes(self):
        for spec in PLAN:
            phase_gate(valid_report(spec), spec, UUID, 0, PG)

    def test_nonzero_exit_running_missing_cleanup_source_drift_never_pass(self):
        spec = PLAN[1]
        for mutation in (lambda r: r.update(result="RUNNING"), lambda r: r["cleanup"].pop("owned_port_closed"),
                         lambda r: r["source_after"].update(commit="changed"),
                         lambda r: r["portable_postgres"].update(context_closed=False),
                         lambda r: r.update(fixture_id="f"*32)):
            report = valid_report(spec)
            mutation(report)
            with self.assertRaises(ValueError):
                phase_gate(report, spec, UUID, 0, PG)
        with self.assertRaises(ValueError):
            phase_gate(valid_report(spec), spec, UUID, 1, PG)
        with self.assertRaises(ValueError):
            phase_gate(valid_report(spec), spec, UUID, 0, {"cleanup": {"verified": False}})

    def test_elapsed_missing_phase_and_threshold_change_are_failures(self):
        spec = PLAN[2]
        for mutation in (lambda r: r["measurements"]["phases"].pop(),
                         lambda r: r["measurements"]["phases"][1].update(elapsed_seconds=59.999),
                         lambda r: r["workload"].update(write_p95_ms=99999),
                         lambda r: r["measurements"]["phases"][0].update(failures=["failed200"])):
            report = valid_report(spec)
            mutation(report)
            with self.assertRaises(ValueError):
                phase_gate(report, spec, UUID, 0, PG)

    def test_24h_windows_and_growth_observation_cannot_be_summarized_away(self):
        spec = PLAN[-1]
        for mutation in (lambda r: r["measurements"]["phases"][0]["health_windows"]["retained_windows"].pop(),
                         lambda r: r["measurements"]["phases"][0]["health_windows"]["retained_windows"][7].update(result="FAIL"),
                         lambda r: r["measurements"]["memory_growth"].update(windows=[]),
                         lambda r: r["measurements"]["memory_growth"]["windows"][0].update(observed_seconds=3599)):
            report = valid_report(spec)
            mutation(report)
            with self.assertRaises(ValueError):
                phase_gate(report, spec, UUID, 0, PG)

    def test_505_total_does_not_substitute_for_every_client_and_real_duration(self):
        spec = PLAN[-1]
        for mutation in (lambda r: r["measurements"]["session_renewal"].pop("per_client"),
                         lambda r: r["measurements"]["session_renewal"]["per_client"].pop("denied"),
                         lambda r: r["measurements"]["session_renewal"]["per_client"]["17"].update(successes=4),
                         lambda r: r["measurements"]["session_renewal"]["per_client"]["17"].update(early_losses=1),
                         lambda r: r["measurements"]["session_renewal"]["per_client"]["17"].update(observed_seconds_since_first_issue=70000),
                         lambda r: r["measurements"]["session_renewal"]["per_client"]["17"].update(overdue_at_workload_end=True)):
            report = valid_report(spec)
            mutation(report)
            with self.assertRaises(ValueError):
                phase_gate(report, spec, UUID, 0, PG)

    def test_preflight_requires_all_nine_real_clients(self):
        report = valid_report(PLAN[0])
        report["measurements"]["session_renewal"]["per_client"]["denied"]["successes"] = 0
        with self.assertRaises(ValueError):
            phase_gate(report, PLAN[0], UUID, 0, PG)

    def test_backend_requires_full_count_both_root_guards_stable_source_cleanup(self):
        source = {"files": {"x": "0"*64}, "sha256": "test"}
        report = {"outcome": "PASS_WITH_SKIPS", "test_run": {"tests_run": 1111, "exit_code": 0, "errors": 0,
                  "failures": 0, "summary": "OK (skipped=6)"}, "postgres_root_guard": {"required": list(GUARDS),
                  "passed": dict.fromkeys(GUARDS, True)}, "cleanup": {"verified": True},
                  "source_before": source, "source_after": source}
        with patch("qa.release_pipeline.contracts.current_snapshot"), \
                patch("qa.run_portable_postgres.source_snapshot", return_value=source):
            backend_gate(report)
            for key, value in (("tests_run", 999), ("exit_code", 1), ("errors", 1)):
                changed = copy.deepcopy(report)
                changed["test_run"][key] = value
                with self.assertRaises(ValueError):
                    backend_gate(changed)
            changed = copy.deepcopy(report)
            changed["postgres_root_guard"]["passed"][next(iter(GUARDS))] = False
            with self.assertRaises(ValueError):
                backend_gate(changed)

    def test_atomic_json_and_numeric_tail_do_not_retain_bodies_or_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            atomic_json(path, {"result": "RUNNING"})
            atomic_json(path, {"result": "FAIL"})
            self.assertEqual(json.loads(path.read_text())["result"], "FAIL")
            with self.assertRaises(ValueError):
                atomic_json(path, {"number": float("nan")})
            resources = Path(directory) / "resources.jsonl"
            resources.write_text('x'*20000 + '\n' + json.dumps({"wall_time": 5, "rss_bytes": 10,
                "cookie": "private-test", "cpu_seconds": float("nan")}) + '\n{partial', encoding="utf-8")
            self.assertEqual(numeric_tail(resources), {"wall_time": 5, "rss_bytes": 10})

    def test_dead_identity_changes_running_to_aborted_without_mutating_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / UUID
            run.mkdir()
            atomic_json(run / "status.json", {"pipeline_id": UUID, "result": "RUNNING", "updated_at": 0})
            atomic_json(run / "owner.json", {"pipeline_id": UUID, "identity": {"pid": 99}})
            with patch("qa.release_pipeline.status.identity_alive", return_value=False):
                result = inspect(run)
            self.assertEqual(result["result"], "ABORTED")
            self.assertEqual(result["persisted_result"], "RUNNING")
            self.assertFalse(result["production_ready"])

    def test_uuid_canonical_only(self):
        for value in ("../anything", UUID.upper(), "{" + UUID + "}"):
            with self.assertRaises(ValueError):
                identifier(value)

    def test_phase_failure_stops_sequence_without_retry_or_later_phase(self):
        from qa.release_pipeline.supervisor import run_sequence
        called = []
        def fail_third(run, index, spec, config, state):
            called.append(index)
            if index == 2:
                raise ValueError("test_phase_failure")
        with self.assertRaises(ValueError):
            run_sequence(None, {}, {}, execute=fail_third)
        self.assertEqual(called, [0, 1, 2])

    def test_current_snapshot_rejects_escape_digest_tamper_and_missing_file(self):
        from qa.release_pipeline.contracts import current_snapshot
        for files in ({"../outside.py": "0"*64}, {"missing-source.py": "0"*64},
                      {".runtime/private-report.json": "0"*64}):
            snapshot = {"files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}
            with self.assertRaises(ValueError):
                current_snapshot(snapshot)
        with self.assertRaises(ValueError):
            current_snapshot({"files": {"backend/example.py": "0"*64}, "sha256": "tampered"})

    def test_runtime_source_exception_is_only_public_compiled_candidate_dist(self):
        from qa.release_pipeline.contracts import candidate_asset
        prefix = ".runtime/cloud-readiness-evidence/frontend-secured-" + UUID + "/dist/"
        self.assertTrue(candidate_asset(prefix + "assets/index-abc.js"))
        for name in (prefix + ".env", prefix + "../private.js", ".runtime/private-report.json", prefix + "secret.env"):
            self.assertFalse(candidate_asset(name))
