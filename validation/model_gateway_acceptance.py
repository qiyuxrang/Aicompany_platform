import http.cookiejar
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
RUN = os.environ.get("MODEL_GATEWAY_VALIDATION_RUN", "20260921")
if not re.fullmatch(r"[a-z0-9-]{1,60}", RUN):
    raise ValueError("Invalid isolated validation run")
RUNTIME = ROOT / (".runtime/model-gateway-" + RUN)
EVIDENCE = ROOT / ("docs/evidence/model-gateway-" + RUN)
PORTAL_URL = "http://127.0.0.1:18412"
GATEWAY_URL = "http://127.0.0.1:18411"


def request(opener, path, data=None, headers=None):
    body = urlencode(data).encode() if isinstance(data, dict) else data
    call = Request(PORTAL_URL + path, data=body, headers=headers or {})
    try:
        with opener.open(call, timeout=10) as response:
            return response.status, response.read().decode()
    except HTTPError as error:
        return error.code, error.read().decode()


def login(username, password):
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
    status, body = request(opener, "/api/csrf/")
    assert status == 200
    token = json.loads(body)["csrfToken"]
    status, body = request(opener, "/api/login/", json.dumps({"username": username, "password": password}).encode(),
                           {"Content-Type": "application/json", "X-CSRFToken": token})
    assert status == 200
    return opener


def post_admin(opener, path, values):
    status, body = request(opener, path)
    assert status == 200
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body).group(1)
    return request(opener, path, {**values, "csrfmiddlewaretoken": token})


