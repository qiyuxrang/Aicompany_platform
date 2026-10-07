from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from portal import agent_api
from portal.agent_models import (AgentConversation, AgentEvent, AgentRequirement, AgentRun,
                                 AgentWorkTask, append_public_event)
from portal.model_config import ModelCallLog

from .base import PortalTestCase


@override_settings(AGENT_PLATFORM_ENABLED=True)
class AgentHistoryTests(PortalTestCase):
    def setUp(self):
        self.owner = self.create_user("history-owner", "product")
        self.owner.department_code = "product"
        self.owner.save(update_fields=["department_code"])
        self.factory = APIRequestFactory()

    def call(self, view, method="get", data=None, **kwargs):
        request = getattr(self.factory, method)("/api/agent/", data=data, format="json")
        force_authenticate(request, user=self.owner)
        response = view(request, **kwargs)
        response.render()
        return response

    def test_question_has_technical_root_without_business_work_then_conversion_preserves_root(self):
        conversation_id = self.call(agent_api.conversations, "post", {}).data["id"]
        conversation = AgentConversation.objects.get(pk=conversation_id)
        root = AgentRun.objects.get(pk=conversation.root_run_id)
        self.assertIsNone(root.work_id)
        self.assertIsNone(root.requirement_id)
        root.policy = {"max_actions": 2}
        root.action_count = 1
        root.deadline_at = timezone.now() + timedelta(minutes=1)
        root.save(update_fields=["policy", "action_count", "deadline_at"])
        message = self.call(agent_api.messages, "post", {"text": "写技术方案", "client_request_id": "m1"},
                            conversation_id=conversation_id)
        created = self.call(agent_api.work_list, "post", {"conversation_id": conversation_id,
            "message_id": message.data["id"], "goal": "写技术方案"})
        self.assertEqual(created.status_code, 201)
        root.refresh_from_db()
        self.assertEqual(root.action_count, 1)
        self.assertEqual(root.policy, {"max_actions": 2})
        self.assertEqual(str(root.work_id), created.data["id"])
        self.assertEqual(AgentWorkTask.objects.count(), 1)

    def test_requirement_is_immutable_and_events_are_ordered_idempotent(self):
        conversation_id = self.call(agent_api.conversations, "post", {}).data["id"]
        message = self.call(agent_api.messages, "post", {"text": "做方案", "client_request_id": "m1"},
                            conversation_id=conversation_id)
        work_id = self.call(agent_api.work_list, "post", {"conversation_id": conversation_id,
            "message_id": message.data["id"], "goal": "做方案"}).data["id"]
        with patch("portal.agent_execution.dispatch_message", return_value={"state": "submitted"}) as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                correction = self.call(agent_api.messages, "post", {"text": "加入预算", "client_request_id": "m2",
                    "work_id": work_id, "defer_dispatch": True}, conversation_id=conversation_id)
            dispatch.assert_not_called()
            with self.captureOnCommitCallbacks(execute=True):
                response = self.call(agent_api.requirements, "post", {"expected_version": 1,
                    "message_id": correction.data["id"], "content": "加入预算"}, work_id=work_id)
            dispatch.assert_called_once()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "received")
        self.assertIsNotNone(AgentRequirement.objects.get(work_id=work_id, version=2).applied_at)
        self.assertEqual(self.call(agent_api.requirements, "post", {"expected_version": 1,
            "message_id": correction.data["id"], "content": "不同内容"}, work_id=work_id).status_code, 409)
        requirement = AgentRequirement.objects.get(work_id=work_id, version=2)
        requirement.content = "篡改"
        with self.assertRaises(ValidationError):
            requirement.save()
        root = AgentRun.objects.get(pk=AgentConversation.objects.get(pk=conversation_id).root_run_id)
        first, created = append_public_event(root.pk, root.pk, "e1", "progress", {"state": "running"},
                                             work=AgentWorkTask.objects.get(pk=work_id))
        again, duplicate = append_public_event(root.pk, root.pk, "e1", "progress", {},
                                               work=AgentWorkTask.objects.get(pk=work_id))
        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(list(AgentEvent.objects.filter(root=root).values_list("seq", flat=True)), [1, 2])

    def test_old_model_logs_stay_nullable_and_physical_ids_unique(self):
        ModelCallLog.objects.create(purpose="business", status="success", duration_ms=1)
        self.assertIsNone(ModelCallLog.objects.get().root_id)
        ModelCallLog.objects.create(purpose="business", status="pending", duration_ms=1,
                                    physical_call_id="call-1")
        self.assertEqual(ModelCallLog.objects.filter(physical_call_id="call-1").count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ModelCallLog.objects.create(purpose="business", status="pending", duration_ms=1,
                                        physical_call_id="call-1")

    def test_cancel_blocks_root_before_native_completion(self):
        conversation_id = self.call(agent_api.conversations, "post", {}).data["id"]
        message = self.call(agent_api.messages, "post", {"text": "做方案", "client_request_id": "cancel-m"},
                            conversation_id=conversation_id)
        work_id = self.call(agent_api.work_list, "post", {"conversation_id": conversation_id,
            "message_id": message.data["id"], "goal": "做方案"}).data["id"]
        root = AgentRun.objects.get(pk=AgentConversation.objects.get(pk=conversation_id).root_run_id)
        root.policy = {"identity": {"owner_id": self.owner.pk}, "fence": 1}
        root.state = "running"
        root.save(update_fields=["policy", "state"])
        response = self.call(agent_api.cancel_work, "post", {"expected_version": 1,
            "request_id": "cancel-once"}, work_id=work_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["state"], "stopping")
        root.refresh_from_db()
        self.assertEqual(root.state, "stopping")
        self.assertEqual(root.policy["fence"], 2)
        self.assertEqual(self.call(agent_api.cancel_work, "post", {"expected_version": 1,
            "request_id": "cancel-once"}, work_id=work_id).data["event_seq"], response.data["event_seq"])
