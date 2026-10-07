from unittest.mock import Mock, patch
import asyncio
from queue import SimpleQueue

from django.test import TransactionTestCase
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool

from portal.agent_model import GatewayChatModel, gateway_transport
from portal.agent_models import AgentRun
from portal.agent_runtime import AgentDenied
from portal.tests.test_agent_runtime import make_guard
from portal.models import User, Provider, GatewayModel, ModelRoute, Module, Role, ModelCallLog


@tool
def lookup(query: str) -> str:
    """Look up synthetic evidence."""
    return query


class ModelAdapterTests(TransactionTestCase):
    def gateway_fixture(self, suffix=""):
        user = User.objects.create_user(username="gateway-synthetic" + suffix, password="synthetic-only",
            must_change_password=False, department_code="product")
        module = Module.objects.create(code="product" + suffix, name="synthetic product")
        role = Role.objects.create(code="product" + suffix, name="synthetic role")
        role.modules.add(module)
        user.roles.add(role)
        user.refresh_from_db()
        guard = make_guard(user=user)
        provider = Provider.objects.create(code="synthetic" + suffix, name="synthetic", enabled=True,
            base_url="https://provider.example/v1", api_key_env="PORTAL_MODEL_KEY_SYNTHETIC")
        model = GatewayModel.objects.create(name="synthetic model", model_name="synthetic", enabled=True,
            provider=provider)
        route = ModelRoute.objects.create(code="synthetic" + suffix, name="synthetic", module=module, model=model, enabled=True)
        return guard, user, model, route

    @patch("portal.agent_model.request_agent_gateway")
    def test_physical_model_log_precedes_io_and_has_durable_scope(self, request):
        guard, user, model, route = self.gateway_fixture()
        def response(payload):
            record = ModelCallLog.objects.get()
            self.assertEqual(record.status, "pending")
            self.assertEqual(str(record.root_id), guard.binding.root_id)
            self.assertEqual(str(record.run_id), guard.binding.run_id)
            self.assertEqual(record.conversation_id, guard.check().conversation_id)
            self.assertIsNone(record.work_id)
            self.assertIsNone(record.requirement_id)
            self.assertTrue(record.physical_call_id)
            return {"content": "synthetic result", "tool_calls": [], "duration_ms": 1,
                    "prompt_tokens": None, "completion_tokens": None}
        request.side_effect = response
        adapter = GatewayChatModel(model_name=model.model_name, guard=guard,
            transport=gateway_transport(user, route.code, None, guard))
        result = adapter.invoke("synthetic")
        record = ModelCallLog.objects.get()
        self.assertEqual(record.physical_call_id, result.response_metadata["physical_call_id"])
        self.assertEqual(record.status, "success")
        self.assertIsNone(record.prompt_tokens)
        with self.assertRaisesRegex(AgentDenied, "physical_call_not_admitted"):
            gateway_transport(user, route.code, None, guard)(messages=[], tools=[], tool_choice=None,
                physical_call_id=record.physical_call_id)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(ModelCallLog.objects.count(), 1)

    @patch("portal.agent_model.request_agent_gateway", side_effect=TimeoutError("synthetic unknown"))
    def test_unknown_physical_result_keeps_reservation_and_log(self, request):
        guard, user, model, route = self.gateway_fixture()
        adapter = GatewayChatModel(model_name=model.model_name, guard=guard,
            transport=gateway_transport(user, route.code, None, guard))
        with self.assertRaises(TimeoutError):
            adapter.invoke("synthetic")
        record = ModelCallLog.objects.get()
        self.assertEqual(record.status, "outcome_unknown")
        self.assertIn(record.physical_call_id, guard.check().policy["active"])
        self.assertEqual(guard.check().model_count, 1)

    def test_unknown_transport_and_failed_log_save_keep_original_error_and_reservation(self):
        from django.db import OperationalError, connections
        from portal.agent_models import AgentRootAction
        guard, user, model, route = self.gateway_fixture()
        original = TimeoutError("synthetic unknown physical result")
        save = ModelCallLog.save
        def fail_final_save(record, *args, **kwargs):
            if "status" in kwargs.get("update_fields", []):
                raise OperationalError("synthetic final log write failure")
            return save(record, *args, **kwargs)
        adapter = GatewayChatModel(model_name=model.model_name, guard=guard,
            transport=gateway_transport(user, route.code, None, guard))
        with patch("portal.agent_model.request_agent_gateway", side_effect=original), \
                patch.object(ModelCallLog, "save", autospec=True, side_effect=fail_final_save):
            with self.assertRaises(TimeoutError) as caught:
                adapter.invoke("synthetic")
        self.assertIs(caught.exception, original)
        self.assertTrue(any("OperationalError" in note for note in original.__notes__))
        if connections["default"].vendor == "postgresql":
            self.assertIsNone(connections["default"].connection)
        record = ModelCallLog.objects.get()
        self.assertEqual(record.status, "pending")
        self.assertIn(record.physical_call_id, guard.check().policy["active"])
        self.assertEqual(guard.check().model_count, 1)
        self.assertEqual(AgentRootAction.objects.get(action_key=record.physical_call_id).status, "reserved")

    def test_successful_transport_with_failed_audit_still_fails(self):
        guard, user, model, route = self.gateway_fixture()
        with patch("portal.security.audit", side_effect=RuntimeError("synthetic audit failure")), \
                patch("portal.agent_model.request_agent_gateway", return_value={"content": "synthetic result",
                    "tool_calls": [], "duration_ms": 1, "prompt_tokens": None, "completion_tokens": None}):
            adapter = GatewayChatModel(model_name=model.model_name, guard=guard,
                transport=gateway_transport(user, route.code, None, guard))
            with self.assertRaisesRegex(RuntimeError, "synthetic audit failure"):
                adapter.invoke("synthetic")
        self.assertEqual(guard.check().model_count, 1)
        self.assertFalse(guard.check().policy["active"])

    def test_failed_action_finalization_does_not_replace_original_authorization_error(self):
        guard = make_guard()
        original = AgentDenied("synthetic revoked authorization")
        adapter = GatewayChatModel(model_name="synthetic", guard=guard, transport=Mock(side_effect=original))
        with patch.object(guard, "finish", side_effect=RuntimeError("synthetic action finalization failure")):
            with self.assertRaises(AgentDenied) as caught:
                adapter.invoke("synthetic")
        self.assertIs(caught.exception, original)
        self.assertTrue(any("RuntimeError" in note for note in original.__notes__))
        self.assertEqual(guard.check().model_count, 1)
        self.assertTrue(guard.check().policy["active"])

    def test_tool_roundtrip_physical_ids_and_unknown_usage(self):
        guard = make_guard()
        transport = Mock(side_effect=[{"content": None, "tool_calls": [{"id": "call-1", "type": "function",
            "function": {"name": "lookup", "arguments": '{"query":"synthetic"}'}}],
            "prompt_tokens": None, "completion_tokens": None},
            {"content": "done", "tool_calls": [], "prompt_tokens": 8, "completion_tokens": 3}])
        model = GatewayChatModel(model_name="synthetic", guard=guard, transport=transport).bind_tools([lookup])
        user = HumanMessage("search")
        first = model.invoke([user])
        self.assertIsNone(first.usage_metadata)
        final = model.invoke([user, first, ToolMessage("evidence", tool_call_id="call-1")])
        self.assertEqual(final.usage_metadata["total_tokens"], 11)
        payload = transport.call_args.kwargs
        self.assertEqual(payload["messages"][-1]["tool_call_id"], "call-1")
        self.assertEqual(AgentRun.objects.get(pk=guard.binding.root_id).model_count, 2)
        self.assertNotEqual(transport.call_args_list[0].kwargs["physical_call_id"], payload["physical_call_id"])

    def test_revoked_result_and_bad_calls_do_not_continue(self):
        guard = make_guard()
        def response(**kwargs):
            guard.stop("revoked")
            return {"content": "late", "tool_calls": [], "prompt_tokens": None, "completion_tokens": None}
        model = GatewayChatModel(model_name="synthetic", guard=guard, transport=response)
        with self.assertRaises(AgentDenied):
            model.invoke("synthetic")
        self.assertFalse(AgentRun.objects.get(pk=guard.binding.root_id).policy["active"])


