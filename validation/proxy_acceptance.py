import http.client
import json
import secrets
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path


BASE_HOST = "127.0.0.1"
BASE_PORT = 18100
results = []
source_address = f"198.18.{secrets.randbelow(256)}.{secrets.randbelow(254) + 1}"
other_address = source_address.replace("198.18.", "198.19.", 1)


def request(path, method="GET", data=None, headers=None):
    connection = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=10)
    connection.request(method, path, body=json.dumps(data) if data is not None else None, headers=headers or {})
    response = connection.getresponse()
    content = response.read()
    returned = response.status, dict(response.getheaders()), content
    connection.close()
    return returned


def check(name, passed):
    results.append({"case": name, "passed": bool(passed)})
    if not passed:
        raise AssertionError(name)


def failed_login(source, username):
    headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": source}
    status, response_headers, raw = request("/api/csrf/", headers=headers)
    if status != 200:
        raise AssertionError("代理HTTPS下CSRF端点不可用")
    cookie = SimpleCookie()
    cookie.load(response_headers["Set-Cookie"])
    headers.update({"Cookie": "; ".join(f"{key}={value.value}" for key, value in cookie.items()),
                    "X-CSRFToken": json.loads(raw)["csrfToken"], "Content-Type": "application/json",
                    "Origin": "https://127.0.0.1:18100"})
    return request("/api/login/", "POST", {"username": username, "password": "deliberately-invalid-fixture"}, headers)[0]


try:
    check("容器直接HTTP重定向HTTPS", request("/health/")[0] == 301)
    trusted = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "192.0.2.11"}
    check("可信反代HTTPS头下健康200", request("/health/", headers=trusted)[0] == 200)
    status, headers, raw = request("/api/csrf/", headers=trusted)
    check("生产CSRF Cookie Secure HttpOnly SameSite", status == 200 and all(flag in headers.get("Set-Cookie", "") for flag in ("Secure", "HttpOnly", "SameSite=Lax")))
    check("HSTS一年", headers.get("Strict-Transport-Security", "").startswith("max-age=31536000"))
    check("未知Host拒绝", request("/health/", headers={**trusted, "Host": "invalid.example"})[0] == 400)
    check("生产无CSRF登录拒绝", request("/api/login/", "POST", {}, trusted)[0] == 403)
    run = secrets.token_hex(6)
    states = [failed_login(source_address, f"proxy_fixture_{run}_{index}") for index in range(31)]
    check("同一来源IP第31次被限流", states[:30] == [401] * 30 and states[30] == 429)
    check("不同来源IP不共享限流桶", failed_login(other_address, f"proxy_fixture_other_{run}") == 401)
finally:
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "host": BASE_HOST, "port": BASE_PORT,
              "synthetic_source_addresses": [source_address, other_address],
              "boundary": "真实Compose Waitress/Django HTTP检查；测试代理头模拟可信反代。并非真实TLS证书或已安装Nginx验收。仅隔离Compose数据库的不存在账号。",
              "results": results, "passed": sum(item["passed"] for item in results),
              "failed": sum(not item["passed"] for item in results)}
    Path("docs/evidence/proxy-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Proxy assertions: {report['passed']} passed, {report['failed']} failed")
