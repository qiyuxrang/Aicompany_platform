"""Start exactly one hidden independent supervisor, then return its owned status path."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .contracts import ROOT, RUNTIME, atomic_json, digest, read_json, require
from .identity import command_hash, identity_alive, process_identity


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-report", required=True, type=Path)
    parser.add_argument("--browser-report", required=True, type=Path)
    parser.add_argument("--postgres-bin", required=True, type=Path)
    args = parser.parse_args(argv)
    require(os.name == "nt", "Windows_owned_job_supervision_required")
    config = {"backend_report": str(args.backend_report.resolve()), "browser_report": str(args.browser_report.resolve()),
              "postgres_bin": str(args.postgres_bin.resolve())}
    for key in ("backend_report", "browser_report"):
        path = Path(config[key])
        require(path.is_relative_to(ROOT / ".runtime") and path.is_file(), "explicit_local_report_required")
    for name in ("initdb.exe", "postgres.exe", "pg_ctl.exe"):
        require((Path(config["postgres_bin"]) / name).is_file(), "explicit_postgres_binaries_required")
    identifier = uuid.uuid4().hex
    run = RUNTIME / identifier
    run.mkdir(parents=True, exist_ok=False)
    config["pipeline_id"] = identifier
    atomic_json(run / "launch.json", {"pipeline_id": identifier, "configuration": config,
                "prerequisite_sha256": {key: digest(config[key]) for key in ("backend_report", "browser_report")}})
    from .supervisor import environment
    command = [sys.executable, "-B", "-m", "qa.release_pipeline.supervisor", "--run-dir", str(run)]
    process = None
    committed = False
    try:
        with (run / "supervisor.log").open("x", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=environment(), stdin=subprocess.PIPE,
                        stdout=log, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
            atomic_json(run / "launcher.json", {"pipeline_id": identifier, "identity": process_identity(process.pid),
                        "argv_sha256": command_hash(command), "created_at": time.time()})
            process.stdin.write(json.dumps(config) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 30
            while not (run / "owner.json").is_file():
                require(process.poll() is None and time.monotonic() < deadline, "supervisor_startup_failed")
                time.sleep(.1)
            owner = read_json(run / "owner.json")
            require(owner.get("pipeline_id") == identifier and identity_alive(owner["identity"]),
                    "supervisor_owner_identity_failed")
            expected_parent = os.getpid() if owner["identity"]["pid"] == process.pid else process.pid
            require(owner.get("parent_pid") == expected_parent, "supervisor_launch_parent_mismatch")
            # A second gate prevents a venv child from starting work if launch ownership verification fails.
            process.stdin.write(json.dumps({"pipeline_id": identifier, "authorized": True}) + "\n")
            process.stdin.flush()
            committed = True
            process.stdin.close()
        print(json.dumps({"pipeline_id": identifier, "result": "RUNNING", "status": str(run / "status.json")}), flush=True)
        return 0
    except BaseException:
        if process is not None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if committed:
                # The registered independent supervisor owns its jobs. Request its own cleanup, never kill a shim tree.
                atomic_json(run / "abort.request.json", {"pipeline_id": identifier, "requested_at": time.time()})
            elif process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        atomic_json(run / "launch-failed.json", {"pipeline_id": identifier, "result": "FAIL", "finished_at": time.time()})
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"result": "FAIL"}), flush=True)
        raise SystemExit(1)
