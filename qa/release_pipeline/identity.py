"""Windows process identity, without PID discovery or foreign-process mutation."""
import ctypes
import hashlib
import json
import os
from ctypes import wintypes


def command_hash(arguments):
    return hashlib.sha256(json.dumps(arguments, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def process_identity(pid):
    if os.name != "nt" or type(pid) is not int or pid <= 0:
        raise ValueError("Windows positive process identity required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise OSError("Owned process identity unavailable")
    try:
        creation, exit_time, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                      ctypes.byref(system), ctypes.byref(user)):
            raise OSError("Owned process creation time unavailable")
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError("Owned process exit state unavailable")
        return {"pid": pid, "creation_filetime": (creation.dwHighDateTime << 32) | creation.dwLowDateTime,
                "active": code.value == 259}
    finally:
        kernel.CloseHandle(handle)


def identity_alive(expected):
    try:
        actual = process_identity(expected["pid"])
        return actual["active"] and actual["creation_filetime"] == expected["creation_filetime"]
    except (OSError, KeyError, ValueError):
        return False


class PipelineMutex:
    """One active local pipeline per workspace; no filesystem locks or stale PID cleanup."""
    def __init__(self, workspace):
        if os.name != "nt":
            raise ValueError("Windows pipeline mutex required")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        name = "Local\\portal-release-pipeline-" + hashlib.sha256(str(workspace).lower().encode()).hexdigest()
        self.handle = self.kernel.CreateMutexW(None, False, name)
        if not self.handle:
            raise OSError("Cannot create owned pipeline mutex")
        if ctypes.get_last_error() == 183:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise ValueError("pipeline_already_running")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
