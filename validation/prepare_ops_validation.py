import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".runtime/ops-validation.env"
CREDENTIALS = ROOT / ".runtime/ops-credentials.json"


def read_environment(path):
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))


def provision():
    if ENV_FILE.exists():
        raise SystemExit("隔离配置已存在，不覆盖、不重建数据库。")
    original = read_environment(ROOT / ".runtime/validation.env")
    if (original.get("PORTAL_DB_HOST") != "127.0.0.1" or original.get("PORTAL_DB_PORT") != "55438"
            or original.get("PORTAL_DB_NAME") != "portal_phase1" or original.get("PORTAL_DEBUG") != "1"):
        raise SystemExit("仅允许从既有本机隔离验证PG配置派生新库。")
    name = "portal_ops_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    with psycopg.connect(dbname="postgres", user=original["PORTAL_DB_USER"], password=original["PORTAL_DB_PASSWORD"],
                        host="127.0.0.1", port=55438, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    values = {**original, "PORTAL_SECRET_KEY": secrets.token_urlsafe(48), "PORTAL_DB_NAME": name,
              "PORTAL_DEBUG": "1", "PORTAL_HTTPS": "0", "PORTAL_BEHIND_PROXY": "0",
              "PORTAL_ALLOWED_HOSTS": "127.0.0.1,localhost,testserver",
              "PORTAL_CSRF_TRUSTED_ORIGINS": "http://127.0.0.1:18210",
              "PORTAL_TRUSTED_MODULE_ORIGINS": "http://127.0.0.1:8018,http://127.0.0.1:18218",
              "PORTAL_BUSINESS_SUMMARY_URL": "", "PORTAL_INTEGRATION_SECRET": "",
              "PORTAL_FRONTEND_DIST": str(ROOT / ".runtime/ops-frontend-dist").replace("\\", "/")}
    ENV_FILE.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    print(f"Created new isolated database {name}; no existing database changed. Restrict environment-file ACL before use.")


def setup():
    sys.path.insert(0, str(ROOT / "backend"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from django.conf import settings
    database = settings.DATABASES["default"]
    if (not settings.DEBUG or not str(database["NAME"]).startswith("portal_ops_") or database["HOST"] != "127.0.0.1"
            or str(database["PORT"]) != "55438" or read_environment(ENV_FILE)["PORTAL_DB_NAME"] != database["NAME"]):
        raise SystemExit("只能操作本次登记的全新运维验收库。")


def seed():
    setup()
    from django.core.management import call_command
    from django.db import transaction
    from portal.models import Module, Role, User
    from portal.security import audit
    if CREDENTIALS.exists() or User.objects.exists():
        raise SystemExit("验收库或凭据已有用户，不覆盖。")
    call_command("seed_portal")
    accounts = {}
    with transaction.atomic():
        for username, display_name, role, first, active in (
            ("ops_admin", "运维验收管理员", "platform_admin", False, True),
            ("ops_product", "产品验收账号", "product", False, True),
            ("ops_manager", "经营验收账号", "general_manager", False, True),
            ("ops_first", "首次改密验收", "product", True, True),
            ("ops_inactive", "停用验收账号", "engineering", False, False),
        ):
            password = secrets.token_urlsafe(24)
            user = User.objects.create_user(username=username, password=password, display_name=display_name,
                                            must_change_password=first, is_active=active)
            user.roles.add(Role.objects.get(code=role))
            accounts[username] = password
            audit(None, "ops_fixture_create", user.pk)
        module = Module.objects.get(code="business")
        module.url = "http://127.0.0.1:8018/"
        module.status = "navigation"
        module.description = "隔离验收入口：原经营网页保留原登录；不接可信数据或SSO。"
        module.full_clean()
        module.save()
        audit(None, "ops_fixture_module", module.pk, changes=["url", "status", "description"])
    CREDENTIALS.write_text(json.dumps(accounts), encoding="utf-8")
    print("Created five synthetic accounts; no fake login, usage, issue or business records. Credentials not printed.")


def close():
    setup()
    from django.db import transaction
    from portal.models import User
    from portal.security import audit
    credentials = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
    if any(not username.startswith("ops_") for username in credentials):
        raise SystemExit("凭据登记中存在非验收账号，拒绝。")
    with transaction.atomic():
        users = list(User.objects.select_for_update().filter(username__in=credentials))
        for user in users:
            user.is_active = False
            user.set_unusable_password()
            user.save(update_fields=["is_active", "password"])
            audit(None, "ops_fixture_close", user.pk, changes=["is_active", "password_invalidated"])
    print(f"Closed {len(users)} registered ops fixture accounts; no existing business accounts touched.")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    if action not in ("provision", "seed", "close"):
        raise SystemExit("使用 provision/seed/close；seed及close必须由uv加载ops-validation.env。")
    {"provision": provision, "seed": seed, "close": close}[action]()
