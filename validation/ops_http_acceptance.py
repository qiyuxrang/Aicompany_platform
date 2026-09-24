import json
import secrets
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener

from prepare_ops_validation import setup

setup()
ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:18210"
CREDENTIALS = ROOT / ".runtime/ops-credentials.json"
credentials = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
checks = []


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        return None


class Client:
    def __init__(self):
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), NoRedirect())

    def request(self, path, method="GET", data=None, form=False, csrf=True):
        headers = {}
        if method != "GET":
            headers["Origin"] = BASE
            if csrf:
                status, payload, _ = self.request("/api/csrf/")
                if status != 200:
                    raise AssertionError("csrf endpoint unavailable")
                headers["X-CSRFToken"] = payload["csrfToken"]
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
            raw = urlencode(data, doseq=True).encode() if form else json.dumps(data).encode()
        else:
            raw = None
        request = Request(BASE + path, method=method, data=raw, headers=headers)
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            body = response.read().decode("utf-8")
            try:
                body = json.loads(body)
            except ValueError:
                pass
            return response.status, body, dict(response.headers)

    def login(self, username, password=None):
        return self.request("/api/login/", "POST", {"username": username, "password": password or credentials[username]})


class RoleOptions(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_roles = False
        self.value = None
        self.label = ""
        self.options = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "select":
            self.in_roles = values.get("name") == "roles"
        if self.in_roles and tag == "option":
            self.value = values.get("value")
            self.label = ""

    def handle_data(self, data):
        if self.in_roles and self.value:
            self.label += data

    def handle_endtag(self, tag):
        if tag == "option" and self.in_roles and self.value:
            self.options[self.label.strip()] = self.value
            self.value = None
        if tag == "select":
            self.in_roles = False


def check(name, condition):
    checks.append({"case": name, "passed": bool(condition)})
    if not condition:
        raise AssertionError(name)


def module_configuration(admin, module, url, description):
    return admin.request(module["admin_url"], "POST", {"name": module["name"], "description": description,
        "url": url, "status": "navigation", "enabled": "on", "_save": "Save"}, form=True)[0]


def run_core(admin):
    anonymous = Client()
    check("匿名运维API拒绝", anonymous.request("/api/ops/overview/")[0] == 401)
    member = Client()
    check("普通用户真实登录", member.login("ops_product")[0] == 200)
    for path in ("overview", "users", "usage", "modules", "issues", "maintenance"):
        check(path + "普通用户403", member.request(f"/api/ops/{path}/")[0] == 403)
        check(path + "管理员真实API200", admin.request(f"/api/ops/{path}/")[0] == 200)
    check("管理HTML直达也拒绝", member.request("/ops/people")[0] == 403)
    check("管理员不默认读取业务摘要", admin.request("/api/business/summary/")[0] == 403)
    check("未知统计窗口拒绝", admin.request("/api/ops/usage/?days=999")[0] == 400)
    check("管理检查缺CSRF拒绝", admin.request("/api/ops/modules/product/check/", "POST", {}, csrf=False)[0] == 403)
    check("管理检查不接受任意URL", admin.request("/api/ops/modules/product/check/", "POST", {"url": "http://127.0.0.1:1/"})[0] == 400)
    check("检查不接受GET", admin.request("/api/ops/modules/product/check/")[0] == 405)
    status, pending, _ = admin.request("/api/ops/modules/product/check/", "POST", {})
    check("待接入不是假健康", status == 200 and pending["module"]["check"]["state"] == "not_configured")

    status, html, _ = admin.request("/admin/portal/user/add/")
    check("复用真实DjangoAdmin新增表单", status == 200 and isinstance(html, str))
    parser = RoleOptions()
    parser.feed(html)
    role_ids = parser.options
    username = "ops_http_" + datetime.now(timezone.utc).strftime("%H%M%S")
    initial = secrets.token_urlsafe(24)
    status, _, _ = admin.request("/admin/portal/user/add/", "POST", {"username": username,
        "display_name": "运维HTTP验收账号", "password1": initial, "password2": initial,
        "roles": [role_ids["产品人员"]], "_save": "Save"}, form=True)
    check("原生Admin创建真实隔离账号", status == 302)
    credentials[username] = initial
    CREDENTIALS.write_text(json.dumps(credentials), encoding="utf-8")
    created = admin.request("/api/ops/users/?q=" + username)[1]["items"][0]
    user_id = created["id"]
    check("新账号标记首次改密", created["must_change_password"] is True)
    user = Client()
    check("新账号真实登录", user.login(username)[0] == 200)
    check("首次改密前不能访问工作模块", user.request("/api/modules/")[0] == 403)
    changed = secrets.token_urlsafe(24)
    check("真实密码修改成功", user.request("/api/password/", "POST", {"old_password": initial, "new_password": changed})[0] == 200)
    credentials[username] = changed
    CREDENTIALS.write_text(json.dumps(credentials), encoding="utf-8")
    check("改密后原会话失效", user.request("/api/me/")[0] == 401)
    check("修改后重新登录", user.login(username)[0] == 200)

    def change_account(roles, active=True):
        values = {"username": username, "display_name": "运维HTTP验收账号", "roles": roles, "_save": "Save"}
        if active:
            values["is_active"] = "on"
        return admin.request(f"/admin/portal/user/{user_id}/change/", "POST", values, form=True)[0]

    check("原生Admin调整多角色", change_account([role_ids["产品人员"], role_ids["工程人员"]]) == 302)
    check("角色并集即时生效", {module["code"] for module in user.request("/api/modules/")[1]} == {"product", "cost"})
    check("原生Admin撤去角色", change_account([role_ids["产品人员"]]) == 302)
    check("撤权后直接模块请求拒绝", user.request("/api/modules/cost/")[0] == 404)
    check("显式授权平台管理", change_account([role_ids["产品人员"], role_ids["平台管理员"]]) == 302)
    check("显式授权后运维可读", user.request("/api/ops/overview/")[0] == 200)
    check("原生Admin撤销平台管理", change_account([role_ids["产品人员"]]) == 302)
    check("撤销后运维下一请求403", user.request("/api/ops/overview/")[0] == 403)
    reset = secrets.token_urlsafe(24)
    check("原生Admin重置密码", admin.request(f"/admin/portal/user/{user_id}/password/", "POST", {"password1": reset, "password2": reset}, form=True)[0] == 302)
    credentials[username] = reset
    CREDENTIALS.write_text(json.dumps(credentials), encoding="utf-8")
    check("重置使旧会话失效", user.request("/api/me/")[0] == 401)
    check("重置后登录仍强制改密", user.login(username)[1]["must_change_password"] is True)
    check("原生Admin停用账号", change_account([role_ids["产品人员"]], active=False) == 302)
    check("停用后会话失效", user.request("/api/me/")[0] == 401)
    check("停用账号登录拒绝", user.login(username)[0] == 401)
    check("原生Admin重新启用", change_account([role_ids["产品人员"]]) == 302)
    check("启用不恢复旧会话", user.request("/api/me/")[0] == 401)
    check("人员详情有真实审计摘要", bool(admin.request(f"/api/ops/users/{user_id}/")[1]["recent_audit"]))

    business = next(item for item in admin.request("/api/ops/modules/")[1]["items"] if item["code"] == "business")
    check("配置只改隔离库受控故障目标", module_configuration(admin, business, "http://127.0.0.1:18218/", "隔离验收：受控不可达目标，不是原经营系统故障。") == 302)
    status, failure, _ = admin.request("/api/ops/modules/business/check/", "POST", {})
    check("真实连接失败产生问题", status == 200 and failure["issue"] is not None and failure["module"]["check"]["state"] in ("unavailable", "error"))
    issue_id = failure["issue"]["id"]
    check("重复探测受冷却限制", admin.request("/api/ops/modules/business/check/", "POST", {})[0] == 429)
    for status_value in ("investigating", "closed"):
        status, issue, _ = admin.request(f"/api/ops/issues/{issue_id}/", "POST", {"status": status_value, "note": "隔离故障验收；人工状态不代表服务恢复。"})
        check("记录人工处理状态" + status_value, status == 200 and issue["status"] == status_value)
        check("人工处理不伪装健康" + status_value, issue["health_state"] == "unresolved")
    admin.request(f"/api/ops/issues/{issue_id}/", "POST", {"status": "open", "note": "重新打开，等待真实检查证据。"})
    result = {"issue_id": issue_id, "fixture_user_id": user_id, "fixture_username": username}
    (ROOT / ".runtime/ops-http-context.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def run_recovery(admin):
    context = json.loads((ROOT / ".runtime/ops-http-context.json").read_text(encoding="utf-8"))
    business = next(item for item in admin.request("/api/ops/modules/")[1]["items"] if item["code"] == "business")
    check("原生Admin恢复隔离库导航地址", module_configuration(admin, business, "http://127.0.0.1:8018/", "隔离验收入口：原经营网页保留原登录；不接可信数据或SSO。") == 302)
    for attempt in range(2):
        status, payload, headers = admin.request("/api/ops/modules/business/check/", "POST", {})
        if status != 429:
            break
        time.sleep(min(61, max(1, int(headers.get("Retry-After", "60")))))
    check("真实原导航目标探测可达", status == 200 and payload["module"]["check"]["state"] == "reachable")
    issue = admin.request(f"/api/ops/issues/{context['issue_id']}/")[1]
    check("系统恢复依赖真实复查", issue["health_state"] == "recovered" and issue["status"] == "recovered")
    manager = Client()
    check("经营角色真实登录", manager.login("ops_manager")[0] == 200)
    status, launch, _ = manager.request("/api/modules/business/launch/", "POST", {})
    check("原经营导航未损坏", status == 200 and launch["url"] == "http://127.0.0.1:8018/")
    usage = admin.request("/api/ops/usage/?days=7")[1]
    check("统计来自真实登录及启动事件", usage["summary"]["login_users"] >= 3 and usage["summary"]["module_launches"] >= 1)
    check("登录人数与次数有区分", usage["summary"]["login_count"] >= usage["summary"]["login_users"])
    return context


phase = sys.argv[1] if len(sys.argv) == 2 else "core"
if phase not in ("core", "recovery"):
    raise SystemExit("仅支持core或recovery；会写入本期独立运维验收数据库。")
try:
    admin = Client()
    check("管理员真实登录", admin.login("ops_admin")[0] == 200)
    context = run_core(admin) if phase == "core" else run_recovery(admin)
finally:
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "base": BASE, "phase": phase,
              "checks": checks, "passed": sum(item["passed"] for item in checks), "failed": sum(not item["passed"] for item in checks),
              "boundary": "真实本机HTTP与Django Admin表单，独立PG及合成账号；18218闭端口是受控故障注入，不是旧系统真实事故。不代表浏览器UI或可信经营数据联调。"}
    (ROOT / f"docs/evidence/ops/http-{phase}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Ops HTTP {phase}: {report['passed']} passed, {report['failed']} failed")
