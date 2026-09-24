import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bridge_environment import ROOT, RUNTIME, EVIDENCE, WORKTREE, PORTAL_PYTHON, LEDGER_PYTHON, environment
import bridge_http_acceptance as acceptance

processes = {}
checks = []
logs = []


def start(side, overrides=None):
    port = 18310 if side == "portal" else 18318
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Refuse to replace listener on {port}")
    directory = ROOT if side == "portal" else WORKTREE
    values = {**os.environ, **environment(side), **(overrides or {}), "PYTHONPATH": str(directory / "backend"), "PYTHONIOENCODING": "utf-8"}
    command = ([str(PORTAL_PYTHON), "-m", "waitress", "--listen=127.0.0.1:18310", "--threads=8", "config.wsgi:application"] if side == "portal" else
               [str(LEDGER_PYTHON), str(WORKTREE / "backend/manage.py"), "runserver", "127.0.0.1:18318", "--noreload"])
    log = (RUNTIME / f"bridge-{side}-server.log").open("ab")
    logs.append(log)
    process = subprocess.Popen(command, cwd=directory, env=values, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    processes[side] = process
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{side} process exited before listening")
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"{side} did not listen")


def stop(side):
    process = processes.pop(side, None)
    if process:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
        process.wait(timeout=10)
        port = 18310 if side == "portal" else 18318
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    return
            time.sleep(0.1)
        raise RuntimeError(f"Owned service port {port} did not close")


def restart(side, overrides=None):
    stop(side)
    start(side, overrides)


class FaultHandler(BaseHTTPRequestHandler):
    mode = "malformed"
    redirected = 0

    def log_message(self, *arguments):
        pass

    def do_POST(self):
        self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 4096))
        if self.path == "/capture":
            type(self).redirected += 1
        if self.mode == "slow":
            time.sleep(4)
        if self.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:18319/capture")
            self.end_headers()
            return
        if self.mode == "drip":
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            try:
                for chunk in range(70):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        raw = b"not-json" if self.mode == "malformed" else b"x" * 300000
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def run_check(name, function, kind="real-service-fault"):
    started = time.monotonic()
    try:
        details = function()
        checks.append({"name": name, "status": "passed", "kind": kind, "elapsed_seconds": round(time.monotonic() - started, 3), "detail": details})
        print(name + ": passed", flush=True)
    except Exception as error:
        checks.append({"name": name, "status": "failed", "kind": kind, "error_type": type(error).__name__})
        print(name + ": FAILED (" + type(error).__name__ + ")" + (str(error) if isinstance(error, RuntimeError) else ""), flush=True)


def faults():
    portal = acceptance.Client(acceptance.PORTAL)
    portal.login("sales")
    legacy = acceptance.Client(acceptance.LEDGER)
    legacy.login("sales")
    def legacy_offline():
        stop("ledger")
        try:
            acceptance.require_status(portal.request("/api/business/summary/"), 503)
        finally:
            start("ledger")
        acceptance.require_status(portal.request("/api/business/summary/"), 200)
    run_check("legacy_service_offline_and_recovery", legacy_offline)

    def callback_offline():
        token = acceptance.ticket()
        stop("portal")
        try:
            acceptance.require_status(acceptance.bridge(token), 503)
        finally:
            start("portal")
        acceptance.require_status(portal.request("/api/business/summary/"), 200)
    run_check("portal_callback_offline_and_recovery", callback_offline)

    def bridge_disabled():
        restart("ledger", {"LEDGER_PORTAL_BRIDGE_ENABLED": "0"})
        try:
            acceptance.require_status(acceptance.bridge(acceptance.ticket()), 404)
            acceptance.require_status(legacy.request("/api/projects/"), 200)
            acceptance.require_status(portal.request("/api/business/summary/"), 503)
        finally:
            restart("ledger")
    run_check("bridge_switch_off_native_session_retained", bridge_disabled)

    def portal_unconfigured():
        restart("portal", {"PORTAL_BUSINESS_SUMMARY_URL": ""})
        try:
            payload = acceptance.require_status(portal.request("/api/business/summary/"), 503)
            assert payload.get("code") == "integration_not_configured"
            acceptance.require_status(legacy.request("/api/projects/"), 200)
        finally:
            restart("portal")
    run_check("portal_navigation_only_rollback", portal_unconfigured)

    fault_server = ThreadingHTTPServer(("127.0.0.1", 18319), FaultHandler)
    fault_server.daemon_threads = True
    threading.Thread(target=fault_server.serve_forever, daemon=True).start()
    try:
        for side in ("ledger", "portal"):
            for mode in ("malformed", "oversized", "redirect", "slow", "drip"):
                def inject(side=side, mode=mode):
                    FaultHandler.mode = mode
                    override = ({"LEDGER_PORTAL_REDEEM_URL": "http://127.0.0.1:18319/api/integration/redeem/"} if side == "ledger" else
                                {"PORTAL_BUSINESS_SUMMARY_URL": "http://127.0.0.1:18319/api/portal-bridge/summary/"})
                    restart(side, override)
                    try:
                        start_time = time.monotonic()
                        acceptance.require_status(portal.request("/api/business/summary/"), 503)
                        duration = time.monotonic() - start_time
                        assert duration < 6, "Failure exceeded bounded timeout"
                        assert FaultHandler.redirected == 0, "Redirect was followed"
                        if mode == "drip":
                            time.sleep(2.5)
                            for retry in range(3):
                                resumed_at = time.monotonic()
                                acceptance.require_status(portal.request("/api/business/summary/"), 503)
                                resumed_duration = time.monotonic() - resumed_at
                                assert 1 < resumed_duration < 6, "Slot did not recover for another real upstream request"
                                time.sleep(0.3)
                        return {"response_status": 503, "request_seconds": round(duration, 3), "redirect_followed": False}
                    finally:
                        restart(side)
                run_check(side + "_injected_" + mode, inject, "controlled-fault-endpoint-not-native-data")
    finally:
        fault_server.shutdown()
        fault_server.server_close()
    run_check("native_chain_recovered_after_faults", lambda: acceptance.require_status(portal.request("/api/business/summary/"), 200)["summary"])


def main():
    keep = "--keep-running" in sys.argv
    try:
        start("portal")
        start("ledger")
        with (RUNTIME / "bridge-http-tests.txt").open("w", encoding="utf-8") as output:
            result = subprocess.run([str(PORTAL_PYTHON), str(ROOT / "validation/bridge_http_acceptance.py")], cwd=ROOT, stdout=output, stderr=output, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        print(f"HTTP acceptance exit code: {result.returncode}", flush=True)
        if result.returncode:
            raise RuntimeError("Fix real HTTP acceptance before fault injection")
        faults()
        (EVIDENCE / "fault-acceptance.json").write_text(json.dumps({"checks": checks, "boundary": "Real process stop/restart and separately labelled HTTP fault endpoints; no business data mocks counted as native comparison"}, ensure_ascii=False, indent=2), encoding="utf-8")
        (RUNTIME / "bridge-processes.json").write_text(json.dumps({side: process.pid for side, process in processes.items()}), encoding="utf-8")
        if any(check["status"] != "passed" for check in checks):
            raise RuntimeError("Some fault checks failed")
    finally:
        if not keep or sys.exc_info()[0]:
            for side in tuple(processes):
                stop(side)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
