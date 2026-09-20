import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = "enterprise-portal-phase1-db"
SOURCE = "portal_phase1"
RESTORE = "portal_phase1_restore_final"
if (os.environ.get("PORTAL_DB_NAME") != SOURCE or os.environ.get("PORTAL_DB_HOST") != "127.0.0.1"
        or os.environ.get("PORTAL_DB_PORT") != "55438"):
    raise SystemExit("仅允许独立本机 Phase 1 验收数据库，不接收任意目标参数。")
backup_directory = ROOT / "backups" / "phase1-rehearsal-final"
backup_directory.mkdir(parents=True, exist_ok=True)


def docker(*arguments):
    subprocess.run(["docker", "exec", CONTAINER, *arguments], check=True, capture_output=True)


def connect(database):
    return psycopg.connect(dbname=database, user=os.environ["PORTAL_DB_USER"],
        password=os.environ["PORTAL_DB_PASSWORD"], host="127.0.0.1", port=55438)


def fingerprint(database):
    records = {}
    with connect(database) as connection:
        tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
        for (table,) in tables:
            rows = connection.execute(sql.SQL("SELECT row_to_json(record) FROM {} AS record").format(sql.Identifier(table))).fetchall()
            canonical = sorted(json.dumps(row[0], ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows)
            records[table] = {"rows": len(canonical), "sha256": hashlib.sha256("\n".join(canonical).encode()).hexdigest()}
    return records


with connect("postgres") as connection:
    if connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (RESTORE,)).fetchone():
        raise SystemExit("隔离恢复库已存在，不覆盖、不清理；请人工确认新的演练安排。")
source_before = fingerprint(SOURCE)
docker("pg_dump", "-U", os.environ["PORTAL_DB_USER"], "-d", SOURCE,
       "--format=custom", "--no-owner", "--no-acl", "--file=/tmp/portal-phase1-rehearsal.dump")
dump = backup_directory / "portal-phase1.dump"
subprocess.run(["docker", "cp", f"{CONTAINER}:/tmp/portal-phase1-rehearsal.dump", str(dump)], check=True, capture_output=True)
for name in ("validation.env", "postgres.env"):
    shutil.copyfile(ROOT / ".runtime" / name, backup_directory / name)
docker("createdb", "-U", os.environ["PORTAL_DB_USER"], RESTORE)
docker("pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "-U", os.environ["PORTAL_DB_USER"],
       "-d", RESTORE, "/tmp/portal-phase1-rehearsal.dump")
restored = fingerprint(RESTORE)
source_after = fingerprint(SOURCE)
report = {"timestamp": datetime.now(timezone.utc).isoformat(), "container": CONTAINER,
          "source": SOURCE, "restore": RESTORE, "tables": source_before,
          "all_tables_equal": source_before == restored, "source_unchanged": source_before == source_after,
          "dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "dump_bytes": dump.stat().st_size,
          "config_backup": "validation.env 与 postgres.env，位于被忽略的受限备份目录；不在报告中披露值"}
(ROOT / "docs/evidence/postgres-restore-final.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Restored {len(restored)} tables; equal={report['all_tables_equal']}; source_unchanged={report['source_unchanged']}")
if not report["all_tables_equal"] or not report["source_unchanged"]:
    raise SystemExit(1)
