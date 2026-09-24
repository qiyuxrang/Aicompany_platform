import getpass
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".runtime/ops-validation.env"
EVIDENCE_FILE = ROOT / "docs/evidence/closure-backup/restore-rehearsal.json"
CONTAINER = "enterprise-portal-phase1-db"
REQUIRED_TABLES = {"portal_modulecheck", "portal_operationalissue"}
MIGRATION = "0003_modulecheck_operationalissue_audit_indexes"


def read_environment(path):
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )


def safe_environment():
    blocked = {"PORTAL_DB_PASSWORD", "PORTAL_SECRET_KEY", "PORTAL_INTEGRATION_SECRET"}
    return {key: value for key, value in os.environ.items() if key not in blocked}


def run(arguments, **kwargs):
    return subprocess.run(
        arguments,
        check=True,
        capture_output=not ("stdout" in kwargs or "stdin" in kwargs),
        env=safe_environment(),
        **kwargs,
    )


def restrict_acl(path, directory=False):
    if os.name == "nt":
        principal = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', getpass.getuser())}".strip("\\")
        permission = "(OI)(CI)(F)" if directory else "(F)"
        run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:{permission}"])
    else:
        path.chmod(0o700 if directory else 0o600)


def connect(database, registered, *, readonly=False, autocommit=False):
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


def database_state(connection):
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ).fetchall()
    ]
    records = {}
    for table in tables:
        rows = connection.execute(
            sql.SQL("SELECT row_to_json(record) FROM {} AS record").format(sql.Identifier(table))
        ).fetchall()
        canonical = sorted(
            json.dumps(row[0], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in rows
        )
        records[table] = {
            "rows": len(canonical),
            "sha256": hashlib.sha256("\n".join(canonical).encode()).hexdigest(),
        }
    migration_applied = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM django_migrations WHERE app='portal' AND name=%s)",
        (MIGRATION,),
    ).fetchone()[0]
    return records, migration_applied


def verify_container():
    inspection = json.loads(run(["docker", "inspect", CONTAINER]).stdout)[0]
    bindings = inspection["NetworkSettings"]["Ports"].get("5432/tcp") or []
    if not any(item["HostIp"] == "127.0.0.1" and item["HostPort"] == "55438" for item in bindings):
        raise SystemExit("数据库容器未按登记边界绑定到 127.0.0.1:55438。")


def revoke_public_access(database, registered):
    with connect("postgres", registered, autocommit=True) as connection:
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
        )
        connection.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(database),
                sql.Literal("Sensitive isolated restore rehearsal data; PUBLIC access revoked; retained for validation."),
            )
        )


def restrict_restored_objects(database, registered):
    with connect(database, registered) as connection:
        connection.execute("REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC")
        connection.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC")
        connection.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
        connection.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC")
        connection.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC")


def public_access_revoked(database, registered):
    with connect("postgres", registered, readonly=True) as connection:
        database_public_acl = connection.execute(
            """
            SELECT database.datacl IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM aclexplode(database.datacl) AS acl WHERE acl.grantee=0
            )
            FROM pg_database AS database
            WHERE database.datname=%s
            """,
            (database,),
        ).fetchone()[0]
    with connect(database, registered, readonly=True) as connection:
        schema_public_acl = connection.execute(
            """
            SELECT namespace.nspacl IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM aclexplode(namespace.nspacl) AS acl WHERE acl.grantee=0
            )
            FROM pg_namespace AS namespace
            WHERE namespace.nspname='public'
            """
        ).fetchone()[0]
        object_public_acl = connection.execute(
            """
            SELECT NOT EXISTS (
                SELECT 1
                FROM pg_class AS object
                JOIN pg_namespace AS namespace ON namespace.oid=object.relnamespace,
                     LATERAL aclexplode(object.relacl) AS acl
                WHERE namespace.nspname='public' AND acl.grantee=0
            )
            """
        ).fetchone()[0]
    return database_public_acl and schema_public_acl and object_public_acl


