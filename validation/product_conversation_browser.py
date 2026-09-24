import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    password = os.environ["FIX_REVIEW_BROWSER_PASSWORD"]
    args.evidence.mkdir(parents=True, exist_ok=True)
    checks = []
    console_errors = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.goto(f"{args.base_url}/login", wait_until="networkidle")
        page.get_by_label("用户名").fill(fixture["owner_username"])
        page.get_by_label("密码").fill(password)
        page.get_by_role("button", name="登录").click()
        page.wait_for_function("window.location.pathname !== '/login'")
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )

        page.goto(f"{args.base_url}/centers/product/documents", wait_until="networkidle")
        page.get_by_role("heading", name="项目成果助手").wait_for()
        page.get_by_label("描述任务、文件或资料路径").fill(
            "浏览器验收：结合上传资料生成技术方案、可研报告和汇报 PPT"
        )
        page.locator('input[type="file"]').first.set_input_files(
            {
                "name": "浏览器验收背景.txt",
                "mimeType": "text/plain",
                "buffer": "项目背景：建设企业统一知识与成果生成工作台。".encode("utf-8"),
            }
        )
        page.get_by_role("button", name="发送并创建任务").click()
        page.get_by_role("region", name="对话任务工作区").wait_for(timeout=30_000)
        page.get_by_text("浏览器验收背景.txt", exact=True).wait_for(timeout=30_000)
        checks.append("single composer creates task and uploads attachment")

        advanced = page.locator("details.product-advanced-workbench")
        require(advanced.count() == 1, "advanced workbench is missing")
        require(not advanced.evaluate("element => element.open"), "advanced workbench should be collapsed")
        page.get_by_text("P2 未授权，暂不生成", exact=True).first.wait_for()
        checks.append("P2 outputs stay visibly locked and advanced controls stay collapsed")

        followup = page.get_by_label("继续补充要求或资料路径")
        followup.fill("补充要求：技术方案需包含实施阶段、风险和验收标准。")
        page.get_by_role("button", name="发送", exact=True).click()
        page.wait_for_function(
            "element => element.value === ''",
            arg=followup.element_handle(),
            timeout=30_000,
        )
        page.get_by_text(
            "补充要求：技术方案需包含实施阶段、风险和验收标准。", exact=False
        ).first.wait_for()
        checks.append("existing task accepts a follow-up without creating a second task")

        page.screenshot(path=args.evidence / "product-conversation-workspace.png", full_page=True)
        browser.close()

    require(not console_errors, f"browser console errors: {console_errors}")
    result = {"checks": checks, "console_errors": console_errors}
    (args.evidence / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
