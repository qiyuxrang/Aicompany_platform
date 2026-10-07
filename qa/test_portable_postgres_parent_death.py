"""Explicit Windows integration command; starts ONLY a fresh UUID-owned PG.

Not included in the pure runner guards. Execute after the shared source freeze:
python -B qa/test_portable_postgres_parent_death.py --postgres-bin <owned binaries>
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import re

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qa.run_portable_postgres import PortablePostgres, port_open, source_snapshot
from qa.release_pipeline.identity import identity_alive, process_identity
from qa.browser_acceptance.process_job import OwnedBrowserJob


class ParentDeathFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(value, code):
    if not value:
        raise ParentDeathFailure(code)


def current_job_members(name):
    """Query and CLOSE the temporary handle before the parent-death injection."""
    from ctypes import wintypes
    require(isinstance(name, str) and re.fullmatch(r"portal-qa-[a-f0-9]{32}", name), "owned_job_name_invalid")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wintypes.DWORD, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenJobObjectW(0x0004, False, name)
    require(bool(handle), "owned_job_query_open_failed")
    try:
        view = OwnedBrowserJob.__new__(OwnedBrowserJob)
        view.kernel, view.handle = kernel, handle
        return view.members()
    finally:
        require(bool(kernel.CloseHandle(handle)), "owned_job_query_handle_close_failed")


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
    temporary.replace(path)


def controller(binary, output):
    with PortablePostgres(binary, runtime_root=output/"postgres") as pg:
        # The controller is the only process retaining the Job handle.
        identities = [process_identity(pid) for pid in pg.process.job.members()]
        write_json(output/"ready.json", {"controller": process_identity(os.getpid()),
            "members": identities, "postmaster": pg.process.identity, "port": pg.port,
            "cluster": str(pg.cluster), "job_name": pg.process.job.name,
            "joined_before_postgres_spawn": True})
        time.sleep(60)  # hard self-owned lifetime if the parent test fails early.


def parent_death(binary):
    if os.name != "nt":
        raise RuntimeError("Windows owned Job parent-death proof required")
    directory = ROOT/".runtime"/"portable-postgres-parent-death"/uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    before = source_snapshot()
    report = {"outcome": "FAIL", "scope": "owned PG parent death only; no HTTP/model/cloud acceptance",
              "run_dir": str(directory), "source_before": before, "other_processes_touched": False,
              "controller_identity": None, "independent_control_identity": None, "ready_identity": None,
              "observed_job_members": [], "fault_injected": False}
    control = owned_controller = None
    identities = []
    stage = "independent_control_start_failed"
    try:
        control = subprocess.Popen([sys._base_executable, "-B", "-c", "import time;time.sleep(75)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        control_identity = process_identity(control.pid)
        report["independent_control_identity"] = control_identity
        with (directory/"controller.log").open("xb") as log:
            # Direct base CPython gives an exact controller PID; add only this
            # selected venv's locked packages so psycopg remains available.
            packages = Path(sys.prefix)/"Lib"/"site-packages"
            bootstrap = "import runpy,site,sys;site.addsitedir(sys.argv.pop(1));p=sys.argv.pop(1);runpy.run_path(p,run_name='__main__')"
            stage = "owned_controller_start_failed"
            owned_controller = subprocess.Popen([sys._base_executable, "-B", "-c", bootstrap,
                str(packages), str(Path(__file__).resolve()),
                "--postgres-bin", str(binary), "--controller", "--output", str(directory)], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            controller_identity = process_identity(owned_controller.pid)
            report["controller_identity"] = controller_identity
            deadline = time.monotonic()+45
            while not (directory/"ready.json").exists():
                if owned_controller.poll() is not None or time.monotonic() >= deadline:
                    raise ParentDeathFailure("owned_controller_exited_before_ready" if owned_controller.poll() is not None
                                             else "owned_pg_readiness_45s_exceeded")
                time.sleep(.05)
            stage = "ready_registration_unreadable"
            ready = json.loads((directory/"ready.json").read_text(encoding="utf-8"))
            report["ready_identity"] = {key: ready.get(key) for key in
                ("controller", "postmaster", "job_name", "port", "cluster", "joined_before_postgres_spawn")}
            identities = ready["members"]
            report["observed_job_members"] = identities
            report["owned_job_members"] = identities  # preserved history, including exited short-lived probes.
            require(ready["controller"] == controller_identity and identity_alive(controller_identity),
                    "owned_controller_creation_identity_changed")
            postmaster = ready["postmaster"]
            require(postmaster in identities, "postmaster_missing_from_observed_job_members")
            require(identity_alive(postmaster), "owned_postmaster_not_alive_before_injection")
            stage = "owned_job_current_members_query_failed"
            members = current_job_members(ready["job_name"])
            report["current_job_member_pids_before_injection"] = members
            require(postmaster["pid"] in members, "live_postmaster_not_in_owned_job")
            # A completed psql/backend in the historical inventory is normal.
            # It remains in the post-injection all-identities-exited check.
            report["observed_members_already_exited_before_injection"] = [value for value in identities
                if not identity_alive(value)]
            require(identity_alive(controller_identity), "controller_changed_during_job_query")
            require(identity_alive(postmaster), "postmaster_changed_during_job_query")
            require(identity_alive(control_identity), "independent_control_not_alive_before_injection")
            stage = "exact_controller_death_injection_failed"
            owned_controller.terminate()  # exact newly launched controller handle + creation proof.
            report["fault_injected"] = True
            owned_controller.wait(5)
            deadline = time.monotonic()+10
            while any(identity_alive(value) for value in identities) and time.monotonic() < deadline:
                time.sleep(.05)
            exited = all(not identity_alive(value) for value in identities)
            closed = not port_open(ready["port"])
            control_alive = identity_alive(control_identity)
            report.update(owned_job_all_exited=exited, owned_port_closed=closed,
                          independent_control_alive=control_alive)
            require(exited, "owned_pg_job_members_survived_10s")
            require(closed, "owned_pg_port_remained_open")
            require(control_alive, "independent_control_died")
            report.update(outcome="PASS", controller_identity=controller_identity,
                independent_control_identity=control_identity, independent_control_alive=True,
                owned_job_members=identities, owned_job_all_exited=exited, owned_port_closed=closed,
                joined_before_postgres_spawn=True, cluster_files_retained=True,
                pid_file_retained=(Path(ready["cluster"])/"postmaster.pid").exists())
    except Exception as error:
        report["error_type"] = type(error).__name__
        report["failure_code"] = error.code if isinstance(error, ParentDeathFailure) else stage
    finally:
        for label, process in (("owned_controller", owned_controller), ("independent_control", control)):
            if process is not None:
                try:
                    if process.poll() is None:
                        process.terminate()  # only exact controllers created above, never PG PID discovery.
                    process.wait(5)
                    report.setdefault("cleanup", {})[label+"_stopped"] = True
                except Exception as error:
                    report.setdefault("cleanup", {})[label+"_error_type"] = type(error).__name__
                    report.setdefault("failure_code", label+"_cleanup_failed")
                    report["outcome"] = "FAIL"
        report["recorded_job_members_exited"] = all(not identity_alive(value) for value in identities)
        report["source_after"] = source_snapshot()
        report["source_stable"] = report["source_after"] == before
        if not report["source_stable"] or not report["recorded_job_members_exited"]:
            report["outcome"] = "FAIL"
            report.setdefault("failure_code", "source_changed" if not report["source_stable"]
                              else "recorded_job_members_remained_after_cleanup")
        write_json(directory/"report.json", report)
    print(json.dumps({"outcome": report["outcome"], "report": str(directory/"report.json")}, ensure_ascii=False))
    return 0 if report["outcome"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres-bin", required=True)
    parser.add_argument("--controller", action="store_true")
    parser.add_argument("--output")
    arguments = parser.parse_args()
    if arguments.controller:
        from qa.run_portable_postgres import runtime_root_path
        output = runtime_root_path(arguments.output)
        if not output.is_dir():
            raise SystemExit("owned parent-death output missing")
        controller(arguments.postgres_bin, output)
    else:
        raise SystemExit(parent_death(arguments.postgres_bin))
