"""Owned synthetic fixture server. No environment files or existing databases are loaded."""
import argparse
import ctypes
import io
import json
import logging
import os
import select
from pathlib import Path
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def waitress_parameters(args):
    return {"host": args.host, "port": args.port, "threads": args.threads,
            "connection_limit": args.connection_limit,
            "asyncore_use_poll": os.name != "nt" and hasattr(select, "poll")}


def process_memory():
    if os.name == "nt":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return {"rss_bytes": counters.WorkingSetSize, "peak_rss_bytes": counters.PeakWorkingSetSize, "memory_metric_current": 1}
        return {"rss_bytes": None, "memory_measurement_error": True}
    try:
        import resource
        import sys
        value = {"peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss *
                 (1 if sys.platform == "darwin" else 1024), "memory_metric_current": 0}
        if sys.platform.startswith("linux"):
            try:
                resident_pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
                value.update(rss_bytes=resident_pages * os.sysconf("SC_PAGE_SIZE"), memory_metric_current=1)
            except (OSError, ValueError, IndexError):
                pass  # Explicit peak fallback remains available and conservative.
        return value
    except ImportError:
        return {"peak_rss_bytes": None, "memory_measurement_error": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--fixture-id", required=True)
    parser.add_argument("--users", type=int, required=True)
    parser.add_argument("--rows", type=int, default=20)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--admission-limit", type=int, default=2)
    parser.add_argument("--connection-limit", type=int, default=512)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--allow-external-bind", action="store_true")
    parser.add_argument("--postgres-dsn-env", help="Explicit RELEASE_ACCEPTANCE_PG_DSN; empty isolated DB only")
    args = parser.parse_args()
    fixture_id = uuid.UUID(args.fixture_id).hex
    run_dir = Path(args.run_dir).resolve()
    allowed = ROOT / ".runtime" / "release-acceptance"
    if not run_dir.is_relative_to(allowed) or run_dir.name != fixture_id or not run_dir.is_dir():
        raise ValueError("run-dir must be an existing UUID-owned release acceptance directory")
    if not 1 <= args.users <= 1000 or not 1 <= args.rows <= 2000:
        raise ValueError("users/rows outside bounded fixture capacity")
    if not 1 <= args.admission_limit < args.threads <= 512:
        raise ValueError("threads must exceed positive synthetic admission limit")
    if not args.users + args.threads + 4 <= args.connection_limit <= 4096 or \
            (os.name == "nt" and args.connection_limit > 512):
        raise ValueError("connection limit lacks user/denial/control budget or exceeds Windows select budget")
    if args.host != "127.0.0.1" and not args.allow_external_bind:
        raise ValueError("external bind requires explicit allow-external-bind")
    password = os.environ.pop("RELEASE_FIXTURE_PASSWORD")
    token = os.environ.pop("RELEASE_FIXTURE_TOKEN")
    secret = os.environ.pop("RELEASE_FIXTURE_SECRET")
    pg_dsn = None
    if args.postgres_dsn_env:
        if args.postgres_dsn_env != "RELEASE_ACCEPTANCE_PG_DSN":
            raise ValueError("only the dedicated explicit PG environment variable is supported")
        pg_dsn = os.environ.pop(args.postgres_dsn_env)
    for key in list(os.environ):
        if key.startswith(("PORTAL_", "LANGSMITH_", "LANGCHAIN_", "LANGGRAPH_")) or key == "DJANGO_SETTINGS_MODULE":
            del os.environ[key]
    import sys
    sys.path.insert(0, str(ROOT / "backend"))
    from django.conf import settings
    # Import settings only after removing inherited business configuration.
    os.environ.update(PORTAL_SECRET_KEY=secret, PORTAL_DEBUG="1", PORTAL_HTTPS="0",
        PORTAL_SQLITE_PATH=str(run_dir / "fixture.sqlite3"),
        PORTAL_ALLOWED_HOSTS="127.0.0.1,localhost," + args.host,
        PORTAL_HR_STORAGE_ROOT=str(run_dir / "hr"), PORTAL_PRODUCT_STORAGE_ROOT=str(run_dir / "product"))
    from config import settings as base
    configuration = {key: getattr(base, key) for key in dir(base) if key.isupper()}
    if pg_dsn:
        import psycopg
        from config.database import postgres_database
        from psycopg.conninfo import conninfo_to_dict
        conn = conninfo_to_dict(pg_dsn)
        if conn.get("dbname") != "release_acceptance_" + fixture_id:
            raise ValueError("PG database must have this fixture UUID name")
        with psycopg.connect(pg_dsn) as connection:
            count = connection.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]
            if count:
                raise ValueError("refusing to migrate a nonempty PG database")
        configuration["DATABASES"] = {"default": postgres_database(name=conn["dbname"],
            user=conn.get("user", ""), password=conn.get("password", ""),
            host=conn.get("host", ""), port=conn.get("port", "5432"))}
    else:
        database = run_dir / "fixture.sqlite3"
        if database.exists():
            raise ValueError("refusing existing fixture database")
        configuration["DATABASES"]["default"]["OPTIONS"] = {"timeout": 20, "transaction_mode": "IMMEDIATE"}
    configuration.update(AGENT_PLATFORM_ENABLED=False, AGENT_RUNTIME_URL="", AGENT_RUNTIME_SERVICE_TOKEN="")
    settings.configure(**configuration)
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connections
    from portal.models import User, Role
    from portal.business_models import BusinessLedgerGrant, BusinessLedgerWorkbook
    with io.StringIO() as sink:
        call_command("migrate", interactive=False, stdout=sink, stderr=sink)
        call_command("seed_portal", stdout=sink, stderr=sink)
    for index in range(args.users):
        user = User.objects.create_user(username=f"release-user-{index:04d}", password=password,
            department_code="finance", must_change_password=False)
        user.roles.set(Role.objects.filter(code="finance"))
        for department in ("finance", "presales"):
            BusinessLedgerGrant.objects.create(user=user, department=department, can_edit=True)
    denied = User.objects.create_user(username="release-denied", password=password,
        department_code="hr", must_change_password=False)
    denied.roles.set(Role.objects.filter(code="hr"))
    actor = User.objects.get(username="release-user-0000")
    for department in ("finance", "presales"):
        rows = [{"project_id": f"SYN-{index:05d}", "project_name": f"Synthetic fixture {index}",
            **({"contract_amount": "100.00", "received_amount": "20.00", "due_date": ""}
               if department == "finance" else {"status": "报价", "owner": "synthetic", "amount": "100.00"})}
            for index in range(args.rows)]
        BusinessLedgerWorkbook.objects.create(department=department, records=rows,
            created_by=actor, updated_by=actor, as_of="2026-01-01")
    connections.close_all()
    from config.database import postgres_pool_evidence
    database_pool = postgres_pool_evidence(connections["default"])
    logging.getLogger("django.request").disabled = True
    logging.getLogger("waitress").setLevel(logging.CRITICAL)
    from django.core.wsgi import get_wsgi_application
    application = get_wsgi_application()
    slots = threading.BoundedSemaphore(args.admission_limit)
    lock = threading.Lock()
    counters = {"active": 0, "max_active": 0, "admission_rejections": 0}
    identity = {"fixture_id": fixture_id, "synthetic": True, "database_kind": "postgresql" if pg_dsn else "sqlite",
                "users": args.users, "rows": args.rows, "agent_mode": "disabled", "model_mode": "no_model_calls",
                "synthetic_admission_limit": args.admission_limit, "server_threads": args.threads,
                "session_cookie_age": settings.SESSION_COOKIE_AGE, "database_pool": database_pool}

    def reply(start_response, status, body):
        data = json.dumps(body).encode()
        start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(data))),
                                ("Cache-Control", "no-store")])
        return [data]

    def wrapped(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith("/__release__/") and environ.get("HTTP_X_RELEASE_TOKEN") != token:
            return reply(start_response, "403 Forbidden", {"code": "fixture_forbidden"})
        if path == "/__release__/identity":
            return reply(start_response, "200 OK", identity)
        if path == "/__release__/shutdown":
            # Only this explicitly authenticated QA server has a shutdown route.
            def stop():
                time.sleep(.15)
                server.close()
                os._exit(0)
            threading.Thread(target=stop, daemon=True).start()
            return reply(start_response, "200 OK", {"status": "owned_shutdown_scheduled"})
        # Synthetic admission faults belong only to the explicit probe. Normal
        # routes use Waitress's actual worker queue and database limits.
        synthetic_hold = path == "/__release__/hold"
        if synthetic_hold and not slots.acquire(blocking=False):
            with lock:
                counters["admission_rejections"] += 1
            return reply(start_response, "429 Too Many Requests", {"code": "synthetic_overload", "retryable": True})
        with lock:
            counters["active"] += 1
            counters["max_active"] = max(counters["max_active"], counters["active"])
        try:
            if path == "/__release__/hold":
                time.sleep(0.25)
                return reply(start_response, "200 OK", {"status": "synthetic_hold_complete"})
            return list(application(environ, start_response))
        finally:
            with lock:
                counters["active"] -= 1
            if synthetic_hold:
                slots.release()

    from waitress import create_server
    server = create_server(wrapped, **waitress_parameters(args))
    identity.update(server_connection_limit=server.adj.connection_limit,
                    server_use_poll=server.adj.asyncore_use_poll)
    ready = {**identity, "port": int(server.effective_port), "owned_pid": os.getpid(), "parent_pid": os.getppid()}
    with (run_dir / "ready.json").open("x", encoding="utf-8") as file:
        json.dump(ready, file)

    def sample():
        with (run_dir / "resources.jsonl").open("x", encoding="utf-8") as file:
            while True:
                with lock:
                    value = dict(counters)
                dispatcher = server.task_dispatcher
                with dispatcher.lock:
                    value.update(waitress_pending_tasks=len(dispatcher.queue),
                        waitress_active_threads=dispatcher.active_count)
                value.update(waitress_socket_map_size=len(server._map),
                    waitress_connection_limit=server.adj.connection_limit,
                    waitress_use_poll=int(server.adj.asyncore_use_poll))
                value.update(monotonic=time.monotonic(), wall_time=time.time(), cpu_seconds=time.process_time(),
                    process_threads=threading.active_count(), **process_memory())
                if pg_dsn:
                    # The pool is shared by the alias across threads. Read only
                    # public counters; do not log connection parameters.
                    pool_stats = connections["default"].pool.get_stats()
                    from config.database import POOL_STATS
                    value.update({"postgres_" + key: pool_stats[key] for key in POOL_STATS
                                  if type(pool_stats.get(key)) in (int, float)})
                # Capacity scan belongs to a separate owned process, never the Web GIL.
                try:
                    disk = json.loads((run_dir / "disk-latest.json").read_text(encoding="utf-8"))
                    value.update(fixture_disk_bytes=disk["fixture_disk_bytes"],
                        disk_measurement_wall_time=disk["measurement_wall_time"],
                        disk_scan_duration_seconds=disk["scan_duration_seconds"], disk_files=disk["files"],
                        disk_monitor_ok=disk["ok"])
                except (OSError, ValueError, KeyError, TypeError):
                    value["disk_monitor_ok"] = 0
                file.write(json.dumps(value) + "\n")
                file.flush()
                time.sleep(1)
    threading.Thread(target=sample, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
