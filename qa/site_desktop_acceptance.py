"""PC-only smoke checks against isolated Django, synthetic data and a built UI.

Run with an existing Playwright Python: python qa/site_desktop_acceptance.py --dist PATH
Evidence stays in .runtime. No production credentials, services or external calls.
"""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {
    "product": ["", "/opportunities", "/projects", "/sources", "/outputs", "/history",
                "/templates", "/knowledge", "/presales", "/new", "/documents"],
    "cost": ["", "/estimate", "/quota"],
    "hr": ["", "/job", "/resumes", "/results", "/history", "/probation", "/profile", "/channels"],
    "business": ["", "/finance", "/presales", "/engineering", "/projects"],
    "ops": ["", "/people", "/usage", "/modules", "/issues", "/maintenance"],
}


def serve(args):
    run = Path(os.environ["DESKTOP_QA_RUN"])
    for key in list(os.environ):
        if key.startswith("PORTAL_") or key == "DJANGO_SETTINGS_MODULE":
            del os.environ[key]
    os.environ.update({
        "DJANGO_SETTINGS_MODULE": "config.settings", "PORTAL_SECRET_KEY": secrets.token_urlsafe(48),
        "PORTAL_DEBUG": "1", "PORTAL_HTTPS": "0", "PORTAL_ALLOWED_HOSTS": "127.0.0.1,localhost",
        "PORTAL_SQLITE_PATH": str(run / "test.sqlite3"), "PORTAL_FRONTEND_DIST": str(args.dist),
        "PORTAL_PRODUCT_STORAGE_ROOT": str(run / "product"), "PORTAL_HR_STORAGE_ROOT": str(run / "hr"),
        "PORTAL_ENGINEERING_STORAGE_ROOT": str(run / "engineering"),
        "PORTAL_TENDER_STORAGE_ROOT": str(run / "tender"), "PORTAL_PRODUCT_P1_ENABLED": "1",
    })
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()
    from django.core.management import call_command
    from portal.models import Role, User
    from portal.product_models import DocumentTask
    from portal.product_service import append_revision
    from portal.business_boards import parse_csv, _record_revision
    from portal.business_models import BusinessLedgerGrant, BusinessLedgerWorkbook
    call_command("migrate", verbosity=0)
    call_command("seed_portal", verbosity=0)
    users = {}
    for department, role in {"product": "product", "cost": "engineering", "hr": "hr",
                             "business": "general_manager", "ops": "platform_admin"}.items():
        user = User.objects.create_user(username=f"desktop-{department}", password=os.environ["DESKTOP_QA_PASSWORD"],
                                       display_name=f"合成验收-{department}", must_change_password=False)
        user.roles.add(Role.objects.get(code=role))
        users[department] = user
    BusinessLedgerGrant.objects.create(user=users["product"], department="presales", can_edit=True,
                                       can_submit=True, can_publish=True)
    task_ids = []
    for index, stage in enumerate(("BLUEPRINT", "FINAL_REVIEW", "INTAKE")):
        task = DocumentTask.objects.create(owner=users["product"], title=f"合成项目{index} · 园区供配电改造与智慧运维项目",
                                          state="DRAFT" if stage == "INTAKE" else "WAITING_REVIEW", stage=stage,
                                          idempotency_key=f"desktop-{index}", payload_hash="a" * 64)
        revision = append_revision(task, "input", {"project": task.title, "requirements": "仅测试页面显示",
            "background": "合成测试数据", "items": [], "conditions": [], "sources": [], "issues": []}, actor=users["product"])
        task.input_version = revision.version
        task.save()
        task_ids.append(str(task.pk))
    csvs = {
        "engineering": "项目编号,项目名称,状态,负责人,计划完成日期,完成进度\nP1,合成建设项目,实施中,测试人员,2026-10-01,50\n",
        "finance": "项目编号,项目名称,合同金额,已收金额,应收日期\nP1,合成建设项目,1000.10,200.05,2026-10-01\n",
        "presales": "项目编号,项目名称,状态,负责人,预计金额\nP1,合成建设项目,报价,测试人员,3000.10\n",
    }
    for department, content in csvs.items():
        workbook = BusinessLedgerWorkbook.objects.create(created_by=users["business"], updated_by=users["business"],
            department=department, state="published", revision=1,
            source_name="合成验收数据", as_of=date(2026, 9, 29), records=parse_csv(content.encode(), department))
        _record_revision(workbook, users["business"], "publish")
    (run / "ready.json").write_text(json.dumps({"tasks": task_ids}), encoding="utf-8")
    from waitress import serve as run_server
    from config.wsgi import application
    run_server(application, host="127.0.0.1", port=args.port, threads=4)


