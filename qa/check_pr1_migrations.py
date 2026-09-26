"""Verify fresh/product-first/HR-first migration paths without touching business DBs."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
TARGET = ("portal", "0016_merge_product_intake_hr_recruitment")


def scenario(name, database):
    os.environ.pop("PORTAL_DB_NAME", None)
    os.environ.update(DJANGO_SETTINGS_MODULE="config.settings", PORTAL_DEBUG="1", PORTAL_HTTPS="0",
        PORTAL_SECRET_KEY=secrets.token_urlsafe(48), PORTAL_SQLITE_PATH=str(database),
        PORTAL_PRODUCT_MODEL_CALLS_ALLOWED="0", PORTAL_PRODUCT_RETRIEVAL_ENABLED="0",
        PORTAL_MODEL_GATEWAY_URL="", PORTAL_BUSINESS_SUMMARY_URL="")
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    leaf = {"product": ("portal", "0012_documentrevision_extraction"),
            "hr": ("portal", "0015_hr_screening_batches")}.get(name)
    sentinel_id = None
    if leaf:
        executor.migrate([leaf])
        old_apps = executor.loader.project_state([leaf]).apps
        owner = old_apps.get_model("portal", "User").objects.create(username="migration-sentinel", password="!")
        if name == "product":
            sentinel = old_apps.get_model("portal", "DocumentTask").objects.create(
                owner=owner, title="保留产品项目", idempotency_key="migration-check", payload_hash="a" * 64)
        else:
            sentinel = old_apps.get_model("portal", "RecruitmentRequest").objects.create(
                created_by=owner, updated_by=owner, position_name="保留招聘需求")
        sentinel_id = sentinel.pk
    executor = MigrationExecutor(connection)
    assert executor.loader.graph.leaf_nodes("portal") == [TARGET]
    executor.migrate(executor.loader.graph.leaf_nodes())
    apps = executor.loader.project_state([TARGET]).apps
    assert "extraction" in dict(apps.get_model("portal", "DocumentRevision")._meta.get_field("kind").choices)
    assert apps.get_model("portal", "ResumeScreeningBatch")._meta.db_table in connection.introspection.table_names()
    if name == "product":
        assert apps.get_model("portal", "DocumentTask").objects.get(pk=sentinel_id).title == "保留产品项目"
    elif name == "hr":
        assert apps.get_model("portal", "RecruitmentRequest").objects.get(pk=sentinel_id).position_name == "保留招聘需求"
    connection.close()
    print(f"PASS {name}: converged, existing records preserved", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("fresh", "product", "hr"))
    parser.add_argument("--database")
    args = parser.parse_args()
    if args.scenario:
        scenario(args.scenario, Path(args.database))
        return
    runtime = ROOT / ".runtime" / "pr1-migration-check"
    runtime.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=runtime) as directory:
        for name in ("fresh", "product", "hr"):
            subprocess.run([sys.executable, __file__, "--scenario", name,
                            "--database", str(Path(directory) / f"{name}.sqlite3")], check=True)


if __name__ == "__main__":
    main()
