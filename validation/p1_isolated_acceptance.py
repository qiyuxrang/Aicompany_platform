import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__:
    from .private_path_safety import checked_path, regular_files
else:
    from private_path_safety import checked_path, regular_files

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
BASE_ENV_FILE = ROOT / ".runtime/validation.env"
ENV_FILE = ROOT / ".runtime/p1-validation.env"
CREDENTIALS_FILE = ROOT / ".runtime/p1-validation-credentials.json"
EVIDENCE_DIRECTORY = ROOT / "qa/execution-20260922"
SYSTEM_EVIDENCE = EVIDENCE_DIRECTORY / "p1-system.json"
BACKUP_EVIDENCE = EVIDENCE_DIRECTORY / "p1-backup-summary.json"
CONTAINER = "enterprise-portal-phase1-db"
SOURCE_NAME = re.compile(r"portal_p1_\d{8}_\d{6}\Z")
RESTORE_NAME = re.compile(r"portal_p1_restore_\d{8}_\d{6}\Z")
PRODUCT_TABLES = {
    "portal_documentapproval",
    "portal_documentartifact",
    "portal_documentattempt",
    "portal_documentrevision",
    "portal_documentsource",
    "portal_documenttask",
}


def read_environment(path):
    if not path.is_file():
        raise SystemExit(f"缺少运行时配置：{path.relative_to(ROOT)}")
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )


def write_environment(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    restrict_acl(path)


def sanitized_environment(extra=None):
    blocked = {
        "PORTAL_DB_PASSWORD",
        "PORTAL_SECRET_KEY",
        "PORTAL_INTEGRATION_SECRET",
        "PORTAL_MODEL_GATEWAY_TOKEN",
    }
    environment = {key: value for key, value in os.environ.items() if key not in blocked}
    if extra:
        environment.update(extra)
    return environment


def command(arguments, **kwargs):
    return subprocess.run(
        arguments,
        check=True,
        capture_output=not ("stdout" in kwargs or "stdin" in kwargs),
        env=sanitized_environment(),
        **kwargs,
    )


def restrict_acl(path, directory=False):
    if os.name == "nt":
        principal = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', getpass.getuser())}".strip("\\")
        permission = "(OI)(CI)(F)" if directory else "(F)"
        command(["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:{permission}"])
    else:
        path.chmod(0o700 if directory else 0o600)


def assert_database_name(name, *, restore=False):
    pattern = RESTORE_NAME if restore else SOURCE_NAME
    if not pattern.fullmatch(name or ""):
        raise SystemExit("数据库名不符合本次 P1 隔离白名单，拒绝操作。")


def connect(database, registered, *, readonly=False, autocommit=False):
    if database != "postgres":
        assert_database_name(database, restore=database.startswith("portal_p1_restore_"))
    options = "-c default_transaction_read_only=on" if readonly else None
    return psycopg.connect(
        dbname=database,
        user=registered["PORTAL_DB_USER"],
        password=registered["PORTAL_DB_PASSWORD"],
        host="127.0.0.1",
        port=55438,
        options=options,
        autocommit=autocommit,
    )


def registered_environment(*, require_process=True):
    registered = read_environment(ENV_FILE)
    assert_database_name(registered.get("PORTAL_DB_NAME", ""))
    expected = {
        "PORTAL_DB_HOST": "127.0.0.1",
        "PORTAL_DB_PORT": "55438",
        "PORTAL_DEBUG": "1",
        "PORTAL_HTTPS": "0",
        "PORTAL_PRODUCT_P1_ENABLED": "1",
        "PORTAL_PRODUCT_MODEL_CALLS_ALLOWED": "0",
        "PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED": "0",
        "PORTAL_MODEL_GATEWAY_URL": "",
        "PORTAL_MODEL_GATEWAY_TOKEN": "",
    }
    if any(registered.get(key) != value for key, value in expected.items()):
        raise SystemExit("P1 验收配置未满足本机隔离、模型替身或草稿门禁要求。")
    storage = Path(registered.get("PORTAL_PRODUCT_STORAGE_ROOT", "")).resolve()
    allowed_storage = (ROOT / ".runtime/p1-private" / registered["PORTAL_DB_NAME"]).resolve()
    if storage != allowed_storage:
        raise SystemExit("私有文件目录不符合本次隔离库白名单。")
    document_python = Path(registered.get("PORTAL_PRODUCT_DOCUMENT_PYTHON", "")).resolve()
    allowed_python = (ROOT / ".runtime/product-documents-python/Scripts/python.exe").resolve()
    if document_python != allowed_python or not document_python.is_file():
        raise SystemExit("文档运行时不符合本项目固定 Python 3.12 路径。")
    if require_process:
        guarded = {
            "PORTAL_DB_NAME",
            "PORTAL_DB_USER",
            "PORTAL_DB_PASSWORD",
            "PORTAL_DB_HOST",
            "PORTAL_DB_PORT",
            "PORTAL_SECRET_KEY",
            "PORTAL_PRODUCT_P1_ENABLED",
            "PORTAL_PRODUCT_MODEL_CALLS_ALLOWED",
            "PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED",
            "PORTAL_PRODUCT_REVIEWER_IDS",
            "PORTAL_PRODUCT_STORAGE_ROOT",
            "PORTAL_MODEL_GATEWAY_URL",
            "PORTAL_MODEL_GATEWAY_TOKEN",
            "PORTAL_PRODUCT_DOCUMENT_PYTHON",
        }
        if any(os.environ.get(key) != registered.get(key) for key in guarded):
            raise SystemExit("必须通过登记的 .runtime/p1-validation.env 运行。")
    return registered


def revoke_public_access(database, registered):
    with connect("postgres", registered, autocommit=True) as connection:
        connection.execute(sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        connection.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(database),
                sql.Literal("Synthetic isolated P1 acceptance data; retained for validation; PUBLIC access revoked."),
            )
        )


