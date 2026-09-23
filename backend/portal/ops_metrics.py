import math
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone as datetime_timezone
from threading import Lock
from zoneinfo import ZoneInfo


WINDOW_SECONDS = 900
BUCKET_SECONDS = 5
MAX_KEYS = 128
_MAX_BUCKETS = WINDOW_SECONDS // BUCKET_SECONDS + 2
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_lock = Lock()
_buckets = deque(maxlen=_MAX_BUCKETS)
_collected_since = None
_last_snapshot = None


def _empty_performance():
    return {
        "state": "not_collected",
        "scope": "single_process_api",
        "window_seconds": WINDOW_SECONDS,
        "collected_since": None,
        "requests": 0,
        "errors": 0,
        "error_rate": None,
        "average_ms": None,
        "max_ms": None,
        "routes": [],
    }


def _timestamp(value):
    if value is None:
        return time.time()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime_timezone.utc)
        return value.timestamp()
    return float(value)


def _prune(now):
    global _collected_since
    cutoff = now - WINDOW_SECONDS
    while _buckets and _buckets[0][0] + BUCKET_SECONDS <= cutoff:
        _buckets.popleft()
    if not _buckets:
        _collected_since = None


def record_request(route, method, status_code, duration_ms, now=None):
    global _collected_since
    try:
        if not isinstance(route, str) or not route or len(route) > 160:
            return False
        method = str(method).upper()
        if method not in {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}:
            method = "OTHER"
        status_code = int(status_code)
        duration_ms = float(duration_ms)
        if not 100 <= status_code <= 599 or duration_ms < 0 or not math.isfinite(duration_ms):
            return False
        timestamp = _timestamp(now)
    except (TypeError, ValueError, OverflowError):
        return False
    if not _lock.acquire(blocking=False):
        return False
    try:
        _prune(timestamp)
        bucket_at = math.floor(timestamp / BUCKET_SECONDS) * BUCKET_SECONDS
        if not _buckets or _buckets[-1][0] != bucket_at:
            _buckets.append((bucket_at, {}))
        status_group = f"{status_code // 100}xx"
        key = (route, method, status_group)
        known_keys = {known for _, bucket in _buckets for known in bucket}
        if key not in known_keys and len(known_keys) >= MAX_KEYS:
            return False
        stats = _buckets[-1][1].setdefault(key, [0, 0, 0.0, 0.0])
        stats[0] += 1
        stats[1] += int(status_code >= 500)
        stats[2] += duration_ms
        stats[3] = max(stats[3], duration_ms)
        if _collected_since is None:
            _collected_since = datetime.fromtimestamp(timestamp, tz=datetime_timezone.utc)
        return True
    except Exception:
        return False
    finally:
        _lock.release()


def get_performance(now=None):
    global _last_snapshot
    try:
        timestamp = _timestamp(now)
    except (TypeError, ValueError, OverflowError):
        return deepcopy(_last_snapshot or _empty_performance())
    if not _lock.acquire(blocking=False):
        return deepcopy(_last_snapshot or _empty_performance())
    try:
        _prune(timestamp)
        combined = {}
        for _, bucket in _buckets:
            for key, stats in bucket.items():
                target = combined.setdefault(key, [0, 0, 0.0, 0.0])
                target[0] += stats[0]
                target[1] += stats[1]
                target[2] += stats[2]
                target[3] = max(target[3], stats[3])
        requests = sum(stats[0] for stats in combined.values())
        errors = sum(stats[1] for stats in combined.values())
        if not requests:
            result = _empty_performance()
        else:
            total_ms = sum(stats[2] for stats in combined.values())
            routes = []
            for (route, method, status_group), stats in combined.items():
                routes.append({
                    "route": route,
                    "method": method,
                    "status_group": status_group,
                    "requests": stats[0],
                    "errors": stats[1],
                    "average_ms": round(stats[2] / stats[0], 2),
                    "max_ms": round(stats[3], 2),
                })
            routes.sort(key=lambda item: (-item["requests"], item["route"], item["method"], item["status_group"]))
            result = {
                "state": "collected",
                "scope": "single_process_api",
                "window_seconds": WINDOW_SECONDS,
                "collected_since": _collected_since.astimezone(_SHANGHAI).isoformat(),
                "requests": requests,
                "errors": errors,
                "error_rate": round(errors / requests, 4),
                "average_ms": round(total_ms / requests, 2),
                "max_ms": round(max(stats[3] for stats in combined.values()), 2),
                "routes": routes,
            }
        _last_snapshot = result
        return deepcopy(result)
    except Exception:
        return deepcopy(_last_snapshot or _empty_performance())
    finally:
        _lock.release()


def _reset_metrics():
    global _collected_since, _last_snapshot
    if not _lock.acquire(blocking=False):
        return False
    try:
        _buckets.clear()
        _collected_since = None
        _last_snapshot = None
        return True
    finally:
        _lock.release()


class ApiMetricsMiddleware:
    excluded_paths = {"/api/csrf/", "/api/me/", "/health/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        try:
            path = request.path
            if (not path.startswith("/api/") or path.startswith("/api/ops/")
                    or path.startswith("/static/") or path in self.excluded_paths):
                return response
            match = request.resolver_match
            route = "/" + match.route.lstrip("/") if match and match.route else ""
            record_request(route, request.method, response.status_code, (time.perf_counter() - started) * 1000)
        except Exception:
            pass
        return response
