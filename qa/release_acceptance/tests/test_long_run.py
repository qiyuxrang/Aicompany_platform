import asyncio
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import sys

import httpx

from qa.release_acceptance.run import (Sample, SampleCollector, parser, validate, login,
                                       request_sample, business_sample, summarize, scheduled_session_renewals, drain_session_renewals, renewal_boundary_snapshot)
from qa.release_acceptance.sessions import ScheduledSession, finalize_renewal_coverage
from qa.release_acceptance.telemetry import Telemetry, DiskTrend, memory_measurement
from qa.release_acceptance.health import MemoryGrowth, WindowHealth
from qa.release_acceptance.fixture_server import process_memory
from qa.release_acceptance.tests.test_acceptance import valid_samples


def renewal_report():
    return {"attempts": 0, "successes": 0, "failures": 0, "events": [], "events_dropped": 0}


class RenewalTests(unittest.TestCase):
    def test_clock_driven_schedule_preserves_real_login_csrf_and_business_timeout(self):
        async def run():
            now = [0]
            report = renewal_report()
            calls, token = [], ["before"]
            def handler(request):
                calls.append((request.url.path, request.extensions["timeout"]["read"]))
                if request.url.path == "/api/me/":
                    return httpx.Response(200, json={"username": "synthetic"})
                if request.url.path == "/api/login/":
                    self.assertEqual(request.headers["X-CSRFToken"], "before")
                    token[0] = "after"
                    return httpx.Response(200, json={"username": "synthetic"})
                return httpx.Response(200, json={"csrfToken": token[0]})
            async with httpx.AsyncClient(base_url="http://fixture", timeout=10,
                                         transport=httpx.MockTransport(handler)) as client:
                async def verify():
                    return await request_sample(client, "session", "GET", "/api/me/", username="synthetic")
                async def renew(trace):
                    await login(client, "synthetic", "never retained", trace)
                session = ScheduledSession(4, 8, verify, renew, report, client_id=0, clock=lambda: now[0])
                now[0] = 3.9
                self.assertTrue(await session.maybe_renew())
                self.assertEqual(calls, [])
                now[0] = 4
                self.assertTrue(await session.maybe_renew())
                self.assertEqual(client.headers["X-CSRFToken"], "after")
                self.assertEqual([c[0] for c in calls], ["/api/me/", "/api/csrf/", "/api/login/", "/api/csrf/"])
                self.assertTrue(all(c[1] == 10 for c in calls))
                self.assertEqual(session.due, 8)
                self.assertEqual(report["successes"], 1)
                self.assertNotIn("never retained", str(report))
        asyncio.run(run())

    def test_early_auth_failure_and_missed_expiry_never_trigger_login(self):
        async def run():
            for now_value, check in ((4, Sample("session", 1, 401, "unexpected")),
                                     (4, Sample("session", 1, 403, "unexpected")), (8, True)):
                now, calls, report = [0], [], renewal_report()
                async def verify():
                    calls.append("verify")
                    return check
                async def renew(trace):
                    calls.append("login")
                session = ScheduledSession(4, 8, verify, renew, report, client_id=0, clock=lambda: now[0])
                now[0] = now_value
                self.assertFalse(await session.maybe_renew())
                self.assertNotIn("login", calls)
                self.assertEqual(report["failures"], 1)
                if now_value == 4:
                    self.assertEqual(report["events"][0]["precheck"]["status"], check.status)
                else:
                    self.assertEqual(calls, [])
        asyncio.run(run())

    def test_renewal_exception_is_explicit_fail_and_does_not_emit_exception_body(self):
        async def run():
            now, report = [0], renewal_report()
            async def verify():
                return True
            async def renew(trace):
                raise httpx.ReadTimeout("private password body")
            session = ScheduledSession(4, 8, verify, renew, report, client_id=0, clock=lambda: now[0])
            now[0] = 4
            self.assertFalse(await session.maybe_renew())
            self.assertEqual(report["events"][0]["result"], "FAIL")
            self.assertEqual(report["events"][0]["error_class"], "ReadTimeout")
            self.assertFalse(session.renewing)
            self.assertNotIn("private password", str(report))
        asyncio.run(run())

    def test_renewal_waits_for_inflight_business_and_blocks_csrf_race(self):
        async def run():
            now, report, calls = [0], renewal_report(), []
            async def verify():
                calls.append("verify")
                return True
            async def renew(trace):
                calls.append("login")
            session = ScheduledSession(4, 8, verify, renew, report, client_id=0, clock=lambda: now[0])
            async with session.operation():
                now[0] = 4
                task = asyncio.create_task(session.maybe_renew())
                await asyncio.sleep(0)
                self.assertTrue(session.renewing)
                self.assertEqual(calls, [])
            self.assertTrue(await asyncio.wait_for(task, 1))
            self.assertEqual(calls, ["verify", "login"])
            async with session.operation():
                self.assertEqual(session.active, 1)
        asyncio.run(run())

    def test_cancelled_renewal_unlocks_and_fails_instead_of_false_success(self):
        async def run():
            now, report = [0], renewal_report()
            async def verify():
                return True
            async def renew(trace):
                pass
            session = ScheduledSession(4, 8, verify, renew, report, client_id=0, clock=lambda: now[0])
            async with session.operation():
                now[0] = 4
                task = asyncio.create_task(session.maybe_renew())
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertFalse(session.renewing)
            self.assertEqual(report["failures"], 1)
        asyncio.run(run())

    def test_invalid_actual_expiry_and_auth_business_response_not_retried(self):
        for interval in (0, 8, 9, float("nan")):
            with self.assertRaises(ValueError):
                ScheduledSession(interval, 8, None, None, renewal_report(), client_id=0)
        async def run():
            calls = []
            def handler(request):
                calls.append(request.url.path)
                return httpx.Response(401, json={"detail": "not retained"})
            async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(handler)) as client:
                sample = await business_sample(client, "ledger_write", 0, 20)
                self.assertEqual(sample.status, 401)
                self.assertEqual(sample.result, "unexpected")
                self.assertEqual(calls, ["/api/business/ledgers/presales/"])
                self.assertEqual(summarize([sample], 1, min_samples=1)["result"], "FAIL")
        asyncio.run(run())


