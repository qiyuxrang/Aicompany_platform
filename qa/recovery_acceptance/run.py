"""Real pg_dump/pg_restore + private-file recovery in an owned UUID cluster."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import secrets
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from qa.run_portable_postgres import PortablePostgres, now, redact, source_snapshot
from qa.recovery_acceptance.safety import (
    assert_same_snapshot, copy_verified, digest, file_manifest, manifest_digest, require_restore_target,
)


def frozen_sources():
    snapshot = source_snapshot()
    files = snapshot["files"]
    for path in sorted((ROOT / "qa/recovery_acceptance").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            files[path.relative_to(ROOT).as_posix()] = digest(path)
    encoded = json.dumps(files, sort_keys=True).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "files": files}


def database_manifest(connection):
    """Small synthetic DB only: read content for hashing, never return raw rows."""
    from psycopg import sql
    tables = connection.execute(
        "SELECT tablename,tableowner FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall()
    result = {"tables": {}}
    for name, owner in tables:
        rows = connection.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t").format(
            sql.Identifier("public", name))).fetchall()
        normalized = [json.dumps(json.loads(row[0]), sort_keys=True, separators=(",", ":")) for row in rows]
        result["tables"][name] = {"rows": len(rows), "owner": owner,
            "sha256": hashlib.sha256("\n".join(sorted(normalized)).encode()).hexdigest()}
    columns = connection.execute("""
        SELECT table_name,column_name,data_type,udt_name,is_nullable,column_default,
               character_maximum_length,numeric_precision,numeric_scale,is_identity,is_generated
        FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,column_name
    """).fetchall()
    constraints = connection.execute("""
        SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid)
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' ORDER BY c.relname,k.conname
    """).fetchall()
    indexes = []
    for table, name, definition, predicate in connection.execute("""
        SELECT t.relname,c.relname,pg_get_indexdef(i.indexrelid),pg_get_expr(i.indpred,i.indrelid)
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_class t ON t.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public'
        ORDER BY t.relname,c.relname
    """).fetchall():
        if predicate is not None:
            # pg_dump can distribute varchar[]->text[] casts over constant elements.
            # Let PostgreSQL's planner fold the expression, preserving its types and
            # operator semantics. EXPLAIN without ANALYZE never runs the query.
            plan = connection.execute(sql.SQL(
                "EXPLAIN (VERBOSE, FORMAT JSON, COSTS OFF) SELECT ({}) FROM {}"
            ).format(sql.SQL(predicate), sql.Identifier("public", table))).fetchone()[0]
            outputs = plan[0]["Plan"].get("Output", [])
            if len(outputs) != 1 or " WHERE " not in definition:
                raise RuntimeError("could not canonicalize partial-index predicate safely")
            definition = definition.rsplit(" WHERE ", 1)[0] + " WHERE " + outputs[0]
        indexes.append((table, name, definition))
    result["structure"] = {"columns": columns, "constraints": constraints, "indexes": indexes}
    result["structure_sha256"] = hashlib.sha256(json.dumps(
        result["structure"], sort_keys=True, default=str).encode()).hexdigest()
    result["sequences"] = {}
    for name, owner in connection.execute(
        "SELECT sequencename,sequenceowner FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"
    ).fetchall():
        last, called = connection.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(
            sql.Identifier("public", name))).fetchone()
        result["sequences"][name] = {"last_value": last, "is_called": called, "owner": owner}
    return result


def snapshot_database(pg, name):
    with pg.connect(name) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        result = database_manifest(connection)
        connection.execute("COMMIT")
        return result


def run(postgres_bin):
    pg = PortablePostgres(postgres_bin, runtime_root=ROOT / ".runtime/recovery-acceptance")
    report = {
        "schema_version": 1, "outcome": "RUNNING", "started_at": now(),
        "scope": "synthetic Portal PostgreSQL public schema + HR/product/tender private directories",
        "production_release_approved": False,
        "not_verified": ["Native licensed Runtime PostgreSQL/checkpoint/store/Redis recovery",
                         "production cloud/Kubernetes/Linux topology", "online distributed consistency/WAL PITR",
                         "real employee/business data", "engineering private volume/domain fixture",
                         "production roles/secrets/ACL/encryption and offsite backup custody",
                         "production capacity or contractual RPO/RTO"],
        "consistency": {"fixture_writers_quiesced": True,
                        "database": "pg_export_snapshot -> pg_dump --snapshot; whole-table and sequence equality",
                        "files": "before/after manifests and copied byte hashes while fixture writers are stopped",
                        "limitation": "not an online atomic DB/files snapshot; production requires maintenance/drain or coordinated snapshot"},
        "backup_policy": {"format": "PostgreSQL custom", "acl": "--no-acl, owners retained in same isolated cluster",
                          "target": "new UUID database, never --clean/drop/overwrite", "synthetic_only": True},
    }
    try:
        with pg:
            report["run_dir"] = str(pg.run_dir)
            report["source_before"] = frozen_sources()
            report["packages"] = {name: importlib.metadata.version(name)
                                    for name in ("Django", "psycopg", "djangorestframework")}
            for name in ("pg_dump", "pg_restore"):
                pg.executable(name)
            pg.migrate()
            fixture_password = secrets.token_urlsafe(32)
            pg._sensitive.append(fixture_password)
            fixture_env = {**pg.env, "RECOVERY_FIXTURE_PASSWORD": fixture_password}
            fixture = ROOT / "qa/recovery_acceptance/fixture.py"
            pg.run([sys.executable, fixture, "create", "--run-dir", pg.run_dir],
                   name="create-synthetic-fixture", env=fixture_env, timeout=180)
            fixture_manifest = json.loads((pg.run_dir / "fixture.json").read_text(encoding="utf-8"))
            report["fixture"] = fixture_manifest
            backup_dir = pg.run_dir / "backup"
            backup_dir.mkdir(mode=0o700)
            dump = backup_dir / "portal.dump"
            pg_tools_env = {**pg.env, "PGPASSWORD": pg._password, "PGCONNECT_TIMEOUT": "5"}
            common = ["--host=127.0.0.1", f"--port={pg.port}", f"--username={pg.username}"]
            backup_started = time.perf_counter()
            with pg.connect(pg.database_name) as connection:
                connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                exported = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
                # ACCESS SHARE prevents concurrent DDL for the complete scoped table set.
                from psycopg import sql
                for (table,) in connection.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall():
                    connection.execute(sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE").format(sql.Identifier("public", table)))
                database = database_manifest(connection)
                files = file_manifest(pg.run_dir / "storage")
                report["recovery_point_at"] = now()
                pg.run([pg.executable("pg_dump"), *common, f"--dbname={pg.database_name}",
                        "--format=custom", "--no-acl", f"--snapshot={exported}", f"--file={dump}"],
                       name="pg-dump", env=pg_tools_env, timeout=180)
                dump.chmod(0o600)
                copy_verified(pg.run_dir / "storage", backup_dir / "private", files)
                assert_same_snapshot(files, file_manifest(pg.run_dir / "storage"), "source private files")
                connection.execute("COMMIT")
            assert_same_snapshot(database, snapshot_database(pg, pg.database_name), "quiesced source database")
            report["backup"] = {"duration_seconds": round(time.perf_counter() - backup_started, 3),
                                "database": database, "files": files, "files_sha256": manifest_digest(files),
                                "dump_sha256": digest(dump), "dump_bytes": dump.stat().st_size}
            (backup_dir / "manifest.json").write_text(json.dumps(report["backup"], indent=2), encoding="utf-8")
            recovery_started = time.perf_counter()
            target = "portal_pg_" + uuid.uuid4().hex
            require_restore_target(pg.database_name, target)
            pg.create_database(target)  # CREATE fails on existing DB; target is never dropped.
            assert_same_snapshot(report["backup"]["dump_sha256"], digest(dump), "dump checksum")
            pg.run([pg.executable("pg_restore"), *common, f"--dbname={target}", "--exit-on-error",
                    "--single-transaction", "--no-acl", dump], name="pg-restore", env=pg_tools_env, timeout=180)
            restored_database = snapshot_database(pg, target)
            report["restored_database_before_probes"] = restored_database
            assert_same_snapshot(database, restored_database, "restored database data/structure/owners/sequences")
            restored_storage = pg.run_dir / "restored-storage"
            copy_verified(backup_dir / "private", restored_storage, files)
            restored_env = {**fixture_env, "PORTAL_DB_NAME": target,
                            "PORTAL_PRODUCT_STORAGE_ROOT": str(restored_storage / "product"),
                            "PORTAL_HR_STORAGE_ROOT": str(restored_storage / "hr"),
                            "PORTAL_TENDER_STORAGE_ROOT": str(restored_storage / "tender")}
            pg.run([sys.executable, ROOT / "backend/manage.py", "migrate", "--check"],
                   name="restored-migration-check", env=restored_env, timeout=120)
            pg.run([sys.executable, fixture, "verify", "--run-dir", pg.run_dir],
                   name="verify-restored-business", env=restored_env, timeout=180)
            assert_same_snapshot(files, file_manifest(restored_storage), "private files after rejected resurrection attempts")
            report["restore"] = {"source_database": pg.database_name, "target_database": target,
                                 "same_owned_cluster": True, "database_equal_before_http_probes": True,
                                 "private_files_equal": True,
                                 "migration_check": True,
                                 "application": json.loads((pg.run_dir / "application-verification.json").read_text(encoding="utf-8"))}
            report["metrics"] = {"observed_rto_seconds": round(time.perf_counter() - recovery_started, 3),
                                 "rto_scope": "create empty DB + pg_restore + private copy + integrity/migration/business gates",
                                 "observed_rpo_seconds": 0,
                                 "rpo_scope": "all committed synthetic fixture writes before backup, quiescent writers only",
                                 "production_slo_claim": False}
            report["source_after"] = frozen_sources()
            assert_same_snapshot(report["source_before"], report["source_after"], "frozen source files")
            pg.evidence["outcome"] = "PASS"
            report["outcome"] = "PASS"
    except Exception as error:
        report["outcome"] = "FAIL"
        report["error"] = redact(f"{type(error).__name__}: {error}", pg._sensitive)
    finally:
        report["finished_at"] = now()
        report["cleanup"] = pg.evidence.get("cleanup", {})
        if not report["cleanup"].get("verified"):
            report["outcome"] = "FAIL"
        if pg.run_dir.is_dir():
            (pg.run_dir / "recovery-report.json").write_text(
                redact(json.dumps(report, indent=2, ensure_ascii=False), pg._sensitive) + "\n", encoding="utf-8")
        print(json.dumps({"outcome": report["outcome"], "report": str(pg.run_dir / "recovery-report.json"),
                          "metrics": report.get("metrics"), "cleanup_verified": report["cleanup"].get("verified", False),
                          "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["outcome"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", required=True)
    args = parser.parse_args(argv)
    return run(args.postgres_bin)


if __name__ == "__main__":
    raise SystemExit(main())
