import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase
from django.utils import timezone
from django.db import transaction

from portal.agent_models import AgentConversation, AgentRun, AgentEvent, AgentMessage, AgentWorkTask, AgentRequirement
from portal.agent_runtime import AgentDenied, NativeRuntime, RuntimeGuard, RunBinding, initialize_root
from portal.models import User


def make_guard(username="agent-a", *, user=None, **limits):
    user = user or User.objects.create_user(username=username, password="synthetic-only", must_change_password=False)
    conversation = AgentConversation.objects.create(owner=user, department_code="product")
    policy = {"max_actions": 30, "max_model_calls": 10, "max_tool_calls": 12,
              "max_launches": 6, "max_concurrent": 6, "max_active_ms": 60000, **limits}
    root = AgentRun.objects.create(id=conversation.root_run_id, root_run_id=conversation.root_run_id,
        conversation=conversation, policy=policy, deadline_at=timezone.now() + timedelta(minutes=5))
    return RuntimeGuard(initialize_root(root.pk, user.pk))


def native_stub(guard):
    client = SimpleNamespace(threads=SimpleNamespace(create=AsyncMock(),
        get_state=AsyncMock(return_value={"values": {"messages": [{"content": "synthetic child"}]}})), runs=SimpleNamespace(
        create=AsyncMock(return_value={"run_id": str(uuid.uuid4())}),
        get=AsyncMock(return_value={"status": "success"}), list=AsyncMock(return_value=[]), cancel=AsyncMock()))
    return NativeRuntime(guard, client, {"researcher"})


