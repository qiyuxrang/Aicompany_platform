import json
import re
import secrets
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener

from bridge_environment import RUNTIME, EVIDENCE as EVIDENCE_DIRECTORY


ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:18310"
FORBIDDEN_BASE = "http://127.0.0.1:18210"
ADMIN_USERNAME = "bridge_admin"
FORBIDDEN_USERNAME = "bjrunner"
CREDENTIALS = RUNTIME / "bridge-portal-credentials.json"
EVIDENCE = EVIDENCE_DIRECTORY / "admin-http-acceptance.json"
DISPLAY_NAME = "桥接Admin HTTP合成账号"
checks = []
cleanup = {}


class AcceptanceFailure(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        return None


class Client:
    def __init__(self):
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), NoRedirect())

    def request(self, path, method="GET", data=None, form=False, csrf=True):
        if not path.startswith("/") or FORBIDDEN_BASE in path:
            raise AcceptanceFailure("拒绝非18310目标")
        headers = {}
        if method != "GET":
            headers["Origin"] = BASE
            if csrf:
                status, payload, _ = self.request("/api/csrf/")
                if status != 200 or not isinstance(payload, dict) or not payload.get("csrfToken"):
                    raise AcceptanceFailure("CSRF端点不可用")
                headers["X-CSRFToken"] = payload["csrfToken"]
        if data is None:
            raw = None
        elif form:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            raw = urlencode(data, doseq=True).encode("utf-8")
        else:
            headers["Content-Type"] = "application/json"
            raw = json.dumps(data).encode("utf-8")
        request = Request(BASE + path, method=method, data=raw, headers=headers)
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except ValueError:
                payload = body
            return response.status, payload, dict(response.headers)

    def login(self, username, password):
        if username == FORBIDDEN_USERNAME:
            raise AcceptanceFailure("拒绝触碰bjrunner")
        return self.request("/api/login/", "POST", {"username": username, "password": password})