def restrict_database_objects(database, registered):
    with connect(database, registered) as connection:
        connection.execute("REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC")
        connection.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC")
        connection.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
        connection.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC")
        connection.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC")


def public_access_revoked(database, registered):
    with connect("postgres", registered, readonly=True) as connection:
        database_acl = connection.execute(
            """
            SELECT database.datacl IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM aclexplode(database.datacl) AS acl WHERE acl.grantee=0
            )
            FROM pg_database AS database WHERE database.datname=%s
            """,
            (database,),
        ).fetchone()[0]
    with connect(database, registered, readonly=True) as connection:
        schema_acl = connection.execute(
            """
            SELECT namespace.nspacl IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM aclexplode(namespace.nspacl) AS acl WHERE acl.grantee=0
            )
            FROM pg_namespace AS namespace WHERE namespace.nspname='public'
            """
        ).fetchone()[0]
        object_acl = connection.execute(
            """
            SELECT NOT EXISTS (
                SELECT 1 FROM pg_class AS object
                JOIN pg_namespace AS namespace ON namespace.oid=object.relnamespace,
                     LATERAL aclexplode(object.relacl) AS acl
                WHERE namespace.nspname='public' AND acl.grantee=0
            )
            """
        ).fetchone()[0]
    return bool(database_acl and schema_acl and object_acl)


def provision():
    if ENV_FILE.exists() or CREDENTIALS_FILE.exists():
        raise SystemExit("P1 隔离配置或凭据已存在，不覆盖、不重建数据库。")
    original = read_environment(BASE_ENV_FILE)
    required = ("PORTAL_DB_USER", "PORTAL_DB_PASSWORD")
    if (
        original.get("PORTAL_DB_HOST") != "127.0.0.1"
        or original.get("PORTAL_DB_PORT") != "55438"
        or original.get("PORTAL_DB_NAME") != "portal_phase1"
        or original.get("PORTAL_DEBUG") != "1"
        or any(not original.get(key) for key in required)
    ):
        raise SystemExit("仅允许从 127.0.0.1:55438 的既有 portal_phase1 验证连接派生新库。")
    database = "portal_p1_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    assert_database_name(database)
    with psycopg.connect(
        dbname="postgres",
        user=original["PORTAL_DB_USER"],
        password=original["PORTAL_DB_PASSWORD"],
        host="127.0.0.1",
        port=55438,
        autocommit=True,
    ) as connection:
        if connection.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=%s)", (database,)).fetchone()[0]:
            raise SystemExit("本次隔离库名已存在，拒绝覆盖。")
        connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
    storage = ROOT / ".runtime/p1-private" / database
    values = {
        "PORTAL_DEBUG": "1",
        "PORTAL_HTTPS": "0",
        "PORTAL_BEHIND_PROXY": "0",
        "PORTAL_SECRET_KEY": secrets.token_urlsafe(48),
        "PORTAL_ALLOWED_HOSTS": "127.0.0.1,localhost,testserver",
        "PORTAL_TRUSTED_MODULE_ORIGINS": "http://127.0.0.1:18320",
        "PORTAL_DB_NAME": database,
        "PORTAL_DB_USER": original["PORTAL_DB_USER"],
        "PORTAL_DB_PASSWORD": original["PORTAL_DB_PASSWORD"],
        "PORTAL_DB_HOST": "127.0.0.1",
        "PORTAL_DB_PORT": "55438",
        "PORTAL_CSRF_TRUSTED_ORIGINS": "http://127.0.0.1:18320",
        "PORTAL_BUSINESS_SUMMARY_URL": "",
        "PORTAL_INTEGRATION_SECRET": "",
        "PORTAL_FRONTEND_DIST": str((ROOT / "frontend/dist").resolve()).replace("\\", "/"),
        "PORTAL_MODEL_GATEWAY_URL": "",
        "PORTAL_MODEL_GATEWAY_TOKEN": "",
        "PORTAL_PRODUCT_P1_ENABLED": "1",
        "PORTAL_PRODUCT_MODEL_CALLS_ALLOWED": "0",
        "PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED": "0",
        "PORTAL_PRODUCT_REVIEWER_IDS": "",
        "PORTAL_PRODUCT_STORAGE_ROOT": str(storage.resolve()).replace("\\", "/"),
        "PORTAL_PRODUCT_DOCUMENT_PYTHON": str(
            (ROOT / ".runtime/product-documents-python/Scripts/python.exe").resolve()
        ).replace("\\", "/"),
    }
    write_environment(ENV_FILE, values)
    revoke_public_access(database, values)
    print(f"Created isolated database {database}; existing databases were not modified.")