class ModelIOBoundaryTests(TransactionTestCase):
    gateway_fixture = ModelAdapterTests.gateway_fixture

    def _blocked_gateway(self, *, asynchronous):
        from portal.tests.test_agent_storage import BlockedIOWindow
        window = BlockedIOWindow()
        adapters, guards = SimpleQueue(), []
        # Each model retains the real four-pending gate. Distinct models/roots
        # expose process-wide pool lifetime without relaxing that business gate.
        for index in range(5):
            guard, user, model, route = self.gateway_fixture(f"-io-{index}")
            guards.append(guard)
            adapters.put(GatewayChatModel(model_name=model.model_name, guard=guard,
                transport=gateway_transport(user, route.code, None, guard)))
        def response(payload):
            window.wait()
            return {"content": "synthetic result", "tool_calls": [], "duration_ms": 1,
                    "prompt_tokens": None, "completion_tokens": None}
        with patch("portal.agent_model.request_agent_gateway", side_effect=response):
            def operation():
                adapter = adapters.get_nowait()
                return asyncio.run(adapter.ainvoke("synthetic")) if asynchronous else adapter.invoke("synthetic")
            window.run(self, operation)
        self.assertEqual(ModelCallLog.objects.filter(status="success").count(), 5)
        for guard in guards:
            root = guard.check()
            self.assertEqual(root.model_count, 1)
            self.assertFalse(root.policy["active"])

    def test_sync_gateway_releases_committed_reservation_checkout_during_transport(self):
        self._blocked_gateway(asynchronous=False)

    def test_async_gateway_releases_executor_checkout_during_transport(self):
        self._blocked_gateway(asynchronous=True)

    def test_rejected_model_preserves_caller_transaction_and_never_starts_io(self):
        from django.db import connection, transaction
        guard = make_guard()
        response = Mock()
        adapter = GatewayChatModel(model_name="synthetic", guard=guard, transport=response)
        with self.assertRaisesRegex(RuntimeError, "caller rollback"):
            with transaction.atomic():
                raw = connection.connection
                User.objects.create(username="io-caller-rollback")
                with self.assertRaisesRegex(AgentDenied, "admission_requires_committed_boundary"):
                    adapter.invoke("synthetic")
                self.assertIs(connection.connection, raw)
                self.assertTrue(connection.in_atomic_block)
                self.assertFalse(connection.closed_in_transaction)
                response.assert_not_called()
                raise RuntimeError("caller rollback")
        self.assertEqual(guard.check().model_count, 0)
        self.assertFalse(User.objects.filter(username="io-caller-rollback").exists())

    def test_manual_model_admission_requires_committed_boundary_without_touching_caller(self):
        from django.db import connection
        from portal.agent_models import AgentRootAction
        guard = make_guard()
        def response(**kwargs):
            self.assertFalse(connection.get_autocommit())
            self.assertIs(connection.connection, raw)
            return {"content": "synthetic uncommitted dispatch", "tool_calls": [],
                    "prompt_tokens": None, "completion_tokens": None}
        transport = Mock(side_effect=response)
        adapter = GatewayChatModel(model_name="synthetic", guard=guard, transport=transport)
        action_count, log_count = AgentRootAction.objects.count(), ModelCallLog.objects.count()
        connection.set_autocommit(False)
        raw = connection.connection
        try:
            with connection.cursor() as cursor:
                cursor.execute("BEGIN" if connection.vendor == "sqlite" else "SELECT 1")
            self.assertFalse(connection.in_atomic_block)
            if connection.vendor == "postgresql":
                self.assertEqual(raw.info.transaction_status.name, "INTRANS")
            else:
                self.assertTrue(raw.in_transaction)
            User.objects.create(username="model-manual-caller")
            with self.assertRaisesRegex(AgentDenied, "admission_requires_committed_boundary"):
                adapter.invoke("synthetic")
            transport.assert_not_called()
            self.assertEqual(guard.check().model_count, 0)
            self.assertEqual(AgentRootAction.objects.count(), action_count)
            self.assertEqual(ModelCallLog.objects.count(), log_count)
        finally:
            self.assertIs(connection.connection, raw)
            self.assertFalse(connection.get_autocommit())
            connection.rollback()
            connection.set_autocommit(True)
        self.assertFalse(User.objects.filter(username="model-manual-caller").exists())
