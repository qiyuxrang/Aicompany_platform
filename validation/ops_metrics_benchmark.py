import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from portal import ops_metrics


def measure(action, count=10000):
    samples = []
    for _ in range(count):
        start = time.perf_counter_ns()
        action()
        samples.append((time.perf_counter_ns() - start) / 1000)
    samples.sort()
    return {"iterations": count, "median_us": round(statistics.median(samples), 3),
            "p95_us": round(samples[int(count * 0.95)], 3), "max_us": round(max(samples), 3)}


ops_metrics._reset_metrics()
empty = measure(lambda: None)
normal = measure(lambda: ops_metrics.record_request("/api/modules/", "GET", 200, 1.0))
ops_metrics._reset_metrics()
origin = time.time() - 890
for bucket in range(180):
    for route in range(128):
        ops_metrics.record_request(f"/api/synthetic/{route}/", "GET", 200, 1.0, now=origin + bucket * 5)
full = measure(lambda: ops_metrics.record_request("/api/synthetic/0/", "GET", 200, 1.0, now=origin + 899), 3000)
snapshot = ops_metrics.get_performance(now=origin + 899)
bucket_count = len(ops_metrics._buckets)
key_count = len({key for _, bucket in ops_metrics._buckets for key in bucket})
ops_metrics._lock.acquire()
try:
    busy = measure(lambda: ops_metrics.record_request("/api/modules/", "GET", 200, 1.0))
finally:
    ops_metrics._lock.release()
assert bucket_count <= 182 and key_count <= 128
report = {"timestamp": datetime.now(timezone.utc).isoformat(), "empty_call": empty,
          "single_route": normal, "full_window_128_keys": full, "lock_busy_drop": busy,
          "buckets": bucket_count, "keys": key_count, "snapshot_routes": len(snapshot["routes"]),
          "boundary": "隔离Python进程内合成基准；不请求运行服务，不代表生产压测。仅测记录器增量开销，不包括数据库、网络及页面查询。锁忙直接丢弃样本。"}
(ROOT / "docs/evidence/ops/metrics-benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
