"""Detached one-shot supervisor. A phase's job handle owns its entire child tree."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .contracts import (PLAN, ROOT, atomic_json, backend_gate, browser_gate, digest, expected_phases,
                        manifest, owned_run, phase_arguments, phase_gate, read_json, require)
from .identity import PipelineMutex, command_hash, process_identity
from .observer import numeric_tail

LIMITATIONS = ["Local synthetic Portal HTTP only; production_ready=false",
               "No AI/model, HR/product worker load, Native license or AG-15/16 closure",
               "No cloud, Linux Office, whole-host/PG/Redis resource or full-stack recovery acceptance"]


def environment():
    from qa.release_acceptance.run import sanitized_environment
    result = sanitized_environment()
    result.update(PYTHONPATH=str(ROOT), PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    return result


def pg_evidence(pressure_dir):
    # Exactly this phase's preallocated UUID subtree, never a latest-run search.
    directory = pressure_dir / "postgres"
    children = list(directory.iterdir()) if directory.is_dir() else []
    require(len(children) == 1 and children[0].is_dir(), "phase_pg_directory_not_unique")
    child = children[0].resolve()
    require(child.parent == directory.resolve(), "phase_pg_directory_escape")
    owner = read_json(child / "owner.json")
    require(owner.get("token") == child.name and Path(owner.get("cluster", "")).resolve() == child / "cluster",
            "phase_pg_owner_mismatch")
    report = read_json(child / "report.json")
    require(Path(report.get("run_dir", "")).resolve() == child, "phase_pg_report_path_mismatch")
    return child / "report.json", report


def execute_phase(run, index, spec, config, state):
    from qa.browser_acceptance.process_job import OwnedBrowserJob
    fixture = uuid.uuid4().hex
    pressure_dir = ROOT / ".runtime/release-acceptance" / fixture
    require(not pressure_dir.exists(), "fresh_phase_uuid_required")
    arguments = phase_arguments(spec, fixture, config["postgres_bin"])
    command = [sys.executable, "-B", "-m", "qa.release_pipeline.phase_worker", "--run-dir", str(run),
               "--phase-index", str(index)]
    record = {"name": spec["name"], "phase_index": index, "fixture_id": fixture, "result": "RUNNING",
              "pressure_report": str(pressure_dir / "report.json"), "started_at": time.time(),
              "planned_workload_seconds": sum(p[2] for p in expected_phases(spec)),
              "argv_sha256": command_hash(arguments)}
    state["phases"].append(record)
    state["current_phase"] = index
    atomic_json(run / "status.json", state)
    process, job = None, None
    start = time.monotonic()
    maximum = record["planned_workload_seconds"] + 1800
    try:
        with (run / f"phase-{index}.log").open("x", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=environment(), stdin=subprocess.PIPE,
                        stdout=log, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                        creationflags=subprocess.CREATE_NO_WINDOW)
            record["launcher_identity"] = process_identity(process.pid)
            job = OwnedBrowserJob(process)
            record["job_name"] = job.name
            gate = {"pipeline_id": run.name, "phase_index": index, "fixture_id": fixture,
                    "job_name": job.name, "postgres_bin": config["postgres_bin"]}
            process.stdin.write(json.dumps(gate) + "\n")
            process.stdin.flush()
            process.stdin.close()
            registration_path = run / f"phase-{index}-worker.json"
            last_publish = 0
            while process.poll() is None:
                now = time.monotonic()
                require(now - start <= maximum, "phase_supervision_deadline_exceeded")
                if registration_path.is_file():
                    registration = read_json(registration_path)
                    require(registration.get("fixture_id") == fixture and registration.get("job_name") == job.name
                            and registration.get("phase_index") == index
                            and registration.get("argv_sha256") == record["argv_sha256"], "phase_registration_mismatch")
                    members = job.members()
                    if registration["identity"]["pid"] not in members:
                        # A venv child may exit just before its launcher reports the exit.
                        terminal = read_json(run / f"phase-{index}-exit.json")
                        require(terminal.get("fixture_id") == fixture and terminal.get("exit_code") == 0,
                                "worker_not_in_owned_job")
                    else:
                        parent = registration.get("parent_pid")
                        require(parent == os.getpid() if registration["identity"]["pid"] == process.pid else parent in members,
                                "worker_launch_parent_mismatch")
                    record["worker_identity"] = registration["identity"]
                    record["job_member_count"] = len(members)
                else:
                    require(now - start < 30, "worker_job_join_not_confirmed")
                if (run / "abort.request.json").exists():
                    request = read_json(run / "abort.request.json", 4096)
                    require(request.get("pipeline_id") == run.name, "abort_request_identity_mismatch")
                    raise InterruptedError("explicit_owned_pipeline_abort")
                if now - last_publish >= 30:
                    require(manifest(state.get("candidate_assets", [])) == state["source_before"], "pipeline_source_changed")
                    for item in state["prerequisites"].values():
                        require(digest(item["path"]) == item["sha256"], "prerequisite_report_changed")
                    state["updated_at"] = time.time()
                    record["supervised_elapsed_seconds"] = now - start
                    record["resource_tail"] = numeric_tail(pressure_dir / "resources.jsonl")
                    # Absence during startup is distinct from measured resource failure.
                    record["resource_scope"] = "bounded Web numeric tail; live semantic windows remain runner-owned"
                    atomic_json(run / "status.json", state)
                    last_publish = now
                time.sleep(.5)
            record["launcher_exit_code"] = process.returncode
            worker_exit = read_json(run / f"phase-{index}-exit.json")
            require(worker_exit.get("fixture_id") == fixture and worker_exit.get("exit_code") == 0
                    and process.returncode == 0, "worker_exit_not_successful")
            report_path = pressure_dir / "report.json"
            report = read_json(report_path, 64*1024*1024)
            pg_path, pg_report = pg_evidence(pressure_dir)
            phase_gate(report, spec, fixture, process.returncode, pg_report)
            require(manifest(state.get("candidate_assets", [])) == state["source_before"], "pipeline_source_changed")
            record.update(pressure_report_sha256=digest(report_path), pg_report=str(pg_path),
                          pg_report_sha256=digest(pg_path), effective_workload_seconds=report["measurements"]["effective_workload_seconds"],
                          renewal_successes=report["measurements"]["session_renewal"]["successes"], result="PASS")
    except BaseException as error:
        record.update(result="ABORTED" if isinstance(error, (InterruptedError, KeyboardInterrupt)) else "FAIL",
                      failure_class=type(error).__name__)
        raise
    finally:
        if job is not None:
            record["owned_job_cleanup"] = job.close()
        elif process is not None:
            # Before gate delivery no worker may initialize any fixture or descendant.
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            record["owned_job_cleanup"] = {"verified": False, "gated_launcher_stopped": True}
        if process is not None:
            process.wait(timeout=10)
        record["finished_at"] = time.time()
        record["supervised_elapsed_seconds"] = time.monotonic() - start
        if not record.get("owned_job_cleanup", {}).get("verified"):
            record["result"] = "FAIL"
        state["updated_at"] = time.time()
        atomic_json(run / "status.json", state)
    require(record["result"] == "PASS", "phase_cleanup_not_verified")


def run_sequence(run, config, state, execute=execute_phase):
    """A failure propagates immediately; no retry, parallel scheduling or phase resume."""
    for index, spec in enumerate(PLAN):
        execute(run, index, spec, config, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    require(os.name == "nt", "owned_windows_job_required")
    run = owned_run(args.run_dir)
    line = sys.stdin.readline(65537)
    require(len(line) <= 65536 and line.endswith("\n"), "bounded_supervisor_gate_required")
    config = json.loads(line)
    require(config["pipeline_id"] == run.name and run.is_dir(), "supervisor_gate_identity_mismatch")
    require(not (run / "owner.json").exists() and not (run / "status.json").exists()
            and not (run / "report.json").exists(), "one_shot_pipeline_already_started")
    state = {"pipeline_id": run.name, "result": "RUNNING", "production_ready": False,
             "limitations": LIMITATIONS, "phases": [], "current_phase": None,
             "started_at": time.time(), "updated_at": time.time(), "source_before": manifest()}
    owner = {"pipeline_id": run.name, "identity": process_identity(os.getpid()), "parent_pid": os.getppid(),
             "argv_sha256": command_hash(sys.argv), "configuration_sha256": command_hash(config)}
    atomic_json(run / "owner.json", owner)
    atomic_json(run / "status.json", state)
    mutex = None
    try:
        commit_line = sys.stdin.readline(4097)
        require(len(commit_line) <= 4096 and commit_line.endswith("\n"), "supervisor_commit_gate_required")
        commit = json.loads(commit_line)
        require(commit.get("pipeline_id") == run.name and commit.get("authorized") is True,
                "supervisor_commit_identity_mismatch")
        mutex = PipelineMutex(ROOT.resolve())
        launch = read_json(run / "launch.json")
        require(launch.get("pipeline_id") == run.name and launch.get("configuration") == config,
                "supervisor_launch_configuration_changed")
        prerequisites = {}
        for name, function in (("backend", backend_gate), ("browser", browser_gate)):
            path = Path(config[name + "_report"]).resolve()
            require(path.is_relative_to(ROOT / ".runtime"), "explicit_runtime_report_required")
            require(digest(path) == launch["prerequisite_sha256"][name + "_report"], "prerequisite_report_changed")
            value = read_json(path)
            function(value, path) if name == "browser" else function(value)
            if name == "browser":
                state["candidate_assets"] = [name for name in value["source_after"]["files"] if name.startswith(".runtime/")]
            prerequisites[name] = {"path": str(path), "sha256": digest(path), "accepted": True}
        state["prerequisites"] = prerequisites
        state["source_before"] = manifest(state.get("candidate_assets", []))
        atomic_json(run / "status.json", state)
        run_sequence(run, config, state)
        state["result"] = "PASS"
    except BaseException as error:
        state.update(result="ABORTED" if isinstance(error, (KeyboardInterrupt, InterruptedError)) else "FAIL",
                     failure_class=type(error).__name__)
    finally:
        try:
            state["source_after"] = manifest(state.get("candidate_assets", []))
        except Exception as error:
            state.update(source_after=None, result="FAIL", source_capture_error_class=type(error).__name__)
        if state["source_before"] != state["source_after"]:
            state.update(result="FAIL", source_changed=True)
        state["updated_at"] = state["finished_at"] = time.time()
        atomic_json(run / "status.json", state)
        atomic_json(run / "report.json", state)
        if mutex is not None:
            mutex.close()
    print(json.dumps({"pipeline_id": run.name, "result": state["result"], "report": str(run / "report.json")}), flush=True)
    return 0 if state["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
