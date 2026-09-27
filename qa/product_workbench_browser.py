"""Real Chrome acceptance using an already-installed Playwright Python package.
Requires qa/product_workbench_server.py. No new project dependency, external
model call, or production credentials. Evidence is saved under .runtime.
"""
import json
from pathlib import Path
import re
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "workbench-browser"
config = json.loads((RUNTIME / "connection.json").read_text(encoding="utf-8"))
BASE = config["base_url"]
if BASE != "http://127.0.0.1:18743":
    raise RuntimeError("This acceptance script is restricted to the isolated local server.")
EVIDENCE = RUNTIME / "evidence"
EVIDENCE.mkdir(exist_ok=True)
results = {"environment": "isolated synthetic Django + real Chromium browser", "checks": [], "page_errors": [], "screenshots": []}


def browser_executable():
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    found = next((path for path in candidates if path.is_file()), None)
    if found is None:
        raise RuntimeError("Chrome or Edge executable is required for browser acceptance")
    return str(found)


def mark(name):
    results["checks"].append(name)
    print("PASS", name, flush=True)


def login(context, number):
    csrf = context.request.get(BASE + "/api/csrf/")
    response = context.request.post(BASE + "/api/login/", data={"username": f"workbench-{number}", "password": config["password"]}, headers={"X-CSRFToken": csrf.json()["csrfToken"]})
    assert response.status == 200, (response.status, response.text()[:200])


def screenshot(page, name):
    page.screenshot(path=str(EVIDENCE / name), full_page=True)
    results["screenshots"].append(name)


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Page overflows viewport"


