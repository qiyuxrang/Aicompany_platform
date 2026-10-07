"""Playwright worker; configuration and temporary credentials arrive via stdin."""
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parent))
from semantic import csrf_rejected, permission_rejected, expected_page_error, expected_console_error, expected_workspace_url, KNOWLEDGE_SCOPE_DETAIL, KNOWLEDGE_DENIED_ROUTES

ROUTES = {
    "product": ["", "/opportunities", "/projects", "/sources", "/outputs", "/history",
                "/templates", "/knowledge", "/presales", "/new", "/documents", "/assistant"],
    "engineering": ["", "/estimate", "/quota"],
    "hr": ["", "/job", "/resumes", "/results", "/history", "/probation", "/profile", "/channels", "/assistant"],
    "finance": ["", "/assistant"],
    "manager": ["", "/finance", "/presales", "/engineering", "/projects", "/ledgers", "/assistant"],
    "ops": ["", "/people", "/usage", "/modules", "/issues", "/maintenance"],
}
HOMES = {"product": "/centers/product", "engineering": "/centers/cost", "hr": "/centers/hr",
         "finance": "/centers/finance", "manager": "/centers/business", "ops": "/ops"}
POSITIVE_API = {"product": "/api/product/tasks/", "engineering": "/api/engineering/jobs/",
                "hr": "/api/hr/recruitment/requests/", "finance": "/api/business/ledgers/finance/",
                "manager": "/api/business/boards/finance/", "ops": "/api/ops/overview/"}
DENIED_API = {"product": "/api/ops/overview/", "engineering": "/api/hr/recruitment/requests/",
              "hr": "/api/engineering/jobs/", "finance": "/api/engineering/jobs/",
              "manager": "/api/engineering/jobs/", "ops": "/api/engineering/jobs/"}


