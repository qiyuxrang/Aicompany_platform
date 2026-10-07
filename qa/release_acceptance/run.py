"""Real HTTP mixed-workload acceptance against an owned synthetic Django/Waitress fixture."""
import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import asdict, dataclass
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid

import httpx
from .disk_monitor import cache_is_fresh
from .sessions import ScheduledSession, finalize_renewal_coverage
from .telemetry import Telemetry, DiskTrend, memory_measurement
from .health import MemoryGrowth, WindowHealth
from .fixture_server import process_memory

ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ("session", "ledger_read", "ledger_write", "permission_denied", "agent_disabled")
HISTOGRAM_OVERFLOW_MS = 60000


def validate_database_pool_identity(identity):
    """Reject an unpooled PostgreSQL fixture or invented/unsafe pool evidence."""
    from backend.config.database import CONNECT_TIMEOUT_SECONDS, POOL_OPTIONS, POOL_STATS
    evidence = identity.get("database_pool")
    if identity.get("database_kind") not in ("sqlite", "postgresql"):
        raise ValueError("fixture_database_pool_identity_mismatch")
    if identity.get("database_kind") != "postgresql":
        if evidence != {"enabled": False, "scope": "not_postgresql"}:
            raise ValueError("fixture_database_pool_identity_mismatch")
        return
    expected = {"enabled": True, "scope": "per_process", "implementation": "django_psycopg3",
                "version": importlib.metadata.version("psycopg-pool"),
                "min_size": POOL_OPTIONS["min_size"], "max_size": POOL_OPTIONS["max_size"],
                "timeout_seconds": POOL_OPTIONS["timeout"], "max_waiting": POOL_OPTIONS["max_waiting"],
                "conn_max_age": 0, "health_checks": True,
                "connect_timeout_seconds": CONNECT_TIMEOUT_SECONDS}
    if not isinstance(evidence, dict) or set(evidence) != set(expected) | {"stats"} or \
            any(evidence.get(key) != value or type(evidence.get(key)) is not type(value)
                for key, value in expected.items()):
        raise ValueError("fixture_database_pool_identity_mismatch")
    stats = evidence["stats"]
    if not isinstance(stats, dict) or set(stats) - set(POOL_STATS) or \
            any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in stats.values()) or \
            stats.get("pool_min") != 1 or stats.get("pool_max") != 4 or \
            type(stats.get("pool_size")) is not int or not 1 <= stats["pool_size"] <= 4 or \
            type(stats.get("pool_available")) is not int or not 0 <= stats["pool_available"] <= stats["pool_size"] or \
            type(stats.get("requests_waiting")) is not int or not 0 <= stats["requests_waiting"] <= 16:
        raise ValueError("fixture_database_pool_identity_mismatch")


@dataclass
class Sample:
    operation: str
    milliseconds: float
    status: int
    result: str
    error: str = ""


class SampleCollector:
    """Bounded 1ms histograms; long runs never retain every HTTP sample in RAM."""
    def __init__(self):
        self.groups = defaultdict(lambda: {"count": 0, "latency": Counter(), "successful_latency": Counter(),
            "results": Counter(), "statuses": Counter(), "errors": Counter(), "hard_timeout": False,
            "hard_business_or_auth": False, "max_latency": 0., "max_successful_latency": 0.})
        self.count = 0

    def append(self, sample):
        group = self.groups[sample.operation]
        self.count += 1
        group["count"] += 1
        group["latency"][min(math.ceil(sample.milliseconds), HISTOGRAM_OVERFLOW_MS)] += 1
        group["max_latency"] = max(group["max_latency"], sample.milliseconds)
        group["results"][sample.result] += 1
        group["statuses"][str(sample.status)] += 1
        if sample.result == "success":
            group["successful_latency"][min(math.ceil(sample.milliseconds), HISTOGRAM_OVERFLOW_MS)] += 1
            group["max_successful_latency"] = max(group["max_successful_latency"], sample.milliseconds)
        if sample.error:
            group["errors"][sample.error] += 1
        group["hard_timeout"] |= sample.error == "timeout"
        group["hard_business_or_auth"] |= sample.error in ("failed_200", "invalid_or_failed_business_payload") or \
            (sample.result == "unexpected" and sample.status in (200, 401, 403))

    def __len__(self):
        return self.count


def histogram_percentile(histogram, quantile):
    target = math.ceil(sum(histogram.values()) * quantile)
    if not target:
        return None
    total = 0
    for value, count in sorted(histogram.items()):
        total += count
        if total >= target:
            return value


def bounded_percentile(histogram, quantile, maximum):
    value = histogram_percentile(histogram, quantile)
    # Tail overflow uses the actual maximum as a conservative upper bound, never truncating a slow P95 to 60s.
    return math.ceil(maximum) if value == HISTOGRAM_OVERFLOW_MS else value


def workload_plan(args):
    if args.mode == "step":
        return [("target", args.concurrency, args.duration, args.target_rps),
                ("step-2x", args.concurrency * 2, args.duration, args.target_rps * 2)]
    if args.mode == "burst":
        return [("target-before", args.concurrency, args.duration, args.target_rps),
                ("burst-5x", args.concurrency * 5, args.burst_duration, args.target_rps * 5),
                ("target-recovery", args.concurrency, args.duration, args.target_rps)]
    return [(args.mode, args.concurrency, args.duration, args.target_rps)]


def capacity_sample_valid(sample, now, age):
    return cache_is_fresh({"fixture_disk_bytes": sample.get("fixture_disk_bytes"),
        "measurement_wall_time": sample.get("disk_measurement_wall_time"),
        "scan_duration_seconds": sample.get("disk_scan_duration_seconds"), "files": sample.get("disk_files"),
        "ok": sample.get("disk_monitor_ok")}, now, age)


def paced_delivery_valid(measured_rate, offered_rate, minimum_ratio):
    return offered_rate == 0 or measured_rate >= offered_rate * minimum_ratio


def connection_budget(peak_users, denial_connections):
    # One sequential connection per user, bounded denial pool, probe + listener/trigger + spare.
    return peak_users + denial_connections + 4


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def semantic(operation, status, data, *, username="", rows=0, expected_revision=None):
    """Every expected status has its own business check; no blanket 4xx exclusions."""
    if not isinstance(data, dict) or data.get("success") is False or data.get("failed") is True:
        return "unexpected", "invalid_or_failed_business_payload"
    if status == 200 and data.get("status") in ("failed", "error", "unavailable"):
        return "unexpected", "failed_200"
    if operation == "session" and status == 200 and data.get("username") == username:
        return "success", ""
    if operation == "ledger_read" and status == 200 and data.get("department") == "finance" and \
            isinstance(data.get("records"), list) and len(data["records"]) == rows and \
            type(data.get("revision")) is int and all(isinstance(row, dict) and
                row.get("project_id") == f"SYN-{index:05d}" and row.get("contract_amount") == "100.00" and
                row.get("received_amount") == "20.00" for index, row in enumerate(data["records"])):
        return "success", ""
    if operation == "ledger_write":
        if status == 200 and data.get("department") == "presales" and \
                type(data.get("revision")) is int and data["revision"] == expected_revision + 1 and \
                data.get("source_name") == "release-synthetic-fixture":
            return "success", ""
        if status == 409 and data == {"detail": "台账已被其他人更新，请刷新后重试。"}:
            return "expected_conflict", ""
    if operation == "permission_denied" and status == 403 and data == {"detail": "无该台账访问权限。"}:
        return "expected_permission_denial", ""
    if operation == "agent_disabled" and status == 503 and data.get("code") == "unavailable":
        return "expected_agent_disabled", ""
    if operation == "overload":
        if status == 429 and data.get("code") == "synthetic_overload" and data.get("retryable") is True:
            return "expected_overload", ""
        if status == 200 and data.get("status") == "synthetic_hold_complete":
            return "success", ""
    return "unexpected", "unexpected_status_or_business_payload"


