import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKTREE = ROOT.parent / "ledger-portal-bridge-isolated"
RUN_ID = os.environ.get("PORTAL_BRIDGE_VALIDATION_RUN", "")
if RUN_ID and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", RUN_ID):
    raise SystemExit("Invalid isolated validation run identifier")
RUNTIME = ROOT / ".runtime" / "bridge-runs" / RUN_ID if RUN_ID else ROOT / ".runtime"
EVIDENCE = ROOT / "docs/evidence" / ("bridge-" + RUN_ID if RUN_ID else "closure-integration")
MANIFEST = RUNTIME / "bridge-environment.json"
PORTAL_PYTHON = ROOT / ".venv/Scripts/python.exe"
LEDGER_PYTHON = ROOT.parent / "监控看板/.venv/Scripts/python.exe"


def read_env(path):
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))


def environment(side):
    if side not in {"portal", "ledger"}:
        raise ValueError("Unknown side")
    values = read_env(RUNTIME / f"bridge-{side}.env")
    prefix = "PORTAL_DB_" if side == "portal" else "POSTGRES_"
    name_key = "PORTAL_DB_NAME" if side == "portal" else "POSTGRES_DB"
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if (values[name_key] != manifest[side + "_database"] or not values[name_key].startswith(side + "_bridge_e2e_")
            or values[prefix + "HOST"] != "127.0.0.1" or values[prefix + "PORT"] != "55438"):
        raise SystemExit("Only registered isolated bridge databases are allowed")
    return values


def setup(side):
    os.environ.update(environment(side))
    sys.path.insert(0, str((ROOT if side == "portal" else WORKTREE) / "backend"))
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django
    django.setup()


