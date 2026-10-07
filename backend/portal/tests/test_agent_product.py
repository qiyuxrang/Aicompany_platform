import hashlib
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from portal.agent_models import AgentBusinessReference, AgentConversation, AgentMessage, AgentRequirement, AgentRun, AgentWorkTask
from portal.agent_runtime import AgentDenied, RuntimeGuard, initialize_root
from portal.agent_tools import AgentTools, tools_for_run
from portal.product_documents import DocumentError, render_report_draft
from portal.product_agent import bind_task, record_product_sources
from portal.product_models import DocumentApproval, DocumentArtifact, DocumentSource, DocumentTask
from portal.product_service import ProductError, append_revision, approval_authorization, digest
from portal.product_worker import ExecutionError, _finish, _formal_content_guard, _guard, claim_task

from .base import PortalTestCase


@override_settings(PRODUCT_P1_ENABLED=True, PRODUCT_MODEL_CALLS_ALLOWED=True,
                   PRODUCT_BLUEPRINT_KNOWLEDGE_MODE="source_only_preview")
class AgentProductTests(TransactionTestCase):
    create_user = PortalTestCase.create_user

    def setUp(self):
        call_command("seed_portal", stdout=StringIO())
        self.owner = self.create_user("agent-product-owner", "product")
        self.owner.department_code = "product"
        self.owner.save(update_fields=["department_code"])
        self.owner.refresh_from_db()
        conversation = AgentConversation.objects.create(owner=self.owner, department_code="product")
        self.work = AgentWorkTask.objects.create(
            owner=self.owner, department_code="product", conversation=conversation, goal="依据资料形成产品方案",
            state="running", current_requirement_version=1,
        )
        message = AgentMessage.objects.create(conversation=conversation, work=self.work, role="user", content="形成方案")
        requirement = AgentRequirement.objects.create(work=self.work, version=1, user_message=message, content="形成方案")
        self.root = AgentRun.objects.create(
            id=conversation.root_run_id, root_run_id=conversation.root_run_id, conversation=conversation,
            work=self.work, requirement=requirement, state="running",
            deadline_at=timezone.now() + timedelta(hours=1),
            policy={"max_actions": 30, "max_model_calls": 20, "max_tool_calls": 20,
                    "max_launches": 5, "max_concurrent": 5, "max_active_ms": 3600000},
        )
        self.guard = RuntimeGuard(initialize_root(self.root.pk, self.owner.pk))
        self.tools = AgentTools(self.guard)

    def test_create_is_bound_idempotent_and_requires_real_sources(self):
        created = self.tools.execute("product_create_task", {"title": "设备系统方案"}, "create-1")
        replay = self.tools.execute("product_create_task", {"title": "设备系统方案"}, "create-1")
        self.assertEqual(created, replay)
        self.assertEqual(DocumentTask.objects.count(), 1)
        task = DocumentTask.objects.get(pk=created["task_id"])
        self.assertEqual(task.agent_root_id, str(self.root.pk))
        self.assertEqual(task.agent_work_id, str(self.work.pk))
        self.assertEqual(task.agent_requirement_version, 1)
        self.assertEqual(task.agent_root_fence, self.guard.binding.fence)
        self.assertEqual(task.revisions.get(kind="input").payload["requirements"], self.work.goal)
        self.assertEqual(AgentBusinessReference.objects.get(
            root=self.root, operation_key="product:create:create-1").revision, str(task.version))
        with self.assertRaisesMessage(ProductError, "请先上传一份设备清单"):
            self.tools.execute("product_queue_blueprint", {"task_id": str(task.pk), "expected_version": task.version}, "blueprint-1")
        with self.assertRaises(ProductError):
            self.tools.execute("product_create_task", {"title": "另一个", "owner_id": self.owner.pk}, "forged")

    def test_correction_and_cancellation_reject_late_worker_commit(self):
        task_id = self.tools.create_product_task("设备系统方案", "create-2")["task_id"]
        task = DocumentTask.objects.get(pk=task_id)
        task.state = "QUEUED"
        task.pending_action = "blueprint"
        task.save(update_fields=["state", "pending_action"])
        claimed = claim_task()
        self.assertIsNotNone(claimed)
        self.work.current_requirement_version = 2
        self.work.save(update_fields=["current_requirement_version"])
        with self.assertRaises(ExecutionError) as error, transaction.atomic():
            _guard(claimed[0], claimed[1])
        self.assertEqual(error.exception.code, "agent_binding_stale")
        self.assertFalse(_finish(*claimed, "COMPLETED", "FINAL_REVIEW"))
        task.refresh_from_db()
        self.assertEqual((task.state, task.error_code), ("WAITING_INPUT", "agent_binding_stale"))
        self.assertEqual(task.artifacts.count(), 0)

        self.work.current_requirement_version = 1
        self.work.save(update_fields=["current_requirement_version"])
        self.root.state = "terminated"
        self.root.save(update_fields=["state"])
        with self.assertRaises(AgentDenied):
            self.tools.create_product_task("不可创建", "create-after-stop")

    def test_owner_blueprint_confirmation_is_only_generation_gate(self):
        input_payload = {"project": "手工任务", "requirements": "根据已有资料", "background": "", "items": [], "conditions": []}
        task = DocumentTask.objects.create(owner=self.owner, title="手工任务", idempotency_key="manual-task",
                                           payload_hash=digest(input_payload), state="WAITING_REVIEW", stage="BLUEPRINT")
        input_revision = append_revision(task, "input", input_payload, actor=self.owner)
        blueprint = append_revision(task, "blueprint", {
            "purpose": "设备系统方案", "audience": "项目人员", "conditions": [], "missing": [], "conflicts": [],
            "chapters": [{"id": "chapter-1", "title": "系统设计", "scope": "技术设计", "source_ids": []}],
            "template_version": "frozen-original-v1",
        }, input_hash=input_revision.sha256, actor=self.owner)
        task.input_version = input_revision.version
        task.blueprint_version = blueprint.version
        task.save(update_fields=["input_version", "blueprint_version"])
        bind_task(self.guard, task.pk, task.version)
        with self.assertRaises(ProductError):
            self.tools.queue_product_outputs(str(task.pk), task.version, "outputs-before-confirm")
        DocumentApproval.objects.create(task=task, revision=blueprint, actor=self.owner, decision="approve",
                                        sha256=blueprint.sha256, authorization=approval_authorization(task, self.owner))
        queued = self.tools.queue_product_outputs(str(task.pk), task.version, "outputs-after-confirm")
        self.assertEqual((queued["state"], queued["blocked"]), ("QUEUED", False))

    def test_independent_product_task_keeps_original_worker_path(self):
        task = DocumentTask.objects.create(owner=self.owner, title="独立旧任务", idempotency_key="legacy-task",
                                           payload_hash=digest({}), state="QUEUED", pending_action="knowledge")
        claimed = claim_task()
        self.assertEqual(claimed[0], task.pk)

    def test_begin_work_uses_current_message_and_keeps_root_usage(self):
        conversation = AgentConversation.objects.create(owner=self.owner, department_code="product")
        message = AgentMessage.objects.create(conversation=conversation, role="user", content="根据设备清单写方案")
        AgentRun.objects.create(id=conversation.root_run_id, root_run_id=conversation.root_run_id,
            conversation=conversation, state="running", deadline_at=timezone.now() + timedelta(hours=1),
            policy={"max_actions": 30, "max_model_calls": 20, "max_tool_calls": 20,
                    "max_launches": 5, "max_concurrent": 5, "max_active_ms": 3600000})
        guard = RuntimeGuard(initialize_root(conversation.root_run_id, self.owner.pk))
        action = guard.admit("tool", "user-message-1")
        guard.finish(action)
        tools = AgentTools(guard, message_id=message.pk)
        result = tools.begin_work("begin-1")
        self.assertEqual(result, tools.begin_work("begin-1"))
        root = AgentRun.objects.get(pk=conversation.root_run_id)
        message.refresh_from_db()
        self.assertEqual(root.action_count, 1)
        self.assertEqual(root.work_id, message.work_id)
        self.assertEqual(root.requirement.version, 1)
        self.assertIn("begin_work", [tool.name for tool in tools_for_run(guard, message_id=message.pk)])

    def test_completed_work_allows_new_message_without_resetting_root(self):
        original_message = self.work.agentrequirement_set.get(version=1).user_message
        conversation = self.root.conversation
        new_message = AgentMessage.objects.create(conversation=conversation, role="user",
                                                   content="另起一份产品方案")
        for state in ("queued", "running"):
            self.work.state = state
            self.work.save(update_fields=["state"])
            with self.assertRaises(ProductError):
                AgentTools(self.guard, message_id=new_message.pk).begin_work(f"blocked-{state}")
        self.work.state = "completed"
        self.work.save(update_fields=["state"])
        action = self.guard.admit("tool", "before-next-work")
        self.guard.finish(action)
        self.root.refresh_from_db()
        previous_count = self.root.action_count
        next_work = AgentTools(self.guard, message_id=new_message.pk).begin_work("next-work")
        self.assertNotEqual(next_work["work_id"], str(self.work.pk))
        self.root.refresh_from_db()
        self.work.refresh_from_db()
        self.assertEqual(self.root.action_count, previous_count)
        self.assertEqual(self.root.state, "running")
        self.assertEqual(str(self.root.work_id), next_work["work_id"])
        self.assertEqual(self.work.state, "completed")
        self.assertEqual(AgentTools(self.guard, message_id=new_message.pk).begin_work("next-work"), next_work)
        self.assertEqual(AgentTools(self.guard, message_id=original_message.pk).begin_work("old-replay"),
                         {"work_id": str(self.work.pk), "requirement_version": 1})

    def test_formal_worker_rejects_repeated_filler_and_unsupported_financial_claim(self):
        chapter = type("Chapter", (), {"payload": {"paragraphs": ["合成重复段落内容" * 8000]}})()
        with self.assertRaises(ExecutionError) as repeated:
            _formal_content_guard([chapter], "technical-solution")
        self.assertEqual(repeated.exception.code, "repeated_body_filler")
        chapter.payload = {"paragraphs": ["投资成本100万元，预计回报率30%。"]}
        with self.assertRaises(ExecutionError) as unsupported:
            _formal_content_guard([chapter], "feasibility")
        self.assertEqual(unsupported.exception.code, "investment_evidence_required")
        with self.assertRaises(DocumentError) as rendered:
            render_report_draft(None, None, None, [chapter], "feasibility")
        self.assertEqual(rendered.exception.code, "investment_evidence_required")
        chapter.payload = {"paragraphs": ["本项目包含2台测试设备，设备参数为30%。"]}
        _formal_content_guard([chapter], "feasibility")

    def test_completed_worker_projects_exact_artifact_references(self):
        task_id = self.tools.create_product_task("成果引用项目", "create-result")['task_id']
        task = DocumentTask.objects.get(pk=task_id)
        task.state = "QUEUED"
        task.pending_action = "generate_outputs"
        task.save(update_fields=["state", "pending_action"])
        claimed = claim_task()
        self.assertIsNotNone(claimed)
        artifacts = [DocumentArtifact.objects.create(
            task=task, family=family, version=index, path=f"synthetic-{index}",
            sha256=str(index) * 64, blueprint_hash="b" * 64, input_hash="a" * 64,
            template_hash="c" * 64)
            for index, family in enumerate(("technical-solution", "feasibility", "presentation"), 1)]
        with self.assertRaises(ProductError):
            _finish(*claimed, "COMPLETED", "FINAL_REVIEW")
        task.refresh_from_db()
        self.assertEqual(task.state, "RUNNING")
        with patch("portal.product_pair.output_current", return_value=True), patch(
                "portal.product_storage.verified_artifact", return_value=True):
            self.assertTrue(_finish(*claimed, "COMPLETED", "FINAL_REVIEW"))
        self.work.refresh_from_db()
        self.assertEqual(len(self.work.result_references), 3)
        self.assertEqual({row["object_id"] for row in self.work.result_references},
                         {str(artifact.pk) for artifact in artifacts})
        references = AgentBusinessReference.objects.filter(work=self.work, domain_type="document_artifact")
        self.assertEqual({(row.object_id, row.revision) for row in references},
                         {(str(artifact.pk), str(artifact.version)) for artifact in artifacts})

    def test_owner_confirmed_source_registers_exact_file_hash(self):
        task_id = self.tools.create_product_task("来源引用项目", "create-source")['task_id']
        task = DocumentTask.objects.get(pk=task_id)
        with tempfile.TemporaryDirectory() as directory, override_settings(PRODUCT_STORAGE_ROOT=directory):
            content = b"synthetic source evidence"
            Path(directory, "source.txt").write_bytes(content)
            checksum = hashlib.sha256(content).hexdigest()
            source = DocumentSource.objects.create(
                task=task, original_name="source.txt", purpose="background", media_type="text/plain",
                path="source.txt", sha256=checksum, size=len(content), uploaded_by=self.owner)
            input_payload = dict(task.revisions.get(kind="input").payload)
            input_payload["sources"] = [{"id": str(source.pk), "sha256": checksum}]
            input_revision = append_revision(task, "input", input_payload, actor=self.owner)
            blueprint = append_revision(task, "blueprint", {
                "purpose": "合成方案", "audience": "测试", "conditions": [], "missing": [], "conflicts": [],
                "chapters": [{"id": "overview", "title": "合成概述", "scope": "合成范围",
                              "source_ids": [str(source.pk)]}], "template_version": "frozen-original-v1",
            }, input_hash=input_revision.sha256, actor=self.owner)
            task.input_version = input_revision.version
            task.blueprint_version = blueprint.version
            task.save(update_fields=["input_version", "blueprint_version"])
            self.root.refresh_from_db()
            with self.assertRaises(ProductError):
                record_product_sources(task, self.root, self.work)
            DocumentApproval.objects.create(task=task, revision=blueprint, actor=self.owner,
                decision="approve", sha256=blueprint.sha256,
                authorization=approval_authorization(task, self.owner))
            record_product_sources(task, self.root, self.work)
        reference = AgentBusinessReference.objects.get(work=self.work, domain_type="document_source")
        self.assertEqual((reference.object_id, reference.revision, reference.digest),
                         (str(source.pk), checksum, checksum))
