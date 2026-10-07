import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from asgiref.sync import async_to_sync
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langchain_core.tools import tool

from portal.agent_harness import BoundaryMiddleware, create_harness, main, binding_from_runtime
from portal.agent_model import GatewayChatModel
from portal.agent_models import AgentRun, AgentMessage, AgentWorkTask
from portal.agent_runtime import AgentDenied
from portal.agent_storage import scoped_backend
from portal.models import User
from portal.tests.test_agent_runtime import make_guard, native_stub
from portal.tests.test_agent_model import ModelAdapterTests


class HarnessTests(TransactionTestCase):
    def test_native_summary_uses_metered_model_and_guarded_history(self):
        guard = make_guard()
        store = InMemoryStore()
        calls = []

        def transport(**payload):
            calls.append(payload)
            return {"content": "synthetic summary" if len(calls) == 1 else "done", "tool_calls": [],
                    "prompt_tokens": None, "completion_tokens": None}

        graph = create_harness(GatewayChatModel(model_name="summary", guard=guard, transport=transport),
            guard, store=store)
        messages = [("user" if index % 2 == 0 else "assistant", f"synthetic history {index}")
                    for index in range(51)]
        result = graph.invoke({"messages": messages})
        self.assertEqual(result["messages"][-1].content, "done")
        self.assertEqual(len(calls), 2)
        self.assertEqual(AgentRun.objects.get(pk=guard.binding.root_id).model_count, 2)
        self.assertEqual(len({call["physical_call_id"] for call in calls}), 2)
        self.assertTrue(all(len(call["messages"]) <= 64 for call in calls))
        backend = scoped_backend(guard, store=store)
        history = backend.glob("**", "/conversation_history")
        self.assertIsNone(history.error)
        self.assertTrue(history.matches)
        User.objects.filter(pk=guard.binding.owner_id).update(grant_version=2)
        with self.assertRaises(AgentDenied):
            backend.glob("**", "/conversation_history")

    def test_native_large_tool_eviction_and_revoked_offload_read(self):
        guard = make_guard()
        store = InMemoryStore()
        calls = []

        @tool
        def synthetic_large_result() -> str:
            """Return an intentionally oversized synthetic result."""
            return "synthetic-only " * 10000

        def transport(**payload):
            calls.append(payload)
            return {"content": None if len(calls) == 1 else "done", "tool_calls": [
                {"id": "large-1", "type": "function", "function": {
                    "name": "synthetic_large_result", "arguments": "{}"}}] if len(calls) == 1 else [],
                "prompt_tokens": None, "completion_tokens": None}

        graph = create_harness(GatewayChatModel(model_name="offload", guard=guard, transport=transport),
            guard, store=store, tools=[synthetic_large_result])
        graph.invoke({"messages": [("user", "large synthetic result")]})
        backend = scoped_backend(guard, store=store)
        files = backend.glob("**", "/large_tool_results")
        self.assertIsNone(files.error)
        self.assertTrue(files.matches)
        self.assertLess(len(json.dumps(calls[-1]["messages"])), 140000)
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual((root.model_count, root.tool_count), (2, 1))
        User.objects.filter(pk=guard.binding.owner_id).update(grant_version=2)
        with self.assertRaises(AgentDenied):
            backend.read(files.matches[0]["path"])

    def test_registered_main_factory_calls_real_begin_work_for_bound_message(self):
        guard, user, selected, route = ModelAdapterTests.gateway_fixture(self)
        native = native_stub(guard)
        root = guard.check()
        message = AgentMessage.objects.create(conversation=root.conversation, role="user", content="synthetic work")
        operation = "message:" + str(message.pk)
        async_to_sync(native.start)(message.content, operation)
        context = native.client.runs.create.call_args.kwargs["context"]
        runtime = SimpleNamespace(user={"binding": guard.binding.__dict__},
            execution_runtime=SimpleNamespace(context=context), store=InMemoryStore())
        config = {"configurable": {"thread_id": guard.binding.run_id}}
        calls = []
        def transport(**payload):
            calls.append(payload)
            return {"content": None if len(calls) == 1 else "tracked", "tool_calls":
                [{"id": "work-once", "type": "function", "function": {"name": "begin_work", "arguments": "{}"}}]
                if len(calls) == 1 else [], "prompt_tokens": None, "completion_tokens": None}
        with override_settings(AGENT_PLATFORM_ENABLED=True, AGENT_RUNTIME_MODEL_PRESETS={"product": {
                "main": {"route_code": route.code, "selection": None}}}), patch(
                "portal.agent_model.gateway_transport", return_value=transport), patch(
                "portal.agent_harness.same_deployment_client", return_value=native.client):
            graph = async_to_sync(main)(config, runtime)
            output = graph.invoke({"messages": [("user", message.content)]}, config, context=context)
        self.assertEqual(output["messages"][-1].content, "tracked")
        initial_names = {tool["function"]["name"] for tool in calls[0]["tools"]}
        after_work_names = {tool["function"]["name"] for tool in calls[1]["tools"]}
        self.assertIn("begin_work", initial_names)
        self.assertTrue({"product_read_task", "product_read_source", "product_search_sources",
                         "product_list_outputs"}.issubset(initial_names))
        self.assertNotIn("product_create_task", initial_names)
        self.assertIn("product_create_task", after_work_names)
        self.assertEqual(AgentWorkTask.objects.count(), 1)
        root.refresh_from_db()
        self.assertIsNotNone(root.work_id)
        self.assertEqual((root.model_count, root.tool_count, root.launch_count), (2, 1, 1))
        foreign = make_guard("foreign-factory")
        with self.assertRaisesRegex(AgentDenied, "context_identity_mismatch"):
            binding_from_runtime(SimpleNamespace(user={"binding": foreign.binding.__dict__},
                execution_runtime=SimpleNamespace(context=context)), config)

    def test_native_tools_loop_and_checkpoint_restore_reauthorization(self):
        guard = make_guard()
        inventory = []

        def transport(**payload):
            inventory.append(sorted(tool["function"]["name"] for tool in payload["tools"]))
            calls = [] if len(inventory) > 1 else [{"id": "write-1", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"file_path": "/workspace/check.txt", "content": "synthetic"})}}]
            return {"content": None if calls else "done", "tool_calls": calls,
                    "prompt_tokens": None, "completion_tokens": None}

        model = GatewayChatModel(model_name="synthetic-harness", guard=guard, transport=transport)
        graph = create_harness(model, guard, store=InMemoryStore(), checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": guard.binding.run_id}}
        result = graph.invoke({"messages": [("user", "write synthetic file")]}, config)
        self.assertEqual(result["messages"][-1].content, "done")
        self.assertTrue({"ls", "read_file", "write_file", "edit_file", "glob", "grep"}.issubset(inventory[0]))
        self.assertFalse({"execute", "task", "delete", "start_async_task"} & set(inventory[0]))
        User.objects.filter(pk=guard.binding.owner_id).update(grant_version=2)
        with self.assertRaises(AgentDenied):
            graph.invoke({"messages": [("user", "resume")]}, config)
        self.assertEqual(len(inventory), 2)

    def test_read_only_tools_do_not_require_or_duplicate_business_work(self):
        guard = make_guard()
        tools = [{"name":name} for name in ("begin_work", "gm_list_work", "gm_read_reference",
            "gm_read_business", "finance_read_drafts", "hr_read_jd", "hr_read_batch", "finance_save_draft")]
        boundary = BoundaryMiddleware(guard, business_tools=[item["name"] for item in tools])
        request = SimpleNamespace(tools=tools, override=lambda **values: values)
        before = {item["name"] for item in boundary._model_request(request)["tools"]}
        self.assertNotIn("finance_save_draft", before)
        self.assertTrue({"gm_list_work", "gm_read_reference", "finance_read_drafts", "hr_read_jd"}.issubset(before))
        self.assertEqual(AgentWorkTask.objects.count(), 0)

    def test_native_async_tools_present_only_on_supervisor(self):
        guard = make_guard()
        inventory = []
        def transport(**payload):
            inventory.extend(tool["function"]["name"] for tool in payload["tools"])
            return {"content": "done", "tool_calls": [], "prompt_tokens": None, "completion_tokens": None}
        model = GatewayChatModel(model_name="synthetic-parent", guard=guard, transport=transport)
        graph = create_harness(model, guard, native=native_stub(guard), store=InMemoryStore())
        async_to_sync(graph.ainvoke)({"messages": [("user", "inventory")]})
        self.assertTrue({"start_async_task", "check_async_task", "update_async_task",
                         "cancel_async_task", "list_async_tasks"}.issubset(inventory))
        self.assertNotIn("task", inventory)

    def test_successful_infinite_native_tool_loop_terminates(self):
        guard = make_guard(max_tool_calls=2)
        calls = []
        def transport(**payload):
            calls.append(payload["physical_call_id"])
            return {"content": None, "tool_calls": [{"id": f"poll-{len(calls)}", "type": "function",
                "function": {"name": "ls", "arguments": '{"path":"/workspace"}'}}],
                "prompt_tokens": None, "completion_tokens": None}
        model = GatewayChatModel(model_name="synthetic-loop", guard=guard, transport=transport)
        graph = create_harness(model, guard, store=InMemoryStore())
        with self.assertRaises(AgentDenied):
            graph.invoke({"messages": [("user", "poll forever")]})
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual((root.tool_count, root.state, len(calls)), (2, "terminated", 3))
