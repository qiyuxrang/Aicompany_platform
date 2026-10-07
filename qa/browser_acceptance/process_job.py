"""Named Windows jobs scoped to newly created, stdin-gated QA workers."""
import ctypes
import os
import re
import socket
import struct
import time
import uuid


def join_owned_job(name):
    """Join before Django/Playwright initialization, including venv child races."""
    if os.name != "nt" or not re.fullmatch(r"portal-qa-[a-f0-9]{32}", name):
        raise ValueError("A UUID-owned Windows QA job is required")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenJobObjectW(0x0001 | 0x0004, False, name)  # assign + query
    if not handle:
        raise OSError("Cannot open the parent-owned QA job")
    try:
        process = kernel.GetCurrentProcess()
        included = wintypes.BOOL()
        if not kernel.IsProcessInJob(process, handle, ctypes.byref(included)):
            raise OSError("Cannot query worker QA job membership")
        if not included.value and not kernel.AssignProcessToJobObject(handle, process):
            raise OSError("Cannot join the parent-owned QA job")
        if not kernel.IsProcessInJob(process, handle, ctypes.byref(included)) or not included.value:
            raise OSError("Worker QA job membership is unverified")
    finally:
        kernel.CloseHandle(handle)


def port_listener_identity(port, members):
    """Inspect actual IPv4 listener owners; never contact or stop a foreign port."""
    if os.name != "nt":
        raise RuntimeError("TCP listener ownership currently requires Windows")
    from ctypes import wintypes

    class Row(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in
                    ("state", "local_address", "local_port", "remote_address", "remote_port", "pid")]

    api = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                    wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.restype = wintypes.DWORD
    size = wintypes.DWORD()
    if api(None, ctypes.byref(size), False, 2, 3, 0) != 122:
        raise OSError("Cannot size the IPv4 listener table")
    table = ctypes.create_string_buffer(size.value)
    if api(table, ctypes.byref(size), False, 2, 3, 0):
        raise OSError("Cannot query the IPv4 listener table")
    count = wintypes.DWORD.from_buffer(table).value
    listeners = []
    for index in range(count):
        row = Row.from_buffer(table, ctypes.sizeof(wintypes.DWORD) + index * ctypes.sizeof(Row))
        if socket.ntohs(row.local_port & 0xffff) == port:
            listeners.append({"pid": row.pid, "address": socket.inet_ntoa(struct.pack("=I", row.local_address))})
    return {"port": port, "listeners": listeners,
            "owned": bool(listeners) and all(row["pid"] in members and row["address"] == "127.0.0.1"
                                             for row in listeners)}


class OwnedBrowserJob:
    def __init__(self, process):
        self.handle = None
        self.process = process
        self.name = "portal-qa-" + uuid.uuid4().hex
        if os.name != "nt":
            raise RuntimeError("Owned browser process-tree verification currently requires Windows")
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IOCounters)] + [
                (name, ctypes.c_size_t) for name in
                ("ProcessMemoryLimit", "JobMemoryLimit", "PeakProcessMemoryUsed", "PeakJobMemoryUsed")]

        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [
                (name, wintypes.DWORD) for name in
                ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]

        self.accounting_type = Accounting
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                        wintypes.DWORD, ctypes.c_void_p]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, self.name)
        already_exists = ctypes.get_last_error() == 183
        try:
            if not self.handle or already_exists:
                raise OSError("Cannot create browser-owned process job")
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError("Cannot configure browser-owned process job")
            # Only this runner's newly created Popen handle is ever assigned.
            # Worker also joins by this unique name before initialization: a
            # Windows venv launcher may have spawned it before this assignment.
            if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise OSError("Cannot assign owned browser worker to process job")
        except Exception:
            if self.handle:
                self.kernel.CloseHandle(self.handle)
                self.handle = None
            raise

    def members(self):
        from ctypes import wintypes
        if not self.handle:
            raise OSError("Owned QA job is already closed")
        for capacity in (16, 64, 256, 1024, 4096):
            class ProcessIds(ctypes.Structure):
                _fields_ = [("assigned", wintypes.DWORD), ("listed", wintypes.DWORD),
                            ("pids", ctypes.c_size_t * capacity)]
            info = ProcessIds()
            if self.kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(info), ctypes.sizeof(info), None):
                return [int(pid) for pid in info.pids[:info.listed]]
            if ctypes.get_last_error() != 234:
                raise OSError("Cannot query owned QA job process IDs")
        raise OSError("Owned QA job exceeds bounded process-list capacity")

    def close(self):
        if not self.handle:
            return {"verified": False, "reason": "job_not_initialized"}
        result = {"owned_worker_pid": self.process.pid, "baseline_processes_touched": False,
                  "verified": False}
        try:
            if not self.kernel.TerminateJobObject(self.handle, 1):
                raise OSError("Cannot stop browser-owned process tree")
            deadline = time.monotonic() + 10
            while True:
                accounting = self.accounting_type()
                if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                        ctypes.sizeof(accounting), None):
                    raise OSError("Cannot verify browser-owned process tree")
                result["active_processes"] = accounting.ActiveProcesses
                result["total_owned_processes"] = accounting.TotalProcesses
                if accounting.ActiveProcesses == 0:
                    result["verified"] = True
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(.1)
        except OSError as error:
            result["error"] = str(error)
        finally:
            result["handle_closed"] = bool(self.kernel.CloseHandle(self.handle))
            self.handle = None
            result["verified"] = result["verified"] and result["handle_closed"]
        return result
