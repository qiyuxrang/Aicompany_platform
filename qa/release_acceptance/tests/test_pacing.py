"""Arrival-window and real-completion regressions without HTTP or PostgreSQL."""
import asyncio
import time
import unittest

from qa.release_acceptance.run import (finish_phase_window, leased_business_call,
                                     paced_phase_worker, summarize_paced_phase)
from qa.release_acceptance.sessions import ScheduledSession
from qa.release_acceptance.tests.test_acceptance import valid_samples


class PacingTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_client_does_not_sleep_to_next_arrival_outside_window(self):
        now, sleeps, arrivals = [0.0], [], []

        async def sleep(delay):
            sleeps.append(delay)
            now[0] += delay

        async def perform(index, iteration, due):
            arrivals.append((index, iteration, due, now[0]))
            now[0] += .001
            return True

        await paced_phase_worker(499, 500, 100, 0, 60, asyncio.Event(), perform,
                                 clock=lambda: now[0], sleep=sleep)
        self.assertEqual(len(arrivals), 12)
        self.assertAlmostEqual(arrivals[-1][2], 59.99)
        self.assertAlmostEqual(now[0], 59.991)
        self.assertEqual(len(sleeps), 12)
        self.assertTrue(all(start < 60 for _, _, _, start in arrivals))

    async def test_fast_workers_still_wait_for_entire_real_window(self):
        started = time.monotonic()
        deadline = started + .03
        called = asyncio.Event()

        async def perform(*unused):
            called.set()
            return True

        worker = asyncio.create_task(paced_phase_worker(
            0, 1, 1, started, deadline, asyncio.Event(), perform))
        window = asyncio.create_task(finish_phase_window([worker], deadline, asyncio.Event()))
        await called.wait()
        await worker
        self.assertFalse(window.done())
        ended = await asyncio.wait_for(window, 1)
        self.assertGreaterEqual(ended, deadline)

    async def test_admitted_slow_call_finishes_after_deadline_and_low_delivery_fails(self):
        now, completed, cancelled = [0.0], [], []
        admitted, release = asyncio.Event(), asyncio.Event()
        aborted = asyncio.Event()
        lease = self.lease(lambda: now[0])

        async def action():
            admitted.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            completed.append(True)
            return valid_samples()

        async def perform(*unused):
            self.assertEqual(await leased_business_call(
                lease, action, .02, aborted, clock=lambda: now[0]), valid_samples())
            return True

        window = asyncio.create_task(finish_phase_window(
            [paced_phase_worker(0, 1, 1, 0, .02, aborted, perform, clock=lambda: now[0])],
            .02, aborted, clock=lambda: now[0]))
        await admitted.wait()
        now[0] = .02
        await asyncio.sleep(.025)  # The independent deadline waiter can finish.
        self.assertFalse(window.done())
        self.assertEqual(lease.active, 1)
        now[0] = .04
        release.set()
        ended = await asyncio.wait_for(window, 1)
        self.assertEqual(ended, .04)
        self.assertEqual(completed, [True])
        self.assertEqual(cancelled, [])
        self.assertEqual(lease.active, 0)
        # All HTTP semantics/latencies pass; real drain time alone makes the
        # unchanged 95% delivery gate fail. A fixed requested denominator lies.
        nominal = summarize_paced_phase(valid_samples(), .02, 5000, .95)
        actual = summarize_paced_phase(valid_samples(), ended, 5000, .95)
        self.assertEqual(nominal["result"], "PASS")
        self.assertEqual(actual["failures"], ["offered_target_rate_not_delivered"])
        self.assertEqual(actual["result"], "FAIL")
        self.assertEqual(actual["logical_operations_per_second"], 2500)

    async def test_renewal_lease_wait_crossing_deadline_admits_no_new_call(self):
        now, calls = [0.0], []
        lease = self.lease(lambda: now[0])
        lease.renewing = True

        async def action():
            calls.append(True)
            return object()

        task = asyncio.create_task(leased_business_call(
            lease, action, .02, asyncio.Event(), clock=lambda: now[0]))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        now[0] = .021
        async with lease.condition:
            lease.renewing = False
            lease.condition.notify_all()
        self.assertIsNone(await task)
        self.assertEqual(calls, [])
        self.assertEqual(lease.active, 0)

    async def test_abort_after_lease_wait_admits_no_new_call(self):
        aborted, calls = asyncio.Event(), []
        lease = self.lease(time.monotonic)
        lease.renewing = True

        async def action():
            calls.append(True)

        task = asyncio.create_task(leased_business_call(
            lease, action, time.monotonic()+1, aborted))
        await asyncio.sleep(0)
        aborted.set()
        async with lease.condition:
            lease.renewing = False
            lease.condition.notify_all()
        self.assertIsNone(await task)
        self.assertEqual(calls, [])
        self.assertEqual(lease.active, 0)

    async def test_call_error_propagates_and_releases_lease(self):
        lease = self.lease(time.monotonic)
        failure = RuntimeError("synthetic-business-error")

        async def action():
            raise failure

        with self.assertRaises(RuntimeError) as raised:
            await finish_phase_window([leased_business_call(
                lease, action, time.monotonic()+1, asyncio.Event())],
                time.monotonic()+1, asyncio.Event())
        self.assertIs(raised.exception, failure)
        self.assertEqual(lease.active, 0)

    async def test_redline_abort_does_not_wait_for_long_remaining_window(self):
        aborted = asyncio.Event()
        window = asyncio.create_task(finish_phase_window([], time.monotonic()+3600, aborted))
        await asyncio.sleep(0)
        aborted.set()
        await asyncio.wait_for(window, 1)

    async def test_worker_error_drains_other_owned_workers_without_masking_error(self):
        started, closed = asyncio.Event(), []
        failure = RuntimeError("synthetic-worker-error")

        async def pending():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(True)

        async def failing():
            await started.wait()
            raise failure

        with self.assertRaises(RuntimeError) as raised:
            await finish_phase_window([failing(), pending()], time.monotonic()+3600, asyncio.Event())
        self.assertIs(raised.exception, failure)
        self.assertEqual(closed, [True])

    async def test_caller_cancellation_drains_all_owned_workers(self):
        started, closed, tasks = asyncio.Event(), [], []

        async def pending(index):
            tasks.append(asyncio.current_task())
            if len(tasks) == 2:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(index)

        window = asyncio.create_task(finish_phase_window(
            [pending(0), pending(1)], time.monotonic()+3600, asyncio.Event()))
        await started.wait()
        window.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await window
        self.assertCountEqual(closed, [0, 1])
        self.assertTrue(all(task.done() for task in tasks))

    @staticmethod
    def lease(clock):
        async def unused(*args):
            raise AssertionError("No renewal/login is invoked in this scheduling test")
        return ScheduledSession(4, 8, unused, unused, {}, client_id=0, clock=clock)
