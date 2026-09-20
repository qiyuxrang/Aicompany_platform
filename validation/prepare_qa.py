import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from portal.models import Module, Role, User
from portal.security import audit

if not settings.DEBUG or settings.DATABASES["default"]["NAME"] != "portal_phase1":
    raise SystemExit("仅允许在隔离 portal_phase1 调试数据库准备验收账号。")
target = ROOT / ".runtime" / "qa-credentials.json"
rotate = sys.argv[1:] == ["--rotate-closed-fixtures"]
if sys.argv[1:] and not rotate:
    raise SystemExit("未知参数。")
if rotate:
    registered = json.loads(target.read_text(encoding="utf-8"))
    existing = list(User.objects.filter(username__in=registered))
    expected = {"qa_product", "qa_engineering", "qa_hr", "qa_manager", "qa_admin", "qa_empty", "qa_multi", "qa_first"}
    if (set(registered) != expected or len(existing) != 8
            or any(user.is_active or user.has_usable_password() or user.display_name != "隔离验收账号" for user in existing)):
        raise SystemExit("只能轮换本次已停用且密码不可用的完整验收账号组。")
elif target.exists() or User.objects.filter(username__startswith="qa_").exists():
    raise SystemExit("验收账号已存在，不覆盖。")
call_command("seed_portal")
accounts = {}
with transaction.atomic():
    for username, role_codes in {
        "qa_product": ["product"], "qa_engineering": ["engineering"], "qa_hr": ["hr"],
        "qa_manager": ["general_manager"], "qa_admin": ["platform_admin"],
        "qa_empty": [], "qa_multi": ["product", "engineering"], "qa_first": ["product"],
    }.items():
        password = secrets.token_urlsafe(24)
        if rotate:
            user = User.objects.get(username=username)
            user.is_active = True
            user.must_change_password = username == "qa_first"
            user.set_password(password)
            user.save(update_fields=["is_active", "must_change_password", "password"])
        else:
            user = User.objects.create_user(username=username, password=password,
                display_name="隔离验收账号", must_change_password=username == "qa_first")
        user.roles.set(Role.objects.filter(code__in=role_codes))
        accounts[username] = password
        audit(None, "qa_account_create", user.pk)
    module = Module.objects.get(code="business")
    module.url = "http://127.0.0.1:8018/"
    module.status = "navigation"
    module.description = "进入原经营看板，保留原系统登录；可信身份与只读数据尚未验证。"
    module.full_clean()
    module.save()
    audit(None, "qa_navigation_configure", module.pk, changes=["url", "status", "description"])
target.write_text(json.dumps(accounts), encoding="utf-8")
print("已建立隔离验收账号与导航配置；随机凭据仅保存在被忽略的受控运行目录，未打印。")
