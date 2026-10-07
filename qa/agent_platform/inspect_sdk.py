import inspect
import json
import os
import uuid
from importlib.metadata import version
from pathlib import Path

import django
from langgraph_sdk import get_client
from langgraph_sdk.client import RunsClient
from langgraph.store.memory import InMemoryStore

if os.environ.get("DJANGO_SETTINGS_MODULE") != "qa.agent_platform.test_settings":
    raise RuntimeError("A0 isolated settings required")
django.setup()

from portal.agent_harness import create_harness
from portal.agent_model import GatewayChatModel
from portal.agent_tools import tools_for_run
from portal.models import User, Role, Module
from portal.tests.test_agent_runtime import make_guard, native_stub
from portal.agent_models import AgentWorkTask, AgentRequirement, AgentMessage, AgentRun
from portal.agent_runtime import RuntimeGuard, RunBinding


def inspect_environment():
    user = User.objects.create_user(username="inventory-" + uuid.uuid4().hex,
        must_change_password=False, department_code="product")
    role, _ = Role.objects.get_or_create(code="product", defaults={"name": "synthetic product"})
    module, _ = Module.objects.get_or_create(code="product", defaults={"name": "synthetic product"})
    role.modules.add(module)
    user.roles.add(role)
    user.refresh_from_db()
    guard = make_guard(user=user)
    root = guard.check()
    work = AgentWorkTask.objects.create(owner=user, conversation=root.conversation, department_code="product",
        goal="synthetic inventory", current_requirement_version=1)
    message = AgentMessage.objects.create(conversation=root.conversation, role="user", content="synthetic inventory")
    requirement = AgentRequirement.objects.create(work=work, version=1, user_message=message, content=message.content)
    root.work, root.requirement = work, requirement
    root.save(update_fields=["work", "requirement"])
    child = AgentRun.objects.create(root_run_id=root.pk, parent=root, conversation=root.conversation,
        work=work, requirement=requirement)
    inventory = {}
    for role_name in ("main", "child"):
        current_guard = guard if role_name == "main" else RuntimeGuard(RunBinding(
            **{**guard.binding.__dict__, "run_id": str(child.pk)}))
        def transport(**payload):
            inventory[role_name] = sorted(item["function"]["name"] for item in payload["tools"])
            return {"content": "synthetic inventory only", "tool_calls": [],
                    "prompt_tokens": None, "completion_tokens": None}
        model = GatewayChatModel(model_name="synthetic-inventory", guard=current_guard, transport=transport)
        tools = tools_for_run(current_guard)
        graph = create_harness(model, current_guard, tools=tools, store=InMemoryStore(),
            business_tools=[tool.name for tool in tools],
            native=native_stub(guard) if role_name == "main" else None)
        graph.invoke({"messages": [("user", "inventory only")]})
    result = {"simulation": True, "versions": {name: version(name) for name in (
        "deepagents", "langchain", "langchain-core", "langgraph", "langgraph-sdk",
        "langgraph-api", "langgraph-runtime-inmem", "langgraph-cli", "django")},
        "signatures": {"get_client": str(inspect.signature(get_client)),
            "runs.list": str(inspect.signature(RunsClient.list)),
            "runs.create": str(inspect.signature(RunsClient.create)),
            "runs.cancel": str(inspect.signature(RunsClient.cancel))},
        "actual_product_tool_inventory": inventory,
        "disabled_tools": ["execute", "task", "delete"], "no_host_filesystem": True}
    Path(__file__).with_name("sdk-evidence.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    inspect_environment()
