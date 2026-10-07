"""Owned loopback PostgreSQL for Portal tests; never loads a business environment.

Reusable API: with PortablePostgres(bin_path, database_name=...) as pg:
    # pg.env is a clean child-process environment; credentials stay in memory.
    pg.migrate()  # optional: the context initially creates an EMPTY database
    pg.run([...], name="http-check")
Every context retains its own UUID evidence/cluster files and verifies shutdown.
Windows PostgreSQL is local evidence, not Linux cloud or Native Runtime proof.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from qa.agent_platform.verify_portal_pg import (
    ROOT_GUARD_TESTS, parse_test_count, parse_test_summary, tests_for_phase,
)

PROVIDED_ARCHIVE_SHA256 = "4b8db0930c38f6ef845db919551dedda3b6b845aeb0927b3d79a6e8e9e4537cf"
SYSTEM_ENVIRONMENT = {
    "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
    "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMDATA", "ALLUSERSPROFILE", "HOMEDRIVE", "HOMEPATH", "OS",
    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def redact(value, sensitive):
    text = str(value)
    for secret in sorted(filter(None, sensitive), key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return text


def database_name_valid(name):
    return bool(re.fullmatch(r"(?:portal_pg|release_acceptance)_[a-f0-9]{32}", name))


def runtime_root_path(path=None):
    workspace = ROOT.resolve()
    runtime = (ROOT / ".runtime").resolve()
    target = Path(path).resolve() if path is not None else runtime / "portable-postgres"
    if not runtime.is_relative_to(workspace) or not target.is_relative_to(runtime):
        raise ValueError("runtime_root must resolve inside this workspace's .runtime")
    return target


def selected_python(value, label):
    path = Path(value or sys.executable).resolve()
    if not path.is_file():
        raise ValueError(f"{label} Python executable missing: {path}")
    return str(path)


def clean_environment(run_dir, *, database, username, password, port, secret, inherited=None,
                      parser_python=None, document_python=None):
    inherited = os.environ if inherited is None else inherited
    result = {key: value for key, value in inherited.items() if key.upper() in SYSTEM_ENVIRONMENT}
    result.update({
        "PYTHONPATH": os.pathsep.join(map(str, (ROOT / "backend", ROOT / "tests", ROOT))),
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
        "DJANGO_SETTINGS_MODULE": "config.settings", "PORTAL_SECRET_KEY": secret,
        "PORTAL_DEBUG": "1", "PORTAL_HTTPS": "0",
        "PORTAL_ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
        "PORTAL_DB_NAME": database, "PORTAL_DB_USER": username,
        "PORTAL_DB_PASSWORD": password, "PORTAL_DB_HOST": "127.0.0.1",
        "PORTAL_DB_PORT": str(port), "PORTAL_BEHIND_PROXY": "0",
        "PORTAL_PRODUCT_STORAGE_ROOT": str(run_dir / "storage" / "product"),
        "PORTAL_HR_STORAGE_ROOT": str(run_dir / "storage" / "hr"),
        "PORTAL_TENDER_STORAGE_ROOT": str(run_dir / "storage" / "tender"),
        "PORTAL_ENGINEERING_STORAGE_ROOT": str(run_dir / "storage" / "engineering"),
        "PORTAL_FRONTEND_DIST": str(run_dir / "empty-frontend"),
        "PORTAL_PRODUCT_PARSER_PYTHON": selected_python(parser_python, "parser"),
        "PORTAL_PRODUCT_DOCUMENT_PYTHON": selected_python(document_python, "document"),
        "PORTAL_AGENT_ENABLED": "0", "PORTAL_AGENT_RUNTIME_URL": "",
        "PORTAL_AGENT_RUNTIME_SERVICE_TOKEN": "", "PORTAL_AGENT_RUNTIME_MODEL_PRESETS": "{}",
        "PORTAL_MODEL_GATEWAY_URL": "", "PORTAL_MODEL_GATEWAY_TOKEN": "",
        "PORTAL_MODEL_PROVIDER_LOCAL_HTTP": "0",
        "PORTAL_MODEL_GATEWAY_ALLOWED_URLS": "http://127.0.0.1:18410,http://model-gateway:18410",
        "PORTAL_BUSINESS_SUMMARY_URL": "", "PORTAL_INTEGRATION_SECRET": "",
        "PORTAL_PRODUCT_P1_ENABLED": "0", "PORTAL_PRODUCT_MODEL_CALLS_ALLOWED": "0",
        "PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED": "0", "PORTAL_PRODUCT_RETRIEVAL_ENABLED": "0",
        "PORTAL_PRODUCT_RETRIEVAL_URL": "", "PORTAL_PRODUCT_RETRIEVAL_ALLOWED_URLS": "",
        "PORTAL_PRODUCT_RETRIEVAL_AUTHORIZATIONS": "{}",
        "PORTAL_PRODUCT_KNOWLEDGE_ENABLED": "0", "PORTAL_PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED": "0",
        "PORTAL_PRODUCT_KNOWLEDGE_URL": "", "PORTAL_PRODUCT_KNOWLEDGE_ALLOWED_URLS": "",
        "PORTAL_PRODUCT_KNOWLEDGE_AUTHORIZATIONS": "{}", "PORTAL_PRODUCT_OFFICE_RENDER_ENABLED": "0",
        "PORTAL_TENDER_INGESTION_ENABLED": "0", "PORTAL_TENDER_MANUAL_REFRESH_ENABLED": "0",
        "PORTAL_TENDER_SCHEDULE_ENABLED": "0", "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false", "LANGGRAPH_METRICS_ENABLED": "false",
        "LANGGRAPH_LOGS_ENABLED": "false",
    })
    return result


def source_files(directory, suffix=None):
    """Prune installed dependencies; retain frozen assets, including built bundles."""
    from validation.private_path_safety import checked_path
    directory = Path(directory)
    if not directory.exists():
        return
    checked_path(directory, root=ROOT, must_exist=True)
    excluded = {"node_modules", "__pycache__", ".runtime", ".venv", "venv", ".git",
                ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
                "private", "storage", "uploads", "staticfiles"}
    for current, directories, names in os.walk(directory, followlinks=False):
        directories[:] = sorted(name for name in directories if name.lower() not in excluded
                                and not name.lower().endswith("-private")
                                and not name.lower().startswith(".env"))
        for name in directories:
            checked_path(Path(current) / name, root=ROOT, must_exist=True)
        for name in sorted(names):
            path = Path(current) / name
            lowered = name.lower()
            if lowered.startswith(".env") or path.suffix.lower() in (".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".dump", ".backup") \
                    or any(marker in lowered for marker in (".db-", ".sqlite-", ".sqlite3-")):
                continue
            if path.is_file() and (suffix is None or path.suffix == suffix):
                checked_path(path, root=ROOT, must_exist=True)
                yield path


def deployment_non_python_files():
    """Bind public templates/static/test inputs without traversing private roots."""
    known = (("backend/templates", {".html"}),
             ("backend/portal/templates", {".html"}),
             ("backend/portal/static", {".css", ".js"}),
             ("backend/portal/tests/fixtures", {".html", ".json", ".docx"}))
    private = {"node_modules", "__pycache__", ".runtime", ".venv", "venv", ".git",
               ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
               "private", "storage", "uploads", "staticfiles"}
    from validation.private_path_safety import checked_path
    for relative, suffixes in known:
        directory = ROOT / relative
        checked_path(directory, root=ROOT, must_exist=True)
        for current, directories, names in os.walk(directory, followlinks=False):
            directories[:] = sorted(name for name in directories
                                    if name.lower() not in private
                                    and not name.lower().endswith("-private")
                                    and not name.lower().startswith(".env"))
            for name in directories:
                checked_path(Path(current) / name, root=ROOT, must_exist=True)
            for name in sorted(names):
                path = Path(current) / name
                if name.lower().startswith(".env") or path.suffix.lower() not in suffixes:
                    continue
                checked_path(path, root=ROOT, must_exist=True)
                if path.is_file():
                    yield path


def source_snapshot():
    from validation.private_path_safety import checked_path
    files = {}
    for directory in ("backend", "model_gateway", "tests", "validation"):
        for path in source_files(ROOT / directory, ".py"):
            files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("uv.lock", "pyproject.toml", "langgraph.json", "qa/run_portable_postgres.py",
                 "qa/agent_platform/verify_portal_pg.py", "qa/verify_product_deliverables.py",
                 "qa/owned_postgres_process.py", "qa/browser_acceptance/process_job.py",
                 "qa/release_pipeline/identity.py", "qa/postgres_fault_acceptance/postgres.py",
                 "qa/test_portable_postgres.py", "qa/test_owned_postgres_process.py",
                 "qa/test_portable_postgres_parent_death.py",
                 "Dockerfile", ".dockerignore", "compose.yaml", "frontend/package.json",
                 "frontend/pnpm-lock.yaml", "backend/portal/source_parsers/requirements.txt",
                 "backend/portal/source_parsers/requirements.in"):
        path = checked_path(ROOT / name, root=ROOT, must_exist=True)
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    # Backend tests read legacy backup and deployment inputs. Bind those inputs,
    # rather than certifying only the Python test that happened to inspect them.
    for path in source_files(ROOT / "deploy"):
        files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    # The frozen document pack includes non-Python files used by domain tests.
    for path in source_files(ROOT / "backend/portal/product_assets"):
        files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in deployment_non_python_files():
        files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    encoded = json.dumps(files, sort_keys=True).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "files": files}


def root_guard_results(output):
    return {name: bool(re.search(
        rf"(?m)^{re.escape(name)} \(portal\.tests\.test_agent_postgres_root_guard\.[^\n]+\) \.\.\. ok\s*$",
        output,
    )) for name in ROOT_GUARD_TESTS}


def hidden_process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


class PortablePostgres:
    def __init__(self, postgres_bin, *, database_name=None, runtime_root=None,
                 parser_python=None, document_python=None):
        self.token = uuid.uuid4().hex
        self.run_dir = runtime_root_path(runtime_root) / self.token
        self.cluster = self.run_dir / "cluster"
        self.bin = Path(postgres_bin).resolve()
        self.parser_python = selected_python(parser_python, "parser")
        self.document_python = selected_python(document_python, "document")
        self.database_name = database_name or f"portal_pg_{self.token}"
        if not database_name_valid(self.database_name):
            raise ValueError("database name must have an isolated UUID test prefix")
        self.username = f"portal_{self.token}"
        self._password = secrets.token_urlsafe(32)
        self._secret = secrets.token_urlsafe(48)
        self._sensitive = [self._password, self._secret]
        self.port = None
        self.env = None
        self.process = None
        self._server_log = None
        self._reservation = None
        self._entered = False
        self.evidence = {
            "schema_version": 1, "round_uuid": str(uuid.UUID(self.token)), "started_at": now(),
            "run_dir": str(self.run_dir), "outcome": "RUNNING", "commands": [],
            "runtime": {"python": sys.version, "platform": sys.platform,
                        "parser_python": self.parser_python, "document_python": self.document_python,
                        "settings_module": "config.settings", "urlconf": "config.urls",
                        "password_hasher_shortcuts": False},
            "isolation": {"fresh_cluster": True, "loopback_only": True,
                          "synthetic_credentials_only": True, "business_env_loaded": False,
                          "external_features_disabled": True, "network_namespace_isolation": False},
            "postgres": {"bin": str(self.bin), "version": None,
                         "provided_edb_archive_sha256": PROVIDED_ARCHIVE_SHA256,
                         "host_authentication": "scram-sha-256"},
            "unverified": ["Linux cloud deployment/capacity", "Native LangGraph PostgreSQL persistence",
                           "real model/RAGFlow/business quality", "production migration/recovery"],
            "cleanup": {},
        }

    def executable(self, name):
        path = self.bin / (name + (".exe" if os.name == "nt" else ""))
        if not path.is_file():
            raise ValueError(f"PostgreSQL executable missing: {path}")
        return str(path)

    def save_evidence(self):
        if self.run_dir.is_dir():
            text = redact(json.dumps(self.evidence, indent=2, ensure_ascii=False), self._sensitive)
            (self.run_dir / "report.json").write_text(text + "\n", encoding="utf-8")

    def run(self, argv, *, name, timeout=120, env=None, check=True):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise ValueError("command log name must be a safe basename")
        log = self.run_dir / f"{len(self.evidence['commands']):02d}-{name}.log"
        command = {"argv": [redact(arg, self._sensitive) for arg in argv], "name": name,
                   "exit_code": None, "log": str(log), "started_at": now()}
        self.evidence["commands"].append(command)
        self.save_evidence()
        try:
            result = subprocess.run(list(map(str, argv)), cwd=ROOT, env=env or self.env,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout, **hidden_process_options())
            output = result.stdout + result.stderr
            code = result.returncode
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.stdout or b"", error.stderr or b""
            output = "\n".join(item.decode("utf-8", errors="replace") if isinstance(item, bytes) else item
                               for item in (stdout, stderr)) + "\nRunner command timed out.\n"
            code = 124
            result = subprocess.CompletedProcess(argv, code, output, "")
        except OSError as error:
            output = f"Runner could not launch command: {type(error).__name__}: {error}\n"
            code = 126
            result = subprocess.CompletedProcess(argv, code, output, "")
        result.stdout = redact(result.stdout, self._sensitive)
        result.stderr = redact(result.stderr, self._sensitive)
        log.write_text(redact(output, self._sensitive), encoding="utf-8")
        command.update({"exit_code": code, "finished_at": now()})
        self.save_evidence()
        if check and code:
            raise RuntimeError(f"{name} failed with exit code {code}; see {log}")
        return result

    def _assert_owner(self, *, require_pid=False):
        if self.run_dir.resolve() != self.run_dir or self.cluster.resolve() != self.cluster:
            raise RuntimeError("cluster path ownership changed; no process will be stopped")
        from validation.private_path_safety import checked_path
        marker_path = checked_path(self.run_dir / "owner.json", root=self.run_dir, must_exist=True)
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if marker != {"token": self.token, "cluster": str(self.cluster)}:
            raise RuntimeError("cluster owner marker mismatch; no process will be stopped")
        pid_path = checked_path(self.cluster / "postmaster.pid", root=self.cluster, must_exist=False)
        if pid_path.exists():
            lines = pid_path.read_text(encoding="utf-8").splitlines()
            if (len(lines) < 4 or self.process is None or int(lines[0]) != self.process.pid
                    or Path(lines[1]).resolve() != self.cluster or int(lines[3]) != self.port):
                raise RuntimeError("postmaster PID/data directory/port mismatch; no process will be stopped")
        elif require_pid:
            raise RuntimeError("owned postmaster PID file is missing")
        from qa.owned_postgres_process import OwnedPostgresProcess
        if isinstance(self.process, OwnedPostgresProcess) and self.process.poll() is None:
            self.process.assert_live_owner()

    def _launch_owned_postgres(self, *, deadline):
        """Shared first-start/recovery factory; never assigns a running PG to a Job."""
        self._assert_owner()
        if os.name == "nt":
            from qa.owned_postgres_process import OwnedPostgresProcess
            if isinstance(self.process, OwnedPostgresProcess):
                if self.process.poll() is None or (self.cluster / "postmaster.pid").exists():
                    raise RuntimeError("prior owned PostgreSQL generation is not completely stopped")
                previous = self.process.close_tree(deadline=min(deadline, time.monotonic()+10), terminate=False)
                self.evidence.setdefault("retired_postgres_process_trees", []).append(previous)
                if not previous["verified"]:
                    raise RuntimeError("prior owned PostgreSQL process tree is not empty")
            process = OwnedPostgresProcess(executable=self.executable("postgres"), cluster=self.cluster,
                run_dir=self.run_dir, token=self.token, port=self.port, env=self.env,
                log=self._server_log, deadline=deadline)
            self.evidence.setdefault("postgres_process_generations", []).append({
                "identity": process.identity, "job_name": process.job.name,
                "launcher_pid": process.launcher.pid, "joined_before_postgres_spawn": True,
                "cluster": str(self.cluster), "port": self.port})
            return process
        return subprocess.Popen([self.executable("postgres"), "-D", str(self.cluster)],
            cwd=ROOT, env=self.env, stdout=self._server_log, stderr=subprocess.STDOUT,
            **hidden_process_options())

    def __enter__(self):
        if self._entered:
            raise RuntimeError("PortablePostgres contexts cannot be reused")
        self._entered = True
        for name in ("initdb", "postgres", "pg_ctl"):
            self.executable(name)
        self.run_dir.mkdir(parents=True, exist_ok=False)
        (self.run_dir / "owner.json").write_text(json.dumps({"token": self.token, "cluster": str(self.cluster)}), encoding="utf-8")
        for name in ("product", "hr", "tender", "engineering"):
            (self.run_dir / "storage" / name).mkdir(parents=True)
        (self.run_dir / "empty-frontend").mkdir()
        try:
            self._reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._reservation.bind(("127.0.0.1", 0))
            self.port = self._reservation.getsockname()[1]
            self.env = clean_environment(self.run_dir, database=self.database_name, username=self.username,
                                         password=self._password, port=self.port, secret=self._secret,
                                         parser_python=self.parser_python, document_python=self.document_python)
            self.evidence["postgres"].update({"port": self.port, "cluster": str(self.cluster),
                                               "database": self.database_name})
            self.evidence["source_before"] = source_snapshot()
            version = self.run([self.executable("postgres"), "--version"], name="postgres-version")
            self.evidence["postgres"]["binary_version"] = version.stdout.strip()
            password_file = self.run_dir / "init-password"
            password_file.write_text(self._password, encoding="utf-8")
            password_file.chmod(0o600)
            try:
                self.run([self.executable("initdb"), "-D", self.cluster, "--username", self.username,
                          "--pwfile", password_file, "--auth-host=scram-sha-256", "--auth-local=scram-sha-256",
                          "--encoding=UTF8", "--locale=C"], name="initdb")
            finally:
                password_file.unlink(missing_ok=True)
            with (self.cluster / "postgresql.conf").open("a", encoding="utf-8") as stream:
                stream.write(f"\nlisten_addresses = '127.0.0.1'\nport = {self.port}\n"
                             "unix_socket_directories = ''\npassword_encryption = 'scram-sha-256'\n"
                             "logging_collector = off\nmax_connections = 80\n")
            (self.cluster / "pg_hba.conf").write_text(
                "host all all 127.0.0.1/32 scram-sha-256\n"
                "host all all 0.0.0.0/0 reject\nhost all all ::/0 reject\n", encoding="utf-8")
            self._server_log = (self.run_dir / "postgres-server.log").open("wb")
            self._reservation.close()
            self._reservation = None
            deadline = time.monotonic() + 40
            self.process = self._launch_owned_postgres(deadline=deadline)
            self.evidence["postgres"]["pid"] = self.process.pid
            self.save_evidence()
            import psycopg
            while True:
                if self.process.poll() is not None:
                    raise RuntimeError("owned PostgreSQL exited during startup; see postgres-server.log")
                try:
                    with self.connect() as connection:
                        row = connection.execute("SELECT version(), current_setting('data_directory')").fetchone()
                        if Path(row[1]).resolve() != self.cluster:
                            raise RuntimeError("connected PostgreSQL is not this context's cluster")
                        self.evidence["postgres"]["version"] = row[0]
                    self._assert_owner(require_pid=True)
                    break
                except psycopg.OperationalError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("owned PostgreSQL did not become ready") from None
                    time.sleep(0.2)
            self.create_database(self.database_name)
            self.evidence["context_ready"] = True
            self.save_evidence()
            return self
        except BaseException as error:
            self.evidence["outcome"] = "FAIL"
            self.evidence["error"] = redact(f"{type(error).__name__}: {error}", self._sensitive)
            self.close()
            raise

    def connect(self, database="postgres"):
        import psycopg
        return psycopg.connect(host="127.0.0.1", port=self.port, user=self.username,
                               password=self._password, dbname=database, connect_timeout=1, autocommit=True)

    def create_database(self, name):
        if not database_name_valid(name):
            raise ValueError("refusing non-isolated database name")
        self._assert_owner(require_pid=True)
        from psycopg import sql
        with self.connect() as connection:
            connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
        self.evidence.setdefault("created_databases", []).append(name)
        self.save_evidence()

    def migrate(self):
        manage = ROOT / "backend/manage.py"
        inspection = self.run([sys.executable, "-c",
            "import django,json; django.setup(); from django.conf import settings; "
            "from django.contrib.auth.hashers import make_password; "
            "assert settings.DATABASES['default']['ENGINE']=='django.db.backends.postgresql'; "
            "assert settings.ROOT_URLCONF=='config.urls'; "
            "assert make_password('synthetic-runner-check').startswith('pbkdf2_sha256$'); "
            "print(json.dumps({'database_engine':settings.DATABASES['default']['ENGINE'],"
            "'urlconf':settings.ROOT_URLCONF,'password_hashers':settings.PASSWORD_HASHERS}))"],
            name="normal-settings-proof")
        self.evidence["runtime"]["normal_settings_proof"] = json.loads(inspection.stdout.strip())
        self.run([sys.executable, manage, "check"], name="django-check")
        self.run([sys.executable, manage, "makemigrations", "--check", "--dry-run"], name="migration-drift")
        self.run([sys.executable, manage, "migrate", "--noinput"], name="migrate", timeout=300)
        self.evidence["normal_migrations_passed"] = True
        self.save_evidence()

    def close(self, *, ownership_error=None):
        from qa.owned_postgres_process import OwnedPostgresProcess
        cleanup = {"cluster_files_retained": True, "other_processes_touched": False}
        owned_tree = isinstance(self.process, OwnedPostgresProcess)
        fallback_deadline = None
        try:
            if self._reservation:
                self._reservation.close()
                self._reservation = None
            if ownership_error is not None:
                raise RuntimeError(ownership_error)
            self._assert_owner()
            if self.process is not None and self.process.poll() is None:
                if (self.cluster / "postmaster.pid").exists():
                    # pg_ctl only targets the verified UUID data directory and owned PID.
                    result = self.run([self.executable("pg_ctl"), "-D", self.cluster, "-m", "fast", "-w", "-t", "30", "stop"],
                                      name="postgres-stop", timeout=40, check=False)
                    cleanup["fast_stop_exit_code"] = result.returncode
                    cleanup["stop_mode"] = "fast" if result.returncode == 0 else "fast_failed"
                    fallback_deadline = time.monotonic() + 10
                    if result.returncode == 0:
                        try:
                            self.process.wait(timeout=max(.01, fallback_deadline-time.monotonic()))
                        except subprocess.TimeoutExpired:
                            cleanup["fast_stop_process_wait_timed_out"] = True
                if self.process.poll() is None:
                    # The original fallback budget is ten seconds TOTAL, including
                    # immediate-stop, tree accounting, and launcher exit observation.
                    fallback_deadline = fallback_deadline or time.monotonic() + 10
                    self._assert_owner()
                    expected = [self.executable("postgres"), "-D", str(self.cluster)]
                    if self.process.args != expected:
                        raise RuntimeError("owned launch arguments changed; refusing termination")
                    if (self.cluster / "postmaster.pid").exists():
                        self._assert_owner(require_pid=True)
                        result = self.run([self.executable("pg_ctl"), "-D", self.cluster,
                            "-m", "immediate", "-w", "-t", "3", "stop"],
                            name="postgres-immediate-stop", timeout=min(4, max(.01, fallback_deadline-time.monotonic())),
                            check=False)
                        cleanup["immediate_stop_exit_code"] = result.returncode
                        cleanup["stop_mode"] = "immediate_after_fast_failure"
                    if owned_tree:
                        cleanup["owned_process_tree"] = self.process.close_tree(deadline=fallback_deadline,
                            terminate=self.process.poll() is None)
                        if cleanup["owned_process_tree"]["termination_requested"]:
                            cleanup["stop_mode"] = "owned_job_termination"
                        self.process.wait(timeout=max(.01, fallback_deadline-time.monotonic()))
                    else:
                        # No verified tree is available on other platforms. Never
                        # pretend that terminating only its parent cleaned the tree.
                        if not (self.cluster / "postmaster.pid").exists():
                            raise RuntimeError("startup cleanup has no verified process tree or owned PID file")
                        self.process.wait(timeout=max(.01, fallback_deadline-time.monotonic()))
            if owned_tree and "owned_process_tree" not in cleanup:
                cleanup["owned_process_tree"] = self.process.close_tree(
                    deadline=fallback_deadline or time.monotonic()+10, terminate=False)
            cleanup["process_stopped"] = self.process is None or self.process.poll() is not None
            cleanup["pid_file_removed"] = not (self.cluster / "postmaster.pid").exists()
            cleanup["port_closed"] = self.port is None or not port_open(self.port)
            cleanup["verified"] = all(cleanup[key] for key in ("process_stopped", "pid_file_removed", "port_closed"))
            if owned_tree:
                cleanup["verified"] = cleanup["verified"] and cleanup["owned_process_tree"]["verified"]
        except BaseException as error:
            cleanup.update({"verified": False, "error": redact(str(error), self._sensitive)})
            fallback_deadline = fallback_deadline or time.monotonic()+10
        finally:
            if owned_tree and "owned_process_tree" not in cleanup:
                # The saved Job handle proves ONLY this context's child tree,
                # even if the marker/PID/ctime check failed. Never send pg_ctl
                # to that unverified cluster; preserve its original FAIL.
                try:
                    cleanup["owned_process_tree"] = self.process.close_tree(
                        deadline=fallback_deadline or time.monotonic()+10, terminate=True)
                    self.process.wait(timeout=max(.01, (fallback_deadline or time.monotonic()+10)-time.monotonic()))
                except BaseException as error:
                    cleanup["tree_cleanup_error"] = type(error).__name__
                cleanup["verified"] = False
            if self._server_log:
                try:
                    self._server_log.close()
                    path = self.run_dir / "postgres-server.log"
                    path.write_text(redact(path.read_bytes().decode("utf-8", errors="replace"), self._sensitive), encoding="utf-8")
                except OSError as error:
                    cleanup.update({"verified": False, "log_error": redact(str(error), self._sensitive)})
                finally:
                    self._server_log = None
            self.evidence["cleanup"] = cleanup
            self.evidence["finished_at"] = now()
            if not cleanup.get("verified"):
                self.evidence["outcome"] = "FAIL"
            self.save_evidence()
        return bool(cleanup.get("verified"))

    def __exit__(self, exc_type, exc, traceback):
        if exc:
            self.evidence["outcome"] = "FAIL"
            self.evidence["error"] = redact(f"{exc_type.__name__}: {exc}", self._sensitive)
        if not self.close() and exc is None:
            raise RuntimeError(f"owned PostgreSQL shutdown not verified; see {self.run_dir / 'report.json'}")
        return False


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", required=True, help="directory with initdb/postgres/pg_ctl executables")
    parser.add_argument("--phase", choices=("core", "all", "full"), default="core")
    parser.add_argument("--parser-python", help="explicit isolated rich-upload parser runtime")
    parser.add_argument("--document-python", help="explicit isolated document generation runtime")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    pg = PortablePostgres(args.postgres_bin, parser_python=args.parser_python, document_python=args.document_python)
    try:
        with pg:
            pg.evidence["phase"] = args.phase
            pg.evidence["packages"] = {name: importlib.metadata.version(name)
                                       for name in ("Django", "djangorestframework", "psycopg", "deepagents", "langgraph", "langgraph-sdk")}
            pg.evidence["installed_distributions"] = {
                item.metadata["Name"]: item.version for item in importlib.metadata.distributions()
                if item.metadata["Name"]
            }
            pg.migrate()
            labels = ("portal.tests",) if args.phase == "full" else tests_for_phase(args.phase)
            result = pg.run([sys.executable, ROOT / "backend/manage.py", "test", *labels,
                             "--noinput", "--verbosity", "2"], name="django-tests", timeout=7200, check=False)
            output = result.stdout + result.stderr
            summary = parse_test_summary(output)
            guards = root_guard_results(output)
            pg.evidence["test_run"] = {"labels": list(labels), "tests_run": parse_test_count(output),
                                       "exit_code": result.returncode, **summary}
            pg.evidence["postgres_root_guard"] = {"required": list(ROOT_GUARD_TESTS), "passed": guards}
            pg.evidence["source_after"] = source_snapshot()
            before, after = pg.evidence["source_before"]["files"], pg.evidence["source_after"]["files"]
            pg.evidence["source_changes_during_run"] = [name for name in sorted(before.keys() | after.keys())
                                                         if before.get(name) != after.get(name)]
            if (result.returncode or not summary["summary"] or summary["failures"] or summary["errors"]
                    or not pg.evidence["test_run"]["tests_run"]
                    or not all(guards.values())):
                raise RuntimeError("tests failed, summary missing, or required PostgreSQL root guards did not pass")
            # Keep source changes explicit: a moving checkout cannot represent one frozen candidate.
            if pg.evidence["source_changes_during_run"]:
                raise RuntimeError("source changed during test execution; rerun after the candidate is frozen")
            pg.evidence["outcome"] = "PASS_WITH_SKIPS" if summary["skipped"] else "PASS"
            pg.save_evidence()
    except BaseException as error:
        print(redact(f"FAIL: {type(error).__name__}: {error}", pg._sensitive), file=sys.stderr)
        print(f"Evidence: {pg.run_dir / 'report.json'}", file=sys.stderr)
        return 1
    print(f"{pg.evidence['outcome']}: {pg.evidence['test_run']['tests_run']} tests; "
          f"skipped={pg.evidence['test_run']['skipped']}; PostgreSQL root guards both passed; shutdown verified.")
    print(f"Evidence: {pg.run_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
