import json
import os
import socket
import subprocess
import sys

from bridge_environment import ROOT, RUNTIME, EVIDENCE, WORKTREE, PORTAL_PYTHON, LEDGER_PYTHON, environment, setup


def close_accounts(side):
    setup(side)
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.models import Session
    from django.db import transaction
    from django.utils import timezone

    names = json.loads((RUNTIME / f"bridge-{side}-credentials.json").read_text(encoding="utf-8"))
    if not names or any(not name.startswith("bridge_") for name in names):
        raise RuntimeError("Refuse to change non-fixture users")
    users = get_user_model()
    with transaction.atomic():
        accounts = list(users.objects.select_for_update().all())
        if not set(names).issubset({account.username for account in accounts}):
            raise RuntimeError("Registered fixture accounts do not match")
        if any(account.username not in names and not account.username.startswith("bridge_admin_http_") for account in accounts):
            raise RuntimeError("Unregistered account in fixture database")
        for account in accounts:
            account.is_active = False
            account.set_unusable_password()
            account.save()
        Session.objects.all().delete()
        if side == "portal":
            from portal.models import IntegrationTicket
            from portal.security import audit
            IntegrationTicket.objects.all().update(expires_at=timezone.now())
            audit(None, "test_fixture_cleanup", "registered bridge accounts only")
        if users.objects.filter(is_active=True).exists():
            raise RuntimeError("Unexpected active account remains in isolated database")
    report = {"side": side, "registered_accounts_disabled": len(accounts), "passwords_unusable": True,
              "sessions_cleared": True, "boundary": "Registered new isolated bridge database only; original native accounts and bjrunner untouched"}
    (EVIDENCE / f"cleanup-{side}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def main():
    for port in (18310, 18318):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("Stop only owned isolated services before credential cleanup")
    for side, python, directory in (("portal", PORTAL_PYTHON, ROOT), ("ledger", LEDGER_PYTHON, WORKTREE)):
        values = environment(side)
        subprocess.run([str(python), str(ROOT / "validation/bridge_cleanup.py"), side], cwd=directory,
                       env={**os.environ, **values, "PYTHONIOENCODING": "utf-8"}, check=True)
        values.update({"PORTAL_INTEGRATION_SECRET": "", "PORTAL_BUSINESS_SUMMARY_URL": ""} if side == "portal" else
                      {"LEDGER_PORTAL_BRIDGE_SECRET": "", "LEDGER_PORTAL_BRIDGE_ENABLED": "0"})
        (RUNTIME / f"bridge-{side}.env").write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    print("Isolated fixture accounts, sessions and bridge secrets revoked; databases retained")


if __name__ == "__main__":
    if len(sys.argv) == 2:
        if sys.argv[1] not in {"portal", "ledger"}:
            raise SystemExit("Unknown isolated side")
        close_accounts(sys.argv[1])
    else:
        main()
