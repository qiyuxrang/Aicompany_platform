"""Synthetic fixture using normal settings, URLs, migrations and password hashing."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--uuid", required=True)
    args = parser.parse_args()
    identifier = uuid.UUID(args.uuid).hex
    run = args.run_dir.resolve()
    if run != (ROOT / ".runtime/browser-acceptance" / identifier).resolve() or not run.is_dir():
        raise ValueError("An existing owned UUID directory is required")
    gate = json.loads(sys.stdin.readline())
    if gate.get("uuid") != identifier:
        raise ValueError("Owned fixture stdin gate UUID mismatch")
    from qa.browser_acceptance.process_job import join_owned_job
    join_owned_job(gate["job_name"])
    if (os.environ.get("PORTAL_DB_NAME") != "portal_pg_" + identifier
            or os.environ.get("PORTAL_DB_HOST") != "127.0.0.1"
            or os.environ.get("DJANGO_SETTINGS_MODULE") != "config.settings"):
        raise ValueError("Only this fixture's loopback PostgreSQL is accepted")
    password = os.environ.pop("BROWSER_FIXTURE_PASSWORD")
    token = os.environ.pop("BROWSER_FIXTURE_TOKEN")
    # Reject accidental outbound access even if a future feature ignores its flag.
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def local_connect(connection, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            with (run / "denied-network.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps({"host": str(address[0]), "port": address[1]}) + "\n")
            raise OSError("External connections are disabled in browser acceptance")
        return connect(connection, address)

    def local_connect_ex(connection, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            return local_connect(connection, address)
        return connect_ex(connection, address)

    socket.socket.connect = local_connect
    socket.socket.connect_ex = local_connect_ex
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection, connections
    from portal.models import Role, User
    from portal.business_models import BusinessLedgerGrant
    if (connection.vendor != "postgresql" or settings.ROOT_URLCONF != "config.urls"
            or settings.PASSWORD_HASHERS[0] != "django.contrib.auth.hashers.PBKDF2PasswordHasher"
            or settings.AGENT_PLATFORM_ENABLED or settings.PRODUCT_MODEL_CALLS_ALLOWED
            or settings.PRODUCT_KNOWLEDGE_ENABLED or settings.PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED
            or settings.PRODUCT_KNOWLEDGE_AUTHORIZATIONS != {} or settings.PRODUCT_RETRIEVAL_ENABLED):
        raise ValueError("Normal PostgreSQL/URL/hash settings and disabled external features required")
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        if cursor.fetchone()[0]:
            raise ValueError("Refusing to seed or migrate a nonempty database")
    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_portal", verbosity=0)
    accounts = {
        "product": ("product", "product"), "engineering": ("engineering", "engineering"),
        "hr": ("hr", "hr"), "finance": ("finance", "finance"),
        "manager": ("general_manager", "general_manager"), "ops": ("platform_admin", ""),
    }
    for name, (role, department) in accounts.items():
        assigned, _ = Role.objects.get_or_create(code=role, defaults={"name": "合成验收角色"})
        user = User.objects.create_user(username="browser-" + name, password=password,
            display_name="合成验收-" + name, department_code=department, must_change_password=False)
        user.roles.add(assigned)
        if not user.password.startswith("pbkdf2_sha256$"):
            raise ValueError("Synthetic password must use the production default hasher")
        if name in ("finance", "engineering", "product"):
            BusinessLedgerGrant.objects.create(user=user,
                department={"finance": "finance", "engineering": "engineering", "product": "presales"}[name],
                can_edit=True, can_submit=True, can_publish=True)
    user = User.objects.create_user(username="browser-first-login", password=password,
        display_name="合成首次登录", department_code="hr", must_change_password=True)
    user.roles.add(Role.objects.get(code="hr"))
    connections.close_all()
    from django.core.wsgi import get_wsgi_application
    from waitress import create_server
    application = get_wsgi_application()
    identity = {"uuid": identifier, "synthetic": True, "database": "postgresql",
                "job_name": gate["job_name"],
                "urlconf": settings.ROOT_URLCONF, "default_hasher": settings.PASSWORD_HASHERS[0],
                "roles": list(accounts), "agent_enabled": False, "real_model_calls": False,
                "knowledge_scope_unassigned": settings.PRODUCT_KNOWLEDGE_AUTHORIZATIONS == {},
                "owned_pid": os.getpid(), "parent_pid": os.getppid()}

    def wrapped(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith("/__browser__/"):
            authenticated = environ.get("HTTP_X_BROWSER_TOKEN") == token
            if not authenticated:
                status, body = "403 Forbidden", {"code": "fixture_forbidden"}
            elif path == "/__browser__/identity":
                status, body = "200 OK", identity
            elif path == "/__browser__/shutdown":
                def stop():
                    time.sleep(.2)
                    server.close()
                    os._exit(0)
                threading.Thread(target=stop, daemon=True).start()
                status, body = "200 OK", {"status": "owned_shutdown_scheduled"}
            else:
                status, body = "404 Not Found", {"code": "fixture_route_missing"}
            data = json.dumps(body).encode()
            start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(data)))])
            return [data]
        return application(environ, start_response)

    server = create_server(wrapped, host="127.0.0.1", port=0, threads=4)
    identity["port"] = int(server.effective_port)
    with (run / "ready.json").open("x", encoding="utf-8") as file:
        json.dump(identity, file)
    server.run()


if __name__ == "__main__":
    main()
