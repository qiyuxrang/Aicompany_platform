import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import sync_to_async
from django.db import connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from portal import agent_api
from portal.agent_models import AgentRun, AgentRootAction, AgentWorkTask, AgentRequirement, AgentMessage
from portal.agent_runtime import AgentDenied, RunBinding, RuntimeGuard
from portal.models import User
from portal.tests.test_agent_runtime import make_guard, native_stub


def attach_work(guard, *, state="running", stop_reason=""):
    root = guard.check()
    work = AgentWorkTask.objects.create(owner_id=guard.binding.owner_id,
        conversation=root.conversation, department_code="product", goal="synthetic",
        current_requirement_version=1, state=state, stop_reason=stop_reason)
    message = AgentMessage.objects.create(conversation=root.conversation, role="user", content="synthetic")
    requirement = AgentRequirement.objects.create(work=work, version=1,
        user_message=message, content="synthetic")
    root.work, root.requirement = work, requirement
    root.save(update_fields=["work", "requirement"])
    return work


class TerminationTests(TransactionTestCase):
    def tearDown(self):
        try:
            super().tearDown()
        finally:
            asyncio.run(sync_to_async(connections.close_all)())

    def test_unknown_child_cancel_preserves_reservation_and_known_run_ack(self):
        guard = make_guard()
        native = native_stub(guard)
        first = asyncio.run(native.launch("researcher", "child", "child"))
        native.client.runs.create.side_effect = TimeoutError("unknown update response")
        with self.assertRaises(TimeoutError):
            asyncio.run(native.update(first["thread_id"], "correction", "update"))
        guard.stop()
        result = asyncio.run(native.cancel(first["thread_id"]))
        self.assertEqual(result["status"], "dispatch_unknown")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual(root.policy["dispatches"]["update"]["state"], "sending")
        self.assertIn("update", root.policy["active"])
        self.assertIn("child", root.policy["active"])
        self.assertEqual(AgentRun.objects.get(pk=first["thread_id"]).state, "stopping")
        self.assertEqual(native.client.runs.cancel.call_count, 1)
        asyncio.run(native.reconcile())
        self.assertEqual(native.client.runs.cancel.call_count, 1)
        native.client.runs.list.assert_not_called()

    def test_prepared_child_can_abort_without_native_dispatch(self):
        guard = make_guard()
        native = native_stub(guard)
        native.client.threads.create.side_effect = TimeoutError("no run sent")
        with self.assertRaises(TimeoutError):
            asyncio.run(native.launch("researcher", "child", "child"))
        guard.stop()
        result = asyncio.run(native.reconcile())
        self.assertEqual(result[0]["status"], "cancelled")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual(root.policy["dispatches"]["child"]["state"], "aborted")
        self.assertEqual(root.policy["active"], {})
        self.assertEqual(root.launch_count, 1)
        native.client.runs.cancel.assert_not_called()
        native.client.runs.create.assert_not_called()

    def test_unknown_root_resume_and_physical_call_survive_known_cancel_ack(self):
        guard = make_guard()
        native = native_stub(guard)
        asyncio.run(native.start("first", "first"))
        guard.admit("model", "physical-unknown")
        native.client.runs.create.side_effect = TimeoutError("unknown resume")
        with self.assertRaises(TimeoutError):
            asyncio.run(native.resume(None, "resume"))
        guard.stop()
        result = asyncio.run(native.reconcile())
        self.assertEqual(result[0]["status"], "dispatch_unknown")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual(set(root.policy["active"]), {"resume", "physical-unknown"})
        self.assertEqual(root.state, "stopping")
        self.assertEqual(root.model_count, 1)

    def test_success_polling_and_resume_cannot_reset_counts(self):
        guard = make_guard(max_tool_calls=3)
        for index in range(3):
            action = guard.admit("tool", f"poll-{index}")
            guard.finish(action)
            guard = RuntimeGuard(guard.binding)
        with self.assertRaises(AgentDenied):
            guard.admit("tool", "poll-4")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual((root.tool_count, root.state), (3, "terminated"))
        guard.finish("poll-2")
        self.assertEqual(AgentRootAction.objects.count(), 3)

    def test_physical_retries_and_unknown_reservations_are_durable(self):
        guard = make_guard(max_concurrent=1)
        guard.admit("model", "physical-1")
        with self.assertRaises(AgentDenied):
            RuntimeGuard(guard.binding).admit("model", "physical-1")
        with self.assertRaises(AgentDenied):
            RuntimeGuard(guard.binding).admit("model", "physical-2")
        guard.finish("physical-1", "error")
        guard.admit("model", "physical-2")
        self.assertEqual(AgentRun.objects.get(pk=guard.binding.root_id).model_count, 2)

    def test_deadline_active_time_and_non_llm_cleanup(self):
        guard = make_guard(max_active_ms=1)
        action = guard.admit("model")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        root.policy["active"][action]["started"] -= 1
        root.save(update_fields=["policy"])
        with self.assertRaises(AgentDenied):
            guard.admit("tool")
        guard.finish(action)
        self.assertFalse(AgentRun.objects.get(pk=root.pk).policy["active"])
        second = make_guard("deadline")
        AgentRun.objects.filter(pk=second.binding.root_id).update(deadline_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(AgentDenied):
            second.admit("model")

    def test_domain_launch_requires_work_and_shares_root_limit(self):
        guard = make_guard(max_launches=2)
        with self.assertRaises(AgentDenied):
            guard.admit("domain")
        root = guard.check()
        work = AgentWorkTask.objects.create(owner_id=guard.binding.owner_id, conversation=root.conversation,
            department_code="product", goal="synthetic", current_requirement_version=1)
        message = AgentMessage.objects.create(conversation=root.conversation, role="user", content="synthetic")
        requirement = AgentRequirement.objects.create(work=work, version=1, user_message=message, content="synthetic")
        root.work, root.requirement = work, requirement
        root.save(update_fields=["work", "requirement"])
        for kind in ("domain", "launch"):
            guard.finish(guard.admit(kind))
        with self.assertRaises(AgentDenied):
            guard.admit("domain")
        self.assertEqual(AgentRun.objects.get(pk=root.pk).launch_count, 2)

    @skipUnlessDBFeature("has_select_for_update")
    def test_parallel_admission_never_copies_root_allowance(self):
        guard = make_guard(max_model_calls=2)

        def attempt(index):
            try:
                action = RuntimeGuard(guard.binding).admit("model", f"parallel-{index}")
                return action
            except AgentDenied:
                return None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(attempt, range(10)))
        count = AgentRun.objects.get(pk=guard.binding.root_id).model_count
        self.assertEqual(count, sum(result is not None for result in results))
        self.assertEqual(count, 2)

    def assert_work_api_state(self, guard, work, state, reason):
        from portal.agent_api import work_data

        work.refresh_from_db()
        self.assertEqual((work_data(work)["state"], work_data(work)["stop_reason"]), (state, reason))
        request = APIRequestFactory().get("/api/agent/management/work/", {"owner_id": work.owner_id})
        owner = User.objects.get(pk=guard.binding.owner_id)
        force_authenticate(request, user=owner)
        with override_settings(AGENT_PLATFORM_ENABLED=True), patch("portal.agent_api.actor", return_value=owner):
            response = agent_api.management_work(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data["items"][0]["state"], response.data["items"][0]["id"]),
                         (state, str(work.pk)))

    def test_root_budget_termination_is_visible_on_active_work_apis(self):
        guard = make_guard(max_model_calls=1)
        work = attach_work(guard)
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        deadline, fence = root.deadline_at, root.policy["fence"]
        action = guard.admit("model", "first-model")
        guard.finish(action)

        with self.assertRaisesRegex(AgentDenied, "max_model_calls"):
            guard.admit("model", "exhaust-model-budget")

        root.refresh_from_db()
        self.assertEqual((root.state, root.stop_reason, root.model_count),
                         ("terminated", "max_model_calls", 1))
        self.assertEqual((root.deadline_at, root.policy["fence"]), (deadline, fence + 1))
        self.assert_work_api_state(guard, work, "terminated", "max_model_calls")
        guard.stop("duplicate-stop")
        root.refresh_from_db()
        self.assertEqual((root.stop_reason, root.model_count, root.deadline_at,
                          root.policy["fence"]), ("max_model_calls", 1, deadline, fence + 1))

    def assert_stopping_preserves_root_meter(self, guard, work, reason, deadline, fence):
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual((root.state, root.stop_reason), ("stopping", reason))
        self.assertEqual((root.policy["fence"], root.deadline_at, root.model_count, root.action_count),
                         (fence + 1, deadline, 0, 0))
        self.assert_work_api_state(guard, work, "stopping", reason)
        guard.stop("duplicate-stop")
        root.refresh_from_db()
        self.assertEqual((root.stop_reason, root.policy["fence"]), (reason, fence + 1))

    def test_source_revocation_stops_current_work(self):
        guard = make_guard(username="revoke-source")
        work = attach_work(guard)
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        deadline, fence = root.deadline_at, root.policy["fence"]

        with patch("portal.agent_source_permissions.check_sources",
                   side_effect=AgentDenied("source_authorization_changed")):
            with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
                guard.check()

        self.assert_stopping_preserves_root_meter(
            guard, work, "source_authorization_changed", deadline, fence)

    def test_department_revocation_stops_current_work(self):
        guard = make_guard(username="revoke-department")
        work = attach_work(guard)
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        deadline, fence = root.deadline_at, root.policy["fence"]
        User.objects.filter(pk=guard.binding.owner_id).update(
            grant_version=guard.binding.grant_version + 1)

        with self.assertRaisesRegex(AgentDenied, "authorization_changed"):
            guard.check()

        self.assert_stopping_preserves_root_meter(
            guard, work, "authorization_changed", deadline, fence)

    def test_budget_exhaustion_does_not_rewrite_completed_work(self):
        guard = make_guard(max_model_calls=1)
        work = attach_work(guard, state="completed", stop_reason="published")
        action = guard.admit("model", "root-followup")
        guard.finish(action)

        with self.assertRaisesRegex(AgentDenied, "max_model_calls"):
            guard.admit("model", "root-followup-limit")

        self.assert_work_api_state(guard, work, "completed", "published")

    def test_stale_child_stop_does_not_change_new_requirement_work(self):
        guard = make_guard()
        work = attach_work(guard)
        root = guard.check()
        old_requirement = root.requirement
        child = AgentRun.objects.create(root_run_id=root.pk, parent=root,
            conversation=root.conversation, work=work, requirement=old_requirement,
            deadline_at=root.deadline_at, state="running")
        work.current_requirement_version = 2
        work.save(update_fields=["current_requirement_version"])
        message = AgentMessage.objects.create(conversation=root.conversation, role="user", content="new version")
        current_requirement = AgentRequirement.objects.create(work=work, version=2,
            user_message=message, content="new version")
        root.requirement = current_requirement
        root.save(update_fields=["requirement"])
        child_guard = RuntimeGuard(RunBinding(**{
            **guard.binding.__dict__, "run_id": str(child.pk),
        }))

        child_guard.stop("stale_child")

        work.refresh_from_db()
        self.assertEqual((work.state, work.stop_reason), ("running", ""))
