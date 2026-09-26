"""Isolated, synthetic-only browser acceptance server. Never use a real business DB.
Run with the repository's Python environment. Credentials live only under .runtime.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "workbench-browser"
RUNTIME.mkdir(parents=True, exist_ok=True)
RUN_ID = uuid.uuid4().hex
os.environ.pop("PORTAL_DB_NAME", None)
os.environ.update({
    "DJANGO_SETTINGS_MODULE": "config.settings",
    "PORTAL_SECRET_KEY": secrets.token_urlsafe(48), "PORTAL_DEBUG": "1", "PORTAL_HTTPS": "0",
    "PORTAL_ALLOWED_HOSTS": "127.0.0.1,localhost", "PORTAL_SQLITE_PATH": str(RUNTIME / f"{RUN_ID}.sqlite3"),
    "PORTAL_PRODUCT_STORAGE_ROOT": str(RUNTIME / f"{RUN_ID}-files"),
    "PORTAL_FRONTEND_DIST": str(ROOT / "frontend" / "dist"),
    "PORTAL_PRODUCT_P1_ENABLED": "1", "PORTAL_PRODUCT_MODEL_CALLS_ALLOWED": "0",
    "PORTAL_PRODUCT_RETRIEVAL_ENABLED": "0", "PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED": "0",
    "PORTAL_PRODUCT_OFFICE_RENDER_ENABLED": "0", "PORTAL_PRODUCT_REVIEWER_IDS": "2",
    "PORTAL_PRODUCT_COST_POLICY": "{}", "PORTAL_PRODUCT_TEMPLATE_APPROVAL": "{}",
    "PORTAL_MODEL_GATEWAY_URL": "", "PORTAL_PRODUCT_RETRIEVAL_AUTHORIZATIONS": "{}",
})
sys.path.insert(0, str(ROOT / "backend"))
import django

django.setup()
from django.core.management import call_command
from portal.models import Role, User
from portal.product_models import DocumentTask
from portal.product_service import append_revision

call_command("migrate", verbosity=0)
call_command("seed_portal", verbosity=0)
password = secrets.token_urlsafe(24) + "!9"
for identifier, name in ((1, "项目负责人"), (2, "蓝图审核人"), (3, "无项目授权人员")):
    user = User.objects.create_user(pk=identifier, username=f"workbench-{identifier}", password=password,
                                    display_name=name, must_change_password=False)
    user.roles.set(Role.objects.filter(code="product"))
owner, reviewer = User.objects.get(pk=1), User.objects.get(pk=2)
project_ids = []
for index, (title, state, stage) in enumerate([
    ("榆林高新区污水处理厂建设项目", "WAITING_REVIEW", "BLUEPRINT"),
    ("西安经开区产业园供配电改造", "DRAFT", "INTAKE"),
    ("陕北新能源示范基地配套工程", "WAITING_INPUT", "INTAKE"),
    ("智慧园区综合能源管理项目", "FAILED", "WRITING"),
]):
    title += " · 演示"
    task = DocumentTask.objects.create(owner=owner, reviewer=reviewer, title=title, state=state, stage=stage,
                                       idempotency_key=f"synthetic-{index}", payload_hash="a" * 64)
    revision = append_revision(task, "input", {"project": title, "requirements": "建设稳定可靠的供配电系统，完善监测与维护能力。", "background": "合成验收资料，不对应真实客户或实际工程。", "items": [], "conditions": ["不改变原有系统的运行边界"], "sources": [], "issues": []}, actor=owner)
    task.input_version = revision.version
    task.save()
    if state == "WAITING_REVIEW":
        blueprint = append_revision(task, "blueprint", {"purpose": "为园区建设可靠的供电系统，覆盖高低压配电、动力和照明，明确实施边界与验收条件。", "audience": "项目管理与工程评审人员", "chapters": [{"id": f"chapter-{number}", "title": name, "scope": scope, "source_ids": []} for number, (name, scope) in enumerate([("建设背景与目标", "梳理当前运行情况和建设需求"), ("供配电系统方案", "明确系统结构、设备与边界条件"), ("实施与运维计划", "分阶段实施、风险控制和运行维护")], 1)], "conditions": [{"text": "不改变原有系统的运行边界", "type": "human"}, {"text": "关键参数须结合现场条件复核", "type": "human"}], "missing": ["现场设备清单待补充"], "conflicts": [], "template_version": "frozen-original-v1"}, actor=owner)
        task.blueprint_version = blueprint.version
        task.save()
    project_ids.append(str(task.pk))
(RUNTIME / "connection.json").write_text(json.dumps({"base_url": "http://127.0.0.1:18743", "password": password, "run_id": RUN_ID, "projects": project_ids}, ensure_ascii=False), encoding="utf-8")
print(f"Synthetic workbench server ready on 127.0.0.1:18743; run={RUN_ID}", flush=True)
from waitress import serve
from config.wsgi import application
serve(application, host="127.0.0.1", port=18743, threads=4)