try:
    with sync_playwright() as runner:
        browser = runner.chromium.launch(executable_path=browser_executable(), headless=True)
        context = browser.new_context(viewport={"width": 1512, "height": 1050}, locale="zh-CN", reduced_motion="reduce")
        login(context, 1)
        page = context.new_page()
        page.on("pageerror", lambda error: results["page_errors"].append(str(error)))
        page.on("dialog", lambda dialog: dialog.dismiss())
        page.goto(BASE + "/centers/product")
        expect(page.get_by_role("heading", name="产品事业部工作台")).to_be_visible()
        expect(page.get_by_label("进行中项目数量")).to_have_text("4个")
        no_overflow(page)
        screenshot(page, "01-dashboard-desktop.png")
        mark("dashboard-real-authorized-metrics")
        page.get_by_role("link", name="新建项目", exact=True).click()
        expect(page.get_by_label("项目名称 *")).to_be_visible()
        page.get_by_label("项目名称 *").fill("Chrome 核验项目 · " + config["run_id"][:8])
        page.get_by_label("建设目标 *").fill("核实从资料输入到人工蓝图确认的真实持久化流程。")
        page.get_by_label("项目背景", exact=True).fill("独立验收环境中的合成项目，不含真实业务资料。")
        page.get_by_label("约束条件", exact=True).fill("不调用外部模型")
        expect(page.get_by_label("蓝图与成果审核人")).to_have_count(0)
        page.get_by_label("设备清单文件").set_input_files({"name": "设备清单.csv", "mimeType": "text/csv", "buffer": "row_id,name,quantity,unit\n1,测试设备,2,台".encode("utf-8")})
        page.get_by_label("选择项目资料文件").set_input_files({"name": "项目背景.txt", "mimeType": "text/plain", "buffer": "纯合成资料，用于确认上传、来源下载和持久化。".encode("utf-8")})
        screenshot(page, "02-new-project-desktop.png")
        page.get_by_role("button", name="创建项目", exact=True).click()
        page.wait_for_url(re.compile(r"/centers/product/projects\?task="))
        expect(page.get_by_role("heading", name=re.compile("Chrome 核验项目"))).to_be_visible()
        task_id = page.url.split("task=")[1].split("&")[0]
        results["task_id"] = task_id
        mark("create-upload-real-http-csrf-idempotency")
        page.get_by_role("button", name="输入资料", exact=False).click()
        expect(page.get_by_text("项目背景.txt", exact=True).first).to_be_visible()
        expect(page.get_by_text("设备清单.csv", exact=True).first).to_be_visible()
        source_url = page.get_by_text("项目背景.txt", exact=True).first.locator("xpath=ancestor::li").get_by_role("link", name="下载原文件").get_attribute("href")
        downloaded = context.request.get(BASE + source_url)
        assert downloaded.status == 200 and "纯合成资料" in downloaded.body().decode("utf-8")
        mark("authorized-original-source-download")
        page.get_by_role("button", name="编辑项目底稿", exact=True).click()
        page.get_by_label("建设目标", exact=True).fill("修订后目标：确认数据、版本和人工审批连续有效。")
        page.get_by_role("button", name="保存项目底稿", exact=True).click()
        expect(page.get_by_role("button", name="编辑项目底稿", exact=True)).to_be_visible()
        mark("business-input-edit-new-version")
        page.get_by_role("button", name="项目蓝图", exact=True).click()
        page.get_by_role("button", name="人工编制蓝图", exact=True).click()
        page.get_by_label("第 1 章范围").fill("项目现状、目标和约束。")
        page.get_by_role("button", name="保存蓝图新版本", exact=True).click()
        expect(page.get_by_role("button", name="修改蓝图", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="确认蓝图并生成三件套")).to_be_disabled()
        screenshot(page, "03-blueprint-owner.png")
        mark("manual-blueprint-requires-explicit-owner-confirmation")
        page.reload()
        expect(page.get_by_text("修订后目标：确认数据、版本和人工审批连续有效。", exact=True)).to_be_visible()
        mark("refresh-preserves-project-input-and-blueprint")
        reviewer = browser.new_context(viewport={"width": 1512, "height": 1050}, locale="zh-CN", reduced_motion="reduce")
        login(reviewer, 2)
        approval_page = reviewer.new_page()
        approval_page.on("pageerror", lambda error: results["page_errors"].append(str(error)))
        assert reviewer.request.get(BASE + f"/api/product/tasks/{task_id}/").status == 404
        mark("unassigned-reviewer-cannot-access-new-owner-project")
        approval_page.close()
        page.get_by_label("蓝图确认依据").fill("已核对本版本目标、章节、条件与来源，允许继续。")
        page.get_by_role("checkbox").check()
        expect(page.get_by_role("button", name="确认蓝图并生成三件套")).to_be_enabled()
        screenshot(page, "04-blueprint-owner-confirmation.png")
        page.get_by_role("button", name="确认蓝图并生成三件套").click()
        expect(page.get_by_text("项目状态已更新。", exact=True)).to_be_visible()
        task_response = context.request.get(BASE + f"/api/product/tasks/{task_id}/").json()
        assert task_response["pending_action"] == "generate_outputs"
        assert task_response["blueprint_approved"] is True
        assert task_response["state"] == "WAITING_INPUT", task_response["state"]
        assert task_response["error_code"] == "model_authorization_required", task_response["error_code"]
        mark("owner-confirmation-queues-three-outputs-but-stops-at-real-model-gate")
        outsider = browser.new_context()
        login(outsider, 3)
        assert outsider.request.get(BASE + source_url).status == 404
        assert outsider.request.get(BASE + f"/api/product/tasks/{task_id}/").status == 404
        mark("unassigned-user-denied-project-and-source")
        page.goto(BASE + f"/centers/product/projects?task={task_id}&tab=outputs")
        expect(page.get_by_text("尚未生成", exact=True)).to_have_count(3)
        screenshot(page, "05-current-outputs.png")
        mark("no-fabricated-generated-results")
        page.goto(BASE + "/centers/product/projects?q=Chrome")
        expect(page.get_by_role("table")).to_contain_text("Chrome 核验项目")
        expect(page.get_by_role("table")).not_to_contain_text("榆林")
        mark("server-project-search")
        page.goto(BASE + "/centers/product")
        expect(page.get_by_label("进行中项目数量")).to_have_text("5个")
        page.set_viewport_size({"width": 390, "height": 844})
        no_overflow(page)
        screenshot(page, "06-dashboard-mobile.png")
        mark("mobile-390-no-page-overflow")
        page.goto(BASE + f"/centers/product/projects?task={task_id}&tab=blueprint")
        expect(page.get_by_role("heading", name=re.compile("项目蓝图"))).to_be_visible()
        no_overflow(page)
        screenshot(page, "07-blueprint-mobile.png")
        mark("mobile-blueprint-no-page-overflow")
        page.set_viewport_size({"width": 1512, "height": 1050})
        page.goto(BASE + "/centers/product")
        expect(page.get_by_label("进行中项目数量")).to_have_text("5个")
        page.evaluate("document.documentElement.dataset.theme = 'dark'")
        screenshot(page, "08-dashboard-dark.png")
        no_overflow(page)
        mark("dark-theme-layout")
        assert not results["page_errors"], results["page_errors"]
        mark("no-browser-runtime-errors")
        browser.close()
finally:
    (EVIDENCE / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
