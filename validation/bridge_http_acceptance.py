import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from bridge_environment import ROOT, RUNTIME, EVIDENCE, environment, setup

setup("portal")
from django.utils import timezone
from portal.integration import issue_ticket
from portal.models import BusinessMapping, IntegrationTicket, Module, Role, User

PORTAL = "http://127.0.0.1:18310"
LEDGER = "http://127.0.0.1:18318"
REPORT = EVIDENCE / "http-acceptance.json"
FIXTURES = json.loads((RUNTIME / "bridge-ledger-fixtures.json").read_text(encoding="utf-8"))
SECRET = environment("portal")["PORTAL_INTEGRATION_SECRET"]
checks = []


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *arguments):
        return None


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), NoRedirect())

    def request(self, path, method="GET", body=None, headers=None):
        headers = {"Content-Type": "application/json", **(headers or {})}
        raw = json.dumps(body).encode() if body is not None else None
        try:
            response = self.opener.open(Request(self.base + path, data=raw, method=method, headers=headers), timeout=12)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            try:
                content = json.loads(content)
            except ValueError:
                content = {"non_json": True}
            return response.status, content

    def login(self, name):
        side = "portal" if self.base == PORTAL else "ledger"
        path = "/api/csrf/" if side == "portal" else "/api/auth/csrf/"
        status, body = self.request(path)
        assert status == 200, f"CSRF status {status}"
        credentials = json.loads((RUNTIME / f"bridge-{side}-credentials.json").read_text(encoding="utf-8"))
        login_path = "/api/login/" if side == "portal" else "/api/auth/login/"
        result = self.request(login_path, "POST", {"username": "bridge_" + name, "password": credentials["bridge_" + name]},
                              {"Origin": self.base, "X-CSRFToken": body["csrfToken"]})
        assert result[0] == 200, f"Login status {result[0]}"


def require_status(result, expected):
    assert result[0] == expected, f"Expected status {expected}, got {result[0]}"
    return result[1]


def machine(base, path, token, **overrides):
    body = {"ticket": token, "audience": "business", "purpose": "read_summary", **overrides}
    return Client(base).request(path, "POST", body, {"Authorization": "Bearer " + SECRET})


def ticket(name="sales"):
    return issue_ticket(User.objects.get(username="bridge_" + name))


def redeem(token, **overrides):
    return machine(PORTAL, "/api/integration/redeem/", token, **overrides)


def bridge(token, **overrides):
    return machine(LEDGER, "/api/portal-bridge/summary/", token, **overrides)


def connection():
    config = environment("ledger")
    return psycopg.connect(dbname=config["POSTGRES_DB"], user=config["POSTGRES_USER"], password=config["POSTGRES_PASSWORD"],
                          host=config["POSTGRES_HOST"], port=config["POSTGRES_PORT"], autocommit=True)


@contextmanager
def legacy_change(table, identifier, fields):
    if table not in {"ledger_user", "ledger_project"} or not set(fields) <= {"department", "role", "is_active", "must_change_password", "departments"}:
        raise ValueError("Unapproved fixture mutation")
    with connection() as database:
        columns = sql.SQL(", ").join(map(sql.Identifier, fields))
        previous = database.execute(sql.SQL("SELECT {} FROM {} WHERE id=%s").format(columns, sql.Identifier(table)), [identifier]).fetchone()
        assignments = sql.SQL(", ").join(sql.SQL("{}=%s").format(sql.Identifier(field)) for field in fields)
        statement = sql.SQL("UPDATE {} SET {} WHERE id=%s").format(sql.Identifier(table), assignments)
        encode = lambda values: [Jsonb(value) if isinstance(value, list) else value for value in values]
        database.execute(statement, [*encode(fields.values()), identifier])
        try:
            yield
        finally:
            database.execute(statement, [*encode(previous), identifier])


