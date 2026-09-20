import json
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8100"
credentials = json.loads((ROOT / ".runtime/qa-credentials.json").read_text(encoding="utf-8"))
results = []


class Client:
    def __init__(self):
        self.cookies = CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies))
        self.csrf = None

    def request(self, path, method="GET", data=None, csrf=True):
        headers = {"Content-Type": "application/json"}
        if csrf and self.csrf:
            headers["X-CSRFToken"] = self.csrf
        request = Request(BASE + path, method=method, headers=headers,
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            return response.status, body

    def login(self, username):
        self.csrf = self.request("/api/csrf/")[1]["csrfToken"]
        response = self.request("/api/login/", "POST", {"username": username, "password": credentials[username]})
        self.csrf = self.request("/api/csrf/")[1]["csrfToken"]
        return response


def check(name, condition):
    results.append({"case": name, "passed": bool(condition)})
    if not condition:
        raise AssertionError(name)


try:
    anonymous = Client()
    check("匿名受保护API为401", anonymous.request("/api/me/")[0] == 401)
    check("真实HTTP登录缺CSRF为403", anonymous.request("/api/login/", "POST", {"username": "qa_manager", "password": credentials["qa_manager"]}, csrf=False)[0] == 403)
    expected = {"qa_product": ["product"], "qa_engineering": ["cost"], "qa_hr": ["hr"],
                "qa_manager": ["business"], "qa_admin": [], "qa_empty": [], "qa_multi": ["cost", "product"]}
    for username, codes in expected.items():
        client = Client()
        check(username + "真实登录", client.login(username)[0] == 200)
        status, modules = client.request("/api/modules/")
        check(username + "服务端模块范围", status == 200 and sorted(module["code"] for module in modules) == sorted(codes))
        check(username + "Cookie隔离", {cookie.name for cookie in client.cookies} == {"enterprise_portal_csrf", "enterprise_portal_session"})
        if username == "qa_product":
            check("跨模块详情禁止", client.request("/api/modules/business/")[0] == 404)
            check("伪造用户角色参数无效", client.request("/api/modules/business/launch/", "POST", {"user_id": 1, "role": "general_manager"})[0] == 404)
            check("普通用户管理页面拒绝", client.request("/admin/")[0] == 403)
            check("待接入模块不能启动", client.request("/api/modules/product/launch/", "POST", {})[0] == 409)
        if username == "qa_admin":
            check("管理员访问真实Admin", client.request("/admin/")[0] == 200)
            check("管理员不默认读经营数据", client.request("/api/business/summary/")[0] == 403)
        if username == "qa_manager":
            status, result = client.request("/api/modules/business/launch/", "POST", {})
            check("导航返回已验证在线的原入口", status == 200 and result == {"url": "http://127.0.0.1:8018/"})
            status, result = client.request("/api/business/summary/")
            check("未接入真实数据明确503而非伪造成功", status == 503 and result.get("code") == "integration_not_configured")
        check(username + "退出", client.request("/api/logout/", "POST", {})[0] == 204)
        check(username + "退出后401", client.request("/api/me/")[0] == 401)
    first = Client()
    status, profile = first.login("qa_first")
    check("首次改密标记", status == 200 and profile["must_change_password"] is True)
    status, error = first.request("/api/modules/")
    check("首次改密前功能受限", status == 403 and error.get("code") == "password_change_required")
finally:
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "base": BASE,
              "database": "独立 portal_phase1 PostgreSQL 验收库", "results": results,
              "passed": sum(item["passed"] for item in results), "failed": sum(not item["passed"] for item in results),
              "boundary": "真实门户HTTP与真实经营导航；未登录或查询旧业务数据，不是可信数据集成或SSO验收。"}
    (ROOT / "docs/evidence/http-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"HTTP assertions: {report['passed']} passed, {report['failed']} failed; report excludes credentials.")
