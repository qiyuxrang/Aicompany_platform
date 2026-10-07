"""Bounded, allowlisted numeric observation; never copy logs or business bodies."""
import json
import math
from pathlib import Path

FIELDS = {"wall_time", "cpu_seconds", "rss_bytes", "peak_rss_bytes", "process_threads",
          "waitress_pending_tasks", "waitress_active_threads", "waitress_socket_map_size",
          "fixture_disk_bytes", "disk_measurement_wall_time", "disk_scan_duration_seconds",
          "disk_files", "disk_monitor_ok"}


def numeric_tail(path):
    path = Path(path)
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - 8192))
        lines = stream.read().decode("utf-8", errors="ignore").splitlines()
    for line in reversed(lines):
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                continue
            result = {key: number for key, number in value.items() if key in FIELDS
                      and type(number) in (int, float) and math.isfinite(number)}
            if "wall_time" in result:
                return result
        except ValueError:
            pass
    return None