def main():
    registered = read_environment(ENV_FILE)
    guarded_keys = ("PORTAL_DB_NAME", "PORTAL_DB_USER", "PORTAL_DB_PASSWORD", "PORTAL_DB_HOST", "PORTAL_DB_PORT")
    if any(os.environ.get(key) != registered.get(key) for key in guarded_keys):
        raise SystemExit("必须通过登记的 .runtime/ops-validation.env 运行，拒绝任意目标参数。")
    source = registered["PORTAL_DB_NAME"]
    if (
        not source.startswith("portal_ops_")
        or "_restore_" in source
        or registered["PORTAL_DB_HOST"] != "127.0.0.1"
        or registered["PORTAL_DB_PORT"] != "55438"
    ):
        raise SystemExit("仅允许登记的 127.0.0.1:55438 独立 portal_ops_* 验收库。")
    verify_container()

    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ")
    restore = f"portal_ops_restore_{started_at.strftime('%Y%m%d_%H%M%S')}"
    backup_directory = ROOT / "backups/closure-backup" / run_id
    dump_path = backup_directory / f"{source}.dump"
    backup_relative = dump_path.relative_to(ROOT).as_posix()
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "--", backup_relative],
        cwd=ROOT,
        env=safe_environment(),
    ).returncode == 0
    if not ignored:
        raise SystemExit("备份目标不在 Git 忽略范围，拒绝生成敏感转储。")

    with connect("postgres", registered, readonly=True) as connection:
        if not connection.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=%s)", (source,)).fetchone()[0]:
            raise SystemExit("登记的源验收库不存在。")
        if connection.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=%s)", (restore,)).fetchone()[0]:
            raise SystemExit("本次隔离恢复库已存在；拒绝覆盖且不会删除任何数据库。")

    backup_directory.mkdir(parents=True)
    restrict_acl(backup_directory, directory=True)
    marker = backup_directory / "SENSITIVE-DATA.txt"
    marker.write_text(
        "Contains a full isolated validation database backup. Access is restricted; do not commit, copy to tickets, or expose.\n",
        encoding="utf-8",
    )
    restrict_acl(marker)

    try:
        with connect(source, registered, readonly=True) as source_connection:
            source_connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if source_connection.execute("SHOW transaction_read_only").fetchone()[0] != "on":
                raise SystemExit("源库事务未进入只读模式。")
            source_tables = [
                row[0]
                for row in source_connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
                ).fetchall()
            ]
            missing = REQUIRED_TABLES.difference(source_tables)
            if missing:
                raise SystemExit("源库缺少 0003 新增运维表，拒绝生成不完整验收备份。")
            source_connection.execute(
                sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE").format(
                    sql.SQL(", ").join(sql.Identifier(table) for table in source_tables)
                )
            )
            snapshot = source_connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
            source_state, source_migration = database_state(source_connection)
            if not source_migration:
                raise SystemExit("源库未登记 0003 迁移，拒绝继续。")
            with dump_path.open("wb") as dump_file:
                run(
                    [
                        "docker", "exec", CONTAINER, "pg_dump",
                        "-U", registered["PORTAL_DB_USER"], "-d", source,
                        "--format=custom", "--no-owner", "--no-acl", f"--snapshot={snapshot}",
                    ],
                    stdout=dump_file,
                )
        restrict_acl(dump_path)
    except subprocess.CalledProcessError as error:
        raise SystemExit("pg_dump 失败；未记录命令错误流或任何凭据。") from error

    with connect("postgres", registered, autocommit=True) as connection:
        if connection.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=%s)", (restore,)).fetchone()[0]:
            raise SystemExit("隔离恢复库在执行期间已出现；拒绝覆盖且不会删除任何数据库。")
        connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(restore)))
    revoke_public_access(restore, registered)

    try:
        with dump_path.open("rb") as dump_file:
            run(
                [
                    "docker", "exec", "-i", CONTAINER, "pg_restore",
                    "--exit-on-error", "--no-owner", "--no-acl",
                    "-U", registered["PORTAL_DB_USER"], "-d", restore,
                ],
                stdin=dump_file,
            )
    except subprocess.CalledProcessError as error:
        raise SystemExit("pg_restore 失败；隔离库保留供人工检查，未删除任何数据库。") from error

    restrict_restored_objects(restore, registered)
    with connect(restore, registered, readonly=True) as restore_connection:
        restored_state, restored_migration = database_state(restore_connection)
    acl_restricted = public_access_revoked(restore, registered)

    table_sets_equal = source_state.keys() == restored_state.keys()
    row_counts_equal = table_sets_equal and all(
        source_state[table]["rows"] == restored_state[table]["rows"] for table in source_state
    )
    fingerprints_equal = source_state == restored_state
    required_tables = {
        table: source_state.get(table)
        for table in sorted(REQUIRED_TABLES)
    }
    passed = all(
        (
            table_sets_equal,
            row_counts_equal,
            fingerprints_equal,
            source_migration,
            restored_migration,
            acl_restricted,
        )
    )
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "result": "passed" if passed else "failed",
        "command": "uv run --env-file .runtime/ops-validation.env python validation/ops_restore_rehearsal.py",
        "source": {
            "database": source,
            "endpoint": "127.0.0.1:55438",
            "transaction_read_only": True,
            "snapshot": "pg_export_snapshot shared by source fingerprint and pg_dump",
            "writes_or_account_changes": False,
        },
        "backup": {
            "path": backup_relative,
            "format": "PostgreSQL custom",
            "sha256": hashlib.sha256(dump_path.read_bytes()).hexdigest(),
            "bytes": dump_path.stat().st_size,
            "git_ignored": ignored,
            "filesystem_acl_restricted": True,
            "contains_credentials_files": False,
        },
        "restore": {
            "database": restore,
            "created_new": True,
            "pg_restore_exit_on_error": True,
            "retained": True,
            "dropped_databases": False,
            "public_database_schema_object_privileges_revoked": acl_restricted,
            "sensitive_data_notice": "Full validation data; owner access only under normal PostgreSQL ACLs.",
        },
        "comparison": {
            "boundary": "Source fingerprints and pg_dump share one repeatable-read exported snapshot. Concurrent committed changes after that snapshot are intentionally outside this comparison.",
            "post_snapshot_live_source_compared": False,
            "table_count": len(source_state),
            "table_sets_equal": table_sets_equal,
            "row_counts_equal": row_counts_equal,
            "stable_fingerprints_equal": fingerprints_equal,
            "tables": source_state,
        },
        "migration_0003": {
            "name": MIGRATION,
            "source_applied": source_migration,
            "restore_applied": restored_migration,
            "required_tables": required_tables,
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    for key in ("PORTAL_DB_PASSWORD", "PORTAL_SECRET_KEY", "PORTAL_INTEGRATION_SECRET"):
        secret = registered.get(key, "")
        if len(secret) >= 8 and secret in rendered:
            raise SystemExit("证据生成包含敏感值，已拒绝写入。")
    EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_FILE.write_text(rendered, encoding="utf-8")
    print(
        f"Restored {len(restored_state)} tables from a shared read-only snapshot; "
        f"equal={fingerprints_equal}; restore={restore}; evidence={EVIDENCE_FILE.relative_to(ROOT)}"
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