def summarize(samples, elapsed, *, min_samples=20, read_p95_ms=1000, write_p95_ms=2000,
              max_error_rate=0.001, min_success_rps=0):
    if not isinstance(samples, SampleCollector):
        collected = SampleCollector()
        for sample in samples:
            collected.append(sample)
        samples = collected
    groups = samples.groups
    failures = []
    if elapsed <= 0 or not samples:
        failures.append("zero_load")
    output = {}
    for operation in OPERATIONS:
        group = groups[operation]
        values = group["latency"]
        results = group["results"]
        successful = group["successful_latency"]
        latency_percentile = lambda q: bounded_percentile(values, q, group["max_latency"])
        success_percentile = lambda q: bounded_percentile(successful, q, group["max_successful_latency"])
        output[operation] = {"samples": group["count"], "results": dict(results),
            "status_counts": dict(group["statuses"]),
            "p50_ms": latency_percentile(.5), "p95_ms": latency_percentile(.95),
            "p99_ms": latency_percentile(.99), "success_p95_ms": success_percentile(.95),
            "quantile_precision_ms": 1, "overflow_ms": HISTOGRAM_OVERFLOW_MS,
            "overflow_quantile_policy": "actual maximum upper bound", "errors": dict(group["errors"])}
        if group["count"] < min_samples:
            failures.append(operation + ":insufficient_samples")
        expected_result = {"permission_denied": "expected_permission_denial",
                           "agent_disabled": "expected_agent_disabled"}.get(operation, "success")
        if results[expected_result] < min_samples:
            failures.append(operation + ":no_successful_semantic_sample")
        limit = write_p95_ms if operation == "ledger_write" else read_p95_ms
        if values and latency_percentile(.95) > limit:
            failures.append(operation + ":p95_exceeded")
        if successful and success_percentile(.95) > limit:
            failures.append(operation + ":successful_p95_exceeded")
        # Timeouts are a hard failure even if diluted by a large successful workload.
        if group["hard_timeout"]:
            failures.append(operation + ":timeout")
        if group["hard_business_or_auth"]:
            failures.append(operation + ":business_or_auth_redline")
    unexpected = sum(group["results"]["unexpected"] for group in groups.values())
    error_rate = unexpected / len(samples) if samples else 1.0
    if error_rate > max_error_rate:
        failures.append("unexpected_error_rate_exceeded")
    successes = sum(group["results"]["success"] for group in groups.values())
    rate = successes / elapsed if elapsed > 0 else 0
    if rate < min_success_rps:
        failures.append("successful_throughput_below_target")
    return {"samples": len(samples), "elapsed_seconds": elapsed,
        "logical_operations_per_second": len(samples) / elapsed if elapsed > 0 else 0,
        "successful_operations_per_second": rate, "unexpected_error_rate": error_rate,
        "expected_results": dict(sum((Counter({key: value for key, value in group["results"].items()
            if key != "unexpected"}) for group in groups.values()), Counter())),
        "operations": output, "failures": failures, "result": "FAIL" if failures else "PASS"}


def summarize_paced_phase(samples, elapsed, offered_rate, minimum_ratio, **summary_options):
    result = summarize(samples, elapsed, **summary_options)
    if not paced_delivery_valid(result["logical_operations_per_second"], offered_rate, minimum_ratio):
        result["failures"].append("offered_target_rate_not_delivered")
        result["result"] = "FAIL"
    return result


def sanitized_environment():
    # Explicit inherited-system essentials only; no operator business/provider environment.
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
            "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "LANG", "LC_ALL"}
    return {key: value for key, value in os.environ.items() if key.upper() in keep}


def python_sources(directory):
    # Prune before traversal: installed vendor code is not application source.
    excluded = {"node_modules", "__pycache__", ".runtime", ".venv"}
    for current, directories, files in os.walk(directory, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in excluded)
        for name in sorted(files):
            if name.endswith(".py"):
                yield Path(current) / name


def source_evidence():
    paths = list(python_sources(ROOT / "qa/release_acceptance")) + list(python_sources(ROOT / "backend")) + [
        ROOT / "uv.lock", ROOT / "pyproject.toml", ROOT / "qa/run_portable_postgres.py",
        ROOT / "qa/agent_platform/verify_portal_pg.py",
    ]
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    commit = result.stdout.strip() if result.returncode == 0 else "unknown"
    # Hashes capture concurrent uncommitted changes; commit alone never identifies them.
    return {"commit": commit, "source_sha256": hashes,
        "source_manifest_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        "manifest_scope": "all backend Python, release_acceptance Python, PortablePostgres/verify_portal_pg helpers, uv.lock/pyproject; pruned vendor/cache/runtime/venv; unrelated QA excluded",
        "renderer_asset_scope": "separate renderer asset security and compiled manifest acceptance required"}


async def setup_request(client, trace, stage, method, path, **kwargs):
    started = time.monotonic()
    sample = {"stage": stage}
    try:
        response = await client.request(method, path, **kwargs)
        sample["status"] = response.status_code
        return response
    except (httpx.HTTPError, asyncio.CancelledError) as error:
        sample["error_class"] = type(error).__name__
        raise
    finally:
        sample["seconds"] = time.monotonic() - started
        trace.setdefault("requests", []).append(sample)


async def login(client, username, password, trace=None):
    trace = trace if trace is not None else {}
    response = await setup_request(client, trace, "csrf_before_login", "GET", "/api/csrf/")
    if response.status_code != 200 or not isinstance(response.json().get("csrfToken"), str):
        raise RuntimeError("csrf_setup_failed")
    response = await setup_request(client, trace, "password_login", "POST", "/api/login/", json={"username": username, "password": password},
        headers={"X-CSRFToken": response.json()["csrfToken"]})
    if response.status_code != 200 or response.json().get("username") != username:
        raise RuntimeError("login_setup_failed")
    response = await setup_request(client, trace, "csrf_after_login", "GET", "/api/csrf/")
    if response.status_code != 200 or not isinstance(response.json().get("csrfToken"), str):
        raise RuntimeError("csrf_setup_failed")
    client.headers["X-CSRFToken"] = response.json()["csrfToken"]
    trace["completed_logins"] = trace.get("completed_logins", 0) + 1


async def prepare_login(client, username, password, args, trace, deadline):
    client.timeout = httpx.Timeout(args.setup_timeout)
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("setup_deadline_exceeded")
        await asyncio.wait_for(login(client, username, password, trace), timeout=remaining)
    finally:
        # Preparation tolerance never changes the measured request timeout.
        client.timeout = httpx.Timeout(args.timeout)