def setup_django(registered):
    sys.path.insert(0, str(ROOT / "backend"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.conf import settings

    database = settings.DATABASES["default"]
    if (
        not settings.DEBUG
        or database["NAME"] != registered["PORTAL_DB_NAME"]
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != "55438"
        or not settings.PRODUCT_P1_ENABLED
        or settings.PRODUCT_MODEL_CALLS_ALLOWED
        or settings.PRODUCT_FORMAL_RELEASE_ENABLED
        or settings.MODEL_GATEWAY_URL
        or settings.MODEL_GATEWAY_TOKEN
        or Path(settings.PRODUCT_STORAGE_ROOT).resolve() != Path(registered["PORTAL_PRODUCT_STORAGE_ROOT"]).resolve()
        or Path(settings.PRODUCT_DOCUMENT_PYTHON).resolve() != Path(registered["PORTAL_PRODUCT_DOCUMENT_PYTHON"]).resolve()
    ):
        raise SystemExit("Django 未加载登记的 P1 隔离配置。")
    return settings


def setup():
    registered = registered_environment()
    settings = setup_django(registered)
    from django.core.management import call_command
    from django.db import transaction
    from portal.models import Role, User
    from portal.product_models import DocumentTask

    call_command("migrate", interactive=False, verbosity=1)
    if CREDENTIALS_FILE.exists() or User.objects.exists() or DocumentTask.objects.exists():
        raise SystemExit("P1 验收库或凭据已有数据，不覆盖。")
    call_command("seed_portal", verbosity=0)
    accounts = {}
    with transaction.atomic():
        for key, username, display_name, role_code, is_staff in (
            ("owner", "p1_owner", "P1 合成任务所有人", "product", False),
            ("reviewer", "p1_reviewer", "P1 合成审核人", "product", False),
            ("outsider", "p1_outsider_admin", "P1 合成平台管理员", "platform_admin", True),
        ):
            password = secrets.token_urlsafe(24)
            user = User.objects.create_user(
                username=username,
                password=password,
                display_name=display_name,
                is_staff=is_staff,
                must_change_password=False,
            )
            user.roles.add(Role.objects.get(code=role_code))
            accounts[key] = {"id": user.pk, "username": username, "password": password}
    registered["PORTAL_PRODUCT_REVIEWER_IDS"] = str(accounts["reviewer"]["id"])
    temporary = ENV_FILE.with_suffix(".env.tmp")
    if temporary.exists():
        raise SystemExit("发现未清理的 P1 临时配置，拒绝覆盖。")
    temporary.write_text("\n".join(f"{key}={value}" for key, value in registered.items()) + "\n", encoding="utf-8")
    restrict_acl(temporary)
    os.replace(temporary, ENV_FILE)
    restrict_acl(ENV_FILE)
    CREDENTIALS_FILE.write_text(json.dumps({"accounts": accounts}, ensure_ascii=False), encoding="utf-8")
    restrict_acl(CREDENTIALS_FILE)
    settings.PRODUCT_REVIEWER_IDS = (accounts["reviewer"]["id"],)
    outsider = User.objects.get(pk=accounts["outsider"]["id"])
    if outsider.roles.filter(code="product").exists() or set(settings.PRODUCT_REVIEWER_IDS) != {accounts["reviewer"]["id"]}:
        raise SystemExit("合成角色或审核允许名单不符合隔离要求。")
    restrict_database_objects(registered["PORTAL_DB_NAME"], registered)
    print("Migrated and seeded three synthetic accounts; credentials remain only under .runtime.")


def expect_response(response, status, code=None):
    if response.status_code != status:
        raise RuntimeError(f"API status mismatch: expected {status}, got {response.status_code}")
    body = response.json() if hasattr(response, "json") else None
    if code is not None and (not isinstance(body, dict) or body.get("code") != code):
        raise RuntimeError(f"API code mismatch: expected {code}")
    return body


def login(client, account):
    response = client.post(
        "/api/login/",
        data=json.dumps({"username": account["username"], "password": account["password"]}),
        content_type="application/json",
    )
    expect_response(response, 200)


def json_lines(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def child_environment(registered):
    environment = sanitized_environment(registered)
    for key in tuple(environment):
        upper = key.upper()
        if any(name in upper for name in ("OPENAI", "ANTHROPIC", "DASHSCOPE", "DEEPSEEK", "GEMINI")):
            environment.pop(key)
    environment.update(registered)
    return environment


def wait_for_interruption(process, marker, task_id, timeout_seconds=45):
    from portal.product_models import DocumentRevision

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("First worker exited before the third chapter wait marker.")
        count = DocumentRevision.objects.filter(task_id=task_id, kind="chapter").count()
        if marker.is_file() and count == 2:
            return
        time.sleep(0.1)
    raise RuntimeError("Timed out waiting for two committed chapters and the third chapter marker.")


def stop_process(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    process.communicate()
    return process.returncode


def start_worker(registered, mode, marker, log_path):
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "_worker", mode, str(marker), str(log_path)],
        cwd=ROOT,
        env=child_environment(registered),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def finish_worker(process, timeout_seconds=120):
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError("Recovery worker timed out.") from None
    if process.returncode != 0:
        raise RuntimeError(f"Recovery worker failed with exit code {process.returncode}.")
    return stdout, stderr


def validate_docx(path):
    required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if not required <= names or archive.testzip() is not None:
            raise RuntimeError("Generated artifact is not a structurally valid DOCX package.")
    return {
        "package_entries": len(names),
        "required_entries_present": True,
        "zip_integrity": True,
    }


def render_with_word(registered, source, output):
    script = ROOT / "backend/portal/product_assets/bj_docs/scripts/office_render.py"
    runtime = Path(registered["PORTAL_PRODUCT_DOCUMENT_PYTHON"])
    environment = sanitized_environment()
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        subprocess.run(
            [str(runtime), str(script), "word", str(source), str(output), "--timeout", "180"],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=210,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Frozen office_render.py Word validation failed.") from error
    report_path = output / "render.json"
    if not report_path.is_file():
        raise RuntimeError("Word renderer did not create render.json.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (
        report.get("rendered") is not True
        or report.get("structural_pass") is not True
        or report.get("input_sha256") != sha256_file(source)
        or not report.get("renderer", "").startswith("Microsoft Word ")
        or not Path(report.get("pdf", "")).is_file()
        or not report.get("images")
        or any(not Path(path).is_file() for path in report["images"])
    ):
        raise RuntimeError("Word render evidence is incomplete or does not match the draft artifact.")
    return {
        "executed": True,
        "renderer": report["renderer"],
        "rendered": True,
        "structural_pass": True,
        "visually_reviewed": bool(report.get("visually_reviewed")),
        "page_count": report.get("page_count"),
        "toc_count": report.get("toc_count"),
        "input_sha256": report["input_sha256"],
        "rendered_docx_sha256": report.get("rendered_docx_sha256"),
        "pdf_sha256": report.get("pdf_sha256"),
        "page_images": [Path(path).relative_to(ROOT).as_posix() for path in report["images"]],
        "unverified_items": report.get("unverified_items", []),
    }


def document_runtime_versions(registered):
    runtime = Path(registered["PORTAL_PRODUCT_DOCUMENT_PYTHON"])
    probe = (
        "import importlib.metadata as m,json,sys;"
        "print(json.dumps({'python':sys.version.split()[0],'lxml':m.version('lxml'),"
        "'python-docx':m.version('python-docx'),'PyMuPDF':m.version('PyMuPDF'),'pywin32':m.version('pywin32')}))"
    )
    result = subprocess.run(
        [str(runtime), "-c", probe],
        cwd=ROOT,
        env=sanitized_environment(),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    versions = json.loads(result.stdout)
    expected = {
        "python": "3.12.12",
        "lxml": "6.0.2",
        "python-docx": "1.2.0",
        "PyMuPDF": "1.26.4",
        "pywin32": "311",
    }
    if versions != expected:
        raise SystemExit("固定文档运行时版本与验收登记不一致。")
    return versions


def sha256_file(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def database_state(connection):
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ).fetchall()
    ]
    state = {}
    for table in tables:
        rows = connection.execute(
            sql.SQL("SELECT row_to_json(record) FROM {} AS record").format(sql.Identifier(table))
        ).fetchall()
        canonical = sorted(
            json.dumps(row[0], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in rows
        )
        state[table] = {
            "rows": len(canonical),
            "sha256": hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest(),
        }
    return state


def file_state(root):
    state = {}
    root = checked_path(root)
    if not root.is_dir():
        return state
    for path in regular_files(root):
        state[path.relative_to(root).as_posix()] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return state


def copy_private_files(source, destination):
    """Preflight the complete private tree before non-overwriting copytree."""
    source = checked_path(source, must_exist=True)
    destination = checked_path(destination)
    before = file_state(source)
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    if before != file_state(source):
        raise ValueError("Private source changed during backup copy")
    return before


def verify_container():
    inspection = json.loads(command(["docker", "inspect", CONTAINER]).stdout)[0]
    bindings = inspection["NetworkSettings"]["Ports"].get("5432/tcp") or []
    if not any(item["HostIp"] == "127.0.0.1" and item["HostPort"] == "55438" for item in bindings):
        raise SystemExit("数据库容器未绑定到登记的 127.0.0.1:55438。")


def ignored_path(path):
    relative = path.relative_to(ROOT).as_posix()
    return subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "--", relative],
        cwd=ROOT,
        env=sanitized_environment(),
    ).returncode == 0


def backup_and_restore(registered, run_id):
    source = registered["PORTAL_DB_NAME"]
    restore = "portal_p1_restore_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    assert_database_name(source)
    assert_database_name(restore, restore=True)
    verify_container()
    backup_directory = ROOT / "backups/p1-validation" / run_id
    dump_path = backup_directory / f"{source}.dump"
    source_private = checked_path(registered["PORTAL_PRODUCT_STORAGE_ROOT"], must_exist=True)
    restored_private = checked_path(ROOT / ".runtime/p1-restore-private" / restore)
    file_state(source_private)  # Refuse links/escapes before dump, restore or any private copy.
    if backup_directory.exists() or restored_private.exists():
        raise SystemExit("备份或私有文件恢复目录已存在，拒绝覆盖。")
    if not ignored_path(backup_directory) or not ignored_path(restored_private):
        raise SystemExit("备份目标未处于 Git 忽略范围，拒绝生成。")
    with connect("postgres", registered, readonly=True) as connection:
        source_exists = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=%s)", (source,)
        ).fetchone()[0]
        restore_exists = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=%s)", (restore,)
        ).fetchone()[0]
    if not source_exists or restore_exists:
        raise SystemExit("源隔离库不存在或目标恢复库已存在，拒绝继续。")
    backup_directory.mkdir(parents=True)
    restrict_acl(backup_directory, directory=True)
    notice = backup_directory / "SYNTHETIC-ISOLATED-DATA.txt"
    notice.write_text(
        "Contains only isolated synthetic P1 acceptance data. Retained for restore verification; do not commit.\n",
        encoding="utf-8",
    )
    restrict_acl(notice)
    with connect(source, registered, readonly=True) as source_connection:
        source_connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        tables = [
            row[0]
            for row in source_connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            ).fetchall()
        ]
        missing = PRODUCT_TABLES.difference(tables)
        if missing:
            raise SystemExit("源库缺少产品 P1 表，拒绝生成不完整备份。")
        source_connection.execute(
            sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE").format(
                sql.SQL(", ").join(sql.Identifier(table) for table in tables)
            )
        )
        snapshot = source_connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
        source_state = database_state(source_connection)
        with dump_path.open("xb") as dump_file:
            try:
                command(
                    [
                        "docker",
                        "exec",
                        CONTAINER,
                        "pg_dump",
                        "-U",
                        registered["PORTAL_DB_USER"],
                        "-d",
                        source,
                        "--format=custom",
                        "--no-owner",
                        "--no-acl",
                        f"--snapshot={snapshot}",
                    ],
                    stdout=dump_file,
                )
            except subprocess.CalledProcessError as error:
                raise SystemExit("pg_dump 失败；错误流与凭据均未写入证据。") from error
    restrict_acl(dump_path)
    with connect("postgres", registered, autocommit=True) as connection:
        if connection.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=%s)", (restore,)).fetchone()[0]:
            raise SystemExit("恢复库在执行期间已出现，拒绝覆盖。")
        connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(restore)))
    revoke_public_access(restore, registered)
    try:
        with dump_path.open("rb") as dump_file:
            command(
                [
                    "docker",
                    "exec",
                    "-i",
                    CONTAINER,
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    "-U",
                    registered["PORTAL_DB_USER"],
                    "-d",
                    restore,
                ],
                stdin=dump_file,
            )
    except subprocess.CalledProcessError as error:
        raise SystemExit("pg_restore 失败；新恢复库保留供检查，未删除任何数据库。") from error
    restrict_database_objects(restore, registered)
    with connect(restore, registered, readonly=True) as restored_connection:
        restored_state = database_state(restored_connection)
    source_files = copy_private_files(source_private, restored_private)
    restrict_acl(restored_private, directory=True)
    restored_files = file_state(restored_private)
    table_sets_equal = source_state.keys() == restored_state.keys()
    database_equal = source_state == restored_state
    files_equal = source_files == restored_files and bool(source_files)
    acl_restricted = public_access_revoked(restore, registered)
    passed = table_sets_equal and database_equal and files_equal and acl_restricted
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "result": "passed" if passed else "failed",
        "source": {
            "database": source,
            "endpoint": "127.0.0.1:55438",
            "read_only_repeatable_snapshot": True,
            "existing_database_modified": False,
        },
        "backup": {
            "path": dump_path.relative_to(ROOT).as_posix(),
            "format": "PostgreSQL custom",
            "bytes": dump_path.stat().st_size,
            "sha256": sha256_file(dump_path),
            "git_ignored": True,
            "filesystem_acl_restricted": True,
            "contains_runtime_credentials_file": False,
        },
        "restore": {
            "database": restore,
            "created_new": True,
            "retained": True,
            "dropped_databases": False,
            "public_access_revoked": acl_restricted,
            "private_files_directory": restored_private.relative_to(ROOT).as_posix(),
            "private_files_retained": True,
        },
        "comparison": {
            "snapshot_boundary": "Source fingerprints and pg_dump share one exported repeatable-read snapshot.",
            "table_count": len(source_state),
            "table_sets_equal": table_sets_equal,
            "stable_table_fingerprints_equal": database_equal,
            "tables": source_state,
            "private_file_count": len(source_files),
            "private_file_hashes_equal": files_equal,
            "private_files": source_files,
        },
    }
    if not passed:
        raise RuntimeError("Backup restore comparison failed; retained targets were not overwritten or deleted.")
    return summary


