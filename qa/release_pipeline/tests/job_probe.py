"""Short integration probe only: no HTTP workload, Django, database, or providers."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from qa.release_pipeline.contracts import atomic_json
from qa.release_pipeline.identity import process_identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("owner", "worker", "leaf"), required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--worker-python")
    args = parser.parse_args()
    directory = Path(args.directory)
    if args.mode == "leaf":
        atomic_json(directory / "leaf.json", process_identity(os.getpid()))
        time.sleep(120)
    elif args.mode == "worker":
        from qa.browser_acceptance.process_job import join_owned_job
        gate = json.loads(sys.stdin.readline())
        join_owned_job(gate["job_name"])
        subprocess.Popen([sys.executable, "-B", "-m", "qa.release_pipeline.tests.job_probe",
                          "--mode", "leaf", "--directory", str(directory)], creationflags=subprocess.CREATE_NO_WINDOW)
        atomic_json(directory / "worker.json", process_identity(os.getpid()))
        time.sleep(120)
    else:
        from qa.browser_acceptance.process_job import OwnedBrowserJob
        child = subprocess.Popen([args.worker_python, "-B", "-m", "qa.release_pipeline.tests.job_probe",
                                  "--mode", "worker", "--directory", str(directory)],
                                 stdin=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        job = OwnedBrowserJob(child)
        child.stdin.write(json.dumps({"job_name": job.name}) + "\n")
        child.stdin.flush()
        child.stdin.close()
        deadline = time.monotonic() + 20
        while not (directory / "leaf.json").exists():
            if child.poll() is not None or time.monotonic() >= deadline:
                job.close()
                raise RuntimeError("job probe failed")
            time.sleep(.05)
        atomic_json(directory / "ready.json", {"owner": process_identity(os.getpid()),
                    "members": job.members(), "job_name": job.name})
        time.sleep(120)  # Test kills precisely this owned process. OS closes its sole job handle.


if __name__ == "__main__":
    main()
