"""No PG/HTTP/Django: short real process tree for last-Job-handle death proof."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--kind", choices=("controller", "coordinator", "grandchild", "control"), required=True)
    args = parser.parse_args()
    directory = Path(args.directory)
    from qa.postgres_fault_acceptance.contracts import write_json, require
    from qa.release_pipeline.identity import process_identity
    from qa.browser_acceptance.process_job import OwnedBrowserJob, join_owned_job
    from qa.postgres_fault_acceptance.process import Membership
    gate = {}
    job = child = None
    if args.kind in ("coordinator", "grandchild"):
        line = sys.stdin.readline(65537)
        require(len(line) <= 65536 and line.endswith("\n"), "dummy_bounded_gate_required")
        gate = json.loads(line)
        join_owned_job(gate["job_name"])
        membership = Membership(gate["job_name"])
        require(os.getpid() in membership.members(), "dummy_membership_missing")
        # Keep this object alive through controller death exactly as the real
        # coordinator does. A persistent query handle would prevent Job teardown.
    if args.kind in ("controller", "coordinator"):
        kind = "coordinator" if args.kind == "controller" else "grandchild"
        child = subprocess.Popen([sys.executable, "-B", "-m", "qa.postgres_fault_acceptance.tests.dummy_tree",
            "--directory", str(directory), "--kind", kind], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if args.kind == "controller":
            job = OwnedBrowserJob(child)
            gate = {"job_name": job.name}
        child.stdin.write(json.dumps(gate)+"\n")
        child.stdin.flush()
    write_json(directory / (args.kind+".json"), {"identity": process_identity(os.getpid()), **gate})
    try:
        # Upper safety bound if a test fails before terminating its exact controller.
        time.sleep(30)
    finally:
        if job:
            job.close()
        if child and child.stdin:
            child.stdin.close()

if __name__ == "__main__":
    main()
