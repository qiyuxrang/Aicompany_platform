import base64
import hashlib
import json
import tempfile
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db.models import F
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from portal.agent_models import AgentConversation, AgentMessage, AgentRequirement, AgentRootAction, AgentRun, AgentWorkTask
from portal.agent_runtime import RuntimeGuard, initialize_root
from portal.agent_tools import AgentTools
from portal.hr_recruitment_models import JDVersion, RecruitmentRequest
from portal.hr_resume_storage import save_file
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from portal.hr_screening_worker import claim_one, finish_one, process_one
from portal.model_config import GatewayModel, ModelCallLog, ModelRoute, Provider
from portal.models import Module, Role, User
from .base import PortalTestCase


class AgentHrGuardTests(TransactionTestCase):
    create_user = PortalTestCase.create_user

    def setUp(self):
        call_command("seed_portal", stdout=StringIO())
        self.owner = self.create_user("agent-hr-guard", "hr", must_change_password=False)
        self.owner.department_code = "hr"
        self.owner.save(update_fields=["department_code"])
        self.owner.refresh_from_db()
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.enterContext(override_settings(HR_STORAGE_ROOT=self.storage.name))
        self.work, self.root, self.guard = self.create_runtime(self.owner)
        self.request = RecruitmentRequest.objects.create(
            created_by=self.owner, updated_by=self.owner, position_name="工程师",
            skill_requirements=["SQL"],
        )
        self.jd = JDVersion.objects.create(
            request=self.request, version=1, input_version=1, state="confirmed",
            body="需要SQL", requirements={"skill_requirements": ["SQL"]}, created_by=self.owner,
        )
        self.request.current_jd = self.request.official_jd = self.jd
        self.request.save(update_fields=["current_jd", "official_jd"])
        self.batch, self.item = self.create_batch("primary", "resume.txt", "姓名：张三\n技能：SQL".encode())

    @staticmethod
    def create_runtime(owner):
        conversation = AgentConversation.objects.create(owner=owner, department_code="hr")
        work = AgentWorkTask.objects.create(
            owner=owner, department_code="hr", conversation=conversation, goal="筛选简历",
            state="running", current_requirement_version=1,
        )
        message = AgentMessage.objects.create(
            conversation=conversation, work=work, role="user", content="按 SQL 要求筛选",
        )
        requirement = AgentRequirement.objects.create(work=work, version=1, user_message=message,
                                                       content="按 SQL 要求筛选")
        root = AgentRun.objects.create(
            id=conversation.root_run_id, root_run_id=conversation.root_run_id, conversation=conversation,
            work=work, requirement=requirement, state="running",
            deadline_at=timezone.now() + timedelta(hours=1),
            policy={"max_actions": 60, "max_model_calls": 30, "max_tool_calls": 20,
                    "max_launches": 10, "max_concurrent": 5, "max_active_ms": 3600000},
        )
        return work, root, RuntimeGuard(initialize_root(root.pk, owner.pk))

    def create_batch(self, key, filename, content):
        batch = ResumeScreeningBatch.objects.create(
            jd_version=self.jd, created_by=self.owner, idempotency_key=f"guard-{key}", input_version=1,
            requirements={"skill_requirements": ["SQL"]}, status="queued",
        )
        values = {
            "agent_root_id": str(self.root.pk), "agent_work_id": str(self.work.pk),
            "agent_requirement_version": self.work.current_requirement_version,
            "agent_grant_version": self.guard.binding.grant_version,
            "agent_session_version": self.guard.binding.session_version,
            "agent_root_fence": self.guard.binding.fence,
        }
        for field, value in values.items():
            setattr(batch, field, value)
        batch.save(update_fields=[*values, "updated_at"])
        item = ResumeArtifact.objects.create(batch=batch, uploaded_by=self.owner, **save_file(filename, content))
        item.processing_status = "queued"
        item.save(update_fields=["processing_status", "updated_at"])
        return batch, item

    def create_routes(self):
        provider = Provider.objects.create(
            code="hr-agent-guard-provider", name="Synthetic provider",
            base_url="https://models.example.com/v1", api_key_env="PORTAL_MODEL_KEY_HR_GUARD", enabled=True,
        )
        model = GatewayModel.objects.create(
            name="Synthetic model", provider=provider, model_name="synthetic", supports_text=True,
            supports_vision=True, enabled=True,
        )
        module = Module.objects.get(code="hr")
        for code in ("hr_resume_parse", "hr_match_summary", "hr_resume_extract"):
            ModelRoute.objects.create(code=code, name=code, module=module, model=model, enabled=True)

    @staticmethod
    def fake_gateway(payload):
        content = payload["messages"][-1]["content"]
        if isinstance(content, list):
            answer = {"text": "姓名：张三\n技能：SQL", "readable": True}
        else:
            data = json.loads(content)
            if "requirements" in data:
                answer = {"requirements": [
                    {"requirement_id": item["id"], "verdict": "MATCH",
                     "evidence": [{"quote": "技能：SQL"}]}
                    for item in data["requirements"]
                ]}
            else:
                answer = {"skills": {"value": ["SQL"], "status": "extracted",
                                     "source_ref": {"quote": "技能：SQL"}}}
        return {"content": json.dumps(answer, ensure_ascii=False), "duration_ms": 9,
                "prompt_tokens": 12, "completion_tokens": 8}

    @staticmethod
    def vision_pages(count):
        image = b"\xff\xd8\xffsynthetic"
        encoded = base64.b64encode(image).decode()
        digest = hashlib.sha256(image).hexdigest()
        return [{"page": number, "text": "", "needs_vision": True,
                 "image": encoded, "image_sha256": digest} for number in range(1, count + 1)]

    @patch("portal.model_gateway._request_gateway")
    def test_legacy_text_batch_still_processes_without_root_association(self, gateway):
        self.create_routes()
        for field in (
            "agent_root_id", "agent_work_id", "agent_requirement_version", "agent_grant_version",
            "agent_session_version", "agent_root_fence",
        ):
            setattr(self.batch, field, None)
        self.batch.save(update_fields=[
            "agent_root_id", "agent_work_id", "agent_requirement_version", "agent_grant_version",
            "agent_session_version", "agent_root_fence", "updated_at",
        ])
        gateway.side_effect = self.fake_gateway

        process_one(*claim_one())

        self.item.refresh_from_db()
        self.root.refresh_from_db()
        self.assertEqual(self.item.processing_status, "completed", self.item.error_code)
        self.assertEqual(gateway.call_count, 2)
        self.assertEqual(self.root.model_count, 0)
        self.assertEqual(ModelCallLog.objects.count(), 2)
        self.assertTrue(all(record.root_id is None for record in ModelCallLog.objects.all()))

    @patch("portal.hr_screening_worker.inspect_pdf")
    @patch("portal.model_gateway._request_gateway")
    def test_text_and_each_vision_page_accumulate_and_link_real_model_logs(self, gateway, inspect_pdf):
        self.create_routes()
        _, second_item = self.create_batch("pdf", "resume.pdf", b"%PDF-1.7 synthetic")
        inspect_pdf.return_value = self.vision_pages(3)
        gateway.side_effect = self.fake_gateway
        first_claim = claim_one()
        self.assertEqual(first_claim[0], self.item.pk)
        process_one(*first_claim)
        second_claim = claim_one()
        self.assertEqual(second_claim[0], second_item.pk)

        process_one(*second_claim)

        self.item.refresh_from_db()
        second_item.refresh_from_db()
        self.root.refresh_from_db()
        records = list(ModelCallLog.objects.order_by("created_at", "pk"))
        self.assertEqual(self.item.processing_status, "completed", self.item.error_code)
        self.assertEqual(second_item.processing_status, "completed", second_item.error_code)
        self.assertEqual(gateway.call_count, 7)
        self.assertEqual(self.root.model_count, 7)
        self.assertEqual(AgentRootAction.objects.filter(root=self.root, kind="model").count(), 7)
        self.assertEqual(len(records), 7)
        self.assertEqual({record.root_id for record in records}, {self.root.pk})
        self.assertEqual({record.run_id for record in records}, {self.root.pk})
        self.assertEqual({record.work_id for record in records}, {self.work.pk})
        self.assertEqual({record.requirement_id for record in records}, {self.root.requirement_id})
        self.assertEqual(len({record.physical_call_id for record in records}), 7)
        self.assertTrue(all(record.status == "success" for record in records))
        actions = AgentRootAction.objects.filter(root=self.root, kind="model")
        self.assertEqual(set(actions.values_list("status", flat=True)), {"finished"})
        self.assertEqual(set(actions.values_list("action_key", flat=True)),
                         {record.physical_call_id for record in records})

    def test_agent_tool_binds_then_uses_the_existing_queue_path(self):
        self.batch.status = "pending"
        self.batch.save(update_fields=["status", "updated_at"])
        self.item.processing_status = "pending"
        self.item.save(update_fields=["processing_status", "updated_at"])

        result = AgentTools(self.guard).execute(
            "hr_queue_batch", {"batch_id": str(self.batch.pk), "expected_version": self.batch.version}, "queue-1",
        )

        self.batch.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(result["status"], "queued")
        self.assertEqual(self.batch.agent_root_id, str(self.root.pk))
        self.assertEqual(self.batch.agent_work_id, str(self.work.pk))
        self.assertEqual(self.batch.agent_requirement_version, self.work.current_requirement_version)
        self.assertEqual(self.batch.agent_grant_version, self.owner.grant_version)
        self.assertEqual(self.batch.agent_session_version, self.owner.session_version)
        self.assertEqual(self.batch.agent_root_fence, self.guard.binding.fence)
        self.assertEqual(self.item.processing_status, "queued")

    def test_late_cancelled_requirement_revoked_and_fenced_replies_do_not_commit(self):
        self.create_routes()
        cases = ("cancelled", "corrected", "revoked", "fenced", "artifact_fenced")
        for index, case in enumerate(cases):
            with self.subTest(case=case):
                if index:
                    self.work, self.root, self.guard = self.create_runtime(self.owner)
                    batch, item = self.create_batch(case, "resume.txt", "姓名：张三\n技能：SQL".encode())
                else:
                    item, batch = self.item, self.batch
                work = self.work
                root = self.root
                item.processing_status = "queued"
                item.save(update_fields=["processing_status", "updated_at"])
                batch.status = "queued"
                batch.save(update_fields=["status", "updated_at"])
                claimed = claim_one()
                self.assertIsNotNone(claimed)

                def reply(payload):
                    if case == "cancelled":
                        AgentRun.objects.filter(pk=root.pk).update(state="cancelled")
                    elif case == "corrected":
                        AgentWorkTask.objects.filter(pk=work.pk).update(current_requirement_version=2)
                    elif case == "revoked":
                        self.owner.roles.clear()
                        User.objects.filter(pk=self.owner.pk).update(grant_version=F("grant_version") + 1)
                    elif case == "artifact_fenced":
                        ResumeArtifact.objects.filter(pk=item.pk).update(fence=F("fence") + 1)
                    else:
                        current = AgentRun.objects.get(pk=root.pk)
                        current.policy = {**current.policy, "fence": current.policy["fence"] + 1}
                        AgentRun.objects.filter(pk=root.pk).update(policy=current.policy)
                    return self.fake_gateway(payload)

                with patch("portal.model_gateway._request_gateway", side_effect=reply) as gateway:
                    process_one(*claimed)

                item.refresh_from_db()
                self.assertEqual(gateway.call_count, 1)
                self.assertEqual(item.processing_status, "running" if case == "artifact_fenced" else "failed")
                self.assertEqual(item.extracted_text, "")
                self.assertEqual(item.profile, {})
                self.assertEqual(item.match, {})
                action = AgentRootAction.objects.get(root=root, kind="model")
                record = ModelCallLog.objects.get(actor=self.owner, root_id=root.pk)
                self.assertEqual(action.status, "error")
                self.assertEqual(record.root_id, root.pk)
                self.assertEqual(record.physical_call_id, action.action_key)
                if case == "revoked":
                    hr_role = Role.objects.get(code="hr")
                    self.owner.roles.add(hr_role)
                    User.objects.filter(pk=self.owner.pk).update(grant_version=F("grant_version") + 1)
                    self.owner.refresh_from_db()

    @patch("portal.model_gateway._request_gateway")
    def test_limit_rejection_persists_root_termination_and_does_not_call_gateway(self, gateway):
        self.create_routes()
        current = AgentRun.objects.get(pk=self.root.pk)
        current.model_count = 1
        current.policy = {**current.policy, "max_model_calls": 1}
        current.save(update_fields=["model_count", "policy"])

        process_one(*claim_one())

        self.root.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.root.state, "terminated")
        self.assertEqual(self.root.stop_reason, "max_model_calls")
        self.assertEqual(self.root.model_count, 1)
        self.assertEqual(self.item.processing_status, "failed")
        gateway.assert_not_called()
        self.assertEqual(ModelCallLog.objects.count(), 0)

    def test_partial_binding_and_archived_state_are_never_treated_as_legacy(self):
        self.batch.agent_root_id = None
        self.batch.save(update_fields=["agent_root_id", "updated_at"])
        self.assertIsNone(claim_one())
        self.item.refresh_from_db()
        self.assertEqual(self.item.processing_status, "queued")

        self.batch.archive_state = "deleted"
        self.batch.save(update_fields=[
            "archive_state", "updated_at",
        ])
        self.item.archive_state = "deleted"
        self.item.processing_status = "running"
        self.item.lease_until = timezone.now() + timedelta(minutes=5)
        self.item.save(update_fields=["archive_state", "processing_status", "lease_until", "updated_at"])
        self.assertIsNone(claim_one())
        self.assertFalse(finish_one(self.item.pk, self.item.fence, error="late"))
        self.batch.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.batch.archive_state, "deleted")
        self.assertEqual(self.item.archive_state, "deleted")
        self.assertEqual(self.item.processing_status, "running")
