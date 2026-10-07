from unittest.mock import Mock, patch

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
    def gateway_fixture(self):
        user = User.objects.create_user(username="gateway-synthetic", password="synthetic-only",
            must_change_password=False, department_code="product")
        module = Module.objects.create(code="product", name="synthetic product")
        role = Role.objects.create(code="product", name="synthetic role")
        role.modules.add(module)
        user.roles.add(role)
        user.refresh_from_db()
        guard = make_guard(user=user)
        provider = Provider.objects.create(code="synthetic", name="synthetic", enabled=True,
            base_url="https://provider.example/v1", api_key_env="PORTAL_MODEL_KEY_SYNTHETIC")
        model = GatewayModel.objects.create(name="synthetic model", model_name="synthetic", enabled=True,
            provider=provider)
        route = ModelRoute.objects.create(code="synthetic", name="synthetic", module=module, model=model, enabled=True)
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