class RenewalDrainTests(unittest.TestCase):
    def test_inflight_real_login_finishes_with_csrf_without_starting_post_boundary_client(self):
        async def run():
            now, report, calls = [0], renewal_report(), []
            entered, release = asyncio.Event(), asyncio.Event()
            token = ["before"]
            async def handler(request):
                calls.append(request.url.path)
                self.assertEqual(request.extensions["timeout"]["read"], 10)
                if request.url.path == "/api/me/":
                    return httpx.Response(200, json={"username": "synthetic"})
                if request.url.path == "/api/login/":
                    entered.set()
                    await release.wait()
                    now[0] += .1
                    token[0] = "after"
                    return httpx.Response(200, json={"username": "synthetic"})
                return httpx.Response(200, json={"csrfToken": token[0]})
            async with httpx.AsyncClient(base_url="http://fixture", timeout=10,
                                         transport=httpx.MockTransport(handler)) as client:
                async def verify():
                    return await request_sample(client, "session", "GET", "/api/me/", username="synthetic")
                async def renew(trace):
                    await login(client, "synthetic", "not stored", trace)
                first = ScheduledSession(4, 8, verify, renew, report, client_id=0, clock=lambda: now[0])
                now[0] = .3
                later = ScheduledSession(4, 8, verify, renew, report, client_id="denied", clock=lambda: now[0])
                now[0] = 4
                stop, abort, failures = asyncio.Event(), asyncio.Event(), []
                task = asyncio.create_task(scheduled_session_renewals([first, later], stop, abort, failures,
                                                                      clock=lambda: now[0]))
                await asyncio.wait_for(entered.wait(), 1)
                boundary = now[0]
                snapshot = renewal_boundary_snapshot([first, later])
                stop.set()
                drain_task = asyncio.create_task(drain_session_renewals(task, stop, [first, later],
                    timeout=1, workload_end=boundary, boundary_snapshot=snapshot))
                await asyncio.sleep(0)
                release.set()
                drained = await drain_task
                self.assertEqual(drained["result"], "PASS")
                self.assertEqual(drained["workload_end_monotonic"], 4)
                self.assertEqual(drained["inflight_client_ids_at_boundary"], [0])
                self.assertEqual(drained["completed_records_during_drain"], 1)
                self.assertEqual(drained["new_attempts_during_drain"], 0)
                self.assertEqual(drained["successes_during_drain"], 1)
                self.assertEqual(drained["snapshot_scope"], "workload_boundary")
                self.assertEqual(first.statistics["successes"], 1)
                self.assertEqual(later.statistics["attempts"], 0)
                self.assertEqual(client.headers["X-CSRFToken"], "after")
                self.assertFalse(first.renewing)
                coverage = finalize_renewal_coverage([first, later], report, expected_client_ids=[0, "denied"],
                    observed_end=boundary, requested_seconds=4)
                self.assertEqual(coverage["result"], "PASS")
                self.assertEqual(first.statistics["observed_seconds_since_first_issue"], 4)
                self.assertEqual(calls, ["/api/me/", "/api/csrf/", "/api/login/", "/api/csrf/"])
        asyncio.run(run())

    def test_waiting_for_business_exclusive_slot_rechecks_boundary_before_any_http(self):
        async def run():
            now, report, calls = [0], renewal_report(), []
            async def verify():
                calls.append("verify")
                return True
            async def renew(trace):
                calls.append("login")
            lease = ScheduledSession(4, 8, verify, renew, report, client_id=0, clock=lambda: now[0])
            stop, aborted, failures = asyncio.Event(), asyncio.Event(), []
            async with lease.operation():
                now[0] = 4
                task = asyncio.create_task(scheduled_session_renewals([lease], stop, aborted, failures,
                                                                      clock=lambda: now[0]))
                await asyncio.sleep(.03)
                self.assertFalse(task.done())
                self.assertFalse(lease.renewing)
                stop.set()
            drained = await drain_session_renewals(task, stop, [lease], timeout=1, workload_end=4)
            self.assertEqual(drained["result"], "PASS")
            self.assertEqual(calls, [])
            self.assertEqual(report["attempts"], 0)
        asyncio.run(run())

    def test_real_auth_rejection_or_timeout_during_drain_remains_failure(self):
        async def run(kind):
            now, report = [0], renewal_report()
            entered, release = asyncio.Event(), asyncio.Event()
            async def verify():
                return True
            async def renew(trace):
                entered.set()
                await release.wait()
                if kind == "timeout":
                    raise httpx.ReadTimeout("private body")
                def handler(request):
                    if request.url.path == "/api/csrf/":
                        return httpx.Response(200, json={"csrfToken": "synthetic"})
                    return httpx.Response(403, json={"detail": "not retained"})
                async with httpx.AsyncClient(base_url="http://fixture", timeout=10,
                                             transport=httpx.MockTransport(handler)) as client:
                    await login(client, "synthetic", "not stored", trace)
            lease = ScheduledSession(4, 8, verify, renew, report, client_id=0, clock=lambda: now[0])
            now[0] = 4
            stop, aborted, failures = asyncio.Event(), asyncio.Event(), []
            task = asyncio.create_task(scheduled_session_renewals([lease], stop, aborted, failures,
                                                                  clock=lambda: now[0]))
            await asyncio.wait_for(entered.wait(), 1)
            stop.set()
            drain_task = asyncio.create_task(drain_session_renewals(task, stop, [lease], timeout=1, workload_end=4))
            release.set()
            drained = await drain_task
            self.assertEqual(drained["result"], "FAIL")
            self.assertEqual(report["failures"], 1)
            self.assertTrue(aborted.is_set())
            self.assertFalse(lease.renewing)
            self.assertNotIn("private body", str(report))
            self.assertNotIn("not retained", str(report))
        for kind in ("403", "timeout"):
            with self.subTest(kind=kind):
                asyncio.run(run(kind))

    def test_hanging_inflight_renewal_has_bounded_drain_and_failure_evidence(self):
        async def run():
            now, report = [0], renewal_report()
            entered = asyncio.Event()
            async def verify():
                return True
            async def renew(trace):
                entered.set()
                await asyncio.Event().wait()
            lease = ScheduledSession(4, 8, verify, renew, report, client_id=0, clock=lambda: now[0])
            now[0] = 4
            stop = asyncio.Event()
            task = asyncio.create_task(scheduled_session_renewals([lease], stop, asyncio.Event(), [],
                                                                  clock=lambda: now[0]))
            await asyncio.wait_for(entered.wait(), 1)
            drained = await asyncio.wait_for(drain_session_renewals(task, stop, [lease], timeout=.01, workload_end=4), 1)
            self.assertEqual(drained["result"], "FAIL")
            self.assertEqual(drained["failure"], "scheduled_renewal_drain_timeout")
            self.assertEqual(report["failures"], 1)
            self.assertTrue(task.done())
            self.assertFalse(lease.renewing)
        asyncio.run(run())

    def test_new_round_started_after_frozen_boundary_is_an_explicit_failure(self):
        async def run():
            now, report = [0], renewal_report()
            lease = ScheduledSession(4, 8, None, None, report, client_id=0, clock=lambda: now[0])
            snapshot = renewal_boundary_snapshot([lease])
            async def rogue_round():
                lease.statistics["rounds_started"] = 1
                return True
            task = asyncio.create_task(rogue_round())
            drained = await drain_session_renewals(task, asyncio.Event(), [lease], timeout=1,
                workload_end=1, boundary_snapshot=snapshot)
            self.assertEqual(drained["new_attempts_during_drain"], 1)
            self.assertEqual(drained["result"], "FAIL")
            self.assertEqual(drained["failure"], "new_renewal_started_after_workload_boundary")
        asyncio.run(run())

    def test_idle_scheduler_stops_without_round_and_external_cancellation_is_not_swallowed(self):
        async def run():
            now, report = [0], renewal_report()
            async def verify():
                self.fail("must not start")
            lease = ScheduledSession(4, 8, verify, None, report, client_id=0, clock=lambda: now[0])
            stop = asyncio.Event()
            task = asyncio.create_task(scheduled_session_renewals([lease], stop, asyncio.Event(), [],
                                                                  clock=lambda: now[0]))
            stop.set()
            drained = await drain_session_renewals(task, stop, [lease], timeout=1, workload_end=1)
            self.assertEqual(drained["result"], "PASS")
            self.assertEqual(report["attempts"], 0)
            cancelled = asyncio.create_task(asyncio.Event().wait())
            cancelled.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await drain_session_renewals(cancelled, stop, [lease], timeout=1, workload_end=1)
        asyncio.run(run())


