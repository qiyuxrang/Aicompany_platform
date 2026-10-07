from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase, override_settings

from portal.agent_execution import cancel_root, dispatch_message, reconcile_conversation, runtime_client
from portal.agent_models import AgentMessage, AgentRun
from portal.agent_runtime import AgentDenied, RuntimeGuard
from portal.tests.test_agent_runtime import make_guard


class ClientStub:
    def __init__(self):
        self.threads = SimpleNamespace(create=AsyncMock(), get_state=AsyncMock(return_value={
            "values": {"messages": [{"type": "ai", "content": "authorized public reply"}]}}))
        self.runs = SimpleNamespace(create=AsyncMock(return_value={"run_id": "native-main"}),
            get=AsyncMock(return_value={"status": "success"}), list=AsyncMock(return_value=[]),
            cancel=AsyncMock())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *arguments):
        return False


@override_settings(AGENT_PLATFORM_ENABLED=True, AGENT_RUNTIME_URL="http://127.0.0.1:2024",
    AGENT_RUNTIME_ALLOWED_URLS=("http://127.0.0.1:2024",), AGENT_RUNTIME_SERVICE_TOKEN="synthetic" * 8,
    AGENT_MAIN_GRAPH="portal_main")
class AgentExecutionTests(TransactionTestCase):
    def setUp(self):
        self.guard = make_guard()
        self.root = AgentRun.objects.get(pk=self.guard.binding.root_id)
        self.message = AgentMessage.objects.create(conversation=self.root.conversation,
            role="user", content="authorized request", client_request_id="message-1")
        self.native = ClientStub()

    @patch("portal.agent_execution.runtime_client")
    def test_dispatch_and_replay_use_same_root_and_one_native_run(self, client):
        client.return_value = self.native
        first = dispatch_message(self.message.pk)
        second = dispatch_message(self.message.pk)
        self.assertEqual(first["state"], "submitted")
        self.assertEqual(second["state"], "submitted")
        self.assertEqual(self.native.runs.create.call_count, 1)
        self.root.refresh_from_db()
        self.assertEqual(self.root.launch_count, 1)
        self.assertEqual(self.root.root_run_id, self.root.pk)

    @patch("portal.agent_execution.runtime_client")
    def test_unknown_response_recovers_without_second_dispatch(self, client):
        client.return_value = self.native
        self.native.runs.create.side_effect = TimeoutError("response lost after accepted")
        self.assertEqual(dispatch_message(self.message.pk)["state"], "dispatch_unknown")
        self.native.runs.list.return_value = [{"run_id": "native-main", "metadata": {
            "operation_key": "message:" + str(self.message.pk)}}]
        self.assertEqual(dispatch_message(self.message.pk)["state"], "submitted")
        self.assertEqual(self.native.runs.create.call_count, 1)

    @patch("portal.agent_execution.runtime_client")
    def test_reply_projection_is_idempotent_and_revocation_blocks_replay(self, client):
        client.return_value = self.native
        dispatch_message(self.message.pk)
        for repeat in range(2):
            reconcile_conversation(self.root.conversation_id, self.guard.binding.owner_id)
        self.assertEqual(AgentMessage.objects.filter(conversation_id=self.root.conversation_id,
            role="assistant").count(), 1)
        owner = self.root.conversation.owner
        owner.is_active = False
        owner.save(update_fields=["is_active"])
        before = self.native.threads.get_state.call_count
        with self.assertRaises(AgentDenied):
            reconcile_conversation(self.root.conversation_id, owner.pk)
        self.assertEqual(self.native.threads.get_state.call_count, before)

    @override_settings(AGENT_RUNTIME_URL="http://127.0.0.1:9999")
    def test_runtime_target_cannot_escape_allowlist(self):
        with self.assertRaises(AgentDenied):
            runtime_client(self.guard.binding)

    @override_settings(AGENT_RUNTIME_URL="")
    @patch("portal.agent_execution.runtime_client")
    def test_unconfigured_runtime_archives_without_claiming_execution(self, client):
        self.assertEqual(dispatch_message(self.message.pk)["state"], "runtime_unconfigured")
        client.assert_not_called()
        self.root.refresh_from_db()
        self.assertEqual(self.root.action_count, 0)

    @patch("portal.agent_execution.runtime_client")
    def test_cancel_waits_for_native_acknowledgement_and_is_idempotent(self, client):
        client.return_value = self.native
        self.assertEqual(dispatch_message(self.message.pk)["state"], "submitted")
        self.root.refresh_from_db()
        self.root.state = "stopping"
        self.root.policy["fence"] += 1
        self.root.save(update_fields=["state", "policy"])
        self.native.runs.cancel.side_effect = TimeoutError("not acknowledged")
        self.assertEqual(cancel_root(self.root.pk)["state"], "stopping")
        self.root.refresh_from_db()
        self.assertEqual(self.root.state, "stopping")
        self.native.runs.cancel.side_effect = None
        self.assertEqual(cancel_root(self.root.pk)["state"], "cancelled")
        before = self.native.runs.cancel.call_count
        self.assertEqual(cancel_root(self.root.pk)["state"], "cancelled")
        self.assertEqual(self.native.runs.cancel.call_count, before)

    @patch("portal.agent_execution.runtime_client")
    def test_cancel_does_not_hide_unknown_dispatch_or_active_domain_call(self, client):
        self.root.state = "stopping"
        self.root.policy["active"] = {"model:pending": {"kind": "model"}}
        self.root.save(update_fields=["state", "policy"])
        self.assertEqual(cancel_root(self.root.pk)["state"], "stopping")
        client.assert_not_called()
