import json
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TransactionTestCase, override_settings
from django.db import connection, connections, transaction
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
from portal.agent_db import database_boundary, database_sync_to_async


def pool_idle(test):
    """Connection creation can finish after checkout return; never hide a leak."""
    end = time.monotonic() + 5
    while True:
        stats = connection.pool.get_stats()
        if stats["requests_waiting"] == 0 and stats["pool_available"] == stats["pool_size"]:
            return stats
        test.assertLess(time.monotonic(), end, "Completed operation retained a PostgreSQL checkout")
        time.sleep(.01)


class HarnessTests(TransactionTestCase):
    def tearDown(self):
        if connection.vendor == "postgresql":
            connections.close_all()
            pool_idle(self)

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


class AgentDatabaseBoundaryTests(TransactionTestCase):
    def test_reused_executor_threads_return_every_checkout_after_real_queries(self):
        if connection.vendor != "postgresql":
            self.skipTest("Real native PostgreSQL pool thread lifetime")
        connections.close_all()
        @database_boundary
        def query():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
        def run(index):
            result = query()
            return result and connections["default"].connection is None
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertTrue(all(executor.map(run, range(32))))
        stats = pool_idle(self)
        self.assertLessEqual(stats["pool_size"], 4)

    def test_enclosing_atomic_transaction_stays_open_and_rolls_back_normally(self):
        @database_boundary
        def operation():
            User.objects.create(username="caller-atomic-boundary")
        with self.assertRaisesRegex(RuntimeError, "caller rollback"):
            with transaction.atomic():
                raw = connection.connection
                operation()
                self.assertIs(connection.connection, raw)
                self.assertTrue(connection.in_atomic_block)
                self.assertFalse(connection.closed_in_transaction)
                self.assertTrue(User.objects.filter(username="caller-atomic-boundary").exists())
                raise RuntimeError("caller rollback")
        self.assertFalse(User.objects.filter(username="caller-atomic-boundary").exists())

    def test_enclosing_manual_transaction_is_not_returned_or_committed(self):
        @database_boundary
        def operation():
            User.objects.create(username="caller-manual-boundary")
        connection.set_autocommit(False)
        raw = connection.connection
        try:
            # SQLite's legacy driver only changes isolation_level here. The
            # caller must actually BEGIN before User.save()'s atomic savepoint,
            # otherwise releasing that outermost savepoint commits the insert.
            with connection.cursor() as cursor:
                cursor.execute("BEGIN" if connection.vendor == "sqlite" else "SELECT 1")
            self.assertFalse(connection.in_atomic_block)
            if connection.vendor == "sqlite":
                self.assertTrue(raw.in_transaction)
            elif connection.vendor == "postgresql":
                self.assertEqual(raw.info.transaction_status.name, "INTRANS")
            operation()
            self.assertIs(connection.connection, raw)
            self.assertFalse(connection.get_autocommit())
            self.assertTrue(User.objects.filter(username="caller-manual-boundary").exists())
            if connection.vendor == "sqlite":
                self.assertTrue(raw.in_transaction)
            elif connection.vendor == "postgresql":
                self.assertEqual(raw.info.transaction_status.name, "INTRANS")
            connection.rollback()
        finally:
            connection.set_autocommit(True)
        self.assertFalse(User.objects.filter(username="caller-manual-boundary").exists())

    def test_nested_operation_keeps_outer_checkout_until_outer_return(self):
        @database_boundary
        def inner():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return connection.connection
        @database_boundary
        def outer():
            connection.ensure_connection()
            raw = connection.connection
            self.assertIs(inner(), raw)
            self.assertIs(connection.connection, raw)
        outer()
        if connection.vendor == "postgresql":
            self.assertIsNone(connection.connection)

    def test_child_executor_boundary_releases_independently_of_parent_thread_depth(self):
        if connection.vendor != "postgresql":
            self.skipTest("Real PostgreSQL parent/child thread checkout independence")
        child_handles = []
        def child():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            child_handles.append(connections["default"])
        @database_boundary
        def parent():
            connection.ensure_connection()
            raw = connection.connection
            async_to_sync(database_sync_to_async(child, thread_sensitive=False))()
            self.assertIs(connection.connection, raw)
            self.assertIsNone(child_handles[0].connection)
        parent()
        self.assertIsNone(connection.connection)
        pool_idle(self)

    def test_real_failed_tool_releases_thread_checkout_and_keeps_auth_exception(self):
        denied = AgentDenied("synthetic-authorization-changed")
        @database_boundary
        def operation():
            with transaction.atomic():
                User.objects.create(username="failed-tool-boundary")
                raise denied
        def run():
            try:
                operation()
            except AgentDenied as caught:
                wrapper = connections["default"]
                return caught is denied and not wrapper.in_atomic_block and (
                    wrapper.vendor != "postgresql" or wrapper.connection is None)
            return False
        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertTrue(executor.submit(run).result(timeout=10))
        self.assertFalse(User.objects.filter(username="failed-tool-boundary").exists())

    def test_async_cancellation_still_finishes_physical_thread_cleanup(self):
        entered, release, finished = Event(), Event(), Event()
        handles = []
        def operation():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                handles.append(connections["default"])
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("bounded cancellation fixture did not release")
            finally:
                finished.set()
        async def scenario():
            task = asyncio.create_task(database_sync_to_async(operation, thread_sensitive=False)())
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        async_to_sync(scenario)()
        self.assertTrue(finished.wait(5))
        if connection.vendor == "postgresql":
            end = time.monotonic() + 5
            while handles[0].connection is not None:
                self.assertLess(time.monotonic(), end, "Cancelled ORM executor did not return checkout")
                time.sleep(.01)
            connections.close_all()
            pool_idle(self)

    def test_cleanup_failure_does_not_replace_original_business_exception(self):
        wrapper = SimpleNamespace(in_atomic_block=False, connection=None,
            close_if_unusable_or_obsolete=Mock(), close=Mock(side_effect=RuntimeError("cleanup failure")))
        denied = AgentDenied("authorization_changed")
        @database_boundary
        def operation():
            raise denied
        with patch("portal.agent_db._wrappers", return_value=[wrapper]):
            with self.assertRaises(AgentDenied) as caught:
                operation()
        self.assertIs(caught.exception, denied)
        self.assertTrue(any("RuntimeError" in note for note in caught.exception.__notes__))