def main():
    if RUNTIME.exists():
        raise SystemExit("Refuse to overwrite an existing isolated validation run")
    for port in (18411, 18412):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit("Isolated port already in use")
    RUNTIME.mkdir(parents=True)
    subprocess.run(["icacls", str(RUNTIME), "/inheritance:r", "/grant:r", os.environ["USERNAME"] + ":(OI)(CI)(F)"], check=True, capture_output=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    daily = dict(line.split("=", 1) for line in (ROOT / ".runtime/ops-validation.env").read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    assert daily["PORTAL_DB_HOST"] == "127.0.0.1" and daily["PORTAL_DB_PORT"] == "55438"
    database = "portal_model_e2e_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    with psycopg.connect(dbname="postgres", user=daily["PORTAL_DB_USER"], password=daily["PORTAL_DB_PASSWORD"], host="127.0.0.1", port=55438, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    token = secrets.token_urlsafe(48)
    values = {**daily, "PORTAL_DB_NAME": database, "PORTAL_SECRET_KEY": secrets.token_urlsafe(48),
              "PORTAL_DEBUG": "1", "PORTAL_HTTPS": "0", "PORTAL_BEHIND_PROXY": "0",
              "PORTAL_ALLOWED_HOSTS": "127.0.0.1,localhost,testserver",
              "PORTAL_CSRF_TRUSTED_ORIGINS": PORTAL_URL,
              "PORTAL_MODEL_GATEWAY_URL": GATEWAY_URL, "PORTAL_MODEL_GATEWAY_TOKEN": token,
              "PORTAL_MODEL_GATEWAY_ALLOWED_URLS": GATEWAY_URL,
              "MODEL_GATEWAY_SERVICE_TOKEN": token, "MODEL_GATEWAY_ALLOWED_BASE_URLS": "",
              "PORTAL_INTEGRATION_SECRET": "", "PORTAL_BUSINESS_SUMMARY_URL": "",
              "PORTAL_FRONTEND_DIST": (ROOT / ".runtime/ops-frontend-dist").as_posix()}
    (RUNTIME / "portal.env").write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    os.environ.update(values)
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.sessions.models import Session
    from portal.models import User, Role, Provider, GatewayModel, ModelRoute, ModelCallLog
    from portal.model_gateway import generate_for_use, GatewayError
    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_portal", verbosity=0)
    credentials = {"model_fixture_admin": secrets.token_urlsafe(24), "model_fixture_user": secrets.token_urlsafe(24)}
    (RUNTIME / "model-credentials.json").write_text(json.dumps(credentials))
    for username, password in credentials.items():
        user = User.objects.create_user(username=username, password=password, must_change_password=False)
        user.roles.add(Role.objects.get(code="platform_admin" if username.endswith("admin") else "product"))
    handles = []
    processes = []
    report = {"scope": "Real local Django to FastAPI HTTP and isolated PostgreSQL; no real provider completion", "database": database, "checks": {}}
    checks = report["checks"]
    try:
        for name, command, cwd in (
            ("gateway", [sys.executable, "-m", "uvicorn", "model_gateway.app:app", "--host", "127.0.0.1", "--port", "18411", "--no-access-log"], ROOT),
            ("portal", [sys.executable, "-m", "waitress", "--listen=127.0.0.1:18412", "--threads=4", "config.wsgi:application"], ROOT / "backend"),
        ):
            handle = (RUNTIME / f"{name}.log").open("w", encoding="utf-8")
            handles.append(handle)
            processes.append(subprocess.Popen(command, cwd=cwd, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, stdout=handle, stderr=handle, creationflags=subprocess.CREATE_NO_WINDOW))
        opener = build_opener(ProxyHandler({}))
        for base in (PORTAL_URL, GATEWAY_URL):
            deadline = time.monotonic() + 20
            while True:
                try:
                    with opener.open(base + ("/health/" if base == PORTAL_URL else "/health"), timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Isolated server startup failed") from None
                    time.sleep(0.1)
        try:
            opener.open(Request(GATEWAY_URL + "/v1/generate", data=b"{}", headers={"Content-Type": "application/json"}), timeout=5)
            raise AssertionError("Unauthenticated gateway accepted request")
        except HTTPError as error:
            checks["gateway_unauthenticated_denied"] = error.code == 401
        admin = login("model_fixture_admin", credentials["model_fixture_admin"])
        ordinary = login("model_fixture_user", credentials["model_fixture_user"])
        checks["ordinary_admin_denied"] = request(ordinary, "/admin/portal/provider/")[0] == 403
        status, body = post_admin(admin, "/admin/portal/provider/add/", {"code": "isolated", "name": "隔离无密钥服务", "protocol": "openai_chat", "base_url": "https://unapproved.example/v1", "api_key_env": "PORTAL_MODEL_KEY_FIXTURE", "enabled": "on", "_save": "保存"})
        assert status == 200
        provider = Provider.objects.get(code="isolated")
        checks["admin_provider_creation"] = provider.enabled
        status, body = post_admin(admin, "/admin/portal/gatewaymodel/add/", {"name": "隔离模型", "provider": provider.pk, "model_name": "synthetic-model", "supports_text": "on", "enabled": "on", "timeout_seconds": "5", "max_output_tokens": "16", "token_parameter": "max_tokens", "_save": "保存"})
        assert status == 200
        model = GatewayModel.objects.get(name="隔离模型")
        checks["admin_model_creation"] = model.enabled
        test_path = f"/admin/portal/gatewaymodel/{model.pk}/test-connection/"
        checks["test_get_not_allowed"] = request(admin, test_path)[0] == 405 and ModelCallLog.objects.count() == 0
        checks["test_csrf_required"] = request(admin, test_path, {"confirm_cost": "on"})[0] == 403 and ModelCallLog.objects.count() == 0
        status, body = request(admin, f"/admin/portal/gatewaymodel/{model.pk}/change/")
        csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body).group(1)
        request(admin, test_path, {"csrfmiddlewaretoken": csrf})
        checks["test_explicit_cost_confirmation_required"] = ModelCallLog.objects.count() == 0
        status, body = request(admin, test_path, {"csrfmiddlewaretoken": csrf, "confirm_cost": "on"})
        checks["real_gateway_disallows_unapproved_target"] = ModelCallLog.objects.get().status == "target_not_allowed"
        checks["no_false_success_or_raw_secret"] = "连接测试成功" not in body and token not in body and credentials["model_fixture_admin"] not in body
        from portal.models import Module
        module = Module.objects.get(code="product")
        post_admin(admin, "/admin/portal/modelroute/add/", {"code": "solution", "name": "隔离用途", "module": module.pk, "model": model.pk, "enabled": "on", "_save": "保存"})
        route = ModelRoute.objects.get(code="solution")
        checks["admin_route_creation"] = route.enabled
        user = User.objects.get(username="model_fixture_user")
        try:
            generate_for_use(user, "solution", [{"role": "user", "content": "isolated-synthetic-input"}])
        except GatewayError as error:
            checks["business_authorized_reaches_real_gateway"] = error.code == "target_not_allowed"
        user.roles.clear()
        count = ModelCallLog.objects.count()
        try:
            generate_for_use(user, "solution", [{"role": "user", "content": "isolated-synthetic-input"}])
        except GatewayError as error:
            checks["revocation_blocks_next_call"] = error.code == "forbidden" and ModelCallLog.objects.count() == count
        checks["call_log_admin_readonly"] = request(admin, f"/admin/portal/modelcalllog/{ModelCallLog.objects.first().pk}/change/")[0] == 200 and request(admin, "/admin/portal/modelcalllog/add/")[0] == 403
        assert len(checks) == 13 and all(checks.values()), checks
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=10)
        for handle in handles:
            handle.close()
        for user in User.objects.all():
            assert user.username in credentials
            user.is_active = False
            user.set_unusable_password()
            user.save()
        Session.objects.all().delete()
        Provider.objects.update(enabled=False)
        GatewayModel.objects.update(enabled=False)
        ModelRoute.objects.update(enabled=False)
        for name in ("PORTAL_MODEL_GATEWAY_TOKEN", "MODEL_GATEWAY_SERVICE_TOKEN"):
            values[name] = ""
        (RUNTIME / "portal.env").write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
        report["cleanup"] = {"accounts_disabled": 2, "sessions_cleared": True, "isolated_services_stopped": True, "service_tokens_revoked": True}
        (EVIDENCE / "local-http-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