def render_evidence_summary(value):
    allowed = {
        "status",
        "renderer",
        "rendered",
        "structural_pass",
        "visually_reviewed",
        "page_count",
        "template_version",
    }
    return {key: value[key] for key in sorted(value) if key in allowed}


def secret_values(registered, credentials):
    values = [
        registered.get("PORTAL_DB_PASSWORD", ""),
        registered.get("PORTAL_SECRET_KEY", ""),
        registered.get("PORTAL_INTEGRATION_SECRET", ""),
        registered.get("PORTAL_MODEL_GATEWAY_TOKEN", ""),
    ]
    values.extend(account.get("password", "") for account in credentials["accounts"].values())
    return [value for value in values if len(value) >= 8]


def write_evidence(system_report, backup_report, secrets_to_block):
    if SYSTEM_EVIDENCE.exists() or BACKUP_EVIDENCE.exists():
        raise SystemExit("P1 验收证据已存在，拒绝覆盖。")
    system_json = json.dumps(system_report, ensure_ascii=False, indent=2) + "\n"
    backup_json = json.dumps(backup_report, ensure_ascii=False, indent=2) + "\n"
    if any(secret in system_json or secret in backup_json for secret in secrets_to_block):
        raise SystemExit("证据包含敏感值，拒绝写入。")
    EVIDENCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with BACKUP_EVIDENCE.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(backup_json)
    with SYSTEM_EVIDENCE.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(system_json)