def provision():
    import psycopg
    from psycopg import sql

    if MANIFEST.exists() or any((RUNTIME / f"bridge-{side}.env").exists() for side in ("portal", "ledger")):
        raise SystemExit("Existing resources: refuse overwrite")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if RUN_ID:
        subprocess.run(["icacls", str(RUNTIME), "/inheritance:r", "/grant:r", os.environ["USERNAME"] + ":(OI)(CI)(F)"], check=True, capture_output=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    original = read_env(ROOT / ".runtime/ops-validation.env")
    if original["PORTAL_DB_HOST"] != "127.0.0.1" or original["PORTAL_DB_PORT"] != "55438":
        raise SystemExit("Unexpected database cluster")
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    names = {side + "_database": side + "_bridge_e2e_" + suffix for side in ("portal", "ledger")}
    with psycopg.connect(dbname="postgres", user=original["PORTAL_DB_USER"], password=original["PORTAL_DB_PASSWORD"],
                        host="127.0.0.1", port=55438, autocommit=True) as connection:
        for name in names.values():
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    bridge_secret = secrets.token_urlsafe(48)
    portal = {**original, "PORTAL_DB_NAME": names["portal_database"], "PORTAL_SECRET_KEY": secrets.token_urlsafe(48),
              "PORTAL_DEBUG": "1", "PORTAL_HTTPS": "0", "PORTAL_BEHIND_PROXY": "0",
              "PORTAL_ALLOWED_HOSTS": "127.0.0.1,localhost,testserver", "PORTAL_CSRF_TRUSTED_ORIGINS": "http://127.0.0.1:18310",
              "PORTAL_TRUSTED_MODULE_ORIGINS": "http://127.0.0.1:18318,http://127.0.0.1:18319",
              "PORTAL_BUSINESS_SUMMARY_URL": "http://127.0.0.1:18318/api/portal-bridge/summary/",
              "PORTAL_INTEGRATION_SECRET": bridge_secret,
              "PORTAL_FRONTEND_DIST": str(ROOT / ".runtime/ops-frontend-dist").replace("\\", "/")}
    ledger = {"DJANGO_DEBUG": "1", "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
              "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost,testserver", "DJANGO_CSRF_ORIGINS": "http://127.0.0.1:18318",
              "LEDGER_DATA_DIR": str(RUNTIME / "bridge-ledger-data").replace("\\", "/"),
              "POSTGRES_DB": names["ledger_database"], "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": "55438",
              "POSTGRES_USER": original["PORTAL_DB_USER"], "POSTGRES_PASSWORD": original["PORTAL_DB_PASSWORD"],
              "LEDGER_DEMO_MODE": "0", "LEDGER_PUBLIC_REGISTRATION": "0", "LEDGER_LAN_MODE": "0",
              "LEDGER_PORTAL_BRIDGE_ENABLED": "1", "LEDGER_PORTAL_BRIDGE_SECRET": bridge_secret,
              "LEDGER_PORTAL_REDEEM_URL": "http://127.0.0.1:18310/api/integration/redeem/"}
    for side, values in (("portal", portal), ("ledger", ledger)):
        path = RUNTIME / f"bridge-{side}.env"
        path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", os.environ["USERNAME"] + ":(F)"], check=True, capture_output=True)
    MANIFEST.write_text(json.dumps({**names, "portal_port": 18310, "ledger_port": 18318, "worktree": str(WORKTREE)}, indent=2), encoding="utf-8")
    print("Created two new isolated PostgreSQL databases; existing databases untouched")


def seed(side):
    setup(side)
    credentials_path = RUNTIME / f"bridge-{side}-credentials.json"
    if credentials_path.exists():
        raise SystemExit("Refuse to overwrite fixture credentials")
    if side == "ledger":
        from ledger.models import Project, User
        if User.objects.exists() or Project.objects.exists():
            raise SystemExit("Fixture database is not empty")
        accounts = {}
        identifiers = {}
        for name, department, role, active, first in (
            ("manager", "manager", "manager", True, False), ("sales", "sales", "department", True, False),
            ("engineering", "engineering", "department", True, False), ("inactive", "sales", "department", False, False),
            ("first", "sales", "department", True, True),
        ):
            password = secrets.token_urlsafe(24)
            username = "bridge_" + name
            user = User.objects.create_user(username=username, password=password, display_name="隔离测试" + name,
                                           role=role, department=department, is_active=active, must_change_password=first)
            accounts[username] = password
            identifiers[name] = user.pk
        projects = []
        for code, departments in (("sales-only", ["sales"]), ("engineering-only", ["engineering"]),
                                  ("shared", ["sales", "engineering"]), ("finance-only", ["finance"])):
            project = Project.objects.create(code="BRIDGE-" + code, name="隔离合成项目-" + code, customer="合成客户", departments=departments)
            projects.append({"id": project.pk, "name": project.name, "departments": departments})
        fixture = {"accounts": identifiers, "projects": projects, "boundary": "New isolated synthetic fixtures only; no company financial records"}
        (RUNTIME / "bridge-ledger-fixtures.json").write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        from django.core.management import call_command
        from portal.models import BusinessMapping, Module, Role, User
        if User.objects.exists():
            raise SystemExit("Fixture database is not empty")
        call_command("seed_portal")
        module = Module.objects.get(code="business")
        module.url = "http://127.0.0.1:18318/"
        module.status = "navigation"
        module.save()
        legacy = json.loads((RUNTIME / "bridge-ledger-fixtures.json").read_text(encoding="utf-8"))
        accounts = {}
        for name in (*legacy["accounts"], "nomap", "admin"):
            password = secrets.token_urlsafe(24)
            username = "bridge_" + name
            user = User.objects.create_user(username=username, password=password, display_name="隔离验收" + name, must_change_password=False)
            user.roles.add(Role.objects.get(code="platform_admin" if name == "admin" else "general_manager"))
            if name in legacy["accounts"]:
                BusinessMapping.objects.create(user=user, external_user_id=str(legacy["accounts"][name]))
            accounts[username] = password
    credentials_path.write_text(json.dumps(accounts, indent=2), encoding="utf-8")
    subprocess.run(["icacls", str(credentials_path), "/inheritance:r", "/grant:r", os.environ["USERNAME"] + ":(F)"], check=True, capture_output=True)
    print(f"Seeded {side} isolated fixtures")


def run(side, arguments):
    values = environment(side)
    directory = ROOT if side == "portal" else WORKTREE
    python = PORTAL_PYTHON if side == "portal" else LEDGER_PYTHON
    child = {**os.environ, **values, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(directory / "backend")}
    if arguments == ["serve"]:
        arguments = ["runserver", f"127.0.0.1:{18310 if side == 'portal' else 18318}", "--noreload", "--nothreading"] if side == "portal" else ["runserver", "127.0.0.1:18318", "--noreload"]
        if side == "portal":
            command = [str(python), "-m", "waitress", "--listen=127.0.0.1:18310", "--threads=8", "config.wsgi:application"]
        else:
            command = [str(python), str(directory / "backend/manage.py"), *arguments]
    elif arguments == ["seed"]:
        command = [str(python), str(Path(__file__)), "seed", side]
    else:
        command = [str(python), str(directory / "backend/manage.py"), *arguments]
    return subprocess.call(command, env=child, cwd=directory)


if __name__ == "__main__":
    operation = sys.argv[1]
    if operation == "provision":
        provision()
    elif operation == "seed":
        seed(sys.argv[2])
    elif operation == "run":
        raise SystemExit(run(sys.argv[2], sys.argv[3:]))
