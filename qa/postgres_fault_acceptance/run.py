"""Bounded parent supervisor for one owned Portal PostgreSQL crash/restart case."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

from .contracts import ROOT, RUNTIME, require, source_manifest, validate_budgets, write_json

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--postgres-bin", required=True)
    p.add_argument("--deadline", type=float, default=600)
    p.add_argument("--max-fixture-disk-mb", type=float, default=1024)
    return p

def main(argv=None):
    args = parser().parse_args(argv)
    validate_budgets(args.deadline, args.max_fixture_disk_mb)
    require(os.name == "nt", "owned_windows_jobs_required")
    from qa.run_portable_postgres import SYSTEM_ENVIRONMENT
    from .process import Child
    binary = Path(args.postgres_bin).resolve()
    require(all((binary / (name+".exe")).is_file() for name in ("postgres", "pg_ctl", "initdb")), "explicit_postgres_binaries_required")
    token = uuid.uuid4().hex
    directory = RUNTIME / token
    directory.mkdir(parents=True, exist_ok=False)
    before = source_manifest()
    report = {"fixture_id": token, "result": "FAIL", "production_ready": False,
        "source_before": before, "deadline_seconds": args.deadline, "max_fixture_disk_mb": args.max_fixture_disk_mb}
    env = {key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENVIRONMENT}
    env.update(PYTHONPATH=str(ROOT), PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    child = None
    started = time.monotonic()
    disk = {"scans": 0, "max_bytes": 0, "scan_wall_seconds": 0, "last_scan": 0}
    try:
        child = Child("qa.postgres_fault_acceptance.coordinator", directory, token, "coordinator", env,
            {"postgres_bin": str(binary), "deadline": args.deadline, "disk_mb": args.max_fixture_disk_mb})
        report["outer_job"] = {"job_name": child.job.name, "launcher_identity": child.launcher_identity,
                               "coordinator_identity": child.identity}
        while child.process.poll() is None:
            require(time.monotonic()-started <= args.deadline, "parent_wall_deadline_exceeded")
            require(len(child.job.members()) <= 96, "parent_owned_subtree_budget_exceeded")
            if time.monotonic()-disk["last_scan"] >= 5:
                scan = time.monotonic()
                size = 0
                for path in directory.rglob("*"):
                    try:
                        if path.is_file():
                            size += path.stat().st_size
                    except FileNotFoundError:
                        pass
                disk.update(scans=disk["scans"]+1, max_bytes=max(size, disk["max_bytes"]), last_scan=time.monotonic(),
                            scan_wall_seconds=disk["scan_wall_seconds"]+time.monotonic()-scan)
                require(size <= args.max_fixture_disk_mb*1024**2, "parent_private_disk_budget_exceeded")
            time.sleep(.25)
        require(child.process.returncode == 0, "coordinator_failed")
        result = json.loads((directory / "coordinator-report.json").read_text(encoding="utf-8"))
        require(result.get("fixture_id") == token and result.get("result") == "PASS", "coordinator_proof_missing")
        report["acceptance"] = result
        report["result"] = "PASS"
    except BaseException as error:
        report.update(result="FAIL", error_class=type(error).__name__)
        if isinstance(error, ValueError) and str(error).replace("_", "").isalnum() and len(str(error)) <= 100:
            report["failure_code"] = str(error)
        path = directory / "coordinator-report.json"
        if path.is_file():
            report["acceptance"] = json.loads(path.read_text(encoding="utf-8"))
    finally:
        try:
            report["outer_job_cleanup"] = child.close() if child else {"verified": False, "not_launched": True}
        except Exception as error:
            report["outer_job_cleanup"] = {"verified": False, "error_class": type(error).__name__}
        report["source_after"] = source_manifest()
        report["source_changed"] = before != report["source_after"]
        report["parent_disk_budget"] = disk
        report["actual_wall_seconds_including_preparation_and_cleanup"] = time.monotonic()-started
        if report["source_changed"] or not report["outer_job_cleanup"].get("verified"):
            report["result"] = "FAIL"
        write_json(directory / "report.json", report)
    print(json.dumps({"result": report["result"], "report": str(directory / "report.json"), "production_ready": False}))
    return 0 if report["result"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
