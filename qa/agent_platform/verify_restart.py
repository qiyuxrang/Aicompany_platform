import asyncio
import json
import os
from pathlib import Path

import django
from asgiref.sync import sync_to_async
from langgraph_sdk import get_client
from qa.agent_platform.evidence_paths import evidence_path, runtime_url

if os.environ.get("DJANGO_SETTINGS_MODULE") != "qa.agent_platform.test_settings":
    raise RuntimeError("A0 isolated settings required")
django.setup()

from portal.agent_models import AgentRun, AgentEvent
from portal.agent_runtime import RuntimeGuard, bind_run, NativeRuntime


async def verify():
    path = evidence_path("native-evidence.json")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    root = await sync_to_async(AgentRun.objects.select_related("conversation").get)(pk=evidence["root_id"])
    binding = await sync_to_async(bind_run)(root.pk, root.conversation.owner_id)
    before = (root.action_count, root.model_count, root.tool_count, root.launch_count)
    event_count = await sync_to_async(AgentEvent.objects.filter(root=root).count)()
    async with get_client(url=runtime_url(), api_key=None, headers={
        "Authorization": "Bearer " + os.environ["A0_SERVICE_TOKEN"], "X-Agent-Binding": binding.token()}) as client:
        state = await client.threads.get_state(root.native_thread_id)
        assert state["values"]["messages"]
        native = NativeRuntime(RuntimeGuard(binding), client, {"researcher", "reviewer"})
        replayed = await native.start("Complete two independent synthetic tasks", "start", graph_id="supervisor")
        assert replayed["run_id"] == evidence["native_run"]["run_id"]
        await native.reconcile()
        await native.reconcile()
    await sync_to_async(root.refresh_from_db)()
    after = (root.action_count, root.model_count, root.tool_count, root.launch_count)
    assert before == after
    assert await sync_to_async(AgentEvent.objects.filter(root=root).count)() == event_count
    assert all(item["state"] == "consumed" for item in root.policy["notifications"].values())
    result = {"simulation": True, "root_id": str(root.pk), "native_dev_restart": "PASS",
        "counts_before": before, "counts_after": after, "event_count": event_count,
        "historical_native_run_id": replayed["run_id"], "deadline_unchanged": root.deadline_at.isoformat()}
    path.with_name("restart-evidence.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(verify())
