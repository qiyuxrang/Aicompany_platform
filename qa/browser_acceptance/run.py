"""Run owned loopback PostgreSQL + actual HTTP + isolated Playwright worker."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from qa.run_portable_postgres import PortablePostgres, SYSTEM_ENVIRONMENT, hidden_process_options, port_open, redact, source_snapshot
from qa.browser_acceptance.browser import ROUTES, HOMES
from qa.browser_acceptance.process_job import OwnedBrowserJob, port_listener_identity
from qa.browser_acceptance.semantic import KNOWLEDGE_SCOPE_DETAIL, KNOWLEDGE_DENIED_ROUTES


def snapshot(dist):
    result = source_snapshot()
    files = dict(result["files"])
    for directory in (ROOT / "frontend/src", ROOT / "qa/browser_acceptance", dist):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("frontend/package.json", "frontend/pnpm-lock.yaml"):
        path = ROOT / name
        if path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(), "files": files}


def expected_routes():
    planned = {(role, HOMES[role] + section, width) for role, sections in ROUTES.items()
               for section in sections for width in (1280, 1440, 1920)}
    planned.update(("ops", "/preview/" + center, width)
                   for center in ("product", "cost", "hr", "finance", "business")
                   for width in (1280, 1440, 1920))
    return planned


def validate_browser_result(result, run):
    if result.get("outcome") != "PASS" or not result.get("cleanup", {}).get("playwright_browser_closed"):
        raise ValueError("Browser worker did not pass with verified API close")
    observed = {(item["role"], item["path"], item["width"]) for item in result.get("routes", []) if item.get("passed") is True}
    if not expected_routes() <= observed:
        raise ValueError("Required role/route/viewport assertions are missing")
    for check in ("ui-login-real-http-session", "positive-and-cross-role-denied-api",
                  "mutation-without-csrf-denied", "csrf-protected-logout-invalidates-session"):
        roles = {item.get("role") for item in result.get("checks", []) if item.get("name") == check}
        if roles != set(ROUTES):
            raise ValueError("Required per-role login/authorization/CSRF evidence is missing")
    names = {item.get("name") for item in result.get("checks", [])}
    if not {"first-login-password-change-and-session-invalidation",
            "engineering-failure-is-not-empty-and-recovery-is-real",
            "no-unexpected-browser-errors-and-all-six-roles-covered"} <= names:
        raise ValueError("Required first-login/failure/error assertions are missing")
    scope_checks = {item.get("path") for item in result.get("checks", [])
                    if item.get("name") == "knowledge-unassigned-scope-denied-without-fake-empty"
                    and item.get("role") == "product" and item.get("status") == 403
                    and item.get("response") == {"code": "scope_revoked", "detail": KNOWLEDGE_SCOPE_DETAIL}}
    if scope_checks != set(KNOWLEDGE_DENIED_ROUTES):
        raise ValueError("Required knowledge scope-denial UI evidence is missing")
    for key in ("page_errors", "request_failures", "external_requests", "unexpected_http_errors", "unexpected_console_errors"):
        if key not in result or result[key]:
            raise ValueError("Unexpected browser errors or missing error evidence")
    screenshots = result.get("screenshots", [])
    if len(screenshots) < len(expected_routes()) // 3:
        raise ValueError("Required screenshot evidence is missing")
    for filename in screenshots:
        path = (run / filename).resolve()
        if not path.is_relative_to(run.resolve()) or not path.is_file() or path.stat().st_size < 8:
            raise ValueError("Screenshot evidence is missing or outside owned directory")


def dependency_probe(python, browser=False):
    packages = ["playwright", "greenlet", "pyee"] if browser else ["Django", "djangorestframework", "psycopg", "waitress"]
    code = "import sys,json,importlib.metadata as m;print(json.dumps({'python':sys.version,'packages':{k:m.version(k) for k in " + repr(packages) + "}}))"
    environment = {key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENVIRONMENT}
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    result = subprocess.run([str(python), "-I", "-c", code], env=environment, capture_output=True,
                            text=True, encoding="utf-8", timeout=30, **hidden_process_options())
    if result.returncode:
        raise RuntimeError("Required isolated runtime dependencies unavailable")
    return json.loads(result.stdout)


def executable(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("Explicit runtime/browser executable does not exist")
    return path


def validate_server_identity(ready, identifier, launcher_pid, runner_pid, members, job_name):
    actual_pid, parent_pid = ready.get("owned_pid"), ready.get("parent_pid")
    if (ready.get("uuid") != identifier or ready.get("job_name") != job_name
            or actual_pid not in members or launcher_pid not in members
            or actual_pid == parent_pid
            or (parent_pid != runner_pid if actual_pid == launcher_pid else parent_pid not in members)
            or ready.get("synthetic") is not True or ready.get("database") != "postgresql"
            or ready.get("urlconf") != "config.urls"
            or ready.get("default_hasher") != "django.contrib.auth.hashers.PBKDF2PasswordHasher"
            or ready.get("agent_enabled") is not False or ready.get("real_model_calls") is not False
            or ready.get("knowledge_scope_unassigned") is not True):
        raise RuntimeError("Fixture ownership identity mismatch")


def server_port_cleanup(port, owned, probe=port_open):
    # An absent/untrusted ready port is never evidence of successful shutdown.
    if port is None or not owned:
        return {"verified": False, "port": port, "port_known": port is not None,
                "port_ownership_proven": False, "port_closed_observed": None,
                "reason": "owned_listener_not_proven"}
    closed = not probe(port)
    return {"verified": closed, "port": port, "port_known": True,
            "port_ownership_proven": True, "port_closed_observed": closed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", required=True, type=Path)
    parser.add_argument("--server-python", required=True, type=Path)
    parser.add_argument("--browser-python", required=True, type=Path)
    parser.add_argument("--browser-executable", type=Path,
                        default=Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"))
    parser.add_argument("--dist", type=Path, default=ROOT / "frontend/dist")
    parser.add_argument("--startup-timeout", type=int, default=90)
    parser.add_argument("--browser-timeout", type=int, default=480)
    parser.add_argument("--page-timeout-ms", type=int, default=20000)
    args = parser.parse_args(argv)
    identifier = uuid.uuid4().hex
    run = ROOT / ".runtime/browser-acceptance" / identifier
    run.mkdir(parents=True, exist_ok=False)
    result = {"uuid": identifier, "outcome": "RUNNING", "run_dir": str(run), "started_at": time.time(),
              "plan": {"roles": list(ROUTES), "route_count": len(expected_routes()) // 3,
                       "route_viewport_assertions": len(expected_routes())}, "cleanup": {},
              "limitations": ["Local Windows PostgreSQL and Edge: not Linux cloud acceptance",
                              "Route/authorization/CSRF/layout acceptance, not every business workflow",
                              "No native Agent Runtime/model/RAG/Office execution validation"]}
    server = browser_process = pg = browser_job = server_job = None
    base = None
    candidate_port = None
    listener_owned = False
    password, new_password, token = (secrets.token_urlsafe(32) + "!9" for _ in range(3))
    sensitive = [password, new_password, token]
    source_before = None
    try:
        args.server_python = executable(args.server_python)
        args.browser_python = executable(args.browser_python)
        args.browser_executable = executable(args.browser_executable)
        args.dist = args.dist.resolve()
        if not args.dist.is_relative_to(ROOT.resolve()) or not (args.dist / "index.html").is_file():
            raise ValueError("A built frontend inside this workspace is required")
        if not (10 <= args.startup_timeout <= 300 and 30 <= args.browser_timeout <= 1200
                and 1000 <= args.page_timeout_ms <= 60000):
            raise ValueError("Timeouts must remain bounded")
        result["runtimes"] = {"server": dependency_probe(args.server_python),
                              "browser": dependency_probe(args.browser_python, browser=True)}
        result["browser_executable"] = str(args.browser_executable)
        source_before = snapshot(args.dist)
        result["source_before"] = source_before
        pg = PortablePostgres(args.postgres_bin, database_name="portal_pg_" + identifier)
        with pg:
            sensitive.extend([pg.env["PORTAL_DB_PASSWORD"], pg.env["PORTAL_SECRET_KEY"]])
            result["postgres_report"] = str(pg.run_dir / "report.json")
            env = dict(pg.env)
            env.update(PORTAL_FRONTEND_DIST=str(args.dist), PORTAL_PRODUCT_P1_ENABLED="1",
                       PORTAL_PRODUCT_STORAGE_ROOT=str(run / "storage/product"),
                       PORTAL_HR_STORAGE_ROOT=str(run / "storage/hr"),
                       PORTAL_ENGINEERING_STORAGE_ROOT=str(run / "storage/engineering"),
                       PORTAL_TENDER_STORAGE_ROOT=str(run / "storage/tender"),
                       PORTAL_PRODUCT_PARSER_PYTHON=str(args.server_python),
                       PORTAL_PRODUCT_DOCUMENT_PYTHON=str(args.server_python),
                       BROWSER_FIXTURE_PASSWORD=password, BROWSER_FIXTURE_TOKEN=token)
            with (run / "server.log").open("x", encoding="utf-8") as log:
                server = subprocess.Popen([str(args.server_python), "-B", str(ROOT / "qa/browser_acceptance/fixture_server.py"),
                    "--run-dir", str(run), "--uuid", identifier], cwd=ROOT, env=env,
                    stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", **hidden_process_options())
                result["owned_server_pid"] = server.pid
                try:
                    server_job = OwnedBrowserJob(server)
                    server.stdin.write(json.dumps({"uuid": identifier, "job_name": server_job.name}) + "\n")
                    server.stdin.flush()
                    server.stdin.close()
                    deadline = time.monotonic() + args.startup_timeout
                    while not (run / "ready.json").is_file():
                        if server.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("Owned fixture startup failed; inspect server.log")
                        time.sleep(.2)
                    ready = json.loads((run / "ready.json").read_text(encoding="utf-8"))
                    reported_port = ready.get("port")
                    if type(reported_port) is not int or not 1 <= reported_port <= 65535:
                        raise RuntimeError("Fixture ready port is invalid or unknown")
                    candidate_port = reported_port
                    result["candidate_ready_port"] = candidate_port
                    members = server_job.members()
                    result["server_job_members_at_ready"] = members
                    listener = port_listener_identity(candidate_port, members)
                    result["server_listener_ownership"] = listener
                    listener_owned = listener["owned"]
                    if not listener_owned:
                        raise RuntimeError("Ready port is not an owned loopback listener; no HTTP request sent")
                    validate_server_identity(ready, identifier, server.pid, os.getpid(), members, server_job.name)
                    base = "http://127.0.0.1:" + str(candidate_port)
                    result["fixture"] = ready
                    identity_request = urllib.request.Request(base + "/__browser__/identity", headers={"X-Browser-Token": token})
                    with urllib.request.urlopen(identity_request, timeout=5) as response:
                        if json.load(response) != ready:
                            raise RuntimeError("HTTP fixture identity mismatch")
                    browser_env = {key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENVIRONMENT}
                    browser_env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
                    config = {"uuid": identifier, "run_dir": str(run), "base": base, "password": password,
                              "new_password": new_password, "browser_executable": str(args.browser_executable),
                              "page_timeout_ms": args.page_timeout_ms,
                              "knowledge_scope_unassigned": ready["knowledge_scope_unassigned"]}
                    with (run / "browser.log").open("x", encoding="utf-8") as browser_log:
                        browser_process = subprocess.Popen([str(args.browser_python), "-I", "-B",
                            str(ROOT / "qa/browser_acceptance/browser.py")], cwd=ROOT, env=browser_env,
                            stdin=subprocess.PIPE, stdout=browser_log, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", **hidden_process_options())
                        browser_job = OwnedBrowserJob(browser_process)
                        config["job_name"] = browser_job.name
                        browser_process.communicate(json.dumps(config), timeout=args.browser_timeout)
                    result["browser_exit_code"] = browser_process.returncode
                    if browser_process.returncode != 0:
                        raise RuntimeError("Browser worker failed; inspect browser-result.json/browser.log")
                    browser_result = json.loads((run / "browser-result.json").read_text(encoding="utf-8"))
                    validate_browser_result(browser_result, run)
                    result["browser_summary"] = {"outcome": browser_result["outcome"], "checks": len(browser_result["checks"]),
                                                 "route_assertions": len(browser_result["routes"]),
                                                 "screenshots": len(browser_result["screenshots"])}
                    if (run / "denied-network.jsonl").exists():
                        raise RuntimeError("An unexpected backend outbound request was blocked")
                finally:
                    if browser_job:
                        result["cleanup"]["browser_process_tree"] = browser_job.close()
                    if browser_process and browser_process.poll() is None:
                        browser_process.terminate()
                        try:
                            browser_process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            browser_process.kill()
                            browser_process.wait(timeout=5)
                    result["cleanup"]["browser_worker_stopped"] = browser_process is None or browser_process.poll() is not None
                    if server.poll() is None and base:
                        try:
                            request = urllib.request.Request(base + "/__browser__/shutdown", headers={"X-Browser-Token": token})
                            urllib.request.urlopen(request, timeout=3).close()
                            server.wait(timeout=10)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                    if server_job:
                        result["cleanup"]["server_process_tree"] = server_job.close()
                    if server.poll() is None:
                        server.terminate()
                        try:
                            server.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            server.kill()
                            server.wait(timeout=5)
                    port_cleanup = server_port_cleanup(candidate_port, listener_owned)
                    result["cleanup"].update(server_process_stopped=server.poll() is not None,
                        server_port_closed=port_cleanup["verified"], server_port=port_cleanup)
        result["cleanup"]["postgres"] = pg.evidence.get("cleanup", {})
        if not all(result["cleanup"].get(key) for key in
                   ("browser_worker_stopped", "server_process_stopped", "server_port_closed")):
            raise RuntimeError("Owned browser/server cleanup was not verified")
        if not result["cleanup"]["postgres"].get("verified"):
            raise RuntimeError("Owned PostgreSQL cleanup was not verified")
        if not result["cleanup"].get("browser_process_tree", {}).get("verified"):
            raise RuntimeError("Owned browser process-tree cleanup was not verified")
        if not result["cleanup"].get("server_process_tree", {}).get("verified"):
            raise RuntimeError("Owned server process-tree cleanup was not verified")
        result["source_after"] = snapshot(args.dist)
        if result["source_after"]["sha256"] != source_before["sha256"]:
            raise RuntimeError("Source or compiled frontend changed during acceptance")
        result["outcome"] = "PASS"
    except Exception as error:
        result["outcome"] = "FAIL"
        result["error"] = redact(type(error).__name__ + ": " + str(error), sensitive)
    finally:
        if pg:
            result["cleanup"]["postgres"] = pg.evidence.get("cleanup", {})
        if source_before:
            result["source_after"] = snapshot(args.dist)
        for name in ("server.log", "browser.log"):
            path = run / name
            if path.is_file():
                path.write_text(redact(path.read_bytes().decode("utf-8", errors="replace"), sensitive), encoding="utf-8")
        result["finished_at"] = time.time()
        result["exit_code"] = 0 if result["outcome"] == "PASS" else 1
        (run / "report.json").write_text(redact(json.dumps(result, ensure_ascii=False, indent=2), sensitive), encoding="utf-8")
        print(result["outcome"], "evidence:", run, flush=True)
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