async def request_sample(client, operation, method, path, *, username="", rows=0,
                         expected_revision=None, payload=None):
    started = time.monotonic()
    try:
        response = await client.request(method, path, json=payload)
        try:
            data = response.json()
        except ValueError:
            data = None
        result, error = semantic(operation, response.status_code, data, username=username,
                                 rows=rows, expected_revision=expected_revision)
        return Sample(operation, (time.monotonic() - started) * 1000, response.status_code, result, error)
    except httpx.TimeoutException:
        return Sample(operation, (time.monotonic() - started) * 1000, 0, "unexpected", "timeout")
    except httpx.HTTPError:
        return Sample(operation, (time.monotonic() - started) * 1000, 0, "unexpected", "transport_error")


async def business_sample(client, operation, index, rows):
    if operation != "ledger_write":
        path = {"session": "/api/me/", "ledger_read": "/api/business/ledgers/finance/",
                "permission_denied": "/api/business/ledgers/finance/", "agent_disabled": "/api/agent/conversations/"}[operation]
        return await request_sample(client, operation, "GET", path, username=f"release-user-{index:04d}", rows=rows)
    started = time.monotonic()
    try:
        current = await client.get("/api/business/ledgers/presales/")
        data = current.json()
        if current.status_code != 200 or not isinstance(data, dict) or type(data.get("revision")) is not int or \
                data.get("success") is False or data.get("failed") is True or data.get("status") in ("failed", "error", "unavailable"):
            return Sample(operation, (time.monotonic()-started)*1000, current.status_code,
                          "unexpected", "write_prerequisite_failed")
        sample = await request_sample(client, operation, "PATCH", "/api/business/ledgers/presales/",
            expected_revision=data["revision"], payload={"expected_revision": data["revision"],
            "source_name": "release-synthetic-fixture"})
        sample.milliseconds = (time.monotonic() - started)*1000
        return sample
    except httpx.TimeoutException:
        return Sample(operation, (time.monotonic()-started)*1000, 0, "unexpected", "timeout")
    except (httpx.HTTPError, ValueError, TypeError):
        return Sample(operation, (time.monotonic()-started)*1000, 0, "unexpected", "write_prerequisite_failed")


async def leased_business_call(lease, action, deadline, aborted, *, clock=time.monotonic):
    """A lease wait cannot admit a new operation after the arrival window."""
    async with lease.operation():
        if clock() >= deadline or aborted.is_set():
            return None
        return await action()  # An admitted slow operation finishes normally.


async def paced_phase_worker(index, count, offered_rate, phase_started, deadline,
                             aborted, perform, *, clock=time.monotonic, sleep=asyncio.sleep):
    interval = count / offered_rate if offered_rate else 0
    due = phase_started + (index / count * interval if interval else 0)
    iteration = 0
    while clock() < deadline and not aborted.is_set():
        # The next arrival belongs to another phase. Do not sleep to it and
        # accidentally include an empty per-client interval in throughput.
        if interval and due >= deadline:
            return
        await sleep(max(0, due-clock()))
        if clock() >= deadline or aborted.is_set():
            return
        if await perform(index, iteration, due) is False:
            return
        iteration += 1
        due = due + interval if interval else clock()


async def wait_phase_deadline(deadline, aborted, *, clock=time.monotonic):
    """Observe the whole requested window, unless an actual redline aborts it."""
    while not aborted.is_set():
        remaining = deadline-clock()
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(aborted.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            pass


async def finish_phase_window(workers, deadline, aborted, *, clock=time.monotonic):
    """Wait for BOTH the true deadline and all already-admitted business I/O."""
    tasks = [asyncio.ensure_future(worker) for worker in workers]
    waiter = asyncio.create_task(wait_phase_deadline(deadline, aborted, clock=clock))
    try:
        await asyncio.gather(*tasks, waiter)
        return clock()
    except BaseException:
        # Only exceptional exit/caller cancellation stops our remaining work.
        # Normal deadline completion must keep admitted slow calls alive.
        for task in [*tasks, waiter]:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, waiter, return_exceptions=True)
        raise


async def scheduled_session_renewals(leases, stop, aborted, live_failures, *, clock=time.monotonic):
    """Independent due waits; normal failure drains already-started real logins."""
    for lease in leases:
        lease.statistics.setdefault("rounds_started", 0)

    async def worker(lease):
        try:
            while not stop.is_set():
                remaining = lease.due - clock()
                if remaining > 0:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=remaining)
                        return True
                    except asyncio.TimeoutError:
                        continue  # Recheck the actual clock; never renew early.
                # The lease owns the stop check and exclusive admission in
                # one condition critical section, including rounds_started.
                if not await lease.maybe_renew(stop=stop):
                    live_failures.append("scheduled_session_renewal_failed")
                    aborted.set()
                    stop.set()
                    return False
            return True
        except BaseException:
            aborted.set()
            stop.set()
            raise

    tasks = [asyncio.create_task(worker(lease), name=f"release-renew-client-{lease.client_id}")
             for lease in leases]
    try:
        # False sets stop but does not cancel siblings already doing real I/O.
        # Every worker must finish before this scheduler returns its result.
        results = await asyncio.gather(*tasks)
        return all(result is True for result in results)
    except BaseException:
        stop.set()
        aborted.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def renewal_boundary_snapshot(leases):
    return {"inflight_client_ids": [lease.client_id for lease in leases if lease.renewing],
            **{key: sum(lease.statistics.get(key, 0) for lease in leases)
               for key in ("rounds_started", "attempts", "successes", "failures")}}


async def drain_session_renewals(task, stop, leases, *, timeout, workload_end, boundary_snapshot=None):
    """Bounded lifecycle drain, never part of the measured business duration."""
    stop.set()
    started = time.monotonic()
    before = boundary_snapshot if boundary_snapshot is not None else renewal_boundary_snapshot(leases)
    result = {"timeout_seconds": timeout, "workload_end_monotonic": workload_end,
              "inflight_client_ids_at_boundary": before["inflight_client_ids"],
              "snapshot_scope": "workload_boundary" if boundary_snapshot is not None else "drain_start",
              "started_monotonic": started}
    try:
        completed = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        result["result"] = "PASS" if completed is True else "FAIL"
    except asyncio.TimeoutError:
        # Only our bounded drain timeout cancels here. ScheduledSession records
        # the cancelled real renewal as FAIL and releases its exclusive gate.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        result.update(result="FAIL", failure="scheduled_renewal_drain_timeout")
    after = renewal_boundary_snapshot(leases)
    result.update(seconds=time.monotonic() - started, finished_monotonic=time.monotonic(),
        new_attempts_during_drain=after["rounds_started"]-before["rounds_started"],
        completed_records_during_drain=after["attempts"]-before["attempts"],
        successes_during_drain=after["successes"]-before["successes"],
        failures_during_drain=after["failures"]-before["failures"])
    if result["new_attempts_during_drain"] != 0:
        result.update(result="FAIL", failure="new_renewal_started_after_workload_boundary")
    return result


