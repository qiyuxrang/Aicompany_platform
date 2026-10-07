import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import django
import httpx
from asgiref.sync import sync_to_async
from langgraph_sdk import get_client

if os.environ.get("DJANGO_SETTINGS_MODULE") != "qa.agent_platform.test_settings":
    raise RuntimeError("A0 isolated settings required")
django.setup()

from portal.agent_models import AgentEvent, AgentRun
from portal.agent_runtime import NativeRuntime
from portal.agent_runtime import RunBinding
from portal.models import User
from portal.tests.test_agent_runtime import make_guard


async def verify():
    guard = await sync_to_async(make_guard)("native-" + uuid.uuid4().hex, max_actions=80,
        max_model_calls=20, max_tool_calls=30, max_launches=16, max_concurrent=10)
    token = os.environ["A0_SERVICE_TOKEN"]
    client = get_client(url="http://127.0.0.1:18743", api_key=None,
                        headers={"Authorization": "Bearer " + token, "X-Agent-Binding": guard.binding.token()})
    native = NativeRuntime(guard, client, {"researcher", "reviewer"})
    run = await native.start("Complete two independent synthetic tasks", "start", graph_id="supervisor")
    await client.runs.join(run["thread_id"], run["run_id"])
    status = await client.runs.get(run["thread_id"], run["run_id"])
    assert status["status"] == "success", status
    children = await sync_to_async(list)(AgentRun.objects.filter(parent_id=guard.binding.root_id))
    assert len(children) == 2, len(children)
    await asyncio.gather(*(client.runs.join(child.native_thread_id, child.native_run_id) for child in children))
    if os.environ.get("A0_AUTO_RECONCILE") == "1":
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            current = await sync_to_async(AgentRun.objects.get)(pk=guard.binding.root_id)
            notices = current.policy.get("notifications", {})
            if len(notices) == 2 and all(notice["state"] == "consumed" for notice in notices.values()):
                break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError("native lifespan did not deliver two child results")
    async with httpx.AsyncClient(trust_env=False) as http:
        for _ in range(2):
            result = await http.post("http://127.0.0.1:18743/a0/reconcile",
                headers={"Authorization": "Bearer " + token}, json={"binding": guard.binding.token()})
            assert result.status_code == 200, result.text
        unauthorized = await http.get(f"http://127.0.0.1:18743/threads/{run['thread_id']}/state")
        assert unauthorized.status_code == 401, unauthorized.text
        foreign = await sync_to_async(make_guard)("foreign-" + uuid.uuid4().hex)
        foreign_run = await sync_to_async(foreign.check)()
        foreign_run.native_thread_id = str(foreign_run.pk)
        await sync_to_async(foreign_run.save)(update_fields=["native_thread_id"])
        foreign_headers = {"Authorization": "Bearer " + token, "X-Agent-Binding": foreign.binding.token()}
        foreign_client = get_client(url="http://127.0.0.1:18743", api_key=None, headers=foreign_headers)
        await foreign_client.threads.create(thread_id=foreign_run.native_thread_id,
            metadata={"root_id": foreign.binding.root_id})
        denied_state = await http.get(f"http://127.0.0.1:18743/threads/{run['thread_id']}/state", headers=foreign_headers)
        denied_cancel = await http.post(f"http://127.0.0.1:18743/threads/{run['thread_id']}/runs/{run['run_id']}/cancel", headers=foreign_headers)
        denied_store = await http.post("http://127.0.0.1:18743/store/items/search", headers=foreign_headers,
                                       json={"namespace_prefix": []})
        studio = await http.get(f"http://127.0.0.1:18743/threads/{run['thread_id']}/state",
                                 headers={"x-auth-scheme": "langsmith"})
        assert denied_state.status_code in {403, 404}, denied_state.text
        assert denied_cancel.status_code in {403, 404}, denied_cancel.text
        assert denied_store.status_code == 403, denied_store.text
        assert studio.status_code == 401, studio.text
        same_owner = await sync_to_async(make_guard)(user=await sync_to_async(User.objects.get)(pk=guard.binding.owner_id))
        same_headers = {"Authorization": "Bearer " + token, "X-Agent-Binding": same_owner.binding.token()}
        same_owner_state = await http.get(f"http://127.0.0.1:18743/threads/{run['thread_id']}/state", headers=same_headers)
        assert same_owner_state.status_code == 403, same_owner_state.text
        child_binding = RunBinding(**{**guard.binding.__dict__, "run_id": str(children[0].pk)})
        child_headers = {"Authorization": "Bearer " + token, "X-Agent-Binding": child_binding.token()}
        child_parent = await http.get(f"http://127.0.0.1:18743/threads/{run['thread_id']}/state", headers=child_headers)
        child_sibling = await http.get(f"http://127.0.0.1:18743/threads/{children[1].native_thread_id}/state", headers=child_headers)
        assert child_parent.status_code == child_sibling.status_code == 403
        history = await http.post(f"http://127.0.0.1:18743/threads/{run['thread_id']}/history",
            headers=foreign_headers, json={"limit": 10})
        assert history.status_code == 403, history.text
    root = await sync_to_async(AgentRun.objects.get)(pk=guard.binding.root_id)
    notices = root.policy.get("notifications", {})
    assert len(notices) == 2, notices
    for notice in notices.values():
        await client.runs.join(root.native_thread_id, notice["run_id"])
        assert (await client.runs.get(root.native_thread_id, notice["run_id"]))["status"] == "success"
    root = await sync_to_async(AgentRun.objects.get)(pk=guard.binding.root_id)
    notices = root.policy.get("notifications", {})
    assert all(notice["state"] == "consumed" for notice in notices.values()), notices
    events = await sync_to_async(list)(AgentEvent.objects.filter(root=root).values("type", "payload", "event_key"))
    timing = {event["event_key"]: event["payload"]["at"] for event in events if event["type"] == "synthetic_evidence"}
    overlap = max(timing["researcher:start"], timing["reviewer:start"]) < min(timing["researcher:end"], timing["reviewer:end"])
    main_continued = timing["supervisor:independent_work"] < max(timing["researcher:end"], timing["reviewer:end"])
    assert overlap and main_continued, timing
    evidence = {"root_id": str(root.pk), "native_run": run, "simulation": True,
        "automatic_native_reconcile": os.environ.get("A0_AUTO_RECONCILE") == "1",
        "real_models": "NOT_EXECUTED", "overlap": overlap, "main_continued": main_continued,
        "notifications": notices, "events": events, "counts": {"model": root.model_count,
        "tool": root.tool_count, "launch": root.launch_count}, "unauthenticated_state": 401,
        "foreign_state": denied_state.status_code, "foreign_cancel": denied_cancel.status_code,
        "global_store": denied_store.status_code, "studio_bypass": studio.status_code}
    evidence.update(same_owner_foreign_root=same_owner_state.status_code,
        child_parent=child_parent.status_code, child_sibling=child_sibling.status_code,
        foreign_history=history.status_code)
    crash_guard = await sync_to_async(make_guard)("crash-" + uuid.uuid4().hex)
    crash_root = await sync_to_async(crash_guard.check)()
    crash_root.policy["scenario"] = "crash"
    await sync_to_async(crash_root.save)(update_fields=["policy"])
    crash_client = get_client(url="http://127.0.0.1:18743", api_key=None, headers={
        "Authorization": "Bearer " + token, "X-Agent-Binding": crash_guard.binding.token()})
    crash_native = NativeRuntime(crash_guard, crash_client, {"researcher", "reviewer"})
    failed = await crash_native.start("synthetic checkpoint failure", "crash-start", graph_id="supervisor")
    await crash_client.runs.join(failed["thread_id"], failed["run_id"])
    assert (await crash_client.runs.get(failed["thread_id"], failed["run_id"]))["status"] == "error"
    resumed = await crash_native.resume(None, "explicit-resume")
    await crash_client.runs.join(resumed["thread_id"], resumed["run_id"])
    assert (await crash_client.runs.get(resumed["thread_id"], resumed["run_id"]))["status"] == "success"
    effect_count = await sync_to_async(AgentEvent.objects.filter(root_id=crash_guard.binding.root_id,
        type="synthetic_committed_effect").count)()
    assert effect_count == 1, effect_count
    evidence["checkpoint_failure"] = {"failed_native_run": failed["run_id"],
        "resumed_native_run": resumed["run_id"], "same_thread": resumed["thread_id"] == failed["thread_id"],
        "durable_synthetic_effect_count": effect_count}
    pending_child = await crash_native.launch("researcher", "synthetic cancellation", "cancel-on-revoke")
    await sync_to_async(User.objects.filter(pk=crash_guard.binding.owner_id).update)(is_active=False)
    async with httpx.AsyncClient(trust_env=False) as http:
        revoked = await http.get(f"http://127.0.0.1:18743/threads/{failed['thread_id']}/state",
            headers={"Authorization": "Bearer " + token, "X-Agent-Binding": crash_guard.binding.token()})
        assert revoked.status_code == 403, revoked.text
    maintenance = await crash_native.reconcile()
    assert any(item["task_id"] == pending_child["task_id"] and item["status"] == "cancelled" for item in maintenance), maintenance
    evidence.update(revoked_state=revoked.status_code, revoked_non_llm_cancel=maintenance)
    path = Path(__file__).parent / "native-evidence.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    path.with_name("native-evidence-" + str(root.pk) + ".json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(verify())