class PerClientRenewalTests(unittest.TestCase):
    def make_lease(self, report, now, client_id, *, valid=True):
        async def verify():
            return valid
        async def renew(trace):
            # A real login finishes after its due time; next due uses completion.
            now[0] += .1
        return ScheduledSession(4, 8, verify, renew, report, client_id=client_id, clock=lambda: now[0])

    def test_24h_all_clients_and_denied_need_five_actual_successes_without_assuming_six(self):
        async def run():
            report = renewal_report()
            leases = []
            for client_id in (0, 1, "denied"):
                now = [0]
                lease = self.make_lease(report, now, client_id)
                leases.append(lease)
                for _ in range(5):
                    now[0] = lease.due
                    self.assertTrue(await lease.maybe_renew())
            coverage = finalize_renewal_coverage(leases, report, expected_client_ids=[0, 1, "denied"],
                observed_end=24, requested_seconds=24)
            self.assertEqual(coverage["result"], "PASS")
            for stats in report["per_client"].values():
                self.assertEqual(stats["successes"], 5)
                self.assertEqual(stats["required_successes"], 5)
                self.assertEqual(stats["actual_session_cookie_age"], 8)
                self.assertEqual(stats["interval_seconds"], 4)
            self.assertEqual({e["client_id"] for e in report["events"]}, {0, 1, "denied"})
        asyncio.run(run())

    def test_aggregate_success_cannot_replace_unrenewed_client(self):
        async def run():
            report = renewal_report()
            first_clock, second_clock = [0], [0]
            first = self.make_lease(report, first_clock, 0)
            second = self.make_lease(report, second_clock, "denied")
            for _ in range(10):
                first_clock[0] = first.due
                await first.maybe_renew()
            self.assertEqual(report["successes"], 10)
            coverage = finalize_renewal_coverage([first, second], report, expected_client_ids=[0, "denied"],
                observed_end=24, requested_seconds=24)
            self.assertEqual(coverage["result"], "FAIL")
            self.assertIn("client_renewal_coverage:denied", coverage["failures"])
        asyncio.run(run())

    def test_overdue_renewal_fails_even_before_minimum_round_requirement(self):
        report, now = renewal_report(), [0]
        lease = self.make_lease(report, now, 0)
        coverage = finalize_renewal_coverage([lease], report, expected_client_ids=[0],
            observed_end=4, requested_seconds=4)
        self.assertEqual(lease.statistics["required_successes"], 0)
        self.assertTrue(lease.statistics["overdue_at_workload_end"])
        self.assertEqual(coverage["result"], "FAIL")

    def test_identity_missing_and_aggregate_count_tampering_fail(self):
        report, now = renewal_report(), [0]
        lease = self.make_lease(report, now, 0)
        report["successes"] = 1
        coverage = finalize_renewal_coverage([lease], report, expected_client_ids=[0, "denied"],
            observed_end=1, requested_seconds=1)
        self.assertIn("client_identity_coverage_mismatch", coverage["failures"])
        self.assertIn("per_client_aggregate_mismatch:successes", coverage["failures"])

    def test_event_truncation_never_loses_per_client_failure_or_early_loss(self):
        async def run():
            report, now, valid = renewal_report(), [0], [False]
            async def verify():
                return valid[0]
            async def renew(trace):
                now[0] += .1
            lease = ScheduledSession(4, 8, verify, renew, report, client_id="denied", clock=lambda: now[0])
            now[0] = lease.due
            self.assertFalse(await lease.maybe_renew())
            valid[0] = True
            for _ in range(2200):
                now[0] = lease.due
                await lease.maybe_renew()
            self.assertEqual(len(report["events"]), 2048)
            self.assertEqual(report["events_dropped"], 153)
            self.assertTrue(all(e["result"] == "PASS" for e in report["events"]))
            self.assertEqual(lease.statistics["failures"], 1)
            self.assertEqual(lease.statistics["early_losses"], 1)
            self.assertEqual(report["failures"], 1)
            self.assertEqual(lease.statistics["first_failure"], "unexpected_session_failure_before_scheduled_renewal")
            coverage = finalize_renewal_coverage([lease], report, expected_client_ids=["denied"],
                observed_end=now[0], requested_seconds=24)
            self.assertEqual(coverage["result"], "FAIL")
        asyncio.run(run())

    def test_client_id_is_bounded_synthetic_and_duplicate_registration_rejected(self):
        report, now = renewal_report(), [0]
        for value in ("release-user-0000", True, -1, 1000):
            with self.assertRaises(ValueError):
                self.make_lease(report, now, value)
        for index in range(1000):
            self.make_lease(report, now, index)
        self.make_lease(report, now, "denied")
        self.assertEqual(len(report["per_client"]), 1001)
        with self.assertRaises(ValueError):
            self.make_lease(report, now, 0)

    def test_first_issued_observation_and_missed_expiry_are_per_client(self):
        async def run():
            report, now = renewal_report(), [100]
            lease = self.make_lease(report, now, 0)
            now[0] = 108
            self.assertFalse(await lease.maybe_renew())
            coverage = finalize_renewal_coverage([lease], report, expected_client_ids=[0],
                observed_end=109, requested_seconds=9)
            self.assertEqual(lease.statistics["first_issued_monotonic"], 100)
            self.assertEqual(lease.statistics["observed_seconds_since_first_issue"], 9)
            self.assertEqual(lease.statistics["missed"], 1)
            self.assertEqual(coverage["result"], "FAIL")
        asyncio.run(run())


