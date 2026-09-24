import json
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.db import transaction
from django.test import override_settings
from django.utils import timezone

from portal.model_gateway import GatewayError
from portal.product_models import DocumentApproval, DocumentArtifact, DocumentAttempt, DocumentTask
from portal.product_service import append_revision, digest, approval_authorization
from portal.product_worker import ExecutionError, _store, claim_task, content_checks, current_chapters, run_once

from .base import PortalTestCase


@override_settings(PRODUCT_P1_ENABLED=True, PRODUCT_MODEL_CALLS_ALLOWED=True, PRODUCT_COST_POLICY={
    "approval_ref": "isolated-test-only", "currency": "TEST", "max_task_cost": "100",
    "route_cost_caps": {"product_blueprint": "1", "product_writing": "1", "product_review": "1"},
})
class ProductWorkerTests(PortalTestCase):
    @patch("portal.product_worker.generate_for_use")
    def test_blueprint_prompt_lists_only_current_authorized_source_ids(self, model):
        self.task.pending_action = "blueprint"
        self.task.stage = "BLUEPRINT"
        self.task.save(update_fields=["pending_action", "stage"])
        model.return_value = {"content": json.dumps(self.blueprint_payload), "prompt_tokens": 1, "completion_tokens": 1}

        run_once()

        payload = json.loads(model.call_args.args[2][1]["content"])
        self.assertEqual(payload["allowed_source_ids"], ["r1"])
        self.assertEqual(payload["schema"]["chapters"][0]["source_ids"], ["r1"])

    @patch("portal.product_worker.generate_for_use")
    def test_stage_rules_are_sent_and_bound_to_call_evidence(self, model):
        from portal.product_rules import rules_hash
        from portal.product_worker import _model

        task_id, fence, attempt_id = claim_task()
        model.return_value = {"content": "{}", "prompt_tokens": 1, "completion_tokens": 1}
        self.assertEqual(_model(task_id, fence, attempt_id, "product_writing", {"action": "chapter"}), {})
        system_message = model.call_args.args[2][0]["content"]
        self.assertIn("适用阶段：write", system_message)
        self.assertIn(rules_hash(), system_message)
        self.task.refresh_from_db()
        self.assertEqual(self.task.checkpoint["calls"][0]["rules_hash"], rules_hash())

    def test_chapter_context_excludes_unselected_sources_and_history(self):
        from portal.product_worker import model_input

        payload = {**self.input_payload, "knowledge_sources": [{"id": "selected", "text": "allowed"}, {"id": "other", "text": "unselected"}],
                   "retrieval": {"raw": "private"}, "issue_history": [{"text": "private"}],
                   "statements": [{"text": "included", "source_ids": ["selected"]}, {"text": "excluded", "source_ids": ["other"]}]}
        context = model_input(self.task, payload, ["selected"])
        self.assertEqual(context["items"], [])
        self.assertEqual(context["knowledge_sources"], [{"id": "selected", "text": "allowed"}])
        self.assertEqual(context["statements"], [{"text": "included", "source_ids": ["selected"]}])
        self.assertNotIn("retrieval", context)
        self.assertNotIn("issue_history", context)

    @patch("portal.product_worker._finish", return_value=False)
    @patch("portal.product_documents.render_draft")
    def test_artifact_and_finish_commit_atomically(self, render, finish):
        from portal.product_worker import _render

        chapters = [append_revision(self.task, "chapter", json.loads(self.reply(chapter["id"])["content"]),
                    input_hash=self.input_revision.sha256, blueprint_hash=self.blueprint.sha256) for chapter in self.blueprint_payload["chapters"]]
        task_id, fence, attempt_id = claim_task()
        render.return_value = self.rendered()
        with self.assertRaises(ExecutionError):
            _render(self.task, fence, attempt_id, self.input_revision, self.blueprint, chapters)
        self.assertFalse(DocumentArtifact.objects.filter(task=self.task).exists())

    @override_settings(PRODUCT_COST_POLICY={})
    @patch("portal.product_worker.generate_for_use")
    def test_missing_cost_policy_does_not_block_model_call(self, model):
        self.task.pending_action = "blueprint"
        self.task.stage = "BLUEPRINT"
        self.task.save(update_fields=["pending_action", "stage"])
        model.return_value = {"content": json.dumps(self.blueprint_payload), "prompt_tokens": 10, "completion_tokens": 20}

        run_once()

        self.task.refresh_from_db()
        self.assertEqual(self.task.error_code, "")
        model.assert_called_once()
        self.assertNotIn("budget", self.task.checkpoint)

    def setUp(self):
        self.owner = self.create_user("writer", "product")
        self.reviewer = self.create_user("reviewer", "product")
        self.input_payload = {"project": "隔离合成方案", "requirements": "不新增清单外设备", "background": "仅用于测试",
                              "items": [{"row_id": "r1", "name": "测试设备", "quantity": "2", "unit": "台"}], "conditions": ["不新增清单外设备"]}
        self.blueprint_payload = {"purpose": "隔离验证", "audience": "测试审核人", "chapters": [
            {"id": f"chapter-{index}", "title": f"章节{index}", "scope": "只描述清单", "source_ids": ["r1"]} for index in range(1, 4)],
            "conditions": [{"text": "不新增清单外设备", "type": "program"}], "missing": [], "conflicts": [], "template_version": "frozen-original-v1"}
        self.task = DocumentTask.objects.create(owner=self.owner, reviewer=self.reviewer, title="合成方案", idempotency_key="worker-test", payload_hash=digest(self.input_payload), state="QUEUED", stage="WRITING", pending_action="write")
        self.input_revision = append_revision(self.task, "input", self.input_payload, actor=self.owner)
        self.blueprint = append_revision(self.task, "blueprint", self.blueprint_payload, input_hash=self.input_revision.sha256, actor=self.owner)
        self.task.input_version = self.input_revision.version
        self.task.blueprint_version = self.blueprint.version
        self.task.save(update_fields=["input_version", "blueprint_version"])
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings_override = override_settings(PRODUCT_STORAGE_ROOT=self.temporary.name, PRODUCT_REVIEWER_IDS=[self.reviewer.pk])
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        DocumentApproval.objects.create(task=self.task, revision=self.blueprint, actor=self.owner, decision="approve", sha256=self.blueprint.sha256, authorization=approval_authorization(self.task, self.owner))

    def reply(self, chapter_id):
        chapter = next(chapter for chapter in self.blueprint_payload["chapters"] if chapter["id"] == chapter_id)
        return {"content": json.dumps({"chapter_id": chapter_id, "title": chapter["title"], "paragraphs": ["沿用清单中2台测试设备。"], "source_ids": ["r1"]}), "prompt_tokens": 1, "completion_tokens": 2}

    def rendered(self):
        return {"path": f"{self.task.pk}/draft-test.docx", "sha256": "a" * 64, "template_hash": "b" * 64, "render_evidence": {"status": "not_run"}}

    @override_settings(PRODUCT_MODEL_CALLS_ALLOWED=False)
    @patch("portal.product_worker.generate_for_use")
    def test_external_permission_closed_without_outbound(self, model):
        run_once()
        self.task.refresh_from_db()
        self.assertEqual(self.task.state, "WAITING_INPUT")
        self.assertEqual(self.task.error_code, "model_authorization_required")
        model.assert_not_called()

    @patch("portal.product_documents.render_draft")
    @patch("portal.product_worker.generate_for_use")
    def test_truncated_chapter_is_failure_and_completed_chapters_resume(self, model, render):
        model.side_effect = [self.reply("chapter-1"), self.reply("chapter-2"), GatewayError("output_truncated")]
        run_once()
        self.task.refresh_from_db()
        self.assertEqual(self.task.state, "FAILED")
        self.assertEqual(self.task.error_code, "output_truncated")
        self.assertEqual(len(current_chapters(self.task, self.input_revision.sha256, self.blueprint.sha256)), 2)
        self.assertEqual(DocumentArtifact.objects.count(), 0)
        self.task.state = "QUEUED"
        self.task.save()
        model.reset_mock()
        model.side_effect = [self.reply("chapter-3"), {"content": '{"passed":true,"issues":[]}'}]
        render.return_value = self.rendered()
        run_once()
        self.task.refresh_from_db()
        self.assertEqual(self.task.state, "WAITING_REVIEW")
        self.assertEqual(self.task.stage, "FINAL_REVIEW")
        self.assertEqual(model.call_count, 2)
        self.assertEqual(DocumentArtifact.objects.count(), 1)
        self.assertEqual(DocumentApproval.objects.filter(artifact__isnull=False).count(), 0)

    @patch("portal.product_worker.generate_for_use")
    def test_cancellation_fences_inflight_output(self, model):
        def cancel(*args):
            self.task.refresh_from_db()
            self.task.fence += 1
            self.task.state = "CANCELLED"
            self.task.lease_until = None
            self.task.save()
            return self.reply("chapter-1")
        model.side_effect = cancel
        run_once()
        self.assertFalse(self.task.revisions.filter(kind="chapter").exists())
        self.assertEqual(DocumentAttempt.objects.get().status, "cancelled")

    @patch("portal.product_worker.generate_for_use")
    def test_revocation_before_model_call_refuses(self, model):
        self.owner.roles.clear()
        run_once()
        self.task.refresh_from_db()
        self.assertEqual(self.task.state, "FAILED")
        model.assert_not_called()

    def test_expired_lease_reclaims_with_new_fence_and_late_write_rejected(self):
        first = claim_task()
        self.task.refresh_from_db()
        self.task.lease_until = timezone.now() - timedelta(seconds=1)
        self.task.save()
        second = claim_task()
        self.assertGreater(second[1], first[1])
        with self.assertRaises(ExecutionError):
            with transaction.atomic():
                _store(first[0], first[1], "chapter", {}, self.input_revision.sha256, self.blueprint.sha256)
        self.assertEqual(DocumentAttempt.objects.get(pk=first[2]).error_code, "lease_expired")

    @override_settings(PRODUCT_MAX_MODEL_CALLS=1)
    @patch("portal.product_worker.generate_for_use")
    def test_task_model_budget_is_bounded(self, model):
        model.return_value = self.reply("chapter-1")
        run_once()
        self.task.refresh_from_db()
        self.assertEqual(self.task.state, "WAITING_INPUT")
        self.assertEqual(self.task.error_code, "model_call_limit")
        self.assertEqual(model.call_count, 1)

    def test_attempt_limit_closes_expired_running_attempt(self):
        task_id, fence, attempt_id = claim_task()
        self.task.refresh_from_db()
        self.task.attempt_count = 8
        self.task.lease_until = timezone.now() - timedelta(seconds=1)
        self.task.save()
        self.assertIsNone(claim_task())
        self.task.refresh_from_db()
        self.assertIsNone(self.task.lease_until)
        self.assertGreater(self.task.fence, fence)
        self.assertEqual(DocumentAttempt.objects.get(pk=attempt_id).status, "failed")

    @patch("portal.product_worker.generate_for_use")
    def test_model_cannot_expand_blueprint_source_scope(self, model):
        payload = json.loads(self.reply("chapter-1")["content"])
        payload["source_ids"] = ["unapproved-source"]
        model.return_value = {"content": json.dumps(payload)}
        run_once()
        self.task.refresh_from_db()
        self.assertEqual(self.task.error_code, "invalid_model_output")
        self.assertFalse(self.task.revisions.filter(kind="chapter").exists())

    def test_unknown_number_and_missing_citations_require_review(self):
        chapter = append_revision(self.task, "chapter", {"chapter_id": "chapter-1", "title": "章节", "paragraphs": ["新增999台设备"], "source_ids": []}, input_hash=self.input_revision.sha256, blueprint_hash=self.blueprint.sha256)
        result = content_checks(self.input_payload, self.blueprint_payload, [chapter])
        self.assertFalse(result["passed"])
        self.assertEqual({issue["code"] for issue in result["issues"]}, {"source_review_required", "unsupported_number"})

    @patch("portal.product_worker.generate_for_use")
    def test_owner_revocation_prevents_approved_work(self, model):
        self.owner.roles.clear()
        run_once()
        self.task.refresh_from_db()
        self.assertIn(self.task.error_code, {"blueprint_approval_required", "permission_changed"})
        model.assert_not_called()

    @patch("portal.product_worker.generate_for_use")
    def test_owner_revocation_during_call_discards_output(self, model):
        def revoke(*args):
            self.owner.roles.clear()
            return self.reply("chapter-1")
        model.side_effect = revoke
        run_once()
        self.assertFalse(self.task.revisions.filter(kind="chapter").exists())
        self.assertFalse(self.task.artifacts.exists())

    @patch("portal.product_documents.render_draft")
    def test_manual_change_does_not_reuse_prior_model_review(self, render):
        from portal.product_worker import _render

        chapters = [append_revision(self.task, "chapter", json.loads(self.reply(chapter["id"])["content"]),
                    input_hash=self.input_revision.sha256, blueprint_hash=self.blueprint.sha256) for chapter in self.blueprint_payload["chapters"]]
        append_revision(self.task, "review", {"passed": True, "issues": [], "chapter_hashes": {"chapter-1": "old-hash"}},
                        input_hash=self.input_revision.sha256, blueprint_hash=self.blueprint.sha256)
        task_id, fence, attempt_id = claim_task()
        render.return_value = self.rendered()
        _render(self.task, fence, attempt_id, self.input_revision, self.blueprint, chapters)
        review = DocumentArtifact.objects.get().review
        self.assertFalse(review.payload["passed"])
        self.assertEqual(review.payload["model_review"]["status"], "not_run")
