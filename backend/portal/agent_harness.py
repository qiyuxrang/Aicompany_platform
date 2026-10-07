"""Deep Agents graphs; native async tools are scoped at their dispatch boundary."""

import json
from typing import NotRequired, TypedDict

from asgiref.sync import async_to_sync, sync_to_async as io_sync_to_async
from deepagents import AsyncSubAgent, HarnessProfile, create_deep_agent, register_harness_profile
from deepagents.profiles.harness.harness_profiles import GeneralPurposeSubagentProfile
from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph_sdk.runtime import ServerRuntime

from .agent_model import GatewayChatModel
from .agent_db import database_boundary, database_sync_to_async as sync_to_async
from .agent_runtime import (AgentDenied, NativeRuntime, RunBinding, RuntimeGuard,
                            authorized_native_binding, same_deployment_client)
from .agent_storage import scoped_backend


class AgentContext(TypedDict):
    binding: str
    notification_key: NotRequired[str]
    operation_key: NotRequired[str]
    message_id: NotRequired[str | None]


class BoundaryMiddleware(AgentMiddleware):
    def __init__(self, guard, native=None, business_tools=()):
        self.guard, self.native = guard, native
        self.business_tools = frozenset(business_tools) - {
            "begin_work", "product_read_task", "product_read_source", "product_search_sources",
            "product_list_outputs", "finance_read_drafts", "hr_read_jd", "hr_read_batch",
            "gm_read_business", "gm_list_work", "gm_read_reference"}

    @database_boundary
    def _model_request(self, request):
        run = self.guard.check()
        hidden = set() if run.work_id and run.requirement_id and run.work.state != "completed" else set(self.business_tools)
        if run.parent_id:
            hidden.add("begin_work")
        return request.override(tools=[tool for tool in request.tools if
            (tool.get("name", tool.get("function", {}).get("name")) if isinstance(tool, dict) else tool.name) not in hidden])

    def wrap_model_call(self, request, handler):
        return handler(self._model_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(await sync_to_async(self._model_request)(request))

    @database_boundary
    def before_agent(self, state, runtime):
        self.guard.check()
        if runtime.context and runtime.context.get("notification_key"):
            self.guard.consume_notification(runtime.context["notification_key"])

    async def abefore_agent(self, state, runtime):
        await sync_to_async(self.guard.check)()
        if runtime.context and runtime.context.get("notification_key"):
            await sync_to_async(self.guard.consume_notification)(runtime.context["notification_key"])

    def wrap_tool_call(self, request, handler):
        return async_to_sync(self._tool)(request, io_sync_to_async(handler))

    async def awrap_tool_call(self, request, handler):
        return await self._tool(request, handler)

    async def _tool(self, request, handler):
        call = request.tool_call
        action = await sync_to_async(self.guard.admit)("tool")
        status = "error"
        try:
            if call["name"] in {"start_async_task", "check_async_task", "update_async_task",
                                "cancel_async_task", "list_async_tasks"}:
                if self.native is None:
                    raise AgentDenied("delegation_disabled")
                args = call["args"]
                operation = f"tool:{self.guard.binding.run_id}:{call['id']}"
                if call["name"] == "start_async_task":
                    result = await self.native.launch(args["subagent_type"], args["description"], operation)
                elif call["name"] == "update_async_task":
                    result = await self.native.update(args["task_id"], args["message"], operation)
                elif call["name"] == "check_async_task":
                    result = await self.native.check(args["task_id"])
                elif call["name"] == "cancel_async_task":
                    await sync_to_async(self.guard.child)(args["task_id"])
                    result = await self.native.cancel(args["task_id"])
                else:
                    result = await self.native.list()
                message = ToolMessage(json.dumps(result), tool_call_id=call["id"])
                output = Command(update={"messages": [message], "async_tasks": {
                    result["task_id"]: result}}) if call["name"] == "start_async_task" else message
            else:
                output = await handler(request)
            await sync_to_async(self.guard.check)()
            status = "finished"
            return output
        finally:
            await sync_to_async(self.guard.finish)(action, status)


def create_harness(model, guard, *, tools=(), native=None, store=None, checkpointer=None, skill_digest=None,
                   business_tools=()):
    if not isinstance(model, GatewayChatModel) or model.guard.binding != guard.binding:
        raise AgentDenied("unguarded_model")
    register_harness_profile("portal_agent", HarnessProfile(
        excluded_tools=frozenset({"execute", "task", "delete"}),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))
    subagents = [AsyncSubAgent(name=name, description=f"Complete an independent {name} task.", graph_id=name)
                 for name in sorted(native.graphs)] if native else []
    backend = scoped_backend(guard, store=store, skill_digest=skill_digest)
    return create_deep_agent(model=model, tools=list(tools),
        system_prompt="Use only authorized evidence. Delegate at most one level. After launch, continue independent work; do not poll in a loop. Completion is delivered by the service.",
        middleware=[SummarizationMiddleware(model, backend=backend,
            trigger=[("messages", 48), ("tokens", 60000)], keep=("messages", 12)),
            BoundaryMiddleware(guard, native, business_tools)], subagents=subagents,
        backend=backend,
        permissions=[FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny")],
        skills=["/skills/"] if skill_digest else None, context_schema=AgentContext,
        store=store, checkpointer=checkpointer, name="portal_agent")


def binding_from_runtime(runtime, config):
    authenticated = (RunBinding(**runtime.user["binding"]) if runtime.user is not None
                     else authorized_native_binding.get())
    if authenticated is None:
        raise AgentDenied("native_auth_context_required")
    execution = runtime.execution_runtime
    context = execution.context or {} if execution else {}
    binding = RunBinding.from_token(context["binding"]) if execution else authenticated
    if binding.root_id != authenticated.root_id or binding.owner_id != authenticated.owner_id:
        raise AgentDenied("context_identity_mismatch")
    if authenticated.run_id != authenticated.root_id and binding.run_id != authenticated.run_id:
        raise AgentDenied("context_identity_mismatch")
    thread_id = config.get("configurable", {}).get("thread_id")
    if not execution and authenticated.run_id == authenticated.root_id:
        from .agent_models import AgentRun
        run = AgentRun.objects.get(root_run_id=authenticated.root_id, native_thread_id=thread_id)
        binding = RunBinding(**{**authenticated.__dict__, "run_id": str(run.pk)})
    guard = RuntimeGuard(binding)
    run = guard.check()
    if run.native_thread_id != thread_id:
        raise AgentDenied("thread_binding_mismatch")
    return guard


async def _deployed_graph(config, runtime, name):
    from django.conf import settings
    from .agent_model import gateway_transport
    from .agent_tools import tools_for_run
    from . import model_gateway
    from .models import User
    from .agent_api import department
    from .agent_skills import skill_bundle
    from deepagents.backends import StoreBackend

    if not getattr(settings, "AGENT_PLATFORM_ENABLED", False):
        raise AgentDenied("runtime_not_enabled")
    guard = await sync_to_async(binding_from_runtime)(runtime, config)
    run = await sync_to_async(guard.check)()
    if bool(run.parent_id) == (name == "main"):
        raise AgentDenied("graph_role_mismatch")
    if run.native_thread_id != config.get("configurable", {}).get("thread_id"):
        raise AgentDenied("thread_binding_mismatch")
    presets = getattr(settings, "AGENT_RUNTIME_MODEL_PRESETS", {})
    user = await sync_to_async(User.objects.get)(pk=guard.binding.owner_id)
    user_department = await sync_to_async(department)(user)
    presets = presets.get(user_department, presets)
    preset = presets.get(name)
    if not isinstance(preset, dict) or set(preset) != {"route_code", "selection"}:
        raise AgentDenied("model_preset_required")
    fresh, route = await sync_to_async(model_gateway._route_for)(user, preset["route_code"])
    selected, _, _ = await sync_to_async(model_gateway._select_route_model)(fresh, route, preset["selection"])
    model = GatewayChatModel(model_name=selected.model_name, guard=guard,
        transport=gateway_transport(user, preset["route_code"], preset["selection"], guard))
    root, _ = await sync_to_async(guard._load)()
    execution = runtime.execution_runtime
    context = (execution.context or {}) if execution else {}
    operation = context.get("operation_key")
    intent = root.policy.get("dispatches", {}).get(operation, {})
    message_id = context.get("message_id") if execution else root.policy.get("current_message_id")
    if execution and (not intent or intent["thread_id"] != str(run.pk)
                      or intent["request"].get("message_id") != message_id):
        raise AgentDenied("message_binding_mismatch")
    tools = await sync_to_async(tools_for_run)(guard, message_id=message_id)
    bundle = await sync_to_async(skill_bundle)(user)
    if bundle["digest"] and execution:
        installer = StoreBackend(store=runtime.store, namespace=lambda _: ("agent-skills", bundle["digest"]))
        for path, source in bundle["files"].items():
            name = path.split("/")[2]
            await installer.awrite(path.removeprefix("/skills"),
                f"---\nname: {name}\ndescription: Approved {name} guidance\n---\n{source}")
    client = same_deployment_client(guard.binding)
    native = NativeRuntime(guard, client, set(presets) & {"researcher", "reviewer"}) if name == "main" else None
    return await sync_to_async(create_harness)(model, guard, tools=tools, native=native,
        store=runtime.store, skill_digest=bundle["digest"], business_tools=[tool.name for tool in tools])


async def main(config, runtime: ServerRuntime):
    return await _deployed_graph(config, runtime, "main")


async def researcher(config, runtime: ServerRuntime):
    return await _deployed_graph(config, runtime, "researcher")


async def reviewer(config, runtime: ServerRuntime):
    return await _deployed_graph(config, runtime, "reviewer")
