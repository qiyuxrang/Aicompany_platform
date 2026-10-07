"""Windows PostgreSQL launch ownership; no service starts until the Job gate.

Only the controller retains the KILL_ON_JOB_CLOSE handle. The gated launcher
joins, closes its query handle, and only then creates the actual postmaster.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qa.browser_acceptance.process_job import OwnedBrowserJob, join_owned_job
from qa.release_pipeline.identity import identity_alive, process_identity


def process_image(identity):
    if not identity_alive(identity):
        raise RuntimeError("owned PostgreSQL process creation identity changed")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, identity["pid"])
    if not handle:
        raise OSError("owned PostgreSQL image query failed")
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise OSError("owned PostgreSQL image unavailable")
        if not identity_alive(identity):
            raise RuntimeError("owned PostgreSQL identity changed during image query")
        return Path(buffer.value).resolve()
    finally:
        kernel.CloseHandle(handle)


def validate_gate(gate):
    from validation.private_path_safety import checked_path
    token = gate.get("token")
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
        raise ValueError("owned PostgreSQL UUID required")
    run = checked_path(gate["run_dir"], root=ROOT / ".runtime", must_exist=True)
    cluster = checked_path(gate["cluster"], root=run, must_exist=True)
    if run.name != token or cluster != run / "cluster":
        raise ValueError("owned PostgreSQL cluster boundary mismatch")
    marker_path = checked_path(run / "owner.json", root=run, must_exist=True)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker != {"token": token, "cluster": str(cluster)}:
        raise ValueError("owned PostgreSQL marker mismatch")
    executable = Path(gate["executable"]).resolve()
    if not executable.is_file() or executable.name.lower() != "postgres.exe":
        raise ValueError("owned PostgreSQL binary required")
    if type(gate.get("port")) is not int or not 1 <= gate["port"] <= 65535:
        raise ValueError("owned PostgreSQL port required")
    return run, cluster, executable


def validate_registration(gate, ready):
    if any(ready.get(key) != gate[key] for key in ("token", "cluster", "port", "job_name")) \
            or ready.get("joined_before_postgres_spawn") is not True:
        raise RuntimeError("owned PostgreSQL registration mismatch")
    identity = ready.get("identity", {})
    if type(identity.get("pid")) is not int or identity["pid"] <= 0 \
            or type(identity.get("creation_filetime")) is not int \
            or identity["creation_filetime"] <= 0 or identity.get("active") is not True:
        raise RuntimeError("owned PostgreSQL creation identity missing")
    return identity


def checked_evidence(path, run_dir):
    from validation.private_path_safety import checked_path
    return checked_path(path, root=run_dir, must_exist=False)


def validate_exit(value, identity):
    if not isinstance(value, dict) or value.get("identity") != identity \
            or type(value.get("exit_code")) is not int:
        raise RuntimeError("owned PostgreSQL exit identity/schema mismatch")
    return value["exit_code"]


def close_job(job, *, deadline, terminate=False):
    """Account for the complete owned tree within the caller's existing budget."""
    result = {"verified": False, "baseline_processes_touched": False,
              "termination_requested": terminate, "job_name": job.name}
    if not job.handle:
        result["error"] = "owned PostgreSQL Job already closed"
        return result
    try:
        if terminate and not job.kernel.TerminateJobObject(job.handle, 1):
            raise OSError("cannot terminate owned PostgreSQL Job")
        while True:
            info = job.accounting_type()
            if not job.kernel.QueryInformationJobObject(job.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                raise OSError("cannot query owned PostgreSQL Job accounting")
            result.update(active_processes=info.ActiveProcesses, total_owned_processes=info.TotalProcesses)
            if info.ActiveProcesses == 0:
                result["verified"] = time.monotonic() <= deadline
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
    except OSError as error:
        result["error"] = type(error).__name__
    finally:
        # KILL_ON_JOB_CLOSE also protects controller death. Closing never grants
        # success: accounting must already have proved zero within the deadline.
        result["handle_closed"] = bool(job.kernel.CloseHandle(job.handle))
        job.handle = None
        result["verified"] = result["verified"] and result["handle_closed"]
    return result


class OwnedPostgresProcess:
    """Minimal Popen-compatible view with the REAL PG PID, not a venv launcher."""
    def __init__(self, *, executable, cluster, run_dir, token, port, env, log, deadline):
        self.args = [str(executable), "-D", str(cluster)]
        self.job = self.launcher = None
        self.tree_cleanup = None
        self.identity = None
        self.ready_path = Path(run_dir) / ("pg-process-" + os.urandom(8).hex() + ".json")
        self.exit_path = self.ready_path.with_suffix(".exit.json")
        self.executable = Path(executable).resolve()
        self.run_dir = Path(run_dir)
        try:
            self.launcher = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--launch"],
                cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
            self.job = OwnedBrowserJob(self.launcher)
            gate = {"token": token, "run_dir": str(run_dir), "cluster": str(cluster), "port": port,
                    "executable": str(executable), "job_name": self.job.name,
                    "ready": self.ready_path.name, "exit": self.exit_path.name}
            self.launcher.stdin.write(json.dumps(gate) + "\n")
            self.launcher.stdin.flush()
            self.launcher.stdin.close()
            while not checked_evidence(self.ready_path, self.run_dir).exists():
                if self.launcher.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("owned PostgreSQL gated launcher did not register")
                time.sleep(.05)
            ready = json.loads(checked_evidence(self.ready_path, self.run_dir).read_text(encoding="utf-8"))
            self.identity = validate_registration(gate, ready)
            self.pid = self.identity["pid"]
            self.assert_live_owner()
        except BaseException:
            if self.launcher and self.launcher.stdin and not self.launcher.stdin.closed:
                self.launcher.stdin.close()  # EOF prevents initialization before gate delivery.
            if self.job:
                close_job(self.job, deadline=deadline, terminate=True)
            elif self.launcher and self.launcher.poll() is None:
                self.launcher.terminate()  # only the ungated exact Popen launcher, no PG exists.
            if self.launcher:
                try:
                    self.launcher.wait(timeout=max(.01, deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    pass
            raise

    def assert_live_owner(self):
        if not self.job or not self.identity or not identity_alive(self.identity):
            raise RuntimeError("owned PostgreSQL live identity unavailable")
        if self.pid not in self.job.members() or process_image(self.identity) != self.executable:
            raise RuntimeError("owned PostgreSQL Job/image identity mismatch")

    def poll(self):
        if identity_alive(self.identity):
            return None
        exit_path = checked_evidence(self.exit_path, self.run_dir)
        if exit_path.exists():
            return validate_exit(json.loads(exit_path.read_text(encoding="utf-8")), self.identity)
        code = self.launcher.poll()
        return code if code is not None else 1

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout if timeout is not None else 10)
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(.05)
        self.launcher.wait(timeout=max(.01, deadline-time.monotonic()))
        return self.poll()

    def close_tree(self, *, deadline, terminate=False):
        if self.tree_cleanup is None:
            self.tree_cleanup = close_job(self.job, deadline=deadline, terminate=terminate)
        return self.tree_cleanup


def launch_main():
    line = sys.stdin.readline(65537)
    if not line.endswith("\n") or len(line) > 65536:
        raise ValueError("bounded owned PostgreSQL stdin gate required")
    gate = json.loads(line)
    join_owned_job(gate["job_name"])  # MUST precede any PostgreSQL Popen.
    run, cluster, executable = validate_gate(gate)
    if not re.fullmatch(r"pg-process-[a-f0-9]{16}\.json", gate["ready"]) \
            or gate["exit"] != gate["ready"].replace(".json", ".exit.json"):
        raise ValueError("owned PostgreSQL evidence basename mismatch")
    for name in (gate["ready"], gate["exit"], gate["ready"]+".tmp"):
        if checked_evidence(run/name, run).exists():
            raise FileExistsError("owned PostgreSQL process evidence already exists")
    process = subprocess.Popen([str(executable), "-D", str(cluster)], cwd=ROOT,
        stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    identity = process_identity(process.pid)
    if process_image(identity) != executable:
        raise RuntimeError("owned PostgreSQL launch image mismatch")
    ready = {key: gate[key] for key in ("token", "cluster", "port", "job_name")}
    ready.update(identity=identity, launcher_identity=process_identity(os.getpid()),
                 joined_before_postgres_spawn=True)
    # Replace avoids the parent observing a partially written registration.
    temporary = run / (gate["ready"] + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(ready, stream)
    temporary.replace(run / gate["ready"])
    code = process.wait()
    with (run / gate["exit"]).open("x", encoding="utf-8") as stream:
        json.dump({"identity": identity, "exit_code": code}, stream)
    return code


if __name__ == "__main__":
    if sys.argv[1:] != ["--launch"]:
        raise SystemExit("owned PostgreSQL launcher requires its private stdin gate")
    raise SystemExit(launch_main())