class RoleOptions(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_roles = False
        self.option_value = None
        self.option_label = []
        self.option_selected = False
        self.options = {}
        self.selected = set()
        self.active_checked = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "select":
            self.in_roles = values.get("name") == "roles"
        elif self.in_roles and tag == "option":
            self.option_value = values.get("value")
            self.option_label = []
            self.option_selected = "selected" in values
        elif tag == "input" and values.get("name") == "is_active":
            self.active_checked = "checked" in values

    def handle_data(self, data):
        if self.in_roles and self.option_value is not None:
            self.option_label.append(data)

    def handle_endtag(self, tag):
        if tag == "option" and self.in_roles and self.option_value is not None:
            label = "".join(self.option_label).strip()
            self.options[label] = self.option_value
            if self.option_selected:
                self.selected.add(self.option_value)
            self.option_value = None
        elif tag == "select":
            self.in_roles = False


class ResultRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = False
        self.in_row = False
        self.text = []
        self.links = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "tbody":
            self.in_body = True
        elif self.in_body and tag == "tr":
            self.in_row = True
            self.text = []
            self.links = []
        elif self.in_row and tag == "a" and values.get("href"):
            self.links.append(values["href"])

    def handle_data(self, data):
        if self.in_row:
            value = data.strip()
            if value:
                self.text.append(value)

    def handle_endtag(self, tag):
        if tag == "tr" and self.in_row:
            self.rows.append({"text": " ".join(self.text), "links": self.links[:]})
            self.in_row = False
        elif tag == "tbody":
            self.in_body = False


def record(name, condition, **details):
    item = {"case": name, "passed": bool(condition)}
    if details:
        item["evidence"] = details
    checks.append(item)
    if not condition:
        raise AcceptanceFailure(name)


def parse_roles(html):
    parser = RoleOptions()
    parser.feed(html)
    return parser


def parse_rows(html):
    parser = ResultRows()
    parser.feed(html)
    return parser.rows


def extract_id(links, model):
    patterns = (
        re.compile(rf"(?:^|/){re.escape(model)}/(\d+)/change/?(?:\?.*)?$"),
        re.compile(r"(?:^|/)(\d+)/change/?(?:\?.*)?$"),
    )
    for link in links:
        for pattern in patterns:
            match = pattern.search(link)
            if match:
                return int(match.group(1))
    return None


def find_user_id(admin, username):
    status, html, _ = admin.request("/admin/portal/user/?" + urlencode({"q": username}))
    if status != 200 or not isinstance(html, str):
        return None
    for row in parse_rows(html):
        if username in row["text"]:
            user_id = extract_id(row["links"], "user")
            if user_id is not None:
                return user_id
    return None


def change_account(admin, user_id, username, role_ids, active):
    values = {
        "username": username,
        "display_name": DISPLAY_NAME,
        "roles": role_ids,
        "_save": "Save",
    }
    if active:
        values["is_active"] = "on"
    return admin.request(f"/admin/portal/user/{user_id}/change/", "POST", values, form=True)[0]


def audit_event(admin, user_id, action):
    query = urlencode({"action__exact": action, "result__exact": "success", "q": str(user_id)})
    status, html, _ = admin.request("/admin/portal/auditevent/?" + query)
    if status != 200 or not isinstance(html, str):
        return status, None, []
    rows = [row for row in parse_rows(html) if action in row["text"] and str(user_id) in row["text"]]
    event_id = extract_id(rows[0]["links"], "auditevent") if rows else None
    return status, event_id, rows


def main(admin_password, state):
    admin = Client()
    status, payload, _ = admin.request("/health/")
    record("仅使用18310桥接门户健康端点", status == 200, status=status)
    status, _, _ = admin.login(ADMIN_USERNAME, admin_password)
    record("bridge_admin真实登录", status == 200, status=status)
    status, me, _ = admin.request("/api/me/")
    record("当前会话确认为bridge_admin", status == 200 and isinstance(me, dict) and me.get("username") == ADMIN_USERNAME, status=status)

    status, html, _ = admin.request("/admin/portal/user/add/")
    record("原生DjangoAdmin新增账号表单可读", status == 200 and isinstance(html, str), status=status)
    roles = parse_roles(html)
    required_roles = ("产品人员", "工程人员")
    record("合成账号所需既有角色可选", all(name in roles.options for name in required_roles), roles=list(roles.options))
    primary_role = roles.options[required_roles[0]]
    secondary_role = roles.options[required_roles[1]]

    username = "bridge_admin_http_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + secrets.token_hex(3)
    initial_password = secrets.token_urlsafe(32)
    reset_password = secrets.token_urlsafe(32)
    state.update(username=username, user_id=None, known_passwords=[initial_password, reset_password])

    try:
        status, _, _ = admin.request(
            "/admin/portal/user/add/",
            "POST",
            {
                "username": username,
                "display_name": DISPLAY_NAME,
                "password1": initial_password,
                "password2": initial_password,
                "roles": [primary_role],
                "_save": "Save",
            },
            form=True,
        )
        record("原生DjangoAdmin创建合成账号", status == 302, status=status)
        state["user_id"] = find_user_id(admin, username)
        record("仅定位本轮合成账号", state["user_id"] is not None, user_id=state["user_id"])
        user_id = state["user_id"]

        synthetic = Client()
        status, created_login, _ = synthetic.login(username, initial_password)
        record("合成账号初始密码真实登录", status == 200 and created_login.get("must_change_password") is True, status=status)

        status = change_account(admin, user_id, username, [primary_role, secondary_role], True)
        record("原生DjangoAdmin增加第二角色", status == 302, status=status)
        status, html, _ = admin.request(f"/admin/portal/user/{user_id}/change/")
        selected = parse_roles(html).selected if status == 200 else set()
        record("角色调整由Admin表单持久化", status == 200 and selected == {primary_role, secondary_role}, selected=sorted(selected))

        status = change_account(admin, user_id, username, [primary_role], True)
        record("原生DjangoAdmin撤回第二角色", status == 302, status=status)
        status, html, _ = admin.request(f"/admin/portal/user/{user_id}/change/")
        selected = parse_roles(html).selected if status == 200 else set()
        record("撤回角色后仅保留基准角色", status == 200 and selected == {primary_role}, selected=sorted(selected))

        status, _, _ = admin.request(
            f"/admin/portal/user/{user_id}/password/",
            "POST",
            {"password1": reset_password, "password2": reset_password},
            form=True,
        )
        record("原生DjangoAdmin重置合成账号密码", status == 302, status=status)
        record("重置使旧会话失效", synthetic.request("/api/me/")[0] == 401)
        record("初始密码重置后不可登录", Client().login(username, initial_password)[0] == 401)
        status, reset_login, _ = synthetic.login(username, reset_password)
        record("重置密码可登录且仍强制改密", status == 200 and reset_login.get("must_change_password") is True, status=status)

        status = change_account(admin, user_id, username, [primary_role], False)
        record("原生DjangoAdmin停用合成账号", status == 302, status=status)
        record("停用使现有会话失效", synthetic.request("/api/me/")[0] == 401)
        record("停用账号拒绝真实登录", Client().login(username, reset_password)[0] == 401)

        status = change_account(admin, user_id, username, [primary_role], True)
        record("原生DjangoAdmin重新启用合成账号", status == 302, status=status)
        status, _, _ = Client().login(username, reset_password)
        record("重新启用后重置密码恢复登录", status == 200, status=status)

        audit_ids = {}
        for action in ("user_create", "user_change", "password_reset"):
            status, event_id, rows = audit_event(admin, user_id, action)
            record(f"审计按动作结果及目标过滤-{action}", status == 200 and bool(rows), status=status, rows=len(rows))
            if event_id is not None:
                audit_ids[action] = event_id
        record("审计新增入口只读拒绝", admin.request("/admin/portal/auditevent/add/")[0] == 403)
        event_id = next(iter(audit_ids.values()), None)
        record("过滤结果提供审计只读详情", event_id is not None, event_id=event_id)
        record("审计详情允许只读查看", admin.request(f"/admin/portal/auditevent/{event_id}/change/")[0] == 200)
        record("审计详情拒绝HTTP写入", admin.request(f"/admin/portal/auditevent/{event_id}/change/", "POST", {}, form=True)[0] == 403)
        return admin
    except Exception:
        if state["user_id"] is None:
            state["user_id"] = find_user_id(admin, username)
        raise


def finalize_user(admin, admin_password, state):
    user_id = state.get("user_id")
    username = state.get("username")
    if user_id is None or not username:
        cleanup.update(attempted=False, reason="合成账号未创建或无法定位")
        return False
    if admin is None or admin.request("/api/me/")[0] != 200:
        admin = Client()
        status, _, _ = admin.login(ADMIN_USERNAME, admin_password)
        if status != 200:
            cleanup.update(attempted=True, completed=False, reason="无法重新认证bridge_admin")
            return False
    disposal_password = secrets.token_urlsafe(48)
    reset_status = admin.request(
        f"/admin/portal/user/{user_id}/password/",
        "POST",
        {"password1": disposal_password, "password2": disposal_password},
        form=True,
    )[0]
    disable_status = change_account(admin, user_id, username, [], False)
    status, html, _ = admin.request(f"/admin/portal/user/{user_id}/change/")
    account = parse_roles(html) if status == 200 else None
    known_passwords_rejected = all(Client().login(username, password)[0] == 401 for password in state.get("known_passwords", []))
    completed = (
        reset_status == 302
        and disable_status == 302
        and account is not None
        and not account.active_checked
        and not account.selected
        and known_passwords_rejected
    )
    cleanup.update(
        attempted=True,
        completed=completed,
        reset_status=reset_status,
        disable_status=disable_status,
        final_admin_read_status=status,
        final_active=False if account is not None and not account.active_checked else None,
        final_roles=[] if account is not None and not account.selected else sorted(account.selected) if account else None,
        known_passwords_rejected=known_passwords_rejected,
        final_password_persisted=False,
    )
    return completed


def run():
    if BASE != "http://127.0.0.1:18310" or FORBIDDEN_BASE == BASE or not EVIDENCE.parent.is_dir():
        raise SystemExit("验收环境边界不满足")
    credentials = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
    admin_password = credentials.get(ADMIN_USERNAME)
    if not isinstance(admin_password, str) or not admin_password:
        raise SystemExit("bridge_admin凭据不可用")
    admin = None
    state = {"username": None, "user_id": None, "known_passwords": []}
    failure = None
    try:
        admin = main(admin_password, state)
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
    finally:
        try:
            cleanup_ok = finalize_user(admin, admin_password, state)
        except Exception as error:
            cleanup_ok = False
            cleanup.update(attempted=True, completed=False, error=f"{type(error).__name__}: {error}")
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "base": BASE,
            "admin_username": ADMIN_USERNAME,
            "fixture": {"username": state.get("username"), "user_id": state.get("user_id")},
            "checks": checks,
            "passed": sum(item["passed"] for item in checks),
            "failed": sum(not item["passed"] for item in checks),
            "cleanup": cleanup,
            "failure": failure,
            "safety_boundary": {
                "forbidden_base": FORBIDDEN_BASE,
                "forbidden_username": FORBIDDEN_USERNAME,
                "credentials_modified": False,
                "existing_users_modified": [],
            },
            "boundary": "真实本机HTTP与原生Django Admin表单，仅针对18310 bridge_environment、bridge_admin及本轮合成新用户；不代表普通浏览器UI或浏览器SSO通过。",
        }
        EVIDENCE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Bridge Admin HTTP: {report['passed']} passed, {report['failed']} failed; cleanup={cleanup_ok}")
    if failure or report["failed"] or not cleanup_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