async def workload(base_url, args, credentials, identity, *, owned_resources=None, total_started=None, setup_trace=None, renewal_report=None):
    samples, phases, delays = SampleCollector(), [], Counter()
    maximum_schedule_delay = [0.]
    clients, denied = [], []
    aborted = asyncio.Event()
    live_failures = []
    monitor = None
    renewal_task = None
    renewal_stop = asyncio.Event()
    leases = []
    client_resources = Telemetry()
    renewal_report = renewal_report if renewal_report is not None else {
        "attempts": 0, "successes": 0, "failures": 0, "events": [], "events_dropped": 0}
    session_age = identity["session_cookie_age"]
    if not 0 < args.renewal_interval < session_age:
        raise ValueError("scheduled renewal must precede actual session expiry")
    renewal_report.update(interval_seconds=args.renewal_interval, actual_session_cookie_age=session_age,
        scope="scheduled synthetic real password login; never auth-error-triggered retries")
    growth = None
    setup_trace = setup_trace if setup_trace is not None else {}
    setup_deadline = time.monotonic() + args.setup_deadline

    async def resource_guard():
        while True:
            await asyncio.sleep(1)
            client_resources.observe({"wall_time": time.time(), "cpu_seconds": time.process_time(), **process_memory()})
            if not owned_resources or not owned_resources.exists():
                live_failures.append("live_resource_measurements_missing")
                aborted.set()
                return
            # Read only a bounded numeric telemetry tail, even during a 24h run.
            with owned_resources.open("rb") as file:
                size = file.seek(0, 2)
                file.seek(max(0, size - 8192))
                lines = file.read().decode("utf-8", errors="ignore").splitlines()
            latest = None
            for line in reversed(lines):
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        latest = value
                        break
                except ValueError:
                    continue
            if not latest:
                live_failures.append("live_resource_measurements_missing")
                aborted.set()
                return
            if type(latest.get("wall_time")) not in (int, float) or time.time() - latest["wall_time"] > 5:
                live_failures.append("live_resource_measurements_stale")
                aborted.set()
                return
            if not capacity_sample_valid(latest, time.time(), args.disk_stale_seconds):
                live_failures.append("live_capacity_monitor_missing_stale_or_failed")
                aborted.set()
                return
            growth.observe(latest)
            if growth.failed:
                live_failures.append("live_observed_memory_growth_limit")
                aborted.set()
                return
            _, measured_memory = memory_measurement(latest, conservative=True)
            if measured_memory is None:
                live_failures.append("live_process_memory_measurements_missing")
                aborted.set()
                return
            if args.max_memory_mb and measured_memory > args.max_memory_mb*1024*1024:
                live_failures.append("live_process_memory_limit")
                aborted.set()
                return
            for key, limit, failure in (("fixture_disk_bytes", args.max_fixture_disk_mb * 1024 * 1024, "live_fixture_disk_limit"),
                ("waitress_pending_tasks", args.max_queue, "live_waitress_queue_limit")):
                value = latest.get(key)
                if limit and type(value) in (int, float) and value > limit:
                    live_failures.append(failure)
                    aborted.set()
                    return
    def lease_for(client, username, client_id):
        async def verify():
            return await request_sample(client, "session", "GET", "/api/me/", username=username)
        async def renew(trace):
            await login(client, username, credentials["password"], trace)
        return ScheduledSession(args.renewal_interval, session_age, verify, renew, renewal_report, client_id=client_id)

    async with httpx.AsyncClient(base_url=base_url, timeout=args.setup_timeout, trust_env=False,
                               follow_redirects=False) as probe:
        probe.headers["X-Release-Token"] = credentials["token"]
        response = await asyncio.wait_for(setup_request(probe, setup_trace, "fixture_identity", "GET",
            "/__release__/identity"), timeout=max(.001, setup_deadline - time.monotonic()))
        if response.status_code != 200 or response.json() != identity:
            raise RuntimeError("target_fixture_identity_mismatch")
        try:
            plan = workload_plan(args)
            peak_concurrency = max(count for _, count, _, _ in plan)
            for index in range(peak_concurrency):
                client = httpx.AsyncClient(base_url=base_url, timeout=args.timeout, trust_env=False,
                    follow_redirects=False, limits=httpx.Limits(max_connections=4, max_keepalive_connections=4))
                clients.append(client)
                await prepare_login(client, f"release-user-{index:04d}", credentials["password"],
                                    args, setup_trace, setup_deadline)
                leases.append(lease_for(client, f"release-user-{index:04d}", index))
            denied = httpx.AsyncClient(base_url=base_url, timeout=args.timeout, trust_env=False, follow_redirects=False,
                limits=httpx.Limits(max_connections=args.threads, max_keepalive_connections=args.threads))
            await prepare_login(denied, "release-denied", credentials["password"], args, setup_trace, setup_deadline)
            denied_lease = lease_for(denied, "release-denied", "denied")
            leases.append(denied_lease)
            probe.timeout = httpx.Timeout(args.timeout)
            started_all = time.monotonic()
            wall_started = time.time()
            growth = MemoryGrowth(wall_started, args.memory_warmup_seconds, args.memory_growth_window_seconds,
                                  args.max_memory_growth_mib_hour)
            monitor = asyncio.create_task(resource_guard())
            renewal_task = asyncio.create_task(scheduled_session_renewals(leases, renewal_stop, aborted, live_failures))
            for phase_index, (name, count, seconds, offered_rate) in enumerate(plan):
                phase_started = time.monotonic()
                phase_wall_started = time.time()
                phase_samples = SampleCollector()
                deadline = phase_started + seconds
                health = WindowHealth(args.health_window_seconds, SampleCollector, summarize,
                    {"min_samples": args.min_samples, "read_p95_ms": args.read_p95_ms,
                     "write_p95_ms": args.write_p95_ms, "max_error_rate": args.max_error_rate,
                     "min_success_rps": args.min_success_rps}, offered_rate=offered_rate,
                      minimum_ratio=args.min_target_rate_ratio)
                last_business_completion = [None]

                async def perform(index, iteration, due):
                    client = clients[index]
                    delay = max(0, time.monotonic()-due)*1000 if offered_rate else 0
                    maximum_schedule_delay[0] = max(maximum_schedule_delay[0], delay)
                    delays[min(math.ceil(delay), HISTOGRAM_OVERFLOW_MS)] += 1
                    operation = OPERATIONS[(iteration + index) % len(OPERATIONS)]
                    selected_lease = denied_lease if operation == "permission_denied" else leases[index]
                    sample = await leased_business_call(selected_lease,
                        lambda: business_sample(denied if operation == "permission_denied" else client,
                                                operation, index, identity["rows"]), deadline, aborted)
                    if sample is None:
                        return False
                    phase_samples.append(sample)
                    samples.append(sample)
                    last_business_completion[0] = time.monotonic()
                    health.observe(sample, last_business_completion[0]-phase_started)
                    if health.failed or sample.error == "timeout" or \
                            (sample.result == "unexpected" and sample.status in (200, 401, 403)):
                        live_failures.append("live_business_window_or_auth_timeout_redline")
                        aborted.set()
                    return True
                phase_workload_end = await finish_phase_window(
                    (paced_phase_worker(index, count, offered_rate, phase_started, deadline, aborted, perform)
                     for index in range(count)), deadline, aborted)
                phase_wall_ended = time.time()
                if phase_index == len(plan) - 1:
                    workload_end = phase_workload_end
                    workload_wall_end = phase_wall_ended
                    renewal_stop.set()
                    renewal_boundary = renewal_boundary_snapshot(leases)
                    monitor.cancel()
                elapsed = phase_workload_end - phase_started
                window_evidence = health.finish(elapsed)
                phase_result = summarize_paced_phase(phase_samples, elapsed, offered_rate,
                    args.min_target_rate_ratio, min_samples=args.min_samples,
                    read_p95_ms=args.read_p95_ms, write_p95_ms=args.write_p95_ms,
                    max_error_rate=args.max_error_rate, min_success_rps=args.min_success_rps)
                if health.failed:
                    phase_result["failures"].append("business_health_window_failed")
                    phase_result["result"] = "FAIL"
                phases.append({"name": name, "concurrency": count, "requested_seconds": seconds,
                    "deadline_monotonic": deadline,
                    "last_business_completion_monotonic": last_business_completion[0],
                    "start_wall_time": phase_wall_started, "end_wall_time": phase_wall_ended,
                    "offered_logical_operations_per_second": offered_rate or None,
                    "arrival_model": "paced_bounded_concurrency" if offered_rate else "unpaced_closed_loop",
                    "health_windows": window_evidence, **phase_result})
            elapsed_all = workload_end - started_all
            try:
                await monitor
            except asyncio.CancelledError:
                pass
            monitor = None
            renewal_drain = await drain_session_renewals(renewal_task, renewal_stop, leases,
                timeout=args.timeout * 6, workload_end=workload_end, boundary_snapshot=renewal_boundary)
            renewal_task = None
            renewal_coverage = finalize_renewal_coverage(leases, renewal_report,
                expected_client_ids=[*range(peak_concurrency), "denied"],
                observed_end=workload_end, requested_seconds=sum(p[2] for p in plan))
            probe_started = time.monotonic()
            # Explicit synthetic admission probe, separate from actual business capacity.
            # Business measurement is complete. Release its idle connections before the independent QA probe.
            for client in clients:
                await client.aclose()
            clients.clear()
            await denied.aclose()
            denied = None
            overload_count = identity["server_threads"] * 2
            overload = await asyncio.gather(*(request_sample(probe, "overload", "GET", "/__release__/hold")
                                               for _ in range(overload_count)))
            rejected = sum(s.result == "expected_overload" for s in overload)
            bad = sum(s.result == "unexpected" for s in overload)
            recovery_client = httpx.AsyncClient(base_url=base_url, timeout=args.timeout, trust_env=False,
                                               follow_redirects=False)
            clients.append(recovery_client)
            recovery_trace = {}
            await login(recovery_client, "release-user-0000", credentials["password"], recovery_trace)
            recovery = await request_sample(recovery_client, "session", "GET", "/api/me/", username="release-user-0000")
            timeout_probe = {"executed": False}
            if args.fault_mode == "timeout":
                try:
                    await probe.get("/__release__/hold", timeout=0.01)
                    timeout_probe = {"executed": True, "observed_timeout": False, "result": "FAIL"}
                except httpx.TimeoutException:
                    await asyncio.sleep(.3)
                    after = await request_sample(recovery_client, "session", "GET", "/api/me/", username="release-user-0000")
                    timeout_probe = {"executed": True, "observed_timeout": True,
                        "recovery": after.result, "result": "PASS" if after.result == "success" else "FAIL",
                        "scope": "synthetic slow HTTP request; no process/PG/Runtime crash was injected"}
            summary = summarize(samples, elapsed_all, min_samples=args.min_samples,
                read_p95_ms=args.read_p95_ms, write_p95_ms=args.write_p95_ms,
                max_error_rate=args.max_error_rate, min_success_rps=args.min_success_rps)
            failures = list(summary["failures"])
            failures.extend(live_failures)
            failures.extend("phase:" + phase["name"] for phase in phases if phase["result"] != "PASS")
            if rejected == 0 or bad or recovery.result != "success":
                failures.append("synthetic_overload_or_recovery_failed")
            if timeout_probe.get("result") == "FAIL":
                failures.append("timeout_fault_probe_failed")
            resource_summary = Telemetry()
            resource_validation = set()
            disk_trend = DiskTrend()
            phase_disk_trends = [DiskTrend() for _ in phases]
            if owned_resources and owned_resources.exists():
                with owned_resources.open(encoding="utf-8") as resource_file:
                    for line in resource_file:
                        try:
                            raw = json.loads(line)
                            if not isinstance(raw, dict) or not wall_started <= raw.get("wall_time", 0) <= time.time()+5:
                                continue
                            sample = resource_summary.observe(raw, ("waitress_pending_tasks", "cpu_seconds", "wall_time"))
                            disk_trend.observe(sample)
                            for phase, trend in zip(phases, phase_disk_trends):
                                if phase["start_wall_time"] <= sample["wall_time"] <= phase["end_wall_time"]:
                                    trend.observe(sample)
                            if not capacity_sample_valid(sample, sample["wall_time"], args.disk_stale_seconds):
                                resource_validation.add("capacity_monitor_missing_stale_or_failed")
                            if sample.get("waitress_connection_limit") != identity["server_connection_limit"] or \
                                    sample.get("waitress_use_poll") != int(identity["server_use_poll"]) or \
                                    type(sample.get("waitress_socket_map_size")) is not int:
                                resource_validation.add("server_connection_configuration_evidence_missing_or_mismatched")
                        except (ValueError, TypeError):
                            if line.endswith("\n"):
                                resource_validation.add("invalid_complete_resource_sample")
            if resource_summary.count < 2:
                failures.append("resource_or_queue_measurements_missing")
            if resource_summary.missing:
                failures.append("queue_or_cpu_measurements_missing")
            if not resource_summary.last or resource_summary.last.get("wall_time", 0) < time.time()-5:
                failures.append("resource_measurements_stale")
            failures.extend(sorted(resource_validation))
            maxima = resource_summary.maxima
            if "memory_bytes" not in maxima:
                failures.append("process_memory_measurements_missing")
            if args.max_fixture_disk_mb and "fixture_disk_bytes" not in maxima:
                failures.append("fixture_disk_measurements_missing")
            if maxima.get("fixture_disk_bytes", 0) > args.max_fixture_disk_mb*1024*1024 and args.max_fixture_disk_mb:
                failures.append("fixture_disk_limit_exceeded")
            if maxima.get("memory_bytes", 0) > args.max_memory_mb*1024*1024 and args.max_memory_mb:
                failures.append("process_memory_limit_exceeded")
            if maxima.get("waitress_pending_tasks", 0) > args.max_queue and args.max_queue:
                failures.append("waitress_queue_limit_exceeded")
            if renewal_drain["result"] != "PASS":
                failures.append("scheduled_renewal_drain_failed")
            if renewal_coverage["result"] != "PASS":
                failures.append("per_client_session_renewal_coverage_failed")
            if renewal_report["failures"]:
                failures.append("scheduled_session_renewal_failed")
            if growth.failed:
                failures.append("observed_memory_growth_limit_exceeded")
            if args.mode == "soak" and args.duration >= args.memory_warmup_seconds + args.memory_growth_window_seconds and \
                    not growth.windows:
                failures.append("memory_growth_observation_missing")
            for phase, trend in zip(phases, phase_disk_trends):
                phase["fixture_disk_growth"] = trend.evidence()
            return {**summary, "result": "FAIL" if failures else "PASS", "failures": failures, "phases": phases,
                "preparation_seconds": started_all - total_started if total_started is not None else None,
                "effective_workload_seconds": elapsed_all,
                "post_workload_probe_seconds": time.monotonic() - probe_started,
                "workload_start_monotonic": started_all, "workload_end_monotonic": workload_end,
                "workload_start_wall_time": wall_started, "workload_end_wall_time": workload_wall_end,
                "renewal_drain": renewal_drain,
                "session_renewal": renewal_report,
                "client_resources": {"scope": "load-generator process only, never Web metrics",
                    **client_resources.evidence(), "memory_max_bytes": client_resources.maxima.get("memory_bytes")},
                "memory_growth": growth.evidence(),
                "client_schedule_delay_p95_ms": bounded_percentile(delays, .95, maximum_schedule_delay[0]),
                "client_schedule_delay_p99_ms": bounded_percentile(delays, .99, maximum_schedule_delay[0]),
                "client_schedule_delay_max_ms": maximum_schedule_delay[0],
                "overload_probe": {"requests": len(overload), "expected_429": rejected, "unexpected": bad,
                    "recovery": asdict(recovery), "recovery_login": recovery_trace,
                    "business_connections_released_before_probe": True,
                    "scope": "independent QA synthetic admission gate, not production admission policy"},
                "fault_probe": timeout_probe, "resources": {**resource_summary.evidence(),
                    "capacity_scan_duration_max_seconds": maxima.get("disk_scan_duration_seconds"),
                    "capacity_measurement_age_max_seconds": maxima.get("capacity_measurement_age"),
                    "memory_max_bytes": maxima.get("memory_bytes"),
                    "memory_metric": "max(current RSS, observed peak RSS) safety cap; growth prefers current RSS with explicit peak fallback", 
                    "fixture_disk_max_bytes": maxima.get("fixture_disk_bytes"),
                    "fixture_disk_growth": disk_trend.evidence(),
                    "waitress_queue_max": maxima.get("waitress_pending_tasks"),
                    "waitress_socket_map_max": maxima.get("waitress_socket_map_size")},
                "coverage_limits": ["Agent disabled; no real or mock model execution", "No HR/product worker task load",
                    "Waitress queue is measured; business worker queue is not measured", "No production cloud verification",
                    "No AG-15/AG-16 closure", "No process/DB/Runtime crash recovery proof"]}
        finally:
            if renewal_task:
                renewal_task.cancel()
                try:
                    await renewal_task
                except asyncio.CancelledError:
                    pass
            if monitor:
                monitor.cancel()
                try:
                    await monitor
                except asyncio.CancelledError:
                    pass
            for client in clients:
                await client.aclose()
            if denied:
                await denied.aclose()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--mode", choices=("target", "step", "burst", "soak"), default="target")
    result.add_argument("--concurrency", type=int, default=8)
    result.add_argument("--duration", type=float, default=30)
    result.add_argument("--burst-duration", type=float, default=10)
    result.add_argument("--rows", type=int, default=20)
    result.add_argument("--threads", type=int, default=4)
    result.add_argument("--admission-limit", type=int, default=2)
    result.add_argument("--connection-limit", type=int, default=512, help="Explicit Waitress socket-map limit; Windows select maximum 512")
    result.add_argument("--startup-deadline", type=float, default=600, help="Bounded server migrations/standard-password fixture readiness deadline")
    result.add_argument("--target-rps", type=float, default=0, help="Total logical operation pacing; zero means unpaced closed loop")
    result.add_argument("--timeout", type=float, default=10)
    result.add_argument("--setup-timeout", type=float, default=30, help="Preparation-only HTTP timeout; measured requests retain --timeout")
    result.add_argument("--setup-deadline", type=float, default=600, help="Bounded identity and serial login preparation deadline")
    result.add_argument("--renewal-interval", type=float, default=14400, help="Scheduled real login interval; must precede actual fixture session expiry")
    result.add_argument("--health-window-seconds", type=float, default=300, help="Complete business windows independently enforce SLO and offered rate")
    result.add_argument("--memory-warmup-seconds", type=float, default=900)
    result.add_argument("--memory-growth-window-seconds", type=float, default=3600)
    result.add_argument("--max-memory-growth-mib-hour", type=float, default=32, help="Candidate observed >=1h memory growth gate, not leak diagnosis")
    result.add_argument("--min-samples", type=int, default=20, help="Per operation and per phase")
    result.add_argument("--read-p95-ms", type=float, default=1000)
    result.add_argument("--write-p95-ms", type=float, default=2000)
    result.add_argument("--max-error-rate", type=float, default=.001)
    result.add_argument("--min-success-rps", type=float, default=0)
    result.add_argument("--min-target-rate-ratio", type=float, default=.95,
                        help="Paced phases must deliver at least this fraction of offered logical operation rate")
    result.add_argument("--disk-interval", type=float, default=5, help="Owned capacity subprocess scan cadence, 5 to 10 seconds")
    result.add_argument("--disk-stale-seconds", type=float, default=15, help="Maximum capacity measurement age")
    result.add_argument("--max-memory-mb", type=float, default=0, help="Zero: measured but no frozen memory limit")
    result.add_argument("--max-fixture-disk-mb", type=float, default=1024, help="Candidate safety guard; zero explicitly disables it")
    result.add_argument("--max-queue", type=int, default=0, help="Zero: measured but no frozen queue limit")
    result.add_argument("--fault-mode", choices=("none", "timeout"), default="none")
    result.add_argument("--postgres-dsn-env", choices=("RELEASE_ACCEPTANCE_PG_DSN",))
    result.add_argument("--postgres-bin", help="Explicit PostgreSQL binaries; owned portable UUID cluster, empty fixture DB")
    result.add_argument("--fixture-id", help="Explicit fresh UUID for an owned precreated empty PostgreSQL DB")
    result.add_argument("--target", help="Explicit external synthetic fixture URL; never inferred")
    result.add_argument("--allow-external-synthetic-target", action="store_true")
    result.add_argument("--external-fixture", help="Explicit restricted JSON identity/password/token file, never local.env")
    result.add_argument("--resource-file", help="Explicit sanitized resource JSONL produced by this fixture server")
    return result