class NativeBoundaryTests(TransactionTestCase):
    def test_completed_work_allows_root_questions_without_reset_or_child_reuse(self):
        guard = make_guard()
        root = guard.check()
        work = AgentWorkTask.objects.create(owner_id=guard.binding.owner_id, conversation=root.conversation,
            department_code="product", goal="old work", current_requirement_version=1)
        message = AgentMessage.objects.create(conversation=root.conversation, role="user", content="old work")
        requirement = AgentRequirement.objects.create(work=work, version=1, user_message=message, content="old work")
        root.work, root.requirement = work, requirement
        root.save(update_fields=["work", "requirement"])
        native = native_stub(guard)
        child = asyncio.run(native.launch("researcher", "old child", "old-child"))
        guard.finish(guard.admit("model", "before-completion"))
        deadline, fence = root.deadline_at, root.policy["fence"]
        work.state = "completed"
        work.save(update_fields=["state"])
        guard.check()
        guard.finish(guard.admit("model", "ordinary-question"))
        with self.assertRaisesRegex(AgentDenied, "work_not_writable"):
            guard.check(write=True)
        with self.assertRaisesRegex(AgentDenied, "work_not_writable"):
            guard.admit("domain", "forbidden-domain")
        with self.assertRaisesRegex(AgentDenied, "work_not_writable"):
            asyncio.run(native.launch("researcher", "forbidden child", "new-child"))
        child_guard = RuntimeGuard(RunBinding(**{**guard.binding.__dict__, "run_id": child["thread_id"]}))
        with self.assertRaisesRegex(AgentDenied, "requirement_changed"):
            child_guard.check()
        root.refresh_from_db()
        self.assertNotEqual(root.state, "stopping")
        self.assertEqual((root.model_count, root.deadline_at, root.policy["fence"]), (2, deadline, fence))
        self.assertEqual(AgentRun.objects.get(pk=child["thread_id"]).state, "stopping")

    def test_old_requirement_child_completion_never_wakes_current_requirement(self):
        guard = make_guard()
        native = native_stub(guard)
        asyncio.run(native.start("first", "first"))
        asyncio.run(native.launch("researcher", "unscoped old child", "old-child"))
        root = guard.check()
        work = AgentWorkTask.objects.create(owner_id=guard.binding.owner_id, conversation=root.conversation,
            department_code="product", goal="new work", current_requirement_version=1)
        message = AgentMessage.objects.create(conversation=root.conversation, role="user", content="new work")
        requirement = AgentRequirement.objects.create(work=work, version=1, user_message=message, content="new work")
        root.work, root.requirement = work, requirement
        root.save(update_fields=["work", "requirement"])
        asyncio.run(native.reconcile())
        asyncio.run(native.reconcile())
        self.assertEqual(native.client.runs.create.call_count, 2)
        native.client.threads.get_state.assert_not_called()
        event = AgentEvent.objects.get(type="child_terminal_historical")
        child = event.run
        asyncio.run(native._wake(event, child))
        self.assertEqual(native.client.runs.create.call_count, 2)
        root.refresh_from_db()
        self.assertEqual(root.launch_count, 2)
        self.assertFalse(root.policy.get("notifications"))
        with self.assertRaisesRegex(AgentDenied, "notification_superseded"):
            asyncio.run(native._dispatch("late result", "late-notice", "main", "notify", str(root.pk),
                notification=event.event_key))
        root.policy["notifications"] = {event.event_key: {"state": "delivered"}}
        root.save(update_fields=["policy"])
        with self.assertRaisesRegex(AgentDenied, "notification_superseded"):
            guard.consume_notification(event.event_key)
        root.refresh_from_db()
        self.assertEqual(root.policy["notifications"][event.event_key]["state"], "delivered")

    def test_source_revocation_persists_stop_before_further_model_admission(self):
        guard = make_guard()
        with patch("portal.agent_source_permissions.check_sources",
                   side_effect=AgentDenied("source_authorization_changed")):
            with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
                guard.check()
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual((root.state, root.stop_reason), ("stopping", "source_authorization_changed"))
        self.assertEqual(root.model_count, 0)
        guard.check(maintenance=True)
        with self.assertRaises(AgentDenied):
            guard.admit("model")

    def test_server_message_id_is_bound_to_owned_persisted_message(self):
        guard = make_guard()
        root = guard.check()
        message = AgentMessage.objects.create(conversation=root.conversation, role="user", content="owned")
        native = native_stub(guard)
        asyncio.run(native.start(message.content, "message:" + str(message.pk)))
        context = native.client.runs.create.call_args.kwargs["context"]
        self.assertEqual(context["message_id"], str(message.pk))
        self.assertEqual(context["operation_key"], "message:" + str(message.pk))
        other = make_guard("foreign-msg")
        foreign_message = AgentMessage.objects.create(conversation=other.check().conversation,
            role="user", content="foreign")
        with self.assertRaisesRegex(AgentDenied, "message_binding_mismatch"):
            asyncio.run(native.resume("foreign", "message:" + str(foreign_message.pk)))
        self.assertEqual(native.client.runs.create.call_count, 1)

    def test_historical_messages_return_exact_ids_without_reset(self):
        guard = make_guard()
        native = native_stub(guard)
        native.client.runs.create.side_effect = [{"run_id": "first"}, {"run_id": "second"}]
        first = [{"role": "user", "content": "first complete message", "id": "message-a"}]
        asyncio.run(native.start(first, "message-a"))
        asyncio.run(native.resume("second", "message-b"))
        self.assertEqual(asyncio.run(native.resume(first, "message-a"))["run_id"], "first")
        self.assertEqual(asyncio.run(native.start("second", "message-b"))["run_id"], "second")
        with self.assertRaisesRegex(AgentDenied, "operation_conflict"):
            asyncio.run(native.resume("changed", "message-a"))
        with self.assertRaisesRegex(AgentDenied, "root_already_dispatched"):
            asyncio.run(native.start("third", "message-c"))
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual(root.launch_count, 2)
        self.assertEqual(root.native_run_id, "second")
        self.assertEqual(native.client.runs.create.call_count, 2)

    def test_prepared_intent_child_and_admission_survive_before_network(self):
        guard = make_guard()
        native = native_stub(guard)
        native.client.threads.create.side_effect = TimeoutError("crash before run submission")
        with self.assertRaises(TimeoutError):
            asyncio.run(native.launch("researcher", "task", "launch"))
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        intent = root.policy["dispatches"]["launch"]
        self.assertEqual(intent["state"], "prepared")
        self.assertEqual(AgentRun.objects.filter(parent=root).count(), 1)
        self.assertEqual(root.launch_count, 1)
        native.client.threads.create.side_effect = None
        recovered = asyncio.run(native.launch("researcher", "task", "launch"))
        self.assertEqual(recovered["thread_id"], intent["thread_id"])
        self.assertEqual(native.client.runs.create.call_count, 1)
        root.refresh_from_db()
        self.assertEqual(root.launch_count, 1)

    def test_nested_transaction_cannot_swallow_admission_termination(self):
        guard = make_guard(max_tool_calls=1)
        with transaction.atomic():
            with self.assertRaisesRegex(AgentDenied, "admission_requires_committed_boundary"):
                guard.admit("tool", "nested")
        action = guard.admit("tool", "first")
        guard.finish(action)
        with self.assertRaises(AgentDenied):
            guard.admit("tool", "last")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual(root.state, "terminated")
        self.assertEqual(root.tool_count, 1)

    def test_resume_unknown_never_reissues_without_evidence(self):
        guard = make_guard()
        native = native_stub(guard)
        asyncio.run(native.start("first", "first"))
        native.client.runs.create.side_effect = TimeoutError("accepted or not unknown")
        with self.assertRaises(TimeoutError):
            asyncio.run(native.resume(None, "restore"))
        with self.assertRaisesRegex(AgentDenied, "dispatch_unknown"):
            asyncio.run(native.resume(None, "restore"))
        self.assertEqual(native.client.runs.create.call_count, 2)
        native.client.runs.list.return_value = [{"run_id": "restored", "metadata": {"operation_key": "restore"}}]
        self.assertEqual(asyncio.run(native.resume(None, "restore"))["run_id"], "restored")
        self.assertEqual(native.client.runs.create.call_count, 2)

    def test_unknown_start_is_reconciled_by_metadata_without_redispatch(self):
        guard = make_guard()
        native = native_stub(guard)
        native.client.runs.create.side_effect = TimeoutError("response lost after native accept")
        with self.assertRaises(TimeoutError):
            asyncio.run(native.start("synthetic", "start-key"))
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual(root.state, "dispatch_unknown")
        native.client.runs.list.return_value = [{"run_id": "accepted-native", "metadata": {"operation_key": "start-key"}}]
        result = asyncio.run(native.start("synthetic", "start-key"))
        self.assertEqual(result["run_id"], "accepted-native")
        self.assertEqual(native.client.runs.create.call_count, 1)

    def test_notification_response_loss_recovers_without_second_wakeup(self):
        guard = make_guard()
        native = native_stub(guard)
        asyncio.run(native.start("synthetic", "start"))
        asyncio.run(native.launch("researcher", "child", "child"))
        native.client.runs.create.side_effect = TimeoutError("wake response lost")
        with self.assertRaises(TimeoutError):
            asyncio.run(native.reconcile())
        event = AgentEvent.objects.get(type="child_terminal")
        native.client.runs.list.return_value = [{"run_id": "accepted-wake", "metadata": {"operation_key": "notify:" + str(event.pk)}}]
        asyncio.run(native.reconcile())
        self.assertEqual(native.client.runs.create.call_count, 3)
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual(root.policy["notifications"][event.event_key]["run_id"], "accepted-wake")

    def test_launch_update_cancel_and_notifications_deduplicate(self):
        guard = make_guard()
        native = native_stub(guard)
        first = asyncio.run(native.launch("researcher", "synthetic", "launch-1"))
        self.assertEqual(native.client.runs.create.call_args.kwargs["multitask_strategy"], "interrupt")
        token = native.client.runs.create.call_args.kwargs["context"]["binding"]
        self.assertEqual(RunBinding.from_token(token).root_id, guard.binding.root_id)
        asyncio.run(native.update(first["task_id"], "correction", "update-1"))
        asyncio.run(native.reconcile())
        asyncio.run(native.reconcile())
        self.assertEqual(AgentEvent.objects.filter(type="child_terminal").count(), 1)
        guard.stop()
        asyncio.run(native.cancel(first["task_id"]))
        self.assertTrue(native.client.runs.cancel.called)
        with self.assertRaises(AgentDenied):
            asyncio.run(native.launch("researcher", "late", "late-launch"))

    def test_foreign_root_ids_and_second_level_are_denied(self):
        first, second = make_guard(), make_guard("agent-b")
        native = native_stub(first)
        foreign = asyncio.run(native_stub(second).launch("researcher", "private", "foreign"))
        for method, args in [(native.check, (foreign["task_id"],)),
                             (native.cancel, (foreign["task_id"],)),
                             (native.update, (foreign["task_id"], "x", "bad"))]:
            with self.assertRaises(AgentDenied):
                asyncio.run(method(*args))
        own = asyncio.run(native.launch("researcher", "own", "own"))
        child = first.child(own["task_id"])
        child_guard = RuntimeGuard(RunBinding(**{**first.binding.__dict__, "run_id": str(child.pk)}))
        with self.assertRaises(AgentDenied):
            asyncio.run(native_stub(child_guard).launch("researcher", "nested", "nested"))
        self.assertEqual(len(asyncio.run(native.list())), 1)

    def test_binding_signature_and_disabled_user_block_restore(self):
        guard = make_guard()
        with self.assertRaises(AgentDenied):
            RunBinding.from_token(guard.binding.token() + "invalid")
        User.objects.filter(pk=guard.binding.owner_id).update(is_active=False)
        with self.assertRaises(AgentDenied):
            guard.check()
        guard.stop("revoked")
        guard.check(maintenance=True)
