import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


def check():
    timestamp = "2026-09-30T08:00:00Z"
    user = {"id": 7, "username": "product", "display_name": "产品测试人员", "department_code": "product",
            "roles": [{"code": "product", "name": "产品人员"}], "is_platform_admin": False,
            "must_change_password": False}
    module = {"code": "product", "name": "产品事业部", "description": "产品工作台", "enabled": True, "status": "verified"}
    work = {"id": "w1", "conversation_id": "c1", "goal": "整理项目技术方案", "state": "running",
            "current_requirement_version": 2, "public_summary": "正在核对设备清单与引用来源。",
            "requirements": [{"version": 2, "content": "保留设备原始规格；标注缺失参数。", "received_at": timestamp, "applied_at": None}],
            "result_references": [{"domain_type": "product_artifact", "object_id": "artifact-1", "revision": 1,
                                   "public_summary": "技术方案历史版本", "current": False}], "business_references": []}
    messages = [{"id": f"m{number}", "role": "user" if number % 2 else "assistant",
                 "content": "请按已提供的设备清单编制方案，缺失信息单独列出。" if number % 2 else "已接收资料。工作状态与要求版本以右侧记录为准。",
                 "attachment_references": [], "created_at": timestamp, "work_id": "w1"} for number in range(1, 9)]
    errors = []
    mutations = []

    def route_api(route):
        path = urlparse(route.request.url).path
        params = parse_qs(urlparse(route.request.url).query)
        if route.request.method != "GET":
            mutations.append(path)
        responses = {
            "/api/me/": user, "/api/modules/": [module], "/api/modules/product/": module,
            "/api/agent/projects/": {"items": [], "total": 0},
            "/api/agent/conversations/": {"items": [{"id": "c1", "created_at": timestamp}], "total": 1},
            "/api/agent/work/": {"items": [work], "total": 1}, "/api/agent/work/w1/": work,
            "/api/agent/conversations/c1/messages/": {"items": messages, "total": len(messages)},
            "/api/agent/work/w1/events/": {"items": [] if params.get("after_seq", ["0"])[0] != "0" else
                [{"id": "e1", "seq": 1, "type": "requirement_received", "payload": {"summary": "要求 v2 已接收，尚未生效"}, "created_at": timestamp}], "total": 0 if params.get("after_seq", ["0"])[0] != "0" else 1},
            "/api/agent/skills/": {"items": []}, "/api/agent/installations/": {"items": []},
        }
        route.fulfill(status=200 if path in responses else 503, content_type="application/json",
                      body=json.dumps(responses.get(path, {"detail": "此测试未启用该接口"}), ensure_ascii=False))

    with sync_playwright() as runner:
        browser = runner.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/**", route_api)
            page.goto("http://127.0.0.1:5193/centers/product/assistant?conversation=c1&work=w1")
            page.wait_for_load_state("networkidle")
            page.get_by_text("要求 v2 · 已接收，待生效", exact=True).wait_for()
            sidebar_before = page.locator(".workspace-sidebar").bounding_box()
            metrics = page.evaluate("""() => {
              const main = document.querySelector('.center-main');
              return {documentOverflow: document.documentElement.scrollWidth > innerWidth,
                mainScrolls: main.scrollHeight > main.clientHeight,
                sidebarTop: document.querySelector('.workspace-sidebar').getBoundingClientRect().top};
            }""")
            page.locator(".center-main").evaluate("element => element.scrollTop = 450")
            sidebar_after = page.locator(".workspace-sidebar").bounding_box()
            assert sidebar_before == sidebar_after, "Sidebar moved when right content scrolled"
            assert metrics["mainScrolls"] and not metrics["documentOverflow"], metrics
            page.locator(".center-main").evaluate("element => element.scrollTop = 0")
            if "--screenshots" in sys.argv:
                page.screenshot(path=str(Path(__file__).with_suffix(".png")), full_page=True)
            assert page.get_by_text("已生效", exact=True).count() == 0
            page.get_by_role("button", name="新会话", exact=True).click()
            page.wait_for_load_state("networkidle")
            page.get_by_label("输入消息", exact=True).fill("继续核对来源，不启动真实模型")
            assert page.get_by_label("输入消息", exact=True).input_value() == "继续核对来源，不启动真实模型"
            page.set_viewport_size({"width": 1280, "height": 800})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.get_by_role("button", name="本人技能", exact=True).click()
            page.get_by_text("暂无可用技能，或目录正在读取。", exact=True).wait_for()
            page.get_by_role("button", name="本人技能", exact=True).click()
            page.get_by_label("输入消息", exact=True).fill("")
            if "--screenshots" in sys.argv:
                page.screenshot(path=str(Path(__file__).with_name("AgentWorkspace.initial.png")), full_page=True)
            assert not mutations, mutations
            assert not errors, errors
            print(json.dumps({"browser": browser.version, "viewport": "1440x1000", "metrics": metrics,
                              "sidebar_fixed": True, "initial_1280x800": True, "page_errors": errors, "mutations": mutations,
                              "evidence": "real Chromium rendering with intercepted API fixtures; no live backend/model"}))
        finally:
            browser.close()


if __name__ == "__main__":
    check()