def fingerprints():
    with connection() as database:
        tables = database.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'ledger_%' ORDER BY tablename").fetchall()
        result = {}
        for (table,) in tables:
            if table.startswith("ledger_user") or table == "ledger_loginthrottle":
                continue
            rows = database.execute(sql.SQL("SELECT row_to_json(record) FROM {} AS record").format(sql.Identifier(table))).fetchall()
            canonical = sorted(json.dumps(row[0], sort_keys=True, ensure_ascii=False, default=str) for row in rows)
            result[table] = {"count": len(rows), "sha256": hashlib.sha256("\n".join(canonical).encode()).hexdigest()}
        return result


def compare(name):
    native = require_status(legacy_clients[name].request("/api/projects/"), 200)
    rows = native["results"] if isinstance(native, dict) else native
    result = require_status(portal_clients[name].request("/api/business/summary/"), 200)
    expected = sorted((row["id"], row["name"]) for row in rows)
    actual = sorted((row["id"], row["name"]) for row in result["projects"])
    assert actual == expected, "Native and bridge project scopes differ"
    assert result["summary"] == {"project_count": len(expected)}, "Summary differs"
    assert all(set(row) == {"id", "name"} for row in result["projects"]), "Unexpected business fields"
    assert set(result) == {"projects", "summary", "source", "updated_at"}
    return {"project_ids": [row[0] for row in expected], "project_count": len(expected)}


def check(name, function):
    try:
        detail = function()
        checks.append({"name": name, "status": "passed", "detail": detail})
        print(name + ": passed")
    except Exception as error:
        checks.append({"name": name, "status": "failed", "error_type": type(error).__name__})
        print(name + ": FAILED (" + type(error).__name__ + ")")


def deny_mapping():
    mapping = BusinessMapping.objects.get(user__username="bridge_sales")
    for field, value in (("enabled", False), ("external_user_id", "999999999")):
        previous = getattr(mapping, field)
        setattr(mapping, field, value)
        mapping.save(update_fields=[field])
        try:
            require_status(portal_clients["sales"].request("/api/business/summary/"), 403)
        finally:
            setattr(mapping, field, previous)
            mapping.save(update_fields=[field])


def revoke(kind):
    user = User.objects.get(username="bridge_sales")
    role = Role.objects.get(code="general_manager")
    module = Module.objects.get(code="business")
    mapping = BusinessMapping.objects.get(user=user)
    old_ticket = ticket()
    if kind == "user_role":
        user.roles.remove(role)
    elif kind == "role_module":
        role.modules.remove(module)
    elif kind == "module":
        module.enabled = False
        module.save(update_fields=["enabled"])
    else:
        mapping.enabled = False
        mapping.save(update_fields=["enabled"])
    try:
        require_status(portal_clients["sales"].request("/api/business/summary/"), 403)
        require_status(redeem(old_ticket), 403)
        require_status(legacy_clients["sales"].request("/api/projects/"), 200)
    finally:
        if kind == "user_role":
            user.roles.add(role)
        elif kind == "role_module":
            role.modules.add(module)
        elif kind == "module":
            module.enabled = True
            module.save(update_fields=["enabled"])
        else:
            mapping.enabled = True
            mapping.save(update_fields=["enabled"])
    require_status(redeem(old_ticket), 403)
    compare("sales")
    return {"effective": "next protected request; stale tickets stay invalid after restoration", "native_session_retained": True}


def portal_deactivate():
    user = User.objects.get(username="bridge_sales")
    old_ticket = ticket()
    user.is_active = False
    user.save(update_fields=["is_active"])
    try:
        require_status(portal_clients["sales"].request("/api/business/summary/"), 401)
        require_status(redeem(old_ticket), 403)
        require_status(legacy_clients["sales"].request("/api/projects/"), 200)
    finally:
        user.is_active = True
        user.save(update_fields=["is_active"])
        portal_clients["sales"].login("sales")
    compare("sales")


