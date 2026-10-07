"""Normal config.wsgi stays alive across the controlled PostgreSQL outage."""
import argparse
import faulthandler
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time

from .contracts import require


def diagnostic_path(method, path):
    if method not in ("GET", "POST", "PATCH") or not isinstance(path, str):
        return None
    paths = {"/api/me/": "session", "/api/csrf/": "csrf", "/api/login/": "login",
             "/api/hr/recruitment/requests/": "business_create"}
    if path in paths:
        return path, paths[path]
    if re.fullmatch(r"/api/hr/recruitment/requests/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/", path):
        return "/api/hr/recruitment/requests/<synthetic-id>/", "business_detail"
    return None


class ObservedIterable:
    """Pass bytes/errors through and delegate WSGI close exactly once."""
    def __init__(self, original, observer, record):
        self.original, self.observer, self.record = original, observer, record
        self.closed = False
    def __iter__(self):
        try:
            yield from self.original
        except BaseException as error:
            self.observer.end(self.record, type(error).__name__)
            raise
    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if hasattr(self.original, "close"):
                self.original.close()
        except BaseException as error:
            self.observer.end(self.record, type(error).__name__)
            raise
        finally:
            self.observer.end(self.record)


class Observer:
    """Bounded local metadata/stack evidence; no request inputs or DB checkout."""
    MAX_EVENTS = 1024*1024
    MAX_STACK = 16*1024*1024
    STACK_RESERVE = 8*1024*1024  # CPython dump caps 100 threads/frames and truncates strings
    MAX_DUMPS = 16
    POOL_KEYS = ("pool_min", "pool_max", "pool_size", "pool_available", "requests_waiting", "requests_num",
                 "requests_queued", "requests_errors", "requests_wait_ms", "usage_ms", "connections_num",
                 "connections_errors", "connections_ms")
    def __init__(self, directory, pool, *, clock=time.monotonic, dump=faulthandler.dump_traceback):
        self.directory, self.pool, self.clock, self.dump = Path(directory), pool, clock, dump
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.active, self.errors = {}, []
        self.started = clock()
        self.samples = self.dumps = self.begins = self.ends = 0
        self.events = (self.directory / "web-observer.jsonl").open("xb")
        self.stacks = (self.directory / "web-stacks.log").open("xb", buffering=0)
        self.thread = threading.Thread(target=self.run, name="qa-pg-web-observer", daemon=True)
    def error(self, code):
        with self.lock:
            if code not in self.errors and len(self.errors) < 16:
                self.errors.append(code)
    def emit(self, item):
        encoded = (json.dumps({"wall_time": time.time(), "web_elapsed_seconds": self.clock()-self.started, **item}, allow_nan=False)+"\n").encode()
        with self.lock:
            if self.events.tell()+len(encoded) > self.MAX_EVENTS:
                if "event_file_budget_exceeded" not in self.errors:
                    self.errors.append("event_file_budget_exceeded")
                return
            self.events.write(encoded)
            self.events.flush()
    def begin(self, method, path):
        safe = diagnostic_path(method, path)
        if safe is None:
            return None
        record = {"method": method, "path": safe[0], "label": safe[1], "started": self.clock(),
                  "thread": threading.get_ident(), "dumped": False, "ended": False}
        with self.lock:
            self.active[record["thread"]] = record
            self.begins += 1
        try:
            self.emit({"event": "http_begin", **{key: record[key] for key in ("method", "path", "label")}, "elapsed_seconds": 0})
        except Exception as error:
            self.error(type(error).__name__)
        return record
    def end(self, record, error_class=None):
        if record is None:
            return
        with self.lock:
            if record["ended"]:
                return
            record["ended"] = True
            self.active.pop(record["thread"], None)
            self.ends += 1
        try:
            self.emit({"event": "http_end", **{key: record[key] for key in ("method", "path", "label")},
                       "elapsed_seconds": self.clock()-record["started"], "error_class": error_class})
        except Exception as error:
            self.error(type(error).__name__)
    def wrap(self, application):
        def observed(environ, start_response):
            record = self.begin(environ.get("REQUEST_METHOD", ""), environ.get("PATH_INFO", ""))
            try:
                result = application(environ, start_response)
            except BaseException as error:
                self.end(record, type(error).__name__)
                raise
            return ObservedIterable(result, self, record) if record is not None else result
        return observed
    def sample(self):
        # Hold the actual process pool object; get_stats() does not acquire a connection.
        stats = self.pool.get_stats()
        require(all(type(stats.get(key)) is int and stats[key] >= 0 for key in
                    ("pool_size", "pool_available", "requests_waiting")), "observer_numeric_pool_stats_missing")
        numeric = {key: stats[key] for key in self.POOL_KEYS if type(stats.get(key)) in (int, float) and math.isfinite(stats[key])}
        self.emit({"event": "pool", "stats": numeric})
        self.samples += 1
        with self.lock:
            due = [record for record in self.active.values() if not record["dumped"] and self.clock()-record["started"] >= 8]
            for record in due:
                record["dumped"] = True
        for record in due:
            if self.dumps >= self.MAX_DUMPS or self.stacks.tell()+self.STACK_RESERVE > self.MAX_STACK:
                self.error("stack_file_or_dump_budget_exceeded")
                continue
            self.emit({"event": "http_stack", **{key: record[key] for key in ("method", "path", "label")},
                       "elapsed_seconds": self.clock()-record["started"]})
            # No locals/values: CPython emits only thread IDs and code file/line/function.
            self.dump(file=self.stacks, all_threads=True)
            self.dumps += 1
            if self.stacks.tell() > self.MAX_STACK:
                self.error("stack_file_budget_exceeded")
        self.persist_summary()
    def persist_summary(self):
        with self.lock:
            summary = {"samples": self.samples, "dumps": self.dumps, "http_begins": self.begins, "http_ends": self.ends,
                "active_requests": len(self.active), "errors": list(self.errors), "sample_seconds": 2,
                "updated_wall_time": time.time(),
                "stack_after_seconds": 8, "event_bytes": self.events.tell(), "stack_bytes": self.stacks.tell(),
                "maximum_event_bytes": self.MAX_EVENTS, "maximum_stack_bytes": self.MAX_STACK, "maximum_dumps": self.MAX_DUMPS}
        temp = self.directory / "web-observer-summary.tmp"
        temp.write_text(json.dumps(summary), encoding="utf-8")
        os.replace(temp, self.directory / "web-observer-summary.json")
    def run(self):
        while not self.stop.is_set():
            try:
                self.sample()
            except Exception as error:
                self.error(type(error).__name__)
                try:
                    self.persist_summary()
                except Exception:
                    pass  # final coordinator also rejects missing/stale summary
            self.stop.wait(2)
    def close(self):
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join(3)
        require(not self.thread.is_alive(), "web_observer_thread_not_stopped")
        self.events.close()
        self.stacks.close()

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    from .contracts import gate_valid, require, write_json
    line = sys.stdin.readline(65537)
    require(len(line) <= 65536 and line.endswith("\n"), "bounded_stdin_gate_required")
    gate = gate_valid(json.loads(line), args.run_dir, "web")
    from qa.browser_acceptance.process_job import join_owned_job
    join_owned_job(gate["job_name"])
    require(os.environ.get("DJANGO_SETTINGS_MODULE") == "config.settings"
            and os.environ.get("PORTAL_DB_NAME", "").startswith("portal_pg_"), "normal_owned_pg_environment_required")
    from config.wsgi import application
    from django.conf import settings
    from config.database import postgres_pool_evidence
    from django.db import connections
    from waitress.server import create_server
    from qa.release_pipeline.identity import process_identity
    require(settings.ROOT_URLCONF == "config.urls", "normal_urlconf_required")
    connections["default"].ensure_connection()
    connections.close_all()  # return startup checkout; keep the normal process pool open
    from .pool import wait_returned_pool
    pool, pool_wait = wait_returned_pool(connections["default"], postgres_pool_evidence)
    observer = Observer(Path(args.run_dir), connections["default"].pool)
    observer.sample()
    server = create_server(observer.wrap(application), host="127.0.0.1", port=0, threads=4, connection_limit=512, asyncore_use_poll=False)
    observer.thread.start()
    write_json(Path(args.run_dir) / "web-ready.json", {"fixture_id": gate["fixture_id"], "job_name": gate["job_name"],
        "identity": process_identity(os.getpid()), "port": int(server.effective_port), "postgres_pool": pool,
        "pool_startup_wait": pool_wait,
        "diagnostics": {"events": "web-observer.jsonl", "stacks": "web-stacks.log", "summary": "web-observer-summary.json",
                        "pool_sampling_seconds": 2, "stack_active_seconds": 8, "no_checkout_or_request_input_logging": True},
        "wsgi": "config.wsgi.application", "threads": 4, "connection_limit": 512, "windows_select": True})
    try:
        server.run()
    finally:
        server.close()
        observer.close()
        connections.close_all()

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"result": "FAIL", "error_class": type(error).__name__}), flush=True)
        raise SystemExit(1)
