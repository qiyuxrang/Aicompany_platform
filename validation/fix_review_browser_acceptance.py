import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def login(page, base_url, username, password):
    page.goto(f"{base_url}/login", wait_until="networkidle")
    page.get_by_label("用户名").fill(username)
    page.get_by_label("密码").fill(password)
    page.get_by_role("button", name="登录").click()
    page.wait_for_function("window.location.pathname !== '/login'")
    require(page.url.rstrip("/") == base_url.rstrip("/"), f"{username} login did not reach workbench: {page.url}")


def selected_value(page, selector):
    page.locator(selector).wait_for()
    return page.locator(selector).input_value()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    password = os.environ["FIX_REVIEW_BROWSER_PASSWORD"]
    args.evidence.mkdir(parents=True, exist_ok=True)

    bundle = "\n".join(path.read_text(encoding="utf-8") for path in args.dist.glob("assets/*.js"))
    for expected in ("开发中 · 已隔离", "工作摘要", "P2 未启动", "D-01 / D-04 待批准"):
        require(expected in bundle, f"built bundle missing: {expected}")
    require("工程部准备工作台" not in bundle, "built bundle still contains old engineering workspace")

    checks = []
    console_errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        owner = browser.new_page(viewport={"width": 1440, "height": 1000})
        login(owner, args.base_url, fixture["owner_username"], password)
        owner.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        owner.get_by_role("heading", name="工作摘要").wait_for()
        checks.append("Django-served bundle renders real work summary")

        owner.goto(f"{args.base_url}/centers/product/documents?task={fixture['product_target']}", wait_until="networkidle")
        require(selected_value(owner, "#document-task") == fixture["product_target"], "second product task deep link selected wrong object")
        checks.append("second product task deep link")

        owner.goto(f"{args.base_url}/centers/product/documents?task={fixture['product_target']}&artifact={fixture['foreign_artifact']}", wait_until="networkidle")
        owner.get_by_text("指定成果不存在、无权访问或不属于当前产品任务。").wait_for()
        require(owner.get_by_text(fixture["product_target_title"]).count() == 0, "cross-task artifact exposed selected task detail")
        checks.append("cross-task artifact rejected")

        owner.goto(f"{args.base_url}/centers/hr/job?task={fixture['job_target']}", wait_until="networkidle")
        require(selected_value(owner, "#hr-job-task") == fixture["job_target"], "second JD deep link selected wrong object")
        checks.append("second JD deep link")

        for section in ("", "/estimate", "/quota"):
            owner.goto(f"{args.base_url}/centers/cost{section}", wait_until="networkidle")
            owner.get_by_text("开发中 · 已隔离").wait_for()
            button_text = " ".join(owner.locator("button").all_text_contents())
            require(not any(label in button_text for label in ("录入", "测算", "定额推荐", "保存", "生成")),
                    f"engineering operation exposed at {section or '/'}: {button_text}")
        checks.append("three engineering entries isolated")

        owner.goto(f"{args.base_url}/centers/product/feasibility", wait_until="networkidle")
        owner.get_by_text("产品事业部 · P2 未启动", exact=True).wait_for()
        owner.goto(f"{args.base_url}/centers/hr/resumes", wait_until="networkidle")
        owner.get_by_text("D-01 / D-04 待批准", exact=True).wait_for()
        checks.append("P2 and resume blockers rendered")
        owner.screenshot(path=args.evidence / "owner-workspaces.png", full_page=True)

        manager = browser.new_page(viewport={"width": 1440, "height": 1000})
        login(manager, args.base_url, fixture["manager_username"], password)
        manager.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        link = manager.locator(f'a[href="/centers/hr/probation?case={fixture["case_target"]}"]')
        link.wait_for()
        link.click()
        manager.wait_for_load_state("networkidle")
        require(selected_value(manager, "#probation-case") == fixture["case_target"], "manager summary deep link selected wrong case")
        manager.locator("h4").filter(has_text=fixture["case_target_employee"]).wait_for()
        require(manager.get_by_role("heading", name="创建转正事项").count() == 0, "manager received create permission in UI")
        require(manager.get_by_text("标准岗位说明", exact=True).count() == 0, "manager received JD navigation")
        checks.append("assigned manager summary and scoped probation entry")

        manager.goto(f"{args.base_url}/centers/hr/probation?case=00000000-0000-0000-0000-000000000000", wait_until="networkidle")
        manager.get_by_text("指定转正事项不存在或当前账号无权访问。").wait_for()
        require(manager.get_by_text(fixture["case_other_employee"]).count() == 0, "invalid case fell back to another record")
        require(manager.locator("#probation-case").count() == 0, "invalid case exposed the probation list")
        checks.append("invalid case does not fall back")
        manager.screenshot(path=args.evidence / "manager-probation.png", full_page=True)
        browser.close()

    print(json.dumps({"checks": checks, "console_errors": console_errors}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
