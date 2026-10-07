from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from portal import agent_api
from portal.agent_models import (AgentBusinessReference, AgentMessage, AgentProject, AgentRequirement,
                                 AgentSkillInstallation, AgentWorkTask)
from portal.agent_skills import approved_skill_source, reviewed_catalog, skill_bundle
from portal.model_config import ModelCallLog
from portal.models import Module

from .base import PortalTestCase


@override_settings(AGENT_PLATFORM_ENABLED=True)
class AgentApiTests(PortalTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.owner = self.create_user("api-owner", "product")
        self.other = self.create_user("api-other", "product")
        for user in (self.owner, self.other):
            user.department_code = "product"
            user.save(update_fields=["department_code"])

    def call(self, view, user, method="get", data=None, **kwargs):
        request = getattr(self.factory, method)("/api/agent/", data=data, format="json")
        force_authenticate(request, user=user)
        response = view(request, **kwargs)
        response.render()
        return response

    def test_name_only_project_and_owner_isolation(self):
        first = self.call(agent_api.projects, self.owner, "post", {"name": "同名项目"})
        second = self.call(agent_api.projects, self.owner, "post", {"name": "同名项目"})
        self.assertEqual(first.status_code, 201)
        self.assertNotEqual(first.data["id"], second.data["id"])
        self.assertEqual(AgentProject.objects.count(), 2)
        self.assertEqual(self.call(agent_api.conversations, self.other, "post",
                              {"project_id": first.data["id"]}).status_code, 404)
        self.assertEqual(self.call(agent_api.projects, self.owner, "post",
                              {"name": "x", "owner_id": self.other.pk}).status_code, 400)
        self.assertEqual(AgentWorkTask.objects.count(), 0)

    def test_message_idempotency_and_unverified_attachments_blocked(self):
        conversation_id = self.call(agent_api.conversations, self.owner, "post", {}).data["id"]
        body = {"text": "普通问答", "client_request_id": "request-1"}
        first = self.call(agent_api.messages, self.owner, "post", body, conversation_id=conversation_id)
        second = self.call(agent_api.messages, self.owner, "post", body, conversation_id=conversation_id)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(self.call(agent_api.messages, self.owner, "post",
            {**body, "attachment_references": ["unverified"]},
            conversation_id=conversation_id).status_code, 400)
        self.assertEqual(AgentWorkTask.objects.count(), 0)

    def test_uploaded_attachment_is_private_and_hash_checked(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with override_settings(PRODUCT_STORAGE_ROOT=directory.name):
            request = self.factory.post("/api/agent/attachments/",
                {"file": SimpleUploadedFile("notes.txt", b"private source")}, format="multipart")
            force_authenticate(request, user=self.owner)
            uploaded = agent_api.attachments(request)
            uploaded.render()
            self.assertEqual(uploaded.status_code, 201)
            reference = {key: uploaded.data[key] for key in ("type", "id", "sha256")}
            self.assertEqual(self.call(agent_api.attachment_detail, self.other,
                attachment_id=reference["id"]).status_code, 404)
            conversation_id = self.call(agent_api.conversations, self.owner, "post", {}).data["id"]
            accepted = self.call(agent_api.messages, self.owner, "post", {"text": "读取附件",
                "client_request_id": "attachment-message", "attachment_references": [reference]},
                conversation_id=conversation_id)
            self.assertEqual(accepted.status_code, 201)
            self.assertEqual(accepted.data["attachment_references"], [reference])
            self.assertEqual(self.call(agent_api.messages, self.other, "post", {"text": "越权",
                "client_request_id": "other-message", "attachment_references": [reference]},
                conversation_id=self.call(agent_api.conversations, self.other, "post", {}).data["id"]).status_code, 404)

    def test_skills_are_reviewed_and_personal(self):
        self.assertEqual(self.call(agent_api.installations, self.owner, "post",
            {"skill_id": "unknown", "enabled": True}).status_code, 404)
        response = self.call(agent_api.installations, self.owner, "post",
                             {"skill_id": "source-check", "enabled": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AgentSkillInstallation.objects.filter(owner=self.owner).count(), 1)
        self.assertEqual(self.call(agent_api.installations, self.other).data["items"], [])
        digest = reviewed_catalog()["source-check"]["digest"]
        self.assertIn("来源核对", approved_skill_source("source-check", digest))
        self.assertIsNone(approved_skill_source("source-check", "0" * 64))
        self.assertEqual(skill_bundle(self.owner)["versions"][0]["digest"], digest)
        self.assertIsNone(skill_bundle(self.other)["digest"])

    def test_dispatch_runs_once_after_commit_and_reports_real_state(self):
        conversation_id = self.call(agent_api.conversations, self.owner, "post", {}).data["id"]
        with patch("portal.agent_execution.dispatch_message", return_value={"state": "submitted"}) as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.call(agent_api.messages, self.owner, "post",
                    {"text": "问题", "client_request_id": "once"}, conversation_id=conversation_id)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(AgentMessage.objects.get(pk=response.data["id"]).execution_state, "submitted")
            repeated = self.call(agent_api.messages, self.owner, "post",
                {"text": "问题", "client_request_id": "once"}, conversation_id=conversation_id)
            self.assertTrue(repeated.data["applied"])
            dispatch.assert_called_once()

    def test_manager_usage_preserves_unknown_tokens(self):
        manager = self.create_user("api-manager", "general_manager")
        conversation_id = self.call(agent_api.conversations, self.owner, "post", {}).data["id"]
        conversation = self.owner.agentconversation_set.get(pk=conversation_id)
        root = conversation.agentrun_set.get(pk=conversation.root_run_id)
        ModelCallLog.objects.create(actor=self.owner, purpose="business", status="success", duration_ms=5,
            root=root, run=root, conversation=conversation, physical_call_id="physical-1",
            prompt_tokens=10, completion_tokens=20)
        ModelCallLog.objects.create(actor=self.owner, purpose="business", status="failed", duration_ms=5,
            root=root, run=root, conversation=conversation, physical_call_id="physical-2")
        response = self.call(agent_api.management_usage, manager)
        self.assertEqual(response.data["calls"], 2)
        self.assertEqual(response.data["unknown_usage_calls"], 1)
        self.assertIsNone(response.data["prompt_tokens"])
        module = Module.objects.get(code="business")
        module.enabled = False
        module.save(update_fields=["enabled"])
        self.assertEqual(self.call(agent_api.management_usage, manager).status_code, 403)

    def test_manager_references_only_expose_typed_read_only_links(self):
        manager = self.create_user("reference-manager", "general_manager")
        conversation_id = self.call(agent_api.conversations, self.owner, "post", {}).data["id"]
        message_id = self.call(agent_api.messages, self.owner, "post", {"text": "生成成果",
            "client_request_id": "reference-message"}, conversation_id=conversation_id).data["id"]
        work_id = self.call(agent_api.work_list, self.owner, "post", {"conversation_id": conversation_id,
            "message_id": message_id, "goal": "生成成果"}).data["id"]
        work = AgentWorkTask.objects.get(pk=work_id)
        requirement = AgentRequirement.objects.get(work=work, version=1)
        types = ("document_task", "document_source", "document_artifact", "resume_batch",
                 "resume_artifact", "business_revision", "finance_record", "hr_screening_batch")
        for domain_type in types:
            AgentBusinessReference.objects.create(work=work, root=work.conversation.agentrun_set.get(
                pk=work.conversation.root_run_id), requirement=requirement, domain_type=domain_type,
                object_id="source-1", revision="1", operation_key=domain_type)
        response = self.call(agent_api.management_work, manager)
        self.assertEqual(response.status_code, 200)
        references = response.data["items"][0]["business_references"]
        self.assertEqual({item["domain_type"] for item in references}, set(types[:6]))
        for reference in references:
            self.assertEqual(reference["url"],
                f"/api/agent/management/references/{reference['reference_id']}/")
            self.assertEqual("download_url" in reference,
                reference["domain_type"] in {"document_source", "document_artifact", "resume_artifact"})

    def test_usage_department_comes_from_historical_conversation_not_current_employee(self):
        manager = self.create_user("usage-history-manager", "general_manager")
        conversation_id = self.call(agent_api.conversations, self.owner, "post", {}).data["id"]
        conversation = self.owner.agentconversation_set.get(pk=conversation_id)
        root = conversation.agentrun_set.get(pk=conversation.root_run_id)
        ModelCallLog.objects.create(actor=self.owner, purpose="business", status="success", duration_ms=5,
            root=root, run=root, conversation=conversation, physical_call_id="historical-product-call",
            prompt_tokens=10, completion_tokens=20)
        self.owner.department_code = "hr"
        self.owner.save(update_fields=["department_code"])
        product = self.call(agent_api.management_usage, manager, data={"department_code":"product"})
        human_resources = self.call(agent_api.management_usage, manager, data={"department_code":"hr"})
        self.assertEqual(product.data["calls"], 1)
        self.assertEqual((product.data["prompt_tokens"], product.data["completion_tokens"]), (10, 20))
        self.assertEqual(human_resources.data["calls"], 0)
