import argparse
import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from qa.release_acceptance.run import (OPERATIONS, Sample, SampleCollector, histogram_percentile,
                                     parser, request_sample, semantic, summarize, validate,
                                     capacity_sample_valid, workload_plan, paced_delivery_valid,
                                     python_sources, prepare_login, connection_budget, source_evidence)
from qa.release_acceptance.fixture_server import waitress_parameters


def valid_samples(count=20):
    results = {"permission_denied": "expected_permission_denial", "agent_disabled": "expected_agent_disabled"}
    return [Sample(operation, 1, 200, results.get(operation, "success"))
            for operation in OPERATIONS for _ in range(count)]


class RedlineTests(unittest.TestCase):
    def test_connection_budget_and_actual_waitress_parameter_mapping(self):
        args = parser().parse_args(["--concurrency", "500"])
        validate(args)
        self.assertEqual(connection_budget(500, 4), 508)
        self.assertEqual(args.connection_limit, 512)
        args.host, args.port = "127.0.0.1", 0
        actual = waitress_parameters(args)
        self.assertEqual(actual["connection_limit"], 512)
        self.assertEqual(actual["threads"], 4)
        with patch("qa.release_acceptance.fixture_server.os.name", "nt"):
            self.assertFalse(waitress_parameters(args)["asyncore_use_poll"])
        args.connection_limit = 507
        with self.assertRaises(ValueError):
            validate(args)

    def test_source_manifest_ignores_unrelated_qa_but_binds_business_and_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = ("backend/portal/app.py", "qa/release_acceptance/run.py",
                     "qa/run_portable_postgres.py", "qa/agent_platform/verify_portal_pg.py",
                     "uv.lock", "pyproject.toml", "qa/browser_acceptance/browser.py")
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("original", encoding="utf-8")
            with patch("qa.release_acceptance.run.ROOT", root):
                before = source_evidence()
                (root / "qa/browser_acceptance/browser.py").write_text("unrelated installed QA", encoding="utf-8")
                self.assertEqual(before, source_evidence())
                (root / "backend/portal/app.py").write_text("changed real business source", encoding="utf-8")
                changed = source_evidence()
                self.assertNotEqual(before, changed)
                self.assertNotEqual(before["source_manifest_sha256"], changed["source_manifest_sha256"])
                (root / "backend/portal/app.py").write_text("original", encoding="utf-8")
                (root / "qa/run_portable_postgres.py").write_text("changed actual dependency", encoding="utf-8")
                self.assertNotEqual(before, source_evidence())
    def test_source_discovery_prunes_vendor_runtime_caches_recursively(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "portal" / "actual.py"
            app.parent.mkdir()
            app.write_text("actual business source", encoding="utf-8")
            for name in ("node_modules", "__pycache__", ".runtime", ".venv"):
                child = root / "portal" / "nested" / name / "more" / "vendor.py"
                child.parent.mkdir(parents=True)
                child.write_text("must not bind installed artifact", encoding="utf-8")
            self.assertEqual(list(python_sources(root)), [app])
            app2 = root / "portal" / "nested" / "business.py"
            app2.write_text("new business file", encoding="utf-8")
            self.assertEqual(set(python_sources(root)), {app, app2})

    def test_preparation_timeout_is_independent_and_restored_on_success_and_failure(self):
        async def run():
            args = parser().parse_args([])
            observed = []
            def handler(request):
                observed.append(request.extensions["timeout"]["read"])
                return httpx.Response(200, json={"username": "x"} if request.method == "POST"
                                      else {"csrfToken": "synthetic"})
            trace = {}
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://fixture") as client:
                await prepare_login(client, "x", "synthetic", args, trace, time.monotonic()+1)
                self.assertEqual(client.timeout.read, 10)
                self.assertEqual(observed, [30, 30, 30])
                self.assertEqual(trace["completed_logins"], 1)
                self.assertNotIn("synthetic", str(trace))
            def timeout(request):
                raise httpx.ReadTimeout("body/password not retained", request=request)
            trace = {}
            async with httpx.AsyncClient(transport=httpx.MockTransport(timeout), base_url="http://fixture") as client:
                with self.assertRaises(httpx.ReadTimeout):
                    await prepare_login(client, "x", "synthetic", args, trace, time.monotonic()+1)
                self.assertEqual(client.timeout.read, 10)
                self.assertEqual(trace["requests"][0]["error_class"], "ReadTimeout")
                self.assertNotIn("password", str(trace))
                with self.assertRaises(TimeoutError):
                    await prepare_login(client, "x", "synthetic", args, trace, time.monotonic()-1)
        asyncio.run(run())

    def test_failed_200_never_passes(self):
        for body in ({"success": False}, {"failed": True}, {"status": "failed", "username": "x"},
                     {"status": "unavailable", "username": "x"}):
            self.assertEqual(semantic("session", 200, body, username="x")[0], "unexpected")

    def test_successful_200_requires_business_identity_and_data(self):
        self.assertEqual(semantic("session", 200, {"username": "wrong"}, username="x")[0], "unexpected")
        self.assertEqual(semantic("ledger_read", 200, {"department": "finance", "revision": 0,
            "records": []}, rows=1)[0], "unexpected")
        self.assertEqual(semantic("ledger_write", 200, {"department": "presales", "revision": 2},
            expected_revision=2)[0], "unexpected")

    def test_expected_statuses_not_blanket_4xx_exclusions(self):
        for status in (401, 403, 404, 409, 429):
            self.assertEqual(semantic("session", status, {"detail": "denied"})[0], "unexpected")
        self.assertEqual(semantic("ledger_write", 409,
            {"detail": "台账已被其他人更新，请刷新后重试。"})[0], "expected_conflict")
        for body in ({"detail": "conflict"}, {"detail": "授权已变化"},
                     {"detail": "台账已被其他人更新，请刷新后重试。", "error": True}):
            self.assertEqual(semantic("ledger_write", 409, body)[0], "unexpected")
        self.assertEqual(semantic("permission_denied", 403,
            {"detail": "无该台账访问权限。"})[0], "expected_permission_denial")
        for body in ({"detail": "denied"}, {"detail": "password_change_required"},
                     {"detail": "CSRF Failed"}):
            self.assertEqual(semantic("permission_denied", 403, body)[0], "unexpected")
        self.assertEqual(semantic("ledger_read", 409, {"detail": "conflict"})[0], "unexpected")
        self.assertEqual(semantic("agent_disabled", 503, {"code": "unavailable"})[0], "expected_agent_disabled")
        self.assertEqual(semantic("agent_disabled", 503, {"code": "unknown"})[0], "unexpected")
        self.assertEqual(semantic("overload", 429, {"detail": "rate limit"})[0], "unexpected")

    def test_no_load_and_low_samples_fail(self):
        self.assertEqual(summarize([], 30)["result"], "FAIL")
        self.assertEqual(summarize(valid_samples(1), 30)["result"], "FAIL")
        self.assertEqual(summarize(valid_samples(), 30)["result"], "PASS")

    def test_all_conflicts_without_successful_write_fail(self):
        samples = valid_samples()
        for sample in samples:
            if sample.operation == "ledger_write":
                sample.result = "expected_conflict"
        self.assertIn("ledger_write:no_successful_semantic_sample", summarize(samples, 30)["failures"])

    def test_one_write_success_among_many_conflicts_is_insufficient(self):
        samples = valid_samples()
        writes = [sample for sample in samples if sample.operation == "ledger_write"]
        for sample in writes[1:]:
            sample.result = "expected_conflict"
        self.assertIn("ledger_write:no_successful_semantic_sample", summarize(samples, 30)["failures"])

    def test_fast_conflicts_cannot_hide_slow_successful_write_p95(self):
        samples = valid_samples(1000)
        writes = [sample for sample in samples if sample.operation == "ledger_write"]
        for sample in writes:
            sample.result = "expected_conflict"
        for sample in writes[:20]:
            sample.result = "success"
            sample.milliseconds = 2001
        result = summarize(samples, 30)
        self.assertEqual(result["operations"]["ledger_write"]["p95_ms"], 1)
        self.assertIn("ledger_write:successful_p95_exceeded", result["failures"])

    def test_capacity_observer_missing_failed_stale_and_nonnumeric_rejected(self):
        value = {"fixture_disk_bytes": 100, "disk_measurement_wall_time": 100,
                 "disk_scan_duration_seconds": .1, "disk_files": 3, "disk_monitor_ok": 1}
        self.assertTrue(capacity_sample_valid(value, 105, 15))
        self.assertFalse(capacity_sample_valid(value, 116, 15))
        for change in ({"disk_monitor_ok": 0}, {"disk_files": None},
                       {"disk_measurement_wall_time": float("nan")}, {"fixture_disk_bytes": -1}):
            self.assertFalse(capacity_sample_valid({**value, **change}, 105, 15))
        self.assertFalse(capacity_sample_valid({}, 105, 15))

    def test_stage_multipliers_and_paced_low_delivery_fail(self):
        args = parser().parse_args(["--mode", "step", "--concurrency", "8", "--target-rps", "10"])
        self.assertEqual([(p[1], p[3]) for p in workload_plan(args)], [(8, 10), (16, 20)])
        args.mode = "burst"
        self.assertEqual([(p[1], p[3]) for p in workload_plan(args)], [(8, 10), (40, 50), (8, 10)])
        self.assertFalse(paced_delivery_valid(1, 10, .95))
        self.assertTrue(paced_delivery_valid(9.5, 10, .95))

    def test_timeout_is_failure_even_under_error_rate(self):
        samples = valid_samples(1000)
        samples[0] = Sample("session", 1, 0, "unexpected", "timeout")
        report = summarize(samples, 30)
        self.assertLess(report["unexpected_error_rate"], .001)
        self.assertEqual(report["result"], "FAIL")
        self.assertIn("session:timeout", report["failures"])

    def test_failed_business_and_auth_redlines_cannot_be_diluted(self):
        for sample in (Sample("session", 1, 200, "unexpected", "failed_200"),
                       Sample("session", 1, 403, "unexpected", "unexpected_status_or_business_payload")):
            samples = valid_samples(1000)
            samples[0] = sample
            self.assertEqual(summarize(samples, 30)["result"], "FAIL")

    def test_latency_and_throughput_thresholds_are_enforced(self):
        samples = valid_samples()
        for sample in samples:
            if sample.operation == "ledger_read":
                sample.milliseconds = 1001
        self.assertIn("ledger_read:p95_exceeded", summarize(samples, 30)["failures"])
        self.assertIn("successful_throughput_below_target", summarize(valid_samples(), 30,
            min_success_rps=100)["failures"])

    def test_real_http_transport_timeout_and_failed_json_classification(self):
        async def run():
            def timeout(request):
                raise httpx.ReadTimeout("not included in reports", request=request)
            async with httpx.AsyncClient(transport=httpx.MockTransport(timeout), base_url="http://fixture") as client:
                sample = await request_sample(client, "session", "GET", "/api/me/", username="x")
                self.assertEqual(sample.error, "timeout")
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
                    httpx.Response(200, json={"username": "x", "success": False})), base_url="http://fixture") as client:
                sample = await request_sample(client, "session", "GET", "/api/me/", username="x")
                self.assertEqual(sample.result, "unexpected")
        asyncio.run(run())

    def test_configuration_rejects_zero_load_and_unacknowledged_external_target(self):
        for argv in (["--duration", "0"], ["--concurrency", "0"], ["--min-samples", "0"],
                     ["--target", "https://example.test"], ["--max-error-rate", ".1"],
                     ["--mode", "soak", "--duration", "30"], ["--duration", "nan"],
                     ["--disk-interval", "1"], ["--disk-stale-seconds", "nan"],
                     ["--min-target-rate-ratio", ".1"], ["--mode", "burst", "--concurrency", "201"],
                     ["--startup-deadline", "nan"], ["--startup-deadline", "1801"],
                     ["--concurrency", "100", "--connection-limit", "100"]):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                validate(parser().parse_args(argv))

    def test_long_run_collector_is_bounded_and_quantiles_conservative(self):
        collector = SampleCollector()
        for _ in range(10000):
            collector.append(Sample("session", 1.25, 200, "success"))
        group = collector.groups["session"]
        self.assertEqual(len(group["latency"]), 1)
        self.assertEqual(group["count"], 10000)
        self.assertEqual(histogram_percentile(group["latency"], .99), 2)


if __name__ == "__main__":
    unittest.main()
