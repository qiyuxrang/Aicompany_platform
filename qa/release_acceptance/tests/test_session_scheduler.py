"""Independent renewal lifecycle guards using synthetic HTTP transports only."""
import asyncio
import unittest
from unittest.mock import patch

import httpx

from qa.release_acceptance.run import (
    drain_session_renewals, login, renewal_boundary_snapshot, request_sample,
    scheduled_session_renewals,
)
from qa.release_acceptance.sessions import ScheduledSession, finalize_renewal_coverage


class SessionSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = [0.0]
        self.report = {"attempts": 0, "successes": 0, "failures": 0,
                       "events": [], "events_dropped": 0}
        self.stop, self.aborted, self.failures = asyncio.Event(), asyncio.Event(), []
        self.leases = []

    async def make_session(self, client_id, *, login_gate=None, verify_gate=None,
                           verify_status=200, interval=4, session_age=8):
        entered, done, calls = asyncio.Event(), asyncio.Event(), []
        token = ["before"]

        async def handler(request):
            calls.append(request.url.path)
            self.assertEqual(request.extensions["timeout"]["read"], 10)
            if request.url.path == "/api/me/":
                if verify_gate is not None:
                    await verify_gate.wait()
                return httpx.Response(verify_status, json={"username": "synthetic"})
            if request.url.path == "/api/login/":
                self.assertEqual(request.headers["X-CSRFToken"], "before")
                entered.set()
                if login_gate is not None:
                    await login_gate.wait()
                token[0] = "after"
                return httpx.Response(200, json={"username": "synthetic"})
            return httpx.Response(200, json={"csrfToken": token[0]})

        client = httpx.AsyncClient(base_url="http://fixture", timeout=10,
                                   transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)

        async def verify():
            return await request_sample(client, "session", "GET", "/api/me/", username="synthetic")

        async def renew(trace):
            await login(client, "synthetic", "synthetic-not-retained", trace)
            done.set()

        lease = ScheduledSession(interval, session_age, verify, renew, self.report,
                                 client_id=client_id, clock=lambda: self.now[0])
        self.leases.append(lease)
        return lease, entered, done, calls

    def start(self):
        task = asyncio.create_task(scheduled_session_renewals(
            self.leases, self.stop, self.aborted, self.failures, clock=lambda: self.now[0]))
        self.addAsyncCleanup(self.cancel_scheduler, task)
        return task

    async def cancel_scheduler(self, task):
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def assert_workers_returned(self):
        self.assertFalse([task for task in asyncio.all_tasks()
                          if task.get_name().startswith("release-renew-client-")])
        for lease in self.leases:
            self.assertFalse(lease.renewing)
            self.assertEqual(lease.active, 0)

    async def test_slow_login_does_not_block_other_due_client_real_csrf_login(self):
        release = asyncio.Event()
        first, entered, _, _ = await self.make_session(0, login_gate=release)
        second, _, completed, calls = await self.make_session(1)
        self.now[0] = 4
        task = self.start()
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(completed.wait(), 1)
        self.assertTrue(first.renewing)
        self.assertEqual(second.statistics["successes"], 1)
        self.assertEqual(calls, ["/api/me/", "/api/csrf/", "/api/login/", "/api/csrf/"])
        self.stop.set()
        release.set()
        self.assertTrue(await asyncio.wait_for(task, 1))
        self.assertEqual(self.report["successes"], 2)
        self.assert_workers_returned()

    async def test_business_wait_is_local_and_stop_prevents_its_round_or_http(self):
        first, _, _, calls = await self.make_session(0)
        _, _, other_completed, _ = await self.make_session(1)
        async with first.operation():
            self.now[0] = 4
            task = self.start()
            await asyncio.wait_for(other_completed.wait(), 1)
            self.assertEqual(calls, [])
            self.assertEqual(first.statistics["rounds_started"], 0)
            self.stop.set()
        self.assertTrue(await asyncio.wait_for(task, 1))
        self.assertEqual(first.statistics["attempts"], 0)
        self.assertEqual(calls, [])
        self.assert_workers_returned()

    async def test_awakened_business_lock_waiter_cannot_start_a_post_stop_round(self):
        lease, _, _, calls = await self.make_session(0)
        entered, release = asyncio.Event(), asyncio.Event()
        self.now[0] = 4

        async def business():
            async with lease.operation():
                self.stop.set()
                entered.set()
                await release.wait()

        # Reproduce the old external check/release gap with a REAL queued
        # condition waiter. Lock fairness lets that business reader acquire
        # before the renewal's next acquire, despite no intervening sleep.
        async with lease.condition:
            self.assertEqual(lease.active, 0)
            self.assertFalse(self.stop.is_set())
            reader = asyncio.create_task(business())
            await asyncio.sleep(0)
            self.assertFalse(reader.done())
        renewal = asyncio.create_task(lease.maybe_renew(stop=self.stop))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual(lease.active, 1)
            self.assertFalse(lease.renewing)
            self.assertEqual(lease.statistics.get("rounds_started", 0), 0)
            self.assertEqual(calls, [])
        finally:
            release.set()
            await reader
        self.assertTrue(await asyncio.wait_for(renewal, 1))
        self.assertEqual(lease.statistics.get("rounds_started", 0), 0)
        self.assertEqual(self.report["attempts"], 0)
        self.assertEqual(calls, [])
        self.assert_workers_returned()

    async def test_all_inflight_real_logins_drain_without_any_new_round(self):
        release = asyncio.Event()
        first, first_entered, _, _ = await self.make_session(0, login_gate=release)
        second, second_entered, _, _ = await self.make_session(1, login_gate=release)
        self.now[0] = 4
        task = self.start()
        await asyncio.wait_for(asyncio.gather(first_entered.wait(), second_entered.wait()), 1)
        snapshot = renewal_boundary_snapshot(self.leases)
        self.assertEqual(snapshot["inflight_client_ids"], [0, 1])
        self.stop.set()
        drain = asyncio.create_task(drain_session_renewals(
            task, self.stop, self.leases, timeout=1, workload_end=4, boundary_snapshot=snapshot))
        release.set()
        evidence = await asyncio.wait_for(drain, 1)
        self.assertEqual(evidence["result"], "PASS")
        self.assertEqual(evidence["new_attempts_during_drain"], 0)
        self.assertEqual(evidence["successes_during_drain"], 2)
        self.assertEqual(first.statistics["rounds_started"], 1)
        self.assertEqual(second.statistics["rounds_started"], 1)
        self.assert_workers_returned()

    async def test_false_auth_failure_stops_new_rounds_but_does_not_cancel_started_login(self):
        verify_release, login_release = asyncio.Event(), asyncio.Event()
        bad, _, _, _ = await self.make_session(0, verify_gate=verify_release, verify_status=401)
        good, entered, _, _ = await self.make_session(1, login_gate=login_release)
        self.now[0] = 4
        task = self.start()
        await asyncio.wait_for(entered.wait(), 1)
        verify_release.set()
        await asyncio.wait_for(self.stop.wait(), 1)
        self.assertTrue(self.aborted.is_set())
        self.assertFalse(task.done())
        self.assertTrue(good.renewing)
        login_release.set()
        self.assertFalse(await asyncio.wait_for(task, 1))
        self.assertEqual(bad.statistics["early_losses"], 1)
        self.assertEqual(good.statistics["successes"], 1)
        self.assertEqual(good.statistics["failures"], 0)
        self.assertEqual(self.failures, ["scheduled_session_renewal_failed"])
        self.assert_workers_returned()

    async def test_fatal_worker_error_preserves_original_and_collects_siblings(self):
        release = asyncio.Event()
        first, _, _, _ = await self.make_session(0)
        second, entered, _, _ = await self.make_session(1, login_gate=release)
        original = first.maybe_renew
        failure = RuntimeError("synthetic-scheduler-failure")

        async def unexpected_error(**kwargs):
            await original(**kwargs)  # Actual verify/login/CSRF still executes.
            await entered.wait()
            raise failure

        first.maybe_renew = unexpected_error
        self.now[0] = 4
        task = self.start()
        with self.assertRaises(RuntimeError) as caught:
            await asyncio.wait_for(task, 1)
        self.assertIs(caught.exception, failure)
        self.assertTrue(self.aborted.is_set())
        self.assertEqual(second.statistics["failures"], 1)
        self.assertEqual(second.statistics["first_failure"], "renewal_cancelled_at_workload_boundary")
        self.assert_workers_returned()

    async def test_external_cancellation_collects_all_started_login_tasks_and_unlocks(self):
        release = asyncio.Event()
        _, first_entered, _, _ = await self.make_session(0, login_gate=release)
        _, second_entered, _, _ = await self.make_session(1, login_gate=release)
        self.now[0] = 4
        task = self.start()
        await asyncio.wait_for(asyncio.gather(first_entered.wait(), second_entered.wait()), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.report["failures"], 2)
        self.assertTrue(self.aborted.is_set())
        self.assert_workers_returned()

    async def test_bounded_drain_timeout_records_failure_and_collects_all_workers(self):
        release = asyncio.Event()
        _, first_entered, _, _ = await self.make_session(0, login_gate=release)
        _, second_entered, _, _ = await self.make_session(1, login_gate=release)
        self.now[0] = 4
        task = self.start()
        await asyncio.wait_for(asyncio.gather(first_entered.wait(), second_entered.wait()), 1)
        evidence = await asyncio.wait_for(drain_session_renewals(
            task, self.stop, self.leases, timeout=.01, workload_end=4), 1)
        self.assertEqual(evidence["result"], "FAIL")
        self.assertEqual(evidence["failure"], "scheduled_renewal_drain_timeout")
        self.assertEqual(self.report["failures"], 2)
        self.assert_workers_returned()

    async def test_sub_ten_millisecond_due_wait_has_no_floor_or_early_io(self):
        _, _, completed, calls = await self.make_session(0, interval=.004, session_age=1)
        actual_wait_for = asyncio.wait_for
        timeouts = []

        async def observed_wait(awaitable, *, timeout):
            timeouts.append(timeout)
            self.assertAlmostEqual(timeout, .004)
            if len(timeouts) == 1:
                self.assertEqual(calls, [])
                self.now[0] = .004
            return await actual_wait_for(awaitable, timeout=timeout)

        with patch("qa.release_acceptance.run.asyncio.wait_for", observed_wait):
            task = self.start()
            await actual_wait_for(completed.wait(), 1)
            self.stop.set()
            self.assertTrue(await actual_wait_for(task, 1))
        self.assertTrue(timeouts)
        self.assertLessEqual(len(timeouts), 2)
        self.assertEqual(self.report["successes"], 1)
        self.assert_workers_returned()

    async def test_due_coverage_and_real_expiry_failures_remain_strict(self):
        lease, _, _, calls = await self.make_session(0)
        self.now[0] = 4
        self.stop.set()
        self.assertTrue(await asyncio.wait_for(self.start(), 1))
        coverage = finalize_renewal_coverage(self.leases, self.report, expected_client_ids=[0],
                                             observed_end=4, requested_seconds=4)
        self.assertEqual(coverage["result"], "FAIL")
        self.assertTrue(lease.statistics["overdue_at_workload_end"])
        self.assertEqual(lease.statistics["successes"], 0)
        self.assertEqual(calls, [])
        self.stop.clear()
        self.now[0] = 8
        self.assertFalse(await asyncio.wait_for(self.start(), 1))
        self.assertEqual(lease.statistics["missed"], 1)
        self.assertEqual(calls, [])
        self.assert_workers_returned()


if __name__ == "__main__":
    unittest.main()
