import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import bridge_http_acceptance as acceptance
from bridge_environment import EVIDENCE, WORKTREE
from portal.models import IntegrationTicket


def main():
    clients = []
    scopes = {}
    before_business = acceptance.fingerprints()
    for name in ("manager", "sales", "engineering"):
        native = acceptance.Client(acceptance.LEDGER)
        native.login(name)
        rows = acceptance.require_status(native.request("/api/projects/"), 200)
        if isinstance(rows, dict):
            rows = rows["results"]
        scopes[name] = sorted((row["id"], row["name"]) for row in rows)
        for session in range(3):
            portal = acceptance.Client(acceptance.PORTAL)
            portal.login(name)
            clients.append((name, portal))
    waves = []
    for wave in range(3):
        barrier = Barrier(len(clients) + 1)
        before_tickets = IntegrationTicket.objects.count()

        def read(item):
            name, client = item
            barrier.wait(timeout=10)
            started = time.monotonic()
            status, payload = client.request("/api/business/summary/")
            duration = time.monotonic() - started
            assert status in (200, 503), f"Unexpected status {status}"
            assert duration < 6, "Possible callback starvation"
            if status == 200:
                actual = sorted((row["id"], row["name"]) for row in payload["projects"])
                assert actual == scopes[name], "Cross-user project scope mismatch"
                assert payload["summary"] == {"project_count": len(scopes[name])}
            else:
                assert "繁忙" in payload.get("detail", ""), "Upstream failed rather than bounded capacity rejection"
            return {"user": "bridge_" + name, "status": status, "seconds": round(duration, 3)}

        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            futures = [pool.submit(read, item) for item in clients]
            barrier.wait(timeout=10)
            results = [future.result(timeout=15) for future in futures]
        successes = sum(item["status"] == 200 for item in results)
        busy = sum(item["status"] == 503 for item in results)
        assert successes > 0 and busy > 0, "Wave must exercise successful callbacks and capacity rejection"
        assert IntegrationTicket.objects.count() - before_tickets == successes, "Busy requests minted tickets"
        for name, client in clients[::3]:
            payload = acceptance.require_status(client.request("/api/business/summary/"), 200)
            assert sorted((row["id"], row["name"]) for row in payload["projects"]) == scopes[name]
        acceptance.require_status(clients[0][1].request("/health/"), 200)
        waves.append({"wave": wave + 1, "requests": results, "success": successes, "busy": busy,
                      "busy_minted_tickets": False, "post_wave_recovery": True, "status": "passed"})
    assert acceptance.fingerprints() == before_business, "Legacy business data changed"
    report = {"legacy_commit": subprocess.check_output(["git", "-C", str(WORKTREE), "rev-parse", "HEAD"]).decode().strip(),
              "portal_workers": 8, "portal_summary_slots": 2, "waves": waves, "business_tables_unchanged": True,
              "boundary": "27 concurrent real native bridge requests with synthetic isolated users; successful responses compared to each native user's project scope. Smoke regression only, not production load capacity or browser SSO."}
    (EVIDENCE / "concurrency-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Three waves / 27 concurrent requests passed; callbacks recovered, busy requests minted no tickets")


if __name__ == "__main__":
    main()
