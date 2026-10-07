"""Observe normal startup growth; never checkout, warm or close the pool."""
import math
import time

from .contracts import require

def wait_returned_pool(wrapper, evidence, *, timeout=5, clock=time.monotonic, sleep=time.sleep):
    require(math.isfinite(timeout) and 0 < timeout <= 5, "bounded_pool_startup_wait_required")
    started = clock()
    observations, initial = 0, None
    while True:
        require(wrapper.connection is None, "startup_wrapper_checkout_not_returned")
        value = evidence(wrapper)
        stats = value.get("stats", {})
        require(all(type(stats.get(key)) is int for key in ("pool_available", "pool_size", "requests_waiting")),
                "actual_pool_startup_stats_missing")
        require(0 <= stats["pool_available"] <= stats["pool_size"] <= value["max_size"]
                and stats["pool_size"] >= value["min_size"] and stats["requests_waiting"] >= 0,
                "actual_pool_startup_stats_invalid")
        observations += 1
        if initial is None:
            initial = {key: stats[key] for key in ("pool_available", "pool_size", "requests_waiting")}
        elapsed = clock()-started
        require(elapsed <= timeout, "startup_pool_growth_did_not_settle")
        if stats["pool_available"] == stats["pool_size"] and stats["requests_waiting"] == 0:
            return value, {"seconds": elapsed, "observations": observations, "initial_stats": initial,
                "maximum_wait_seconds": timeout, "wrapper_connection_is_none": True,
                "pool_borrowed_or_prewarmed": False, "pool_closed": False}
        remaining = timeout-elapsed
        require(remaining > 0, "startup_pool_growth_did_not_settle")
        sleep(min(.05, remaining))
