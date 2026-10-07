"""Scheduled synthetic session renewal; never reacts to or retries business auth failures."""
import asyncio
from contextlib import asynccontextmanager
import math
import time


class ScheduledSession:
    def __init__(self, interval, session_age, verify, renew, report, *, client_id, clock=time.monotonic):
        if not math.isfinite(interval) or not 0 < interval < session_age:
            raise ValueError("renewal interval must precede actual fixture session expiry")
        if not (type(client_id) is int and 0 <= client_id < 1000 or client_id == "denied"):
            raise ValueError("client_id must be a synthetic index or denied")
        clients = report.setdefault("per_client", {})
        key = str(client_id)
        if key in clients or len(clients) >= 1001:
            raise ValueError("duplicate or excessive synthetic clients")
        report["schema_version"] = 2
        self.client_id = client_id
        self.interval, self.session_age = interval, session_age
        self.verify, self.renew, self.report, self.clock = verify, renew, report, clock
        self.authenticated_at = clock()
        self.due = self.authenticated_at + interval
        self.statistics = {"client_id": client_id, "actual_session_cookie_age": session_age,
            "interval_seconds": interval, "first_issued_monotonic": self.authenticated_at,
            "first_issued_wall_time": time.time(), "attempts": 0, "successes": 0, "failures": 0,
            "missed": 0, "early_losses": 0, "first_attempt_wall_time": None, "last_attempt_wall_time": None,
            "first_success_wall_time": None, "last_success_wall_time": None, "first_failure": None}
        clients[key] = self.statistics
        self.condition = asyncio.Condition()
        self.active = 0
        self.renewing = False

    @asynccontextmanager
    async def operation(self):
        async with self.condition:
            await self.condition.wait_for(lambda: not self.renewing)
            self.active += 1
        try:
            yield
        finally:
            async with self.condition:
                self.active -= 1
                self.condition.notify_all()

    async def maybe_renew(self, *, stop=None):
        async with self.condition:
            await self.condition.wait_for(lambda: not self.renewing and (stop is None or self.active == 0))
            if stop is not None and stop.is_set():
                return True
            if self.clock() < self.due:
                return True
            if stop is not None:
                self.statistics["rounds_started"] = self.statistics.get("rounds_started", 0) + 1
            self.renewing = True
        started = time.monotonic()
        event = {"client_id": self.client_id, "wall_time": time.time(), "scheduled_age_seconds": self.clock() - self.authenticated_at}
        success = False
        try:
            async with self.condition:
                await self.condition.wait_for(lambda: self.active == 0)
            if self.clock() - self.authenticated_at >= self.session_age:
                event["failure"] = "scheduled_renewal_missed_expiry"
            else:
                check = await self.verify()
                valid = check is True or getattr(check, "result", None) == "success"
                event["precheck"] = {"result": "success" if valid else "unexpected",
                                     "status": getattr(check, "status", None), "error": getattr(check, "error", "")}
                if not valid:
                    # Early expiry/version invalidation must fail before login; never repair it silently.
                    event["failure"] = "unexpected_session_failure_before_scheduled_renewal"
                else:
                    trace = {}
                    await self.renew(trace)
                    event["login"] = trace
                    self.authenticated_at = self.clock()
                    self.due = self.authenticated_at + self.interval
                    success = True
        except asyncio.CancelledError:
            event["failure"] = "renewal_cancelled_at_workload_boundary"
            raise
        except Exception as error:
            event.update(failure="scheduled_real_login_failed", error_class=type(error).__name__)
        finally:
            event.update(result="PASS" if success else "FAIL", seconds=time.monotonic() - started)
            self.report["attempts"] += 1
            self.report["successes" if success else "failures"] += 1
            stats = self.statistics
            stats["attempts"] += 1
            stats["successes" if success else "failures"] += 1
            stats["first_attempt_wall_time"] = stats["first_attempt_wall_time"] or event["wall_time"]
            stats["last_attempt_wall_time"] = event["wall_time"]
            if success:
                stats["first_success_wall_time"] = stats["first_success_wall_time"] or event["wall_time"]
                stats["last_success_wall_time"] = event["wall_time"]
            else:
                stats["first_failure"] = stats["first_failure"] or event.get("failure", "renewal_failed")
                stats["missed"] += int(event.get("failure") == "scheduled_renewal_missed_expiry")
                stats["early_losses"] += int(event.get("failure") == "unexpected_session_failure_before_scheduled_renewal")
            events = self.report["events"]
            if len(events) >= 2048:
                events.pop(0)
                self.report["events_dropped"] += 1
            events.append(event)
            async with self.condition:
                self.renewing = False
                self.condition.notify_all()
        return success


def finalize_renewal_coverage(leases, report, *, expected_client_ids, observed_end, requested_seconds):
    """Require every synthetic client, not interchangeable aggregate successes.

    One terminal interval is excluded from the minimum-round count: successful
    real logins reset due times after their actual completion and take time.
    Independently, an already-due renewal at workload end fails.
    Thus 24h at 4h requires >=5 for each client, without inventing a sixth round.
    """
    expected = {str(value) for value in expected_client_ids}
    clients = report.get("per_client", {})
    failures = []
    if set(clients) != expected or len(leases) != len(expected):
        failures.append("client_identity_coverage_mismatch")
    for lease in leases:
        stats = lease.statistics
        span = max(0, observed_end - stats["first_issued_monotonic"])
        required = max(0, math.floor(span / lease.interval) - 1,
                       math.floor(requested_seconds / lease.interval) - 1)
        overdue = observed_end >= lease.due
        stats.update(observed_seconds_since_first_issue=span, required_successes=required,
                     next_due_monotonic=lease.due, overdue_at_workload_end=overdue)
        invalid = stats["failures"] or stats["missed"] or stats["early_losses"] or overdue or stats["successes"] < required
        stats["coverage_result"] = "FAIL" if invalid else "PASS"
        if invalid:
            failures.append("client_renewal_coverage:" + str(lease.client_id))
    for key in ("attempts", "successes", "failures"):
        if report.get(key) != sum(value[key] for value in clients.values()):
            failures.append("per_client_aggregate_mismatch:" + key)
    coverage = {"expected_clients": len(expected), "observed_clients": len(clients),
        "workload_end_monotonic": observed_end, "requested_workload_seconds": requested_seconds,
        "policy": "each client covers conservative complete rounds and has no failure/missed/overdue renewal",
        "terminal_round_policy": "one interval excluded for actual real-login/scheduler drift; final overdue still fails",
        "result": "FAIL" if failures else "PASS", "failures": failures}
    report["coverage"] = coverage
    return coverage
