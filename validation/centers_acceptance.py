import json
import secrets
import sys

from bridge_environment import ROOT, RUNTIME, EVIDENCE, RUN_ID, setup

if RUN_ID != "20260921-centers":
    raise SystemExit("Use the dedicated 20260921-centers isolated validation namespace")


def prepare():
    setup("portal")
    from portal.models import Role, User
    path = RUNTIME / "bridge-portal-credentials.json"
    accounts = json.loads(path.read_text(encoding="utf-8"))
    for name, role in (("product", "product"), ("engineer", "engineering"), ("hr", "hr")):
        username = "bridge_" + name
        if username in accounts or User.objects.filter(username=username).exists():
            raise RuntimeError("Refuse to overwrite existing test accounts")
        password = secrets.token_urlsafe(24)
        user = User.objects.create_user(username=username, password=password, display_name="隔离页面验收", must_change_password=False)
        user.roles.add(Role.objects.get(code=role))
        accounts[username] = password
        path.write_text(json.dumps(accounts, indent=2), encoding="utf-8")
    env_path = RUNTIME / "bridge-portal.env"
    lines = env_path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith("PORTAL_FRONTEND_DIST=")]
    lines.append("PORTAL_FRONTEND_DIST=" + (ROOT / ".runtime/centers-frontend-dist").as_posix())
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Dedicated role accounts and isolated frontend configured; no existing account changed")


def serve():
    import bridge_system_acceptance as service
    service.start("ledger")
    service.start("portal")
    (RUNTIME / "centers-processes.json").write_text(json.dumps({side: process.pid for side, process in service.processes.items()}), encoding="utf-8")
    print("Isolated services listening on 18310 and 18318")


def verify():
    from bridge_http_acceptance import Client, PORTAL, require_status
    from portal.models import Role, User
    checks = []
    for name, code in (("product", "product"), ("engineer", "cost"), ("hr", "hr"), ("manager", "business")):
        client = Client(PORTAL)
        client.login(name)
        require_status(client.request("/centers/" + code), 200)
        for other in {"product", "cost", "hr", "business"} - {code}:
            require_status(client.request("/centers/" + other + "?role=general_manager"), 404)
        require_status(client.request("/preview/business"), 403)
        checks.append({"name": name + " role page matrix", "status": "passed"})
    admin = Client(PORTAL)
    admin.login("admin")
    for code in ("product", "cost", "hr", "business"):
        require_status(admin.request("/preview/" + code), 200)
        require_status(admin.request("/centers/" + code), 404)
    require_status(admin.request("/api/business/summary/"), 403)
    checks.append({"name": "Admin preview grants no business access", "status": "passed"})
    client = Client(PORTAL)
    client.login("product")
    user = User.objects.get(username="bridge_product")
    role = Role.objects.get(code="product")
    try:
        user.roles.remove(role)
        require_status(client.request("/centers/product"), 404)
        require_status(client.request("/api/modules/product/"), 404)
    finally:
        user.roles.add(role)
    checks.append({"name": "Revocation rejects the next page and API request", "status": "passed"})
    manager = Client(PORTAL)
    manager.login("manager")
    summary = require_status(manager.request("/api/business/summary/"), 200)
    expected = json.loads((RUNTIME / "bridge-ledger-fixtures.json").read_text(encoding="utf-8"))["projects"]
    assert sorted(item["name"] for item in summary["projects"]) == sorted(item["name"] for item in expected)
    checks.append({"name": "Real isolated legacy summary matches seeded projects", "status": "passed", "count": len(expected)})
    report = {"boundary": "Real HTTP against isolated services and synthetic registered fixtures; not production or browser SSO", "checks": checks}
    (EVIDENCE / "centers-http.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    {"prepare": prepare, "serve": serve, "verify": verify}[sys.argv[1]]()
