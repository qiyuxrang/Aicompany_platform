import json
import hashlib
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def postgres_tests():
    import django
    from django.conf import settings
    from django.core.management import call_command

    django.setup()
    database = settings.DATABASES["default"]
    if database["HOST"] != "127.0.0.1" or not database["NAME"].startswith("portal_p1_"):
        raise SystemExit("Only the registered isolated P1 PostgreSQL configuration is accepted.")
    name = "test_p1_increment_" + uuid.uuid4().hex[:16]
    database["TEST"] = {**database.get("TEST", {}), "NAME": name}
    print(json.dumps({"kind": "isolated_postgres_tests", "database": name, "real_services": False}), flush=True)
    call_command("test", "portal", interactive=False, verbosity=1)
    call_command("makemigrations", check=True, dry_run=True)


def word_check():
    import django
    from django.test import override_settings

    django.setup()
    from portal.product_documents import frozen_pack, render_candidate, render_draft
    from portal.product_rendering import render_office

    task = SimpleNamespace(pk=uuid.uuid4(), title="产品技术方案隔离格式验证")
    input_revision = SimpleNamespace(pk=uuid.uuid4(), sha256="1" * 64, payload={
        "project": "合成项目", "requirements": "仅验证已有模板与工具，不作为业务技术方案", "conditions": ["仅使用合成资料"],
        "items": [{"row_id": "r1", "name": "测试设备", "quantity": "2", "unit": "台"}],
        "background": "无公司资料，无外发", "sources": [], "issues": [],
    })
    blueprint = SimpleNamespace(payload={"template_version": "frozen-original-v1", "missing": [], "conflicts": []})
    chapters = [SimpleNamespace(sha256=str(index) * 64, payload={"chapter_id": f"chapter-{index}", "title": title,
        "source_ids": ["r1"], "paragraphs": ["本样例仅使用合成清单的2台测试设备，用于核对模板、可编辑文件与逐页预览，不证明真实业务质量。"]})
        for index, title in enumerate(("项目概述", "实施范围", "验收说明"), 2)]
    template_hash = next(entry["sha256"] for entry in frozen_pack()["files"] if entry["path"].endswith("template.docx"))
    storage = ROOT / ".runtime" / "p1-increment-word" / uuid.uuid4().hex
    with override_settings(PRODUCT_STORAGE_ROOT=storage, PRODUCT_OFFICE_RENDER_ENABLED=True,
                           PRODUCT_TEMPLATE_APPROVAL={"template_hash": template_hash, "approval_ref": "ISOLATED-FORMAT-TEST-ONLY", "organization": "隔离测试单位"}):
        draft = render_draft(task, input_revision, blueprint, chapters)
        candidate = render_candidate(task, input_revision, blueprint, chapters)
        office = render_office(candidate["path"], candidate["sha256"])
    report = {"kind": "actual_office_synthetic_fixture", "real_model_calls": 0, "company_data": False,
              "business_approval": False, "visual_review": "pending", "storage": str(storage),
              "draft": draft, "candidate": candidate, "office": office}
    target = ROOT / "deliverables" / "企业平台SDD_20260921" / "qa" / "p1-increment-20260922" / "actual-word.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(target), "page_count": office["page_count"], "formal_business_pass": False}, ensure_ascii=False))


def source_snapshot():
    paths = {ROOT / name for name in (".gitattributes", ".env.example", "backend/config/settings.py", "backend/config/urls.py", "backend/portal/models.py")}
    for pattern in ("backend/portal/product_*.py", "backend/portal/tests/test_product*.py", "backend/portal/migrations/000[567]*.py", "frontend/src/product/*", "validation/p1_increment*.py"):
        paths.update(ROOT.glob(pattern))
    paths.update((ROOT / "backend/portal/product_assets").rglob("*"))
    entries = [{"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in sorted(paths) if path.is_file() and "__pycache__" not in path.parts]
    pack = ROOT / "backend/portal/product_assets/bj_docs"
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    frozen_ok = all(hashlib.sha256((pack / entry["path"]).read_bytes()).hexdigest() == entry["sha256"] for entry in manifest["files"])
    if not frozen_ok:
        raise SystemExit("Frozen document assets changed.")
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(), "working_tree_not_committed": True,
              "frozen_files_checked": len(manifest["files"]), "frozen_hashes_match": frozen_ok,
              "runtime_secrets_excluded": True, "files": entries}
    target = ROOT / "deliverables/企业平台SDD_20260921/qa/p1-increment-20260922/source-final.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"source_files": len(entries), "frozen_files_checked": len(manifest["files"]), "frozen_hashes_match": frozen_ok}))


if __name__ == "__main__":
    if sys.argv[1:] == ["snapshot"]:
        source_snapshot()
        raise SystemExit(0)
    if sys.argv[1:] == ["postgres-tests"]:
        postgres_tests()
    elif sys.argv[1:] == ["word"]:
        word_check()
    else:
        raise SystemExit("Use postgres-tests or word; no provision or production operations.")
