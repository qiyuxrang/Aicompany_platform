import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings
from django.db import transaction
from portal.models import User
from portal.security import audit

if (not settings.DEBUG or settings.DATABASES["default"]["NAME"] != "portal_phase1"
        or settings.DATABASES["default"]["HOST"] != "127.0.0.1"
        or str(settings.DATABASES["default"]["PORT"]) != "55438"):
    raise SystemExit("仅允许本工作区独立验收数据库。")
credentials = json.loads((ROOT / ".runtime/qa-credentials.json").read_text(encoding="utf-8"))
if not credentials or any(not name.startswith("qa_") for name in credentials):
    raise SystemExit("只处理本次验收登记的qa账号。")
with transaction.atomic():
    users = list(User.objects.select_for_update().filter(username__in=credentials))
    for user in users:
        user.is_active = False
        user.set_unusable_password()
        user.save(update_fields=["is_active", "password"])
        audit(None, "qa_account_close", user.pk, changes=["is_active", "password_invalidated"])
report = {"timestamp": datetime.now(timezone.utc).isoformat(), "closed": len(users),
          "remaining_active": User.objects.filter(username__in=credentials, is_active=True).count(),
          "boundary": "仅本期隔离库qa账号停用且密码失效；未触碰任何旧系统原生账号。已有备份仅限隔离恢复，不能作为业务账号来源。"}
(ROOT / "docs/evidence/qa-closure.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"QA accounts closed: {report['closed']}; active remaining: {report['remaining_active']}")