def check(args):
    from playwright.sync_api import sync_playwright, expect
    if not (args.dist / "index.html").is_file():
        raise RuntimeError("Build the frontend first")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    run = ROOT / ".runtime" / "site-optimization" / ("desktop-" + uuid.uuid4().hex[:12])
    run.mkdir(parents=True)
    password = secrets.token_urlsafe(24) + "!9"
    environment = dict(os.environ, DESKTOP_QA_RUN=str(run), DESKTOP_QA_PASSWORD=password)
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    results = {"checks": [], "page_errors": [], "unexpected_external_requests": [], "console_errors": []}
    base = f"http://127.0.0.1:{port}"
    with (run / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(python), str(Path(__file__).resolve()), "--serve", "--port", str(port),
                                    "--dist", str(args.dist)], cwd=ROOT, env=environment, stdout=log,
                                   stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 60
            while not (run / "ready.json").exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Isolated server failed; see server.log")
                time.sleep(0.2)
            fixture = json.loads((run / "ready.json").read_text(encoding="utf-8"))
            with sync_playwright() as pw:
                candidates = [Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                              Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")]
                executable = next((str(path) for path in candidates if path.is_file()), None)
                browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
                for department, sections in ROUTES.items():
                    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
                    def local_only(route):
                        if route.request.url.startswith(base + "/"):
                            route.continue_()
                        else:
                            results["unexpected_external_requests"].append(route.request.url)
                            route.abort()
                    context.route("**/*", local_only)
                    csrf = context.request.get(base + "/api/csrf/").json()["csrfToken"]
                    login = context.request.post(base + "/api/login/", data={"username": f"desktop-{department}", "password": password},
                                                 headers={"X-CSRFToken": csrf})
                    assert login.status == 200, login.status
                    page = context.new_page()
                    page.on("pageerror", lambda error: results["page_errors"].append(str(error)))
                    page.on("console", lambda message: results["console_errors"].append(message.text) if message.type == "error" else None)
                    page.on("dialog", lambda dialog: dialog.dismiss())
                    paths = [("/ops" if department == "ops" else f"/centers/{department}") + section for section in sections]
                    if department == "product":
                        paths.extend([f"/centers/product/projects?task={fixture['tasks'][2]}",
                                      f"/centers/product/documents?task={fixture['tasks'][2]}"])
                    if department == "ops":
                        paths.extend(f"/preview/{code}" for code in ("product", "cost", "hr", "business"))
                    for path in paths:
                        page.goto(base + path, wait_until="networkidle")
                        main = page.locator("#ops-main, #center-main")
                        expect(main).to_be_visible()
                        expect(page.get_by_text("正在加载工作页面…", exact=True)).to_have_count(0)
                        expect(page.get_by_text("页面暂时无法显示", exact=True)).to_have_count(0)
                        assert "企业统一门户" in page.title()
                        if department == "business" and path.endswith(("/finance", "/presales", "/engineering")):
                            expect(main.get_by_role("link", name="合成建设项目", exact=True).first).to_be_visible()
                        for width, height in ((1280, 800), (1440, 900), (1920, 1080)):
                            page.set_viewport_size({"width": width, "height": height})
                            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (path, width, "viewport overflow")
                            assert main.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (path, width, "workspace overflow")
                            results["checks"].append({"path": path, "width": width})
                        if path == paths[0]:
                            page.screenshot(path=str(run / f"{department}-desktop.png"))
                        print("PASS", path, flush=True)
                    context.close()
                browser.close()
            assert not results["page_errors"], results["page_errors"]
            assert not results["unexpected_external_requests"], results["unexpected_external_requests"]
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            (run / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print("Evidence:", run, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    args.dist = args.dist.resolve()
    serve(args) if args.serve else check(args)
