"""Bounded numeric telemetry snapshots with maxima and validation covering every sample."""
from collections import deque
import math

FIELDS = {"active", "max_active", "admission_rejections", "waitress_pending_tasks", "waitress_active_threads",
    "monotonic", "cpu_seconds", "process_threads", "rss_bytes", "peak_rss_bytes", "wall_time",
    "fixture_disk_bytes", "disk_measurement_wall_time", "disk_scan_duration_seconds", "disk_files",
    "disk_monitor_ok", "waitress_socket_map_size", "waitress_connection_limit", "waitress_use_poll", "memory_metric_current"}


def memory_measurement(sample, *, conservative=False):
    values = [(key, sample[key]) for key in ("rss_bytes", "peak_rss_bytes") if
              type(sample.get(key)) in (int, float) and math.isfinite(sample[key]) and sample[key] >= 0]
    return (max(values, key=lambda item: item[1]) if conservative else values[0]) if values else (None, None)


class Telemetry:
    def __init__(self, capacity=4096):
        self.samples = deque(maxlen=capacity)
        self.count = 0
        self.maxima = {}
        self.missing = set()
        self.first = None
        self.last = None
        self.segments = deque(maxlen=512)
        self.segment = None

    def observe(self, sample, required=()):
        value = {key: number for key, number in sample.items() if key in FIELDS and
                 type(number) in (int, float) and math.isfinite(number)}
        self.missing.update(key for key in required if key not in value)
        self.count += 1
        self.first = self.first or value
        self.last = value
        self.samples.append(value)
        bucket = int(value.get("wall_time", 0) // 300)
        if self.segment is None or self.segment["bucket"] != bucket:
            if self.segment is not None:
                self.segments.append(self.segment)
            self.segment = {"bucket": bucket, "samples": 0, "maxima": {}}
        self.segment["samples"] += 1
        for key, number in value.items():
            self.maxima[key] = max(number, self.maxima.get(key, number))
            self.segment["maxima"][key] = max(number, self.segment["maxima"].get(key, number))
        if "wall_time" in value and "disk_measurement_wall_time" in value:
            age = value["wall_time"] - value["disk_measurement_wall_time"]
            self.maxima["capacity_measurement_age"] = max(age, self.maxima.get("capacity_measurement_age", age))
        _, memory = memory_measurement(value, conservative=True)
        if memory is not None:
            self.maxima["memory_bytes"] = max(memory, self.maxima.get("memory_bytes", memory))
        return value

    def evidence(self):
        return {"sample_count": self.count, "retained_sample_count": len(self.samples),
            "retention": "last bounded snapshots; maxima and validation cover all observed samples",
            "first_sample": self.first, "last_sample": self.last, "samples": list(self.samples),
            "resource_segment_seconds": 300, "resource_segments": list(self.segments) + ([self.segment] if self.segment else [])}


class DiskTrend:
    def __init__(self):
        self.first = None
        self.last = None

    def observe(self, sample):
        if "fixture_disk_bytes" in sample and "disk_measurement_wall_time" in sample:
            value = {"bytes": sample["fixture_disk_bytes"], "measurement_wall_time": sample["disk_measurement_wall_time"]}
            self.first = self.first or value
            self.last = value

    def evidence(self):
        span = self.last["measurement_wall_time"]-self.first["measurement_wall_time"] if self.first and self.last else 0
        rate = (self.last["bytes"]-self.first["bytes"])/span if span > 0 else None
        return {"first": self.first, "last": self.last, "observed_seconds": span,
            "observed_bytes_per_second": rate,
            "linear_additional_24h_bytes": max(rate, 0)*86400 if rate is not None else None,
            "scope": "diagnostic short-window extrapolation only; checkpoint/WAL/storage changes may invalidate it; never a PASS gate"}
