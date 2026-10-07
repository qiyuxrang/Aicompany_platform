"""Stdin gate: join the UUID named Job before Django or business imports."""
import argparse
import json
import os
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    from .contracts import require, validate_gate, write_json
    line = sys.stdin.readline(65537)
    require(len(line) <= 65536 and line.endswith("\n"), "bounded_stdin_gate_required")
    gate = validate_gate(json.loads(line), Path(args.run_dir), args.label)
    from qa.browser_acceptance.process_job import join_owned_job
    join_owned_job(gate["job_name"])
    from qa.release_pipeline.identity import process_identity
    write_json(Path(args.run_dir) / (args.label + "-ready.json"), {
        "fixture_id": gate["fixture_id"], "label": args.label, "job_name": gate["job_name"],
        "identity": process_identity(os.getpid()), "parent_pid": os.getppid()})
    if gate["kind"] == "control":
        require(sys.stdin.readline(32) == "stop\n", "control_stop_required")
        return 0
    # Only clean environment supplied by our own PortablePostgres context.
    require(os.environ.get("PORTAL_DB_NAME", "").startswith("portal_pg_"), "isolated_postgresql_required")
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections
    require(settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
            and settings.ROOT_URLCONF == "config.urls", "normal_postgresql_settings_required")
    try:
        from config.database import postgres_pool_evidence
        connections["default"].ensure_connection()
        write_json(Path(args.run_dir) / (args.label + "-pool.json"), {
            "fixture_id": gate["fixture_id"], "label": args.label,
            "postgres_pool": postgres_pool_evidence(connections["default"])})
        options = {"once": gate["once"]}
        if gate["kind"] == "hr":
            options["concurrency"] = 1
        call_command("run_hr_worker" if gate["kind"] == "hr" else "run_product_worker", **options)
    finally:
        connections.close_all()
    write_json(Path(args.run_dir) / (args.label + "-exit.json"), {"exit_code": 0, "fixture_id": gate["fixture_id"]})
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"result": "FAIL", "error_class": type(error).__name__}), flush=True)
        raise SystemExit(1)