class LongRunHealthTests(unittest.TestCase):
    def test_histogram_overflow_is_bounded_and_never_understates_slow_quantiles(self):
        collector = SampleCollector()
        for milliseconds in range(60000, 70001):
            collector.append(Sample("session", milliseconds, 200, "success"))
        self.assertEqual(len(collector.groups["session"]["latency"]), 1)
        report = summarize(collector, 30, min_samples=1)
        self.assertEqual(report["operations"]["session"]["p95_ms"], 70000)
        self.assertEqual(report["result"], "FAIL")
    def test_telemetry_bounded_stream_retains_early_peak_and_missing_evidence(self):
        collector = Telemetry()
        collector.observe({"wall_time": 0, "peak_rss_bytes": 600*1024**2}, required=("cpu_seconds",))
        for i in range(20000):
            collector.observe({"wall_time": i+1, "rss_bytes": 10*1024**2, "cpu_seconds": i})
        self.assertEqual(len(collector.samples), 4096)
        self.assertEqual(collector.count, 20001)
        self.assertEqual(collector.maxima["memory_bytes"], 600*1024**2)
        self.assertIn("cpu_seconds", collector.missing)
        self.assertLessEqual(len(collector.segments), 512)

    def test_memory_current_and_peak_fallback_and_conservative_cap(self):
        self.assertEqual(memory_measurement({"peak_rss_bytes": 60}), ("peak_rss_bytes", 60))
        self.assertEqual(memory_measurement({"rss_bytes": float("nan"), "peak_rss_bytes": 60}), ("peak_rss_bytes", 60))
        self.assertEqual(memory_measurement({"rss_bytes": 10, "peak_rss_bytes": 60}, conservative=True), ("peak_rss_bytes", 60))
        self.assertEqual(memory_measurement({"rss_bytes": -1}), (None, None))

    def test_linux_sampler_reads_current_rss_and_explicitly_falls_back_to_peak(self):
        fake_resource = SimpleNamespace(RUSAGE_SELF=0, getrusage=lambda _: SimpleNamespace(ru_maxrss=200))
        with patch.dict(sys.modules, {"resource": fake_resource}), \
                patch("qa.release_acceptance.fixture_server.os.name", "posix"), \
                patch("sys.platform", "linux"), \
                patch("qa.release_acceptance.fixture_server.os.sysconf", return_value=4096, create=True):
            with patch("pathlib.Path.read_text", return_value="1 3"):
                value = process_memory()
                self.assertEqual(value["rss_bytes"], 3*4096)
                self.assertEqual(value["peak_rss_bytes"], 200*1024)
                self.assertEqual(value["memory_metric_current"], 1)
            with patch("pathlib.Path.read_text", side_effect=OSError):
                value = process_memory()
                self.assertNotIn("rss_bytes", value)
                self.assertEqual(value["peak_rss_bytes"], 200*1024)
                self.assertEqual(value["memory_metric_current"], 0)

    def test_growth_needs_real_hour_after_warmup_and_fails_observed_positive_trend(self):
        for metric, rate, expected in (("rss_bytes", 31, "PASS"), ("rss_bytes", 33, "FAIL"),
                                        ("peak_rss_bytes", 33, "FAIL")):
            growth = MemoryGrowth(0)
            growth.observe({"wall_time": 899, metric: 1000*1024**2})
            for t in range(900, 4501, 60):
                growth.observe({"wall_time": t, metric: (100 + rate*(t-900)/3600)*1024**2})
            self.assertEqual(growth.evidence()["result"], expected)
            self.assertEqual(growth.evidence()["windows"][0]["metric"], metric)
            self.assertEqual(growth.evidence()["windows"][0]["observed_seconds"], 3600)
        short = MemoryGrowth(0)
        for t in range(900, 1801, 60):
            short.observe({"wall_time": t, "rss_bytes": t*1024**2})
        self.assertEqual(short.evidence()["result"], "NOT_ENOUGH_OBSERVATION")

    def test_sparse_growth_cannot_claim_hour_validation(self):
        growth = MemoryGrowth(0)
        for t in (900, 4500):
            growth.observe({"wall_time": t, "rss_bytes": 100*1024**2})
        self.assertEqual(growth.evidence()["result"], "FAIL")

    def test_late_latency_regression_cannot_be_diluted_by_healthy_first_window(self):
        health = WindowHealth(300, SampleCollector, summarize, {"min_samples": 20})
        all_samples = valid_samples(1000)
        for sample in all_samples:
            health.observe(sample, 1)
        late = valid_samples(20)
        for sample in late:
            if sample.operation == "ledger_read":
                sample.milliseconds = 1001
            health.observe(sample, 301)
        self.assertEqual(summarize(all_samples+late, 600)["result"], "PASS")
        evidence = health.finish(600)
        self.assertEqual(evidence["result"], "FAIL")
        self.assertEqual(evidence["retained_windows"][1]["result"], "FAIL")

    def test_complete_window_low_rate_fails_and_short_tail_keeps_auth_redline(self):
        health = WindowHealth(300, SampleCollector, summarize, {"min_samples": 20}, offered_rate=20)
        for sample in valid_samples():
            health.observe(sample, 1)
        evidence = health.finish(300)
        self.assertIn("offered_target_rate_not_delivered", evidence["retained_windows"][0]["failures"])
        tail = WindowHealth(300, SampleCollector, summarize, {"min_samples": 20})
        tail.observe(Sample("session", 1, 401, "unexpected"), 1)
        self.assertEqual(tail.finish(2)["result"], "FAIL")

    def test_no_arrivals_cannot_skip_complete_health_windows(self):
        health = WindowHealth(300, SampleCollector, summarize, {"min_samples": 20})
        for sample in valid_samples():
            health.observe(sample, 1)
        evidence = health.finish(900)
        self.assertEqual(evidence["total_windows"], 3)
        self.assertEqual(evidence["result"], "FAIL")
        self.assertIn("zero_load", evidence["retained_windows"][1]["failures"])

    def test_empty_millisecond_tail_is_retained_without_invented_error_rate(self):
        for seconds in (300, 7200):
            with self.subTest(seconds=seconds):
                health = WindowHealth(300, SampleCollector, summarize, {"min_samples": 20}, offered_rate=20)
                for index in range(seconds // 300):
                    for sample in valid_samples(1200):
                        health.observe(sample, index*300+1)
                evidence = health.finish(seconds+.0072332)
                windows = evidence["retained_windows"]
                complete = [window for window in windows if window["complete_window"]]
                self.assertEqual(len(complete), seconds // 300)
                self.assertTrue(all(window["result"] == "PASS" for window in complete))
                self.assertEqual(sum(window["samples"] for window in complete), seconds*20)
                self.assertEqual(evidence["total_windows"], seconds // 300+1)
                self.assertEqual(evidence["result"], "PASS")
                tail = windows[-1]
                self.assertFalse(tail["complete_window"])
                self.assertEqual(tail["result"], "PARTIAL")
                self.assertEqual(tail["samples"], 0)
                self.assertEqual(tail["failures"], [])
                self.assertIsNone(tail["unexpected_error_rate"])
                self.assertIs(tail["unexpected_error_rate_observed"], False)
                self.assertAlmostEqual(tail["elapsed_seconds"], .0072332)

    def test_empty_complete_window_still_fails_zero_load_and_capacity(self):
        health = WindowHealth(300, SampleCollector, summarize, {"min_samples": 20}, offered_rate=20)
        evidence = health.finish(300)
        self.assertEqual(evidence["result"], "FAIL")
        self.assertEqual(evidence["total_windows"], 1)
        window = evidence["retained_windows"][0]
        self.assertTrue(window["complete_window"])
        self.assertEqual(window["unexpected_error_rate"], 1)
        self.assertIn("zero_load", window["failures"])
        self.assertIn("offered_target_rate_not_delivered", window["failures"])

    def test_nonempty_partial_keeps_real_error_auth_timeout_and_latency_failures(self):
        cases = (
            (Sample("ledger_read", 1, 500, "unexpected"), "unexpected_error_rate_exceeded"),
            (Sample("session", 1, 401, "unexpected"), "session:business_or_auth_redline"),
            (Sample("session", 10001, 0, "unexpected", "timeout"), "session:timeout"),
            (Sample("ledger_read", 1001, 200, "success"), "ledger_read:p95_exceeded"),
        )
        for sample, failure in cases:
            with self.subTest(failure=failure):
                health = WindowHealth(300, SampleCollector, summarize, {"min_samples": 20})
                health.observe(sample, .001)
                evidence = health.finish(.0072332)
                self.assertEqual(evidence["result"], "FAIL")
                tail = evidence["retained_windows"][0]
                self.assertEqual(tail["samples"], 1)
                self.assertFalse(tail["complete_window"])
                self.assertIn(failure, tail["failures"])
                self.assertIsNotNone(tail["unexpected_error_rate"])

    def test_completed_slow_call_in_tail_is_retained_and_real_drain_rate_still_fails(self):
        # test_pacing exercises actual asynchronous deadline/slow-call drain.
        # Here its later completion must survive the health/statistics layer.
        from qa.release_acceptance.run import summarize_paced_phase
        samples = valid_samples()
        health = WindowHealth(.02, SampleCollector, summarize, {"min_samples": 20}, offered_rate=5000)
        for sample in samples:
            health.observe(sample, .001)
        completed_slow_call = Sample("session", 39, 200, "success")
        samples.append(completed_slow_call)
        health.observe(completed_slow_call, .039)
        evidence = health.finish(.039)
        self.assertEqual(evidence["total_windows"], 2)
        self.assertEqual(evidence["retained_windows"][0]["result"], "PASS")
        tail = evidence["retained_windows"][1]
        self.assertEqual(tail["result"], "PARTIAL")
        self.assertEqual(tail["samples"], 1)
        self.assertEqual(tail["operations"]["session"]["p95_ms"], 39)
        self.assertEqual(tail["unexpected_error_rate"], 0)
        actual = summarize_paced_phase(samples, .039, 5000, .95)
        self.assertEqual(actual["failures"], ["offered_target_rate_not_delivered"])
        self.assertEqual(actual["result"], "FAIL")
        self.assertEqual(actual["elapsed_seconds"], .039)

    def test_disk_projection_uses_observed_scan_times_and_cannot_claim_soak(self):
        trend = DiskTrend()
        trend.observe({"fixture_disk_bytes": 100, "disk_measurement_wall_time": 10})
        self.assertIsNone(trend.evidence()["observed_bytes_per_second"])
        trend.observe({"fixture_disk_bytes": 200, "disk_measurement_wall_time": 20})
        self.assertEqual(trend.evidence()["observed_bytes_per_second"], 10)
        self.assertEqual(trend.evidence()["linear_additional_24h_bytes"], 864000)
        self.assertIn("never a PASS", trend.evidence()["scope"])

    def test_24h_configuration_requires_explicit_resource_caps_and_hour_growth_window(self):
        args = parser().parse_args(["--mode", "soak", "--duration", "86400", "--max-memory-mb", "512",
                                   "--max-queue", "128", "--max-fixture-disk-mb", "1024"])
        validate(args)
        self.assertEqual(args.renewal_interval, 14400)
        args.memory_growth_window_seconds = 3599
        with self.assertRaises(ValueError):
            validate(args)
        args.memory_growth_window_seconds = 3600
        args.max_fixture_disk_mb = 0
        with self.assertRaises(ValueError):
            validate(args)


if __name__ == "__main__":
    unittest.main()
