"""Synthetic models on the real Deep Agents / Agent Server execution path."""

import asyncio
import hmac
import json
import os
import time
import signal
from contextlib import asynccontextmanager

import django
from asgiref.sync import sync_to_async
from langgraph_sdk import Auth, get_client
from langgraph_sdk.runtime import ServerRuntime
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from langchain_core.tools import tool
from langchain.tools import ToolRuntime

if os.environ.get("DJANGO_SETTINGS_MODULE") != "qa.agent_platform.test_settings":
    raise RuntimeError("A0 isolated settings required")
django.setup()

from portal.agent_harness import create_harness, binding_from_runtime
from portal.agent_model import GatewayChatModel
from portal.agent_models import AgentRun
from portal.agent_runtime import (NativeRuntime, RuntimeGuard, RunBinding, bind_run, AgentDenied,
                                  auth, same_deployment_client, runtime_lifespan)


def record(root_id, name, event):
    from portal.agent_models import append_public_event
    append_public_event(root_id, root_id, f"{name}:{event}", "synthetic_evidence",
                        {"model": name, "event": event, "at": time.time()})


def synthetic_transport(guard, role, scenario="parallel"):
    def invoke(**payload):
        root_id = guard.binding.root_id
        if role != "supervisor":
            record(root_id, role, "start")
            time.sleep(1.2)
            record(root_id, role, "end")
            return {"content": f"{role} synthetic done", "tool_calls": [], "prompt_tokens": None, "completion_tokens": None}
        messages = payload["messages"]
        initial = not any(message["role"] == "assistant" for message in messages)
        calls = []
        if initial:
            calls = [{"id": "effect-once", "type": "function", "function": {
                "name": "synthetic_persist", "arguments": "{}"}}] if scenario == "crash" else [{"id": f"launch-{name}", "type": "function", "function": {
                "name": "start_async_task", "arguments": json.dumps({"description": "synthetic independent task", "subagent_type": name})}}
                for name in ("researcher", "reviewer")]
        else:
            record(root_id, role, "independent_work")
        return {"content": None if calls else "main independent work complete", "tool_calls": calls,
                "prompt_tokens": None, "completion_tokens": None}
    return invoke


async def graph_for(config, runtime, role):
    guard = await sync_to_async(binding_from_runtime)(runtime, config)
    binding = guard.binding
    run = await sync_to_async(guard.check)()
    if run.native_thread_id != config.get("configurable", {}).get("thread_id"):
        raise AgentDenied("thread_binding_mismatch")
    client = same_deployment_client(binding)
    native = NativeRuntime(guard, client, {"researcher", "reviewer"}) if role == "supervisor" else None
    model = GatewayChatModel(model_name=f"synthetic-{role}", guard=guard,
                             transport=synthetic_transport(guard, role, run.policy.get("scenario", "parallel")))

    @tool
    def synthetic_persist(runtime: ToolRuntime) -> str:
        """Commit one synthetic side effect; crash once before the graph checkpoint."""
        from portal.agent_models import append_public_event
        _, created = append_public_event(binding.root_id, binding.run_id,
            "effect:" + runtime.tool_call_id, "synthetic_committed_effect", {"object_id": "synthetic-object"})
        if created:
            raise RuntimeError("synthetic crash after commit before checkpoint")
        return "existing committed effect reconciled"

    return await sync_to_async(create_harness)(model, guard, native=native,
        tools=[synthetic_persist] if run.policy.get("scenario") == "crash" else [])


async def supervisor(config, runtime: ServerRuntime):
    return await graph_for(config, runtime, "supervisor")


async def researcher(config, runtime: ServerRuntime):
    return await graph_for(config, runtime, "researcher")


async def reviewer(config, runtime: ServerRuntime):
    return await graph_for(config, runtime, "reviewer")


async def reconcile(request):
    if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + os.environ["A0_SERVICE_TOKEN"]):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    binding = RunBinding.from_token((await request.json())["binding"])
    async with same_deployment_client(binding) as client:
        result = await NativeRuntime(RuntimeGuard(binding), client, {"researcher", "reviewer"}).reconcile()
    return JSONResponse(result)


async def shutdown(request):
    if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + os.environ["A0_SERVICE_TOKEN"]):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    asyncio.get_running_loop().call_later(0.3, signal.raise_signal, signal.SIGINT)
    return JSONResponse({"synthetic_development_server": "shutdown_requested"})


app = Starlette(lifespan=runtime_lifespan, routes=[Route("/a0/reconcile", reconcile, methods=["POST"]),
    Route("/a0/shutdown", shutdown, methods=["POST"])])
