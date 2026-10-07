"""Stdin-gated worker: join its named job before importing pressure/PG code."""
import argparse
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--phase-index", required=True, type=int)
    args = parser.parse_args()
    from .contracts import PLAN, atomic_json, identifier, owned_run, phase_arguments, require
    run = owned_run(args.run_dir)
    require(run.is_dir() and 0 <= args.phase_index < len(PLAN), "owned_phase_required")
    line = sys.stdin.readline(65537)
    require(len(line) <= 65536 and line.endswith("\n"), "bounded_phase_gate_required")
    gate = json.loads(line)
    require(gate["pipeline_id"] == run.name and gate["phase_index"] == args.phase_index,
            "phase_gate_identity_mismatch")
    # Must be before *any* Django, HTTP fixture, PortablePostgres or service initialization.
    from qa.browser_acceptance.process_job import join_owned_job
    join_owned_job(gate["job_name"])
    from .identity import command_hash, process_identity
    fixture = identifier(gate["fixture_id"])
    arguments = phase_arguments(PLAN[args.phase_index], fixture, Path(gate["postgres_bin"]))
    registration = {"pipeline_id": run.name, "phase_index": args.phase_index,
                    "fixture_id": fixture, "job_name": gate["job_name"],
                    "identity": process_identity(os.getpid()), "parent_pid": os.getppid(),
                    "argv_sha256": command_hash(arguments), "joined_at": time.time()}
    atomic_json(run / f"phase-{args.phase_index}-worker.json", registration)
    from qa.release_acceptance.run import main as pressure_main
    code = pressure_main(arguments)
    atomic_json(run / f"phase-{args.phase_index}-exit.json", {"fixture_id": fixture,
                "exit_code": code, "finished_at": time.time()})
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # No exception body, arguments, response, or process environment is printed.
        print(json.dumps({"result": "FAIL", "error_class": type(error).__name__}), flush=True)
        raise SystemExit(1)