def run_acceptance():
    registered = registered_environment()
    credentials = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    settings = setup_django(registered)
    runtime_versions = document_runtime_versions(registered)
    try:
        from portal.product_documents import render_draft
    except (ImportError, ModuleNotFoundError) as error:
        raise SystemExit("product_documents 渲染依赖尚未就绪；隔离库与账号已保留，可稍后运行 run。") from error
    if not callable(render_draft):
        raise SystemExit("product_documents.render_draft 不可调用。")
    if SYSTEM_EVIDENCE.exists() or BACKUP_EVIDENCE.exists():
        raise SystemExit("P1 验收证据已存在，拒绝覆盖。")
    from django.test import Client
    from django.utils import timezone as django_timezone
    from portal.models import Role, User
    from portal.product_models import (
        DocumentApproval,
        DocumentArtifact,
        DocumentAttempt,
        DocumentRevision,
        DocumentTask,
    )
    from portal.product_service import approved_blueprint
    from portal.product_storage import verified_artifact

    if DocumentTask.objects.exists():
        raise SystemExit("隔离库已有产品任务，拒绝复用或覆盖。")
    accounts = credentials.get("accounts", {})
    if set(accounts) != {"owner", "reviewer", "outsider"}:
        raise SystemExit("P1 合成账号登记不完整。")
    owner = User.objects.get(pk=accounts["owner"]["id"])
    reviewer = User.objects.get(pk=accounts["reviewer"]["id"])
    outsider = User.objects.get(pk=accounts["outsider"]["id"])
    if (
        set(settings.PRODUCT_REVIEWER_IDS) != {reviewer.pk}
        or outsider.roles.filter(code="product").exists()
        or not outsider.roles.filter(code="platform_admin").exists()
    ):
        raise SystemExit("审核允许名单或 outsider 角色不符合测试限定。")
    owner_client, reviewer_client, outsider_client = Client(), Client(), Client()
    login(owner_client, accounts["owner"])
    login(reviewer_client, accounts["reviewer"])
    login(outsider_client, accounts["outsider"])
    input_payload = {
        "project": "P1 隔离恢复合成项目",
        "requirements": "只根据合成清单生成三章草稿并保留版本证据。",
        "items": [{"row_id": "r1", "name": "合成防火墙", "quantity": 2, "unit": "台"}],
        "background": "本任务不含真实公司、客户、人员或项目数据。",
        "conditions": ["不得增加合成清单之外的事实"],
    }
    created = owner_client.post(
        "/api/product/tasks/",
        data=json.dumps({"title": "P1 合成技术方案", "input": input_payload, "reviewer_id": reviewer.pk}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="p1-isolated-acceptance-v1",
    )
    task = expect_response(created, 201)
    outsider_list = outsider_client.get("/api/product/tasks/")
    outsider_read = outsider_client.get(f"/api/product/tasks/{task['id']}/")
    expect_response(outsider_list, 404, "not_found")
    expect_response(outsider_read, 404, "not_found")
    stale_update = owner_client.patch(
        f"/api/product/tasks/{task['id']}/",
        data=json.dumps({"expected_version": task["version"] - 1, "title": "不应写入的陈旧标题"}),
        content_type="application/json",
    )
    expect_response(stale_update, 409, "stale_version")
    blueprint_payload = {
        "purpose": "形成仅含合成数据的可恢复 Word 草稿",
        "audience": "P1 隔离验收审核人",
        "chapters": [
            {"id": f"chapter-{index}", "title": f"合成章节{index}", "scope": "仅陈述合成清单", "source_ids": ["r1"]}
            for index in range(1, 4)
        ],
        "conditions": [{"text": "不得增加合成清单之外的事实", "type": "program"}],
        "missing": [],
        "conflicts": [],
        "template_version": "frozen-original-v1",
    }
    saved = owner_client.patch(
        f"/api/product/tasks/{task['id']}/blueprint/",
        data=json.dumps({"expected_version": task["version"], "payload": blueprint_payload}),
        content_type="application/json",
    )
    task = expect_response(saved, 200)
    self_review = owner_client.post(
        f"/api/product/tasks/{task['id']}/decisions/",
        data=json.dumps(
            {
                "expected_version": task["version"],
                "target": "blueprint",
                "target_id": task["blueprint"]["id"],
                "sha256": task["blueprint"]["sha256"],
                "decision": "approve",
                "comment": "不得生效的自审",
            }
        ),
        content_type="application/json",
    )
    expect_response(self_review, 404, "not_found")
    approved = reviewer_client.post(
        f"/api/product/tasks/{task['id']}/decisions/",
        data=json.dumps(
            {
                "expected_version": task["version"],
                "target": "blueprint",
                "target_id": task["blueprint"]["id"],
                "sha256": task["blueprint"]["sha256"],
                "decision": "approve",
                "comment": "仅批准合成蓝图进入隔离 worker",
            }
        ),
        content_type="application/json",
    )
    task = expect_response(approved, 201)["task"]
    manual_stale = owner_client.post(
        f"/api/product/tasks/{task['id']}/chapters/",
        data=json.dumps(
            {
                "expected_version": task["version"] - 1,
                "chapter_id": "chapter-1",
                "title": "合成章节1",
                "paragraphs": ["此陈旧写入不得保存。"],
                "source_ids": ["r1"],
            }
        ),
        content_type="application/json",
    )
    expect_response(manual_stale, 409, "stale_version")
    product_role = Role.objects.get(code="product")
    reviewer.roles.remove(product_role)
    reviewer.refresh_from_db()
    revoked_read = reviewer_client.get(f"/api/product/tasks/{task['id']}/")
    expect_response(revoked_read, 404, "not_found")
    model_task = DocumentTask.objects.get(pk=task["id"])
    if approved_blueprint(model_task) is not None:
        raise RuntimeError("Reviewer revocation did not invalidate the approved blueprint.")
    reviewer.roles.add(product_role)
    reviewer.refresh_from_db()
    model_task.refresh_from_db()
    if approved_blueprint(model_task) is None:
        raise RuntimeError("Synthetic reviewer regrant did not restore the isolated approval.")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runtime_directory = ROOT / ".runtime/p1-validation-runs" / run_id
    if runtime_directory.exists():
        raise SystemExit("本次 worker 运行目录已存在，拒绝覆盖。")
    runtime_directory.mkdir(parents=True)
    restrict_acl(runtime_directory, directory=True)
    marker = runtime_directory / "third-chapter.entered"
    first_log = runtime_directory / "worker-interrupted.jsonl"
    second_log = runtime_directory / "worker-recovered.jsonl"
    first_worker = start_worker(registered, "interrupt", marker, first_log)
    wait_for_interruption(first_worker, marker, model_task.pk)
    model_task.refresh_from_db()
    first_attempt = DocumentAttempt.objects.get(task=model_task, status="running")
    if model_task.state != "RUNNING" or first_attempt.model_calls != 3:
        raise RuntimeError("First worker did not persist the expected running attempt.")
    first_fence = model_task.fence
    first_pid = first_worker.pid
    first_returncode = stop_process(first_worker)
    model_task.refresh_from_db()
    if DocumentRevision.objects.filter(task=model_task, kind="chapter").count() != 2:
        raise RuntimeError("Parent termination did not leave exactly two committed chapters.")
    shortened_to = django_timezone.now() - timedelta(seconds=1)
    DocumentTask.objects.filter(pk=model_task.pk, state="RUNNING", fence=first_fence).update(lease_until=shortened_to)
    model_task.refresh_from_db()
    if model_task.lease_until is None or model_task.lease_until > django_timezone.now():
        raise RuntimeError("Lease was not explicitly shortened to an expired value.")
    recovery_worker = start_worker(registered, "resume", marker, second_log)
    second_pid = recovery_worker.pid
    finish_worker(recovery_worker)
    model_task.refresh_from_db()
    chapters = list(DocumentRevision.objects.filter(task=model_task, kind="chapter").order_by("version"))
    attempts = list(DocumentAttempt.objects.filter(task=model_task).order_by("start_at"))
    artifacts = list(DocumentArtifact.objects.filter(task=model_task))
    if (
        model_task.state != "WAITING_REVIEW"
        or model_task.stage != "FINAL_REVIEW"
        or len(chapters) != 3
        or len(artifacts) != 1
        or len(attempts) != 2
        or attempts[0].status != "failed"
        or attempts[0].error_code != "lease_expired"
        or attempts[1].status != "done"
        or model_task.fence <= first_fence
    ):
        raise RuntimeError("Recovered worker state does not match the persisted lease/fence contract.")
    first_events = json_lines(first_log)
    second_events = json_lines(second_log)
    first_chapters = [event.get("chapter_id") for event in first_events if event.get("action") == "chapter"]
    second_chapters = [event.get("chapter_id") for event in second_events if event.get("action") == "chapter"]
    second_reviews = [event for event in second_events if event.get("action") == "independent_review"]
    if first_chapters != ["chapter-1", "chapter-2", "chapter-3"] or second_chapters != ["chapter-3"] or len(second_reviews) != 1:
        raise RuntimeError("Synthetic model call log does not prove reuse of the first two chapters.")
    artifact = artifacts[0]
    artifact_path = verified_artifact(artifact)
    docx_checks = validate_docx(artifact_path)
    word_render = render_with_word(registered, artifact_path, runtime_directory / "word-render")
    if artifact_path.suffix.lower() != ".docx" or sha256_file(artifact_path) != artifact.sha256:
        raise RuntimeError("Rendered DOCX path or hash does not match the persisted artifact.")
    owner_detail = expect_response(owner_client.get(f"/api/product/tasks/{task['id']}/"), 200)
    if len(owner_detail["artifacts"]) != 1 or not owner_detail["artifacts"][0]["draft"] or owner_detail["artifacts"][0]["approved"]:
        raise RuntimeError("API did not expose exactly one unapproved draft artifact.")
    reviewer_detail = expect_response(reviewer_client.get(f"/api/product/tasks/{task['id']}/"), 200)
    formal_attempt = reviewer_client.post(
        f"/api/product/tasks/{task['id']}/decisions/",
        data=json.dumps(
            {
                "expected_version": reviewer_detail["version"],
                "target": "artifact",
                "target_id": str(artifact.pk),
                "sha256": artifact.sha256,
                "decision": "approve",
                "comment": "验证草稿不得转为正式批准",
            }
        ),
        content_type="application/json",
    )
    expect_response(formal_attempt, 409, "formal_release_blocked")
    outsider_download = outsider_client.get(f"/api/product/artifacts/{artifact.pk}/download/")
    expect_response(outsider_download, 404, "not_found")
    if DocumentApproval.objects.filter(task=model_task, artifact__isnull=False).exists():
        raise RuntimeError("Formal artifact approval was unexpectedly persisted.")
    backup_report = backup_and_restore(registered, run_id)
    postgres_version = ""
    with connect(registered["PORTAL_DB_NAME"], registered, readonly=True) as connection:
        postgres_version = connection.execute("SHOW server_version").fetchone()[0]
    system_report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "result": "passed",
        "command": "uv run --env-file .runtime/p1-validation.env python validation/p1_isolated_acceptance.py run",
        "boundaries": {
            "database": registered["PORTAL_DB_NAME"],
            "endpoint": "127.0.0.1:55438",
            "database_name_whitelist_verified": True,
            "existing_ops_or_daily_database_modified": False,
            "synthetic_data_only": True,
            "real_company_data_used": False,
            "formal_release_enabled": False,
            "reviewer_allowlist": "synthetic reviewer only",
        },
        "model_substitute": {
            "kind": "deterministic child-process monkeypatch",
            "real_or_paid_model_called": False,
            "gateway_url_configured": False,
            "gateway_token_configured": False,
            "registered_model_calls_allowed": False,
            "child_local_model_gate_override": True,
            "patch_scope": "worker subprocesses only",
            "first_worker_chapter_entries": first_chapters,
            "recovery_worker_chapter_entries": second_chapters,
            "recovery_worker_review_calls": len(second_reviews),
        },
        "real_postgres": {
            "used": True,
            "server_version": postgres_version,
            "task_id": str(model_task.pk),
            "committed_chapters_before_termination": 2,
            "committed_chapters_after_recovery": len(chapters),
            "first_two_chapters_reused": second_chapters == ["chapter-3"],
            "attempts": [
                {
                    "fence": attempt.fence,
                    "status": attempt.status,
                    "error_code": attempt.error_code,
                    "model_substitute_calls": attempt.model_calls,
                }
                for attempt in attempts
            ],
        },
        "real_processes": {
            "first_worker_pid": first_pid,
            "first_worker_terminated_by_parent": True,
            "first_worker_returncode": first_returncode,
            "lease_explicitly_shortened_after_termination": True,
            "expired_lease_timestamp": shortened_to.isoformat(),
            "recovery_worker_pid": second_pid,
            "recovery_worker_exit_code": recovery_worker.returncode,
            "new_fence_claimed": model_task.fence > first_fence,
        },
        "word_generation": {
            "implementation": "portal.product_documents.render_draft",
            "fixed_runtime": runtime_versions,
            "called_by_real_worker": True,
            "artifact_count": 1,
            "artifact_version": artifact.version,
            "artifact_path": artifact.path,
            "artifact_bytes": artifact_path.stat().st_size,
            "artifact_sha256": artifact.sha256,
            "draft": True,
            "formal_approval_count": 0,
            "render_evidence": render_evidence_summary(artifact.render_evidence),
            "microsoft_word_render": word_render,
            **docx_checks,
        },
        "api_isolation": {
            "outsider_is_platform_admin_without_product_role": True,
            "outsider_list_status": outsider_list.status_code,
            "outsider_task_status": outsider_read.status_code,
            "outsider_artifact_status": outsider_download.status_code,
            "owner_self_review_status": self_review.status_code,
            "reviewer_live_revocation_status": revoked_read.status_code,
            "stale_owner_update": {"status": stale_update.status_code, "code": "stale_version"},
            "stale_manual_chapter": {"status": manual_stale.status_code, "code": "stale_version"},
            "exact_blueprint_version_approval_status": approved.status_code,
            "exact_artifact_version_formal_gate": {"status": formal_attempt.status_code, "code": "formal_release_blocked"},
        },
        "backup_summary": BACKUP_EVIDENCE.relative_to(ROOT).as_posix(),
        "browser": {
            "executed": False,
            "reason": "Browser acceptance is optional and is handled separately only when Playwright runtime is available.",
            "screenshots_claimed": False,
        },
    }
    write_evidence(system_report, backup_report, secret_values(registered, credentials))
    print(
        f"P1 isolated acceptance passed; task={model_task.pk}; restore={backup_report['restore']['database']}; "
        f"evidence={SYSTEM_EVIDENCE.relative_to(ROOT)}"
    )


