"""Owned, low-frequency numeric capacity observer outside the Web process/GIL."""
import argparse
import json
import math
import os
from pathlib import Path
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def cache_is_fresh(value, now, maximum_age):
    required = ("fixture_disk_bytes", "measurement_wall_time", "scan_duration_seconds", "files", "ok")
    if not isinstance(value, dict) or any(type(value.get(key)) not in (int, float) or
            not math.isfinite(value[key]) for key in required):
        return False
    return value["ok"] == 1 and value["fixture_disk_bytes"] >= 0 and value["files"] >= 0 and value["scan_duration_seconds"] >= 0 and \
        -1 <= now - value["measurement_wall_time"] <= maximum_age


def publish(path, value, initial=False):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8") as file:
        json.dump(value, file)
    if initial and path.exists():
        raise ValueError("refusing existing capacity cache")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--fixture-id", required=True)
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    fixture_id = uuid.UUID(args.fixture_id).hex
    directory = Path(args.run_dir).resolve()
    if directory.name != fixture_id or not directory.is_relative_to(ROOT / ".runtime" / "release-acceptance") or \
            not directory.is_dir() or not math.isfinite(args.interval) or not 5 <= args.interval <= 10:
        raise ValueError("invalid owned observer directory/interval")
    stop_token = os.environ.pop("RELEASE_DISK_STOP_TOKEN")
    with (directory / "disk-monitor-ready.json").open("x", encoding="utf-8") as file:
        json.dump({"fixture_id": fixture_id, "owned_pid": os.getpid(), "parent_pid": os.getppid()}, file)
    cache = directory / "disk-latest.json"
    initial = True
    while True:
        stop = directory / "disk-monitor.stop"
        if stop.exists() and stop.read_text(encoding="utf-8") == stop_token:
            break
        started = time.monotonic()
        value = {"ok": 1, "fixture_disk_bytes": 0, "files": 0}
        try:
            for path in directory.rglob("*"):
                try:
                    if path.is_file():
                        value["fixture_disk_bytes"] += path.stat().st_size
                        value["files"] += 1
                except FileNotFoundError:
                    continue
        except OSError:
            value["ok"] = 0
        value.update(measurement_wall_time=time.time(), scan_duration_seconds=time.monotonic() - started)
        publish(cache, value, initial)
        initial = False
        # Preserve cadence including measured scan time. Check private stop marker promptly.
        until = time.monotonic() + max(0, args.interval - value["scan_duration_seconds"])
        while time.monotonic() < until:
            if stop.exists() and stop.read_text(encoding="utf-8") == stop_token:
                return
            time.sleep(.2)


if __name__ == "__main__":
    main()