def old_permissions():
    identifier = FIXTURES["accounts"]["sales"]
    with legacy_change("ledger_user", identifier, {"department": "engineering"}):
        compare("sales")
    with legacy_change("ledger_user", identifier, {"role": "manager"}):
        assert compare("sales")["project_count"] == 4
    for field in ("is_active", "must_change_password"):
        with legacy_change("ledger_user", identifier, {field: field == "must_change_password"}):
            require_status(portal_clients["sales"].request("/api/business/summary/"), 403)
            native_status = legacy_clients["sales"].request("/api/projects/")[0]
            assert native_status in (401, 403), "Native access not denied"
        legacy_clients["sales"].login("sales")
    project = FIXTURES["projects"][0]
    with legacy_change("ledger_project", project["id"], {"departments": ["engineering"]}):
        assert compare("sales")["project_count"] == 1
    from contextlib import ExitStack
    with ExitStack() as stack:
        for project in FIXTURES["projects"]:
            stack.enter_context(legacy_change("ledger_project", project["id"], {"departments": ["finance"]}))
        assert compare("sales")["project_count"] == 0
    compare("sales")


def ticket_security():
    fresh = ticket()
    require_status(bridge(fresh, user_id=999), 400)
    require_status(redeem(fresh, user_id=999), 400)
    require_status(bridge(fresh, audience="other"), 400)
    require_status(bridge(fresh, purpose="write"), 400)
    require_status(bridge(fresh), 200)
    require_status(bridge(fresh), 403)
    require_status(bridge("invalid-ticket"), 403)
    expired = ticket()
    IntegrationTicket.objects.filter(digest=hashlib.sha256(expired.encode()).hexdigest()).update(expires_at=timezone.now() - timedelta(seconds=1))
    require_status(bridge(expired), 403)
    concurrent = ticket()
    with ThreadPoolExecutor(max_workers=5) as executor:
        statuses = list(executor.map(lambda unused: redeem(concurrent)[0], range(5)))
    assert sorted(statuses) == [200, 403, 403, 403, 403], "Concurrent replay accepted"
    require_status(Client(LEDGER).request("/api/portal-bridge/summary/", "POST", {"ticket": ticket(), "audience": "business", "purpose": "read_summary"}, {"Authorization": "Bearer invalid"}), 403)
    require_status(Client(LEDGER).request("/api/portal-bridge/summary/"), 405)
    return {"concurrent_redemption_statuses": sorted(statuses)}


portal_clients = {}
legacy_clients = {}


def main():
    for name in ("manager", "sales", "engineering", "nomap", "inactive", "first", "admin"):
        portal_clients[name] = Client(PORTAL)
        portal_clients[name].login(name)
    for name in ("manager", "sales", "engineering"):
        legacy_clients[name] = Client(LEDGER)
        legacy_clients[name].login(name)
    before = fingerprints()
    for name in ("manager", "sales", "engineering"):
        check("native_scope_" + name, lambda name=name: compare(name))
    for name in ("nomap", "inactive", "first", "admin"):
        check("denied_" + name, lambda name=name: require_status(portal_clients[name].request("/api/business/summary/"), 403) and None)
    check("mapping_disabled_or_missing_legacy_user", deny_mapping)
    for kind in ("user_role", "role_module", "module", "mapping"):
        check("platform_revoke_" + kind, lambda kind=kind: revoke(kind))
    check("platform_deactivation_preserves_native_session", portal_deactivate)
    check("legacy_permission_changes_and_empty_scope", old_permissions)
    check("ticket_security_real_endpoints", ticket_security)
    after = fingerprints()
    checks.append({"name": "business_table_read_only", "status": "passed" if before == after else "failed", "before": before, "after": after})
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"timestamp": timezone.now().isoformat(), "boundary": "Real current legacy source, two isolated PostgreSQL databases and HTTP services; synthetic fixtures only; excludes browser SSO and company financial comparison", "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(any(item["status"] != "passed" for item in checks))


if __name__ == "__main__":
    main()