def worker_process(mode, marker, log_path):
    if mode not in {"interrupt", "resume"}:
        raise SystemExit("Invalid internal worker mode.")
    registered = registered_environment()
    settings = setup_django(registered)
    import portal.product_worker as product_worker
    from django.test import Client
    from portal.product_models import DocumentTask

    marker_path = Path(marker).resolve()
    event_path = Path(log_path).resolve()
    allowed_root = (ROOT / ".runtime/p1-validation-runs").resolve()
    if not marker_path.is_relative_to(allowed_root) or not event_path.is_relative_to(allowed_root):
        raise SystemExit("Internal worker marker path is outside the runtime boundary.")

    settings.PRODUCT_MODEL_CALLS_ALLOWED = True
    settings.PRODUCT_COST_POLICY = {
        "approval_ref": "synthetic-worker-test-only", "currency": "TEST", "max_task_cost": "100",
        "route_cost_caps": {"product_blueprint": "1", "product_writing": "1", "product_review": "1"},
    }
    if mode == "interrupt":
        credentials = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        account = credentials["accounts"]["owner"]
        task = DocumentTask.objects.get()
        if task.state != "WAITING_INPUT" or task.pending_action != "write" or task.error_code != "model_authorization_required":
            raise SystemExit("Initial synthetic task is not blocked at the model authorization boundary.")
        client = Client()
        login(client, account)
        queued = client.post(
            f"/api/product/tasks/{task.pk}/retry/",
            data=json.dumps({"expected_version": task.version}),
            content_type="application/json",
        )
        expect_response(queued, 200)

    def generate_synthetic(user, route, messages):
        payload = json.loads(messages[-1]["content"])
        action = payload.get("action")
        chapter = payload.get("chapter") or {}
        event = {"route": route, "action": action}
        if action == "chapter":
            event["chapter_id"] = chapter.get("id")
        with event_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        if action == "chapter":
            chapter_id = chapter["id"]
            if mode == "interrupt" and chapter_id == "chapter-3":
                marker_path.write_text("entered\n", encoding="utf-8")
                while True:
                    time.sleep(1)
            result = {
                "chapter_id": chapter_id,
                "title": chapter["title"],
                "paragraphs": [f"{chapter['title']}仅确认合成清单中的2台合成防火墙。"],
                "source_ids": ["r1"],
            }
        elif action == "independent_review":
            result = {"passed": True, "issues": []}
        else:
            raise RuntimeError("Unexpected synthetic model action.")
        return {
            "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    product_worker.generate_for_use = generate_synthetic
    if not product_worker.run_once():
        raise SystemExit("Internal worker found no claimable task.")


def main():
    action = sys.argv[1] if len(sys.argv) >= 2 else ""
    if action == "provision" and len(sys.argv) == 2:
        provision()
    elif action == "setup" and len(sys.argv) == 2:
        setup()
    elif action == "run" and len(sys.argv) == 2:
        run_acceptance()
    elif action == "_worker" and len(sys.argv) == 5:
        worker_process(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit(
            "使用 provision/setup/run；setup 与 run 必须通过 uv 加载 .runtime/p1-validation.env。"
        )


if __name__ == "__main__":
    main()
