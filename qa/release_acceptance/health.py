"""Bounded per-window business health and observed one-hour memory growth gates."""
from collections import deque
from .telemetry import memory_measurement


class MemoryGrowth:
    def __init__(self, start, warmup=900, window=3600, limit_mb_hour=32):
        self.start, self.warmup, self.window, self.limit = start, warmup, window, limit_mb_hour
        self.anchor = None
        self.windows = deque(maxlen=512)
        self.failed = False

    def observe(self, sample):
        now = sample["wall_time"]
        if now < self.start + self.warmup:
            return
        metric, memory = memory_measurement(sample)
        if metric is None:
            self.failed = True
            return
        if self.anchor is None:
            self.anchor = {"wall_time": now, "metric": metric, "first_bytes": memory,
                "count": 0, "sx": 0., "sy": 0., "sxx": 0., "sxy": 0.}
        a = self.anchor
        if a["metric"] != metric:
            self.failed = True
            return
        x, y = now - a["wall_time"], memory / (1024 * 1024)
        a["count"] += 1
        a["sx"] += x
        a["sy"] += y
        a["sxx"] += x*x
        a["sxy"] += x*y
        if x >= self.window:
            denominator = a["count"]*a["sxx"] - a["sx"]**2
            slope = (a["count"]*a["sxy"]-a["sx"]*a["sy"])/denominator*3600 if denominator > 0 else None
            valid = a["count"] >= 30 and slope is not None
            fail = not valid or slope > self.limit
            self.failed |= fail
            self.windows.append({"start_wall_time": a["wall_time"], "end_wall_time": now,
                "observed_seconds": x, "samples": a["count"], "metric": metric,
                "ols_growth_mib_per_hour": slope, "first_bytes": a["first_bytes"], "last_bytes": memory,
                "limit_mib_per_hour": self.limit, "result": "FAIL" if fail else "PASS"})
            self.anchor = None

    def evidence(self):
        return {"warmup_seconds": self.warmup, "minimum_observation_seconds": self.window,
            "limit_mib_per_hour": self.limit, "method": "observed OLS trend after warmup, >=30 samples and >=1 hour; not a leak diagnosis",
            "result": "FAIL" if self.failed else "PASS" if self.windows else "NOT_ENOUGH_OBSERVATION",
            "windows": list(self.windows)}


class WindowHealth:
    def __init__(self, seconds, factory, summarize, options, *, offered_rate=0, minimum_ratio=.95):
        self.seconds, self.factory, self.summarize, self.options = seconds, factory, summarize, options
        self.offered_rate, self.minimum_ratio = offered_rate, minimum_ratio
        self.current = factory()
        self.index = 0
        self.windows = deque(maxlen=512)
        self.failed = False
        self.total_windows = 0

    def _finish(self, elapsed, complete):
        result = self.summarize(self.current, elapsed, **self.options)
        failures = result["failures"]
        if not complete:
            failures = [f for f in failures if not f.endswith((":insufficient_samples", ":no_successful_semantic_sample"))
                        and f not in ("zero_load", "successful_throughput_below_target")]
            if not self.current:
                # A timer/worker scheduling tail can contain no business I/O.
                # summarize's empty-load safety sentinel is not an observed
                # 100% error rate here; complete empty windows still fail.
                failures = [f for f in failures if f != "unexpected_error_rate_exceeded"]
                result["unexpected_error_rate"] = None
                result["unexpected_error_rate_observed"] = False
        if complete and self.offered_rate and result["logical_operations_per_second"] < self.offered_rate*self.minimum_ratio:
            failures.append("offered_target_rate_not_delivered")
        result.update(failures=failures, result="FAIL" if failures else "PASS" if complete else "PARTIAL", complete_window=complete,
                      window_index=self.index, offered_logical_operations_per_second=self.offered_rate or None)
        self.failed |= bool(failures)
        self.windows.append(result)
        self.total_windows += 1
        self.current = self.factory()

    def observe(self, sample, elapsed):
        next_index = int(elapsed // self.seconds)
        while self.index < next_index:
            self._finish(self.seconds, True)
            self.index += 1
        self.current.append(sample)

    def finish(self, elapsed):
        # Final short window still checks latency/auth/timeout/errors, but is not a capacity sample-count claim.
        while elapsed - self.index*self.seconds >= self.seconds:
            self._finish(self.seconds, True)
            self.index += 1
        remaining = elapsed - self.index*self.seconds
        if remaining > 0:
            self._finish(remaining, remaining >= self.seconds)
        return {"window_seconds": self.seconds, "total_windows": self.total_windows,
            "retained_windows": list(self.windows), "result": "FAIL" if self.failed else "PASS"}