def validate(args):
    if not 1 <= args.concurrency <= 1000 or not 1 <= args.rows <= 2000:
        raise ValueError("invalid concurrency/rows")
    if max(phase[1] for phase in workload_plan(args)) > 1000:
        raise ValueError("peak phase concurrency exceeds synthetic fixture limit")
    if not connection_budget(max(phase[1] for phase in workload_plan(args)), args.threads) <= args.connection_limit <= 4096 or \
            (os.name == "nt" and args.connection_limit > 512):
        raise ValueError("connection-limit below peak user/denial/control budget or above Windows select budget")
    if not math.isfinite(args.startup_deadline) or not 10 <= args.startup_deadline <= 1800:
        raise ValueError("startup readiness deadline must be bounded 10 to 1800 seconds")
    if not math.isfinite(args.min_target_rate_ratio) or not .95 <= args.min_target_rate_ratio <= 1:
        raise ValueError("paced delivery ratio must be between .95 and 1")
    if not math.isfinite(args.disk_interval) or not 5 <= args.disk_interval <= 10 or \
            not math.isfinite(args.disk_stale_seconds) or not args.disk_interval + 2 <= args.disk_stale_seconds <= 30:
        raise ValueError("invalid capacity observer interval/freshness")
    if any(value <= 0 or not math.isfinite(value) for value in (args.duration, args.burst_duration,
            args.timeout, args.setup_timeout, args.setup_deadline, args.read_p95_ms, args.write_p95_ms)) or args.min_samples < 1:
        raise ValueError("positive finite durations, thresholds and min-samples required")
    if any(not math.isfinite(value) or value < 0 for value in (args.target_rps, args.min_success_rps, args.max_memory_mb,
                                                             args.max_fixture_disk_mb)):
        raise ValueError("invalid rate/resource threshold")
    if not 0 <= args.max_error_rate <= .001 or args.max_queue < 0:
        raise ValueError("redline error rate cannot exceed candidate 0.1 percent")
    if any(not math.isfinite(v) or v <= 0 for v in (args.renewal_interval, args.health_window_seconds,
            args.memory_growth_window_seconds, args.max_memory_growth_mib_hour)) or \
            not math.isfinite(args.memory_warmup_seconds) or args.memory_warmup_seconds < 900 or \
            args.memory_growth_window_seconds < 3600 or not 60 <= args.health_window_seconds <= 300:
        raise ValueError("invalid renewal/window/growth settings; growth requires >=15min warmup and >=1h observation")
    if args.mode == "soak" and args.duration < 60:
        raise ValueError("soak requires at least 60 seconds; use 86400 for 24h evidence")
    if args.mode == "soak" and (args.max_memory_mb <= 0 or args.max_fixture_disk_mb <= 0 or args.max_queue <= 0):
        raise ValueError("soak requires explicit positive candidate memory, disk and queue limits")
    if not 1 <= args.admission_limit < args.threads <= 512:
        raise ValueError("threads must exceed positive admission-limit")
    if args.postgres_dsn_env and (not args.fixture_id or args.target):
        raise ValueError("owned PG requires an explicit fresh fixture-id and no external target")
    if args.postgres_bin and (args.target or args.postgres_dsn_env):
        raise ValueError("portable PG cannot be combined with another target/DSN")
    if args.target:
        parsed = urlsplit(args.target)
        if not args.allow_external_synthetic_target or not args.external_fixture or \
                parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or \
                parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("external target requires explicit synthetic fixture and safe origin URL")
    elif args.external_fixture or args.resource_file or args.allow_external_synthetic_target:
        raise ValueError("external arguments require explicit target")


