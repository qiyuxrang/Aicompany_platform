"""Only newly launched stdin-gated children may enter/exit these named Jobs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .contracts import require, registration_valid, write_json
from qa.browser_acceptance.process_job import OwnedBrowserJob
from qa.release_pipeline.identity import identity_alive, process_identity

class Worker:
    def __init__(self, directory, fixture_id, kind, label, env, *, once):
        self.directory, self.label = Path(directory), label
        self.log = (self.directory / (label + ".log")).open("x", encoding="utf-8")
        self.process = None
        self.job = None
        self.cleanup = None
        try:
            self.process = subprocess.Popen([sys.executable, "-B", "-m", "qa.worker_fault_acceptance.worker",
                "--run-dir", str(directory), "--label", label], cwd=self.directory.parents[2], env=env,
                stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
            self.launcher_identity = process_identity(self.process.pid)
            self.job = OwnedBrowserJob(self.process)
            self.gate = {"run_dir": str(directory), "fixture_id": fixture_id, "kind": kind, "label": label,
                         "once": once, "job_name": self.job.name}
            self.process.stdin.write(json.dumps(self.gate) + "\n")
            self.process.stdin.flush()
            ready = self.directory / (label + "-ready.json")
            deadline = time.monotonic() + 30
            while not ready.exists():
                require(self.process.poll() is None and time.monotonic() < deadline, "worker_readiness_failed")
                time.sleep(.1)
            registration = json.loads(ready.read_text(encoding="utf-8"))
            self.identity = registration_valid(registration, self.gate, self.job.members(), os.getpid())
            require(identity_alive(self.identity), "worker_creation_identity_failed")
            write_json(self.directory / (label + "-owner.json"), {"fixture_id": fixture_id,
                "launcher": self.launcher_identity, "worker": self.identity, "job_name": self.job.name})
        except BaseException:
            self.close()
            raise
    def alive(self):
        return self.process.poll() is None and identity_alive(self.identity) and self.identity["pid"] in self.job.members()
    def crash(self):
        require(self.alive(), "live_owned_worker_required_before_crash")
        return self.close()
    def wait(self, timeout):
        code = self.process.wait(timeout=timeout)
        require(code == 0, "normal_worker_exit_not_zero")
        marker = json.loads((self.directory / (self.label + "-exit.json")).read_text())
        require(marker.get("fixture_id") == self.gate["fixture_id"] and marker.get("exit_code") == 0,
                "worker_exit_marker_invalid")
        return self.close()
    def close(self):
        if self.cleanup is not None:
            return self.cleanup
        if self.job is not None:
            self.cleanup = self.job.close()
        elif self.process is not None:
            # Gate delivery has not succeeded; this exact Popen has initialized no service.
            if self.process.poll() is None:
                self.process.terminate()
            self.cleanup = {"verified": False, "gated_launcher_only": True}
        else:
            self.cleanup = {"verified": False, "not_launched": True}
        if self.process is not None:
            self.process.wait(timeout=10)
            self.cleanup["launcher_exit_code"] = self.process.returncode
        self.log.close()
        return self.cleanup


def resources(identity):
    """Query only a still-live known PID/creation time; no process discovery."""
    require(identity_alive(identity), "resource_identity_not_live")
    import ctypes
    from ctypes import wintypes
    class Memory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
    memory_api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    memory_api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, identity["pid"])
    if not handle:
        raise OSError("owned_process_resource_handle_failed")
    try:
        mem = Memory()
        mem.cb = ctypes.sizeof(mem)
        if not memory_api(handle, ctypes.byref(mem), ctypes.sizeof(mem)):
            raise OSError("owned_process_memory_failed")
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            raise OSError("owned_process_cpu_failed")
        numbers = [(v.dwHighDateTime << 32) | v.dwLowDateTime for v in times]
        require(numbers[0] == identity["creation_filetime"], "resource_process_creation_changed")
        return {"pid": identity["pid"], "creation_filetime": numbers[0], "rss_bytes": mem.rss,
                "peak_rss_bytes": mem.peak_rss, "private_bytes": mem.private,
                "cpu_seconds": (numbers[2]+numbers[3])/10000000}
    finally:
        kernel.CloseHandle(handle)