def main():
    config = json.load(sys.stdin)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from process_job import join_owned_job
    join_owned_job(config["job_name"])
    from playwright.sync_api import sync_playwright, expect
    base = config["base"]
    origin = urlsplit(base)
    if origin.scheme != "http" or origin.hostname != "127.0.0.1" or not origin.port or origin.path:
        raise ValueError("Browser target must be an explicit loopback origin")
    evidence = Path(config["run_dir"])
    evidence_root = Path(__file__).resolve().parents[2] / ".runtime/browser-acceptance"
    if evidence.resolve() != (evidence_root / config["uuid"]).resolve() or not evidence.is_dir():
        raise ValueError("Evidence must use the owned UUID directory")
    password = config["password"]
    new_password = config["new_password"]
    result = {"outcome": "RUNNING", "checks": [], "routes": [], "screenshots": [],
              "page_errors": [], "console_errors": [], "http_errors": [], "request_failures": [],
              "external_requests": [], "cleanup": {}, "roles_required": list(ROUTES),
              "limitations": ["Agent disabled: no Native Runtime, model or long-task execution proof",
                              "Knowledge grants intentionally empty: scope-denial proof only, no authorized RAG proof",
                              "Engineering quota D-05 deferred", "No Office visual approval or production cloud proof"]}
    active = {"role": "unauthenticated", "path": "/login", "expected_503": False,
              "knowledge_scope_unassigned": config.get("knowledge_scope_unassigned") is True,
              "fixture_origin": base}
    browser = None
    playwright = None

    def mark(name, **details):
        result["checks"].append({"name": name, **details})
        print("PASS", name, flush=True)

    def capture(page, name):
        filename = name + ".png"
        page.screenshot(path=str(evidence / filename), full_page=True)
        result["screenshots"].append(filename)

    def context_for(browser):
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN",
                                      reduced_motion="reduce", service_workers="block")

        def local_only(route):
            request_origin = urlsplit(route.request.url)
            if (request_origin.scheme, request_origin.netloc) == (origin.scheme, origin.netloc):
                route.continue_()
            else:
                result["external_requests"].append({"url": route.request.url, **active})
                route.abort()

        context.route("**/*", local_only)
        page = context.new_page()
        page.set_default_timeout(config["page_timeout_ms"])
        page.on("pageerror", lambda error: result["page_errors"].append({"error": str(error), **active}))
        page.on("console", lambda message: result["console_errors"].append(
            {"text": message.text, "location": message.location, **active})
                if message.type == "error" else None)
        def record_response(response):
            if response.status < 400:
                return
            error = {"url": response.url, "status": response.status, **active}
            try:
                error["body"] = response.json()
            except Exception:
                error["body"] = None
            error["expected_reason"] = expected_page_error(error)
            result["http_errors"].append(error)
        page.on("response", record_response)
        page.on("requestfailed", lambda request: result["request_failures"].append(
            {"url": request.url, "failure": request.failure, **active}))
        page.on("dialog", lambda dialog: dialog.dismiss())
        return context, page

    def login(page, role, username=None, supplied_password=None):
        active.update(role=role, path="/login", expected_503=False)
        page.goto(base + "/login", wait_until="networkidle")
        page.locator("#username").fill(username or "browser-" + role)
        page.locator("#password").fill(supplied_password or password)
        page.get_by_role("button", name="登录", exact=True).click()

    def workspace(page, path):
        active.update(path=path, expected_503=path.endswith("/assistant"))
        response_start = len(result["http_errors"])
        page.goto(base + path, wait_until="networkidle")
        expect(page).to_have_url(expected_workspace_url(base, path))
        main = page.locator("#ops-main, #center-main")
        expect(main).to_be_visible()
        expect(main.get_by_role("heading").first).to_be_visible()
        if path == "/centers/product/opportunities":
            expect(page.get_by_role("combobox", name="公告类型", exact=True)).to_have_value("procurement")
        if active["role"] == "product" and path in KNOWLEDGE_DENIED_ROUTES:
            denial = next((error for error in reversed(result["http_errors"][response_start:])
                           if error["path"] == path and error["role"] == "product"
                           and expected_page_error(error) == "knowledge_unassigned_scope_denied"), None)
            assert denial is not None, "Expected actual HTTP knowledge scope denial"
            if path == "/centers/product/sources":
                expect(main.get_by_role("heading", name="无法访问知识库资料", exact=True)).to_be_visible()
                expect(main.get_by_role("alert").get_by_text(
                    "当前账号未获得这些知识库资料的访问权限，或授权已撤销。", exact=True)).to_be_visible()
                expect(main.get_by_role("heading", name="暂无可浏览的知识库", exact=True)).to_have_count(0)
                expect(main.locator(".km-datasets, .km-documents")).to_have_count(0)
            else:
                expect(main.get_by_role("alert").get_by_text(
                    KNOWLEDGE_SCOPE_DETAIL + "（scope_revoked）", exact=True)).to_be_visible()
                expect(main.get_by_role("heading", name="知识库暂不可用", exact=True)).to_be_visible()
                expect(main.get_by_text("访问权限已变化，旧回答与引用已清除。请确认授权后重新检查。", exact=True)).to_be_visible()
                expect(main.get_by_role("button", name="新建会话", exact=True)).to_be_disabled()
                expect(main.get_by_label("你的问题", exact=True)).to_be_disabled()
                expect(main.get_by_role("button", name="发送问题", exact=True)).to_be_disabled()
                expect(main.get_by_text("暂无已授权知识库。", exact=True)).to_have_count(0)
                expect(main.locator(".pk-turn, .pk-sources")).to_have_count(0)
            mark("knowledge-unassigned-scope-denied-without-fake-empty", role="product", path=path,
                 status=denial["status"], response=denial["body"])
        for text in ("页面暂时无法显示", "正在加载工作页面…", "工作台暂不可用", "页面不存在"):
            expect(page.get_by_text(text, exact=True)).to_have_count(0)
        if path.endswith("/assistant"):
            expect(main.get_by_role("alert").filter(has_text="Agent 功能未启用").first).to_be_visible()
            expect(main.get_by_role("heading", name="把要做的事告诉助手")).to_be_visible()
        for width, height in ((1280, 800), (1440, 900), (1920, 1080)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (path, width, "page overflow")
            assert main.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (path, width, "workspace overflow")
            result["routes"].append({"role": active["role"], "path": path, "actual_url": page.url,
                                     "width": width, "passed": True})
        page.set_viewport_size({"width": 1440, "height": 900})
        capture(page, active["role"] + "-" + path.strip("/").replace("/", "-"))
        mark("authorized-route", role=active["role"], path=path)

    try:
        if config.get("knowledge_scope_unassigned") is not True:
            raise ValueError("Verified owned fixture with empty knowledge grants required")
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(executable_path=config["browser_executable"], headless=True)
        result["browser_version"] = browser.version
        # A real first-login UI changes the stored password and invalidates its session.
        context, page = context_for(browser)
        login(page, "hr", "browser-first-login")
        dialog = page.get_by_role("dialog", name="首次登录，请先修改密码")
        expect(dialog).to_be_visible()
        expect(dialog.locator("input[type=password]")).to_have_count(2)
        capture(page, "first-login-required-password-change")
        page.get_by_label("新密码", exact=True).fill(new_password)
        page.get_by_label("确认新密码", exact=True).fill(new_password)
        page.get_by_role("button", name="确认修改", exact=True).click()
        page.wait_for_url(base + "/login")
        assert context.request.get(base + "/api/me/").status in (401, 403)
        login(page, "hr", "browser-first-login", new_password)
        page.wait_for_url(base + HOMES["hr"])
        mark("first-login-password-change-and-session-invalidation")
        context.close()
        for role, sections in ROUTES.items():
            context, page = context_for(browser)
            login(page, role)
            page.wait_for_url(base + HOMES[role])
            me = context.request.get(base + "/api/me/")
            assert me.status == 200 and me.json()["username"] == "browser-" + role
            mark("ui-login-real-http-session", role=role)
            assert context.request.get(base + POSITIVE_API[role]).status == 200
            denied = context.request.get(base + DENIED_API[role])
            assert permission_rejected(role, denied.status, denied.json()), "Unexpected permission-denial schema"
            mark("positive-and-cross-role-denied-api", role=role)
            # Missing CSRF must be rejected by the actual middleware, with no logout.
            denied = context.request.post(base + "/api/logout/", data={})
            assert csrf_rejected(denied.status, denied.json()), "Expected exact missing-CSRF denial"
            assert context.request.get(base + "/api/me/").status == 200
            mark("mutation-without-csrf-denied", role=role)
            for section in sections:
                workspace(page, HOMES[role] + section)
                if role == "hr" and section == "/history":
                    expect(page.get_by_text("暂无可访问的招聘记录。", exact=True)).to_be_visible()
                if role == "engineering" and section == "/quota":
                    expect(page.get_by_text("未实现", exact=True).first).to_be_visible()
                if role == "manager":
                    expect(page.get_by_role("button", name="新增记录", exact=True)).to_have_count(0)
                    expect(page.get_by_role("button", name="导入到草稿", exact=True)).to_have_count(0)
            if role == "engineering":
                active.update(path=HOMES[role], expected_503=True)
                context.route("**/api/engineering/jobs/", lambda route: route.fulfill(
                    status=503, content_type="application/json", body=json.dumps({"detail": "合成验收列表失败"})))
                page.goto(base + HOMES[role], wait_until="networkidle")
                expect(page.get_by_role("alert").filter(has_text="任务列表读取失败")).to_be_visible()
                expect(page.get_by_text("暂无服务端任务。", exact=True)).to_have_count(0)
                capture(page, "engineering-first-list-failure")
                context.unroute("**/api/engineering/jobs/")
                active["expected_503"] = False
                page.reload(wait_until="networkidle")
                expect(page.get_by_text("暂无服务端任务。", exact=True)).to_be_visible()
                mark("engineering-failure-is-not-empty-and-recovery-is-real")
            if role == "ops":
                for center in ("product", "cost", "hr", "finance", "business"):
                    workspace(page, "/preview/" + center)
                mark("admin-preview-does-not-grant-business-api-rights")
            token = context.request.get(base + "/api/csrf/").json()["csrfToken"]
            assert context.request.post(base + "/api/logout/", data={}, headers={"X-CSRFToken": token}).status == 204
            assert context.request.get(base + "/api/me/").status in (401, 403)
            mark("csrf-protected-logout-invalidates-session", role=role)
            context.close()
        unexpected_http = [error for error in result["http_errors"]
                           if not expected_page_error(error)]
        unexpected_console = [error for error in result["console_errors"]
                              if not expected_console_error(error, result["http_errors"])]
        result["unexpected_http_errors"] = unexpected_http
        result["unexpected_console_errors"] = unexpected_console
        assert not result["page_errors"], "Browser runtime errors recorded"
        assert not result["external_requests"], "Unexpected external requests blocked"
        assert not result["request_failures"], "Failed browser requests recorded"
        assert not unexpected_http, "Unexpected HTTP errors recorded"
        assert not unexpected_console, "Unexpected console errors recorded"
        assert set(item["role"] for item in result["routes"]) == set(ROUTES)
        mark("no-unexpected-browser-errors-and-all-six-roles-covered")
        # Close while Playwright's driver context is still alive.
        browser.close()
        result["outcome"] = "PASS"
    except Exception as error:
        result["outcome"] = "FAIL"
        result["error"] = type(error).__name__ + ": " + str(error)
        if browser and browser.is_connected():
            for context in browser.contexts:
                for index, page in enumerate(context.pages):
                    try:
                        capture(page, "failure-" + active["role"] + "-" + str(index))
                    except Exception:
                        pass
    finally:
        try:
            if browser and browser.is_connected():
                browser.close()
            result["cleanup"]["playwright_browser_closed"] = not browser or not browser.is_connected()
        except Exception as error:
            result["outcome"] = "FAIL"
            result["cleanup"].update(playwright_browser_closed=False, error=type(error).__name__)
        if playwright:
            try:
                playwright.stop()
            except Exception as error:
                result["outcome"] = "FAIL"
                result["cleanup"]["driver_stop_error"] = type(error).__name__
        # Neither request bodies, auth cookies nor credentials are retained.
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        for secret in (password, new_password):
            encoded = encoded.replace(secret, "<redacted>")
        (evidence / "browser-result.json").write_text(encoded, encoding="utf-8")
    return 0 if result["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
