"""Only newly launched stdin-gated processes, never arbitrary PID termination."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from qa.browser_acceptance.process_job import OwnedBrowserJob
from qa.release_pipeline.identity import identity_alive, process_identity
from .contracts import ROOT, require

class Child:
    def __init__(self, module, directory, token, kind, env, payload=None):
        self.kind, self.directory = kind, directory
        self.job = self.process = None
        self.cleanup = None
        self.log = (directory / (kind + ".log")).open("x", encoding="utf-8")
        try:
            self.process = subprocess.Popen([sys.executable, "-B", "-m", module, "--run-dir", str(directory)],
                cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
            self.launcher_identity = process_identity(self.process.pid)
            self.job = OwnedBrowserJob(self.process)
            gate = {"fixture_id": token, "kind": kind, "job_name": self.job.name, **(payload or {})}
            self.process.stdin.write(json.dumps(gate) + "\n")
            self.process.stdin.flush()
            end = time.monotonic()+40
            ready = directory / (kind + "-ready.json")
            while not ready.exists():
                require(self.process.poll() is None and time.monotonic() < end, "gated_child_readiness_failed")
                time.sleep(.1)
            value = json.loads(ready.read_text(encoding="utf-8"))
            require(value.get("fixture_id") == token and value.get("job_name") == self.job.name, "child_registration_mismatch")
            self.identity = value["identity"]
            require(identity_alive(self.identity) and self.identity["pid"] in self.job.members(), "child_creation_membership_failed")
        except BaseException:
            self.close()
            raise
    def alive(self):
        return self.process.poll() is None and identity_alive(self.identity) and self.identity["pid"] in self.job.members()
    def close(self):
        if self.cleanup is not None:
            return self.cleanup
        if self.job is not None:
            self.cleanup = self.job.close()
        else:
            self.cleanup = {"verified": False, "gate_not_delivered": True}
            if self.process is not None and self.process.stdin is not None:
                self.process.stdin.close()  # pre-gate venv child receives EOF and cannot initialize services
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()  # exact new launcher, no gate/business was delivered
        if self.process is not None:
            self.process.wait(10)
            self.cleanup["launcher_exit_code"] = self.process.returncode
            if self.process.stdin is not None and not self.process.stdin.closed:
                self.process.stdin.close()
        self.log.close()
        return self.cleanup

class Membership:
    """Ephemeral query handles: children must never retain KILL_ON_CLOSE Jobs."""
    def __init__(self, name):
        self.name = name
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.OpenJobObjectW.restype = wintypes.HANDLE
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    def members(self):
        handle = self.kernel.OpenJobObjectW(0x0004, False, self.name)
        require(bool(handle), "outer_owned_job_query_failed")
        # A temporary wrapper supplies the frozen helper's bounded PID query.
        # No handle is ever stored on this long-lived Membership instance.
        view = OwnedBrowserJob.__new__(OwnedBrowserJob)
        view.kernel, view.handle = self.kernel, handle
        try:
            return OwnedBrowserJob.members(view)
        finally:
            require(bool(self.kernel.CloseHandle(handle)), "outer_query_handle_close_failed")
            view.handle = None
    def close(self):
        pass  # every query has already closed its handle, including exceptions

def process_image(identity):
    require(identity_alive(identity), "owned_image_identity_required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, identity["pid"])
    require(bool(handle), "owned_image_handle_failed")
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        require(bool(kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))), "owned_image_query_failed")
        require(identity_alive(identity), "owned_image_identity_changed")
        return Path(buffer.value).resolve()
    finally:
        kernel.CloseHandle(handle)