def main(argv=None):
    total_started = time.monotonic()
    args = parser().parse_args(argv)
    validate(args)
    fixture_id = uuid.UUID(args.fixture_id).hex if args.fixture_id else uuid.uuid4().hex
    run_dir = ROOT / ".runtime" / "release-acceptance" / fixture_id
    run_dir.mkdir(parents=True, exist_ok=False)
    report = {"fixture_id": fixture_id, "scope": "synthetic real HTTP mixed-workload baseline",
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
            "dependencies": {name: importlib.metadata.version(name) for name in ("Django", "djangorestframework", "waitress", "httpx", "psycopg")}},
        "threshold_status": "candidate_not_frozen", "source_before": source_evidence(),
        "workload": {key: getattr(args, key) for key in ("mode", "concurrency", "duration", "burst_duration",
            "rows", "threads", "admission_limit", "target_rps", "timeout", "min_samples", "read_p95_ms",
            "write_p95_ms", "max_error_rate", "min_success_rps", "max_memory_mb", "max_fixture_disk_mb", "max_queue", "fault_mode",
            "min_target_rate_ratio", "disk_interval", "disk_stale_seconds", "setup_timeout", "setup_deadline",
            "connection_limit", "startup_deadline", "renewal_interval", "health_window_seconds",
            "memory_warmup_seconds", "memory_growth_window_seconds", "max_memory_growth_mib_hour")},
        "redlines": ["failed HTTP 200 is failure", "timeouts are failures", "zero/insufficient samples never pass",
            "unexpected authentication/CSRF errors never become expected denial", "semantic success required per operation",
            "synthetic 429 distinct from business conflicts", "license blockage never becomes AG pass"]}
    process = None
    log = None
    stack = ExitStack()
    ready = None
    server_startup_started = None
    disk_process = None
    disk_log = None
    disk_ready = None
    disk_stop_token = secrets.token_urlsafe(32)
    peak_concurrency = max(phase[1] for phase in workload_plan(args))
    report["workload"].update(peak_users=peak_concurrency, denial_pool_connections=args.threads,
                             required_connection_budget=connection_budget(peak_concurrency, args.threads))
    setup_trace = {"completed_logins": 0, "requests": [], "scope": "preparation only; excluded from measured business latency"}
    report["setup_measurements"] = setup_trace
    renewal_report = {"attempts": 0, "successes": 0, "failures": 0, "events": [], "events_dropped": 0}
    report["session_renewal"] = renewal_report
    try:
        if args.target:
            # The caller explicitly provides synthetic fixture credentials, never an operator environment file.
            external = json.loads(Path(args.external_fixture).read_text(encoding="utf-8"))
            identity = external["identity"]
            credentials = {key: external[key] for key in ("password", "token")}
            expected_fields = {"fixture_id", "synthetic", "database_kind", "users", "rows", "agent_mode", "model_mode",
                               "synthetic_admission_limit", "server_threads", "server_connection_limit", "server_use_poll", "session_cookie_age", "database_pool"}
            if set(identity) != expected_fields or identity.get("synthetic") is not True or identity.get("model_mode") != "no_model_calls" or \
                    identity.get("agent_mode") != "disabled" or identity.get("database_kind") not in ("sqlite", "postgresql") or \
                    identity.get("users", 0) < peak_concurrency or identity.get("rows") != args.rows or \
                    identity.get("server_connection_limit") != args.connection_limit or \
                    identity.get("server_threads") != args.threads or type(identity.get("server_use_poll")) is not bool or \
                    type(identity.get("session_cookie_age")) is not int or identity["session_cookie_age"] <= args.renewal_interval:
                raise ValueError("external fixture identity violates synthetic/no-model boundary")
            base_url = args.target.rstrip("/")
            resources = Path(args.resource_file) if args.resource_file else None
            report["target"] = {"kind": "explicit_external_synthetic", "origin_sha256": hashlib.sha256(base_url.encode()).hexdigest()}
        else:
            credentials = {"password": secrets.token_urlsafe(32), "token": secrets.token_urlsafe(32)}
            environment = sanitized_environment()
            environment.update(RELEASE_FIXTURE_PASSWORD=credentials["password"], RELEASE_FIXTURE_TOKEN=credentials["token"],
                RELEASE_FIXTURE_SECRET=secrets.token_urlsafe(48), PYTHONPATH=str(ROOT))
            command = [sys.executable, "-m", "qa.release_acceptance.fixture_server", "--run-dir", str(run_dir),
                "--fixture-id", fixture_id, "--users", str(peak_concurrency), "--rows", str(args.rows),
                "--threads", str(args.threads), "--admission-limit", str(args.admission_limit),
                "--connection-limit", str(args.connection_limit)]
            if args.postgres_dsn_env:
                environment[args.postgres_dsn_env] = os.environ[args.postgres_dsn_env]
                command.extend(["--postgres-dsn-env", args.postgres_dsn_env])
            elif args.postgres_bin:
                from qa.run_portable_postgres import PortablePostgres
                from psycopg.conninfo import make_conninfo
                pg = stack.enter_context(PortablePostgres(Path(args.postgres_bin),
                    database_name="release_acceptance_" + fixture_id, runtime_root=run_dir / "postgres"))
                environment["RELEASE_ACCEPTANCE_PG_DSN"] = make_conninfo(dbname=pg.env["PORTAL_DB_NAME"],
                    user=pg.env["PORTAL_DB_USER"], password=pg.env["PORTAL_DB_PASSWORD"],
                    host=pg.env["PORTAL_DB_HOST"], port=pg.env["PORTAL_DB_PORT"])
                command.extend(["--postgres-dsn-env", "RELEASE_ACCEPTANCE_PG_DSN"])
                report["portable_postgres"] = {"database_name": pg.database_name, "port": pg.port,
                    "owned_cluster": True, "migration_owner": "fixture_server"}
            disk_environment = sanitized_environment()
            disk_environment.update(RELEASE_DISK_STOP_TOKEN=disk_stop_token, PYTHONPATH=str(ROOT))
            disk_log = (run_dir / "owned-disk-monitor.log").open("x", encoding="utf-8")
            disk_process = subprocess.Popen([sys.executable, "-m", "qa.release_acceptance.disk_monitor",
                "--run-dir", str(run_dir), "--fixture-id", fixture_id, "--interval", str(args.disk_interval)],
                cwd=ROOT, env=disk_environment, stdout=disk_log, stderr=disk_log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 30
            while not (run_dir / "disk-latest.json").exists():
                if disk_process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("owned_capacity_monitor_setup_failed")
                time.sleep(.1)
            disk_ready = json.loads((run_dir / "disk-monitor-ready.json").read_text(encoding="utf-8"))
            if disk_process.pid not in (disk_ready["owned_pid"], disk_ready["parent_pid"]) or disk_ready["fixture_id"] != fixture_id:
                raise RuntimeError("owned_capacity_monitor_identity_mismatch")
            if not cache_is_fresh(json.loads((run_dir / "disk-latest.json").read_text(encoding="utf-8")),
                                  time.time(), args.disk_stale_seconds):
                raise RuntimeError("owned_capacity_monitor_setup_failed")
            report["capacity_observer"] = {"kind": "owned_separate_process", "launcher_pid": disk_process.pid,
                "owned_pid": disk_ready["owned_pid"], "interval_seconds": args.disk_interval,
                "max_age_seconds": args.disk_stale_seconds}
            log = (run_dir / "owned-server.log").open("x", encoding="utf-8")
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            server_startup_started = time.monotonic()
            deadline = server_startup_started + args.startup_deadline
            while not (run_dir / "ready.json").exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("owned_server_setup_failed")
                time.sleep(.2)
            ready = json.loads((run_dir / "ready.json").read_text(encoding="utf-8"))
            report["server_startup"] = {"deadline_seconds": args.startup_deadline,
                "elapsed_seconds": time.monotonic() - server_startup_started,
                "standard_password_hashing_preserved": True}
            if process.pid not in (ready["owned_pid"], ready["parent_pid"]) or ready["fixture_id"] != fixture_id:
                raise RuntimeError("owned_server_identity_mismatch")
            identity = {key: value for key, value in ready.items() if key not in ("port", "owned_pid", "parent_pid")}
            base_url = "http://127.0.0.1:" + str(ready["port"])
            resources = run_dir / "resources.jsonl"
            report["target"] = {"kind": "owned_loopback_dynamic_port", "launcher_pid": process.pid,
                                "owned_pid": ready["owned_pid"], "port": ready["port"]}
        validate_database_pool_identity(identity)
        report["fixture"] = identity
        report["database_pool"] = {"identity_verified": True, **identity["database_pool"]}
        report["measurements"] = asyncio.run(workload(base_url, args, credentials, identity,
            owned_resources=resources, total_started=total_started, setup_trace=setup_trace, renewal_report=renewal_report))
        report["result"] = report["measurements"]["result"]
    except Exception as error:
        # Exception text may contain URL, credentials or response body: record class only.
        report.update(result="FAIL", setup_error_class=type(error).__name__)
        safe_codes = {"csrf_setup_failed", "login_setup_failed", "target_fixture_identity_mismatch",
                      "owned_server_setup_failed", "owned_server_identity_mismatch",
                      "owned_capacity_monitor_setup_failed", "owned_capacity_monitor_identity_mismatch",
                      "fixture_database_pool_identity_mismatch"}
        if str(error) in safe_codes:
            report["setup_error_code"] = str(error)
    finally:
        if server_startup_started is not None:
            report.setdefault("server_startup", {"deadline_seconds": args.startup_deadline,
                "elapsed_seconds": time.monotonic() - server_startup_started,
                "standard_password_hashing_preserved": True})["readiness_reached"] = ready is not None
        if process is not None:
            if ready and ready.get("fixture_id") == fixture_id:
                try:
                    with httpx.Client(base_url="http://127.0.0.1:" + str(ready["port"]),
                                      trust_env=False, timeout=5) as client:
                        client.get("/__release__/shutdown", headers={"X-Release-Token": credentials["token"]})
                    process.wait(timeout=5)
                except (httpx.HTTPError, subprocess.TimeoutExpired):
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            report["cleanup"] = {"owned_process_stopped": process.poll() is not None,
                "database_and_evidence_retained": True, "external_services_touched": False}
            if ready:
                try:
                    with socket.create_connection(("127.0.0.1", ready["port"]), timeout=1):
                        report["cleanup"]["owned_port_closed"] = False
                        report["result"] = "FAIL"
                except OSError:
                    report["cleanup"]["owned_port_closed"] = True
        if log:
            log.close()
        if disk_process is not None:
            # This exclusive UUID marker is read only by our observer. No PID discovery or shared-process stop.
            with (run_dir / "disk-monitor.stop").open("x", encoding="utf-8") as file:
                file.write(disk_stop_token)
            try:
                disk_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                disk_process.terminate()
                disk_process.wait(timeout=10)
            report.setdefault("cleanup", {})["owned_capacity_monitor_stopped"] = disk_process.poll() is not None
            if disk_process.returncode != 0:
                report["result"] = "FAIL"
                report["capacity_observer_exit_failed"] = True
        if disk_log:
            disk_log.close()
        try:
            stack.close()
            if "portable_postgres" in report:
                report["portable_postgres"]["context_closed"] = True
        except Exception as error:
            report.update(result="FAIL", pg_cleanup_error_class=type(error).__name__)
        report["source_after"] = source_evidence()
        report["total_seconds"] = time.monotonic() - total_started
        if report["source_before"] != report["source_after"]:
            report["source_changed_during_run"] = True
            report["result"] = "FAIL"
        with (run_dir / "report.json").open("x", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps({"result": report["result"], "report": str(run_dir / "report.json"),
        "scope": report["scope"], "production_ready": False}, ensure_ascii=False))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
