import hashlib
from pathlib import Path
from unittest.mock import patch

from django.test import override_settings

from portal.product_documents import frozen_pack
from portal.product_models import DocumentArtifact, DocumentTask
from portal.product_release import CONTENT_CHECKS
from portal.product_service import append_revision, effective_artifact_approval
from portal.product_worker import run_once

from .base import PortalTestCase, json_body
from .test_product_api import ProductApiTests


class ProductReleaseFlowTests(PortalTestCase):
    setUp = ProductApiTests.setUp
    input_payload = ProductApiTests.input_payload
    create_task = ProductApiTests.create_task
    blueprint_payload = ProductApiTests.blueprint_payload
    save_blueprint = ProductApiTests.save_blueprint
    approve_blueprint = ProductApiTests.approve_blueprint

    def file(self, relative, content):
        target = Path(self.storage.name) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return {"path": relative, "sha256": hashlib.sha256(content).hexdigest()}

    def rendered_task(self):
        task = self.save_blueprint(self.create_task())
        self.approve_blueprint(task)
        model_task = DocumentTask.objects.get(pk=task["id"])
        input_revision = model_task.revisions.get(kind="input", version=model_task.input_version)
        blueprint = model_task.revisions.get(kind="blueprint", version=model_task.blueprint_version)
        chapter = append_revision(model_task, "chapter", {"chapter_id": "overview", "title": "项目概述", "paragraphs": ["测试设备"], "source_ids": ["1"]}, input_revision.sha256, blueprint.sha256)
        review = append_revision(model_task, "review", {"passed": True, "issues": [], "chapter_hashes": {"overview": chapter.sha256}}, input_revision.sha256, blueprint.sha256)
        template_hash = next(entry["sha256"] for entry in frozen_pack()["files"] if entry["path"].endswith("template.docx"))
        self.policy = {"template_hash": template_hash, "approval_ref": "ISOLATED-CONTRACT-TEST-NOT-BUSINESS-APPROVAL", "organization": "隔离测试单位"}
        self.release_settings = override_settings(PRODUCT_TEMPLATE_APPROVAL=self.policy, PRODUCT_FORMAL_RELEASE_ENABLED=True, PRODUCT_OFFICE_RENDER_ENABLED=True)
        self.release_settings.enable()
        self.addCleanup(self.release_settings.disable)
        prefix = str(model_task.pk)
        self.draft_content = b"EXPLICIT DRAFT CONTRACT FIXTURE"
        self.final_content = b"RENDERED DOCUMENT CONTRACT FIXTURE"
        draft = {**self.file(f"{prefix}/draft.docx", self.draft_content), "template_hash": template_hash, "render_evidence": {"status": "draft_unverified"}}
        candidate = {**self.file(f"{prefix}/candidate.docx", b"candidate fixture"), "template_hash": template_hash, "render_evidence": {"template_approval": self.policy}}
        office = {"status": "rendered", "docx_sha256": candidate["sha256"], "page_count": 1,
                  "rendered_docx": self.file(f"{prefix}/office/reviewed.docx", self.final_content),
                  "pdf": self.file(f"{prefix}/office/document.pdf", b"%PDF-isolated-fixture"),
                  "pages": [self.file(f"{prefix}/office/page-001.png", b"isolated-image-contract-fixture")]}
        model_task.state, model_task.pending_action, model_task.stage = "QUEUED", "candidate", "RENDER"
        model_task.save()
        with patch("portal.product_documents.render_draft", return_value=draft), patch("portal.product_documents.render_candidate", return_value=candidate), patch("portal.product_rendering.render_office", return_value=office):
            self.assertTrue(run_once())
        model_task.refresh_from_db()
        self.assertEqual(model_task.state, "WAITING_REVIEW", model_task.error_code)
        artifact = DocumentArtifact.objects.get(task=model_task)
        self.assertEqual(artifact.review_id, review.pk)
        self.assertEqual(artifact.sha256, office["rendered_docx"]["sha256"])
        return model_task, artifact

    def verify(self, task, artifact, **overrides):
        preview = self.reviewer_client.get(f"/api/product/artifacts/{artifact.pk}/preview/?page=1")
        if preview.status_code == 200:
            b"".join(preview.streaming_content)
        page = artifact.render_evidence["pages"][0]
        payload = {"expected_version": task.version, "sha256": artifact.sha256,
                   "pages": [{"page": 1, "sha256": page["sha256"], "passed": True, "comment": "隔离合同核对，不代表真实视觉验收"}],
                   "content_checks": {key: True for key in CONTENT_CHECKS}, "comment": "合成材料的接口流程验证"}
        payload.update(overrides)
        return self.reviewer_client.post(f"/api/product/artifacts/{artifact.pk}/verification/", json_body(**payload), content_type="application/json")

    def test_page_hashes_without_preview_cannot_mark_verified(self):
        task, artifact = self.rendered_task()
        page = artifact.render_evidence["pages"][0]
        result = self.reviewer_client.post(f"/api/product/artifacts/{artifact.pk}/verification/", json_body(
            expected_version=task.version, sha256=artifact.sha256,
            pages=[{"page": 1, "sha256": page["sha256"], "passed": True, "comment": "未预览"}],
            content_checks={key: True for key in CONTENT_CHECKS}, comment="无预览回执"), content_type="application/json")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["code"], "page_preview_required")

    def test_duplicate_candidate_queue_reuses_exact_existing_artifact(self):
        task, artifact = self.rendered_task()
        task.state, task.pending_action = "QUEUED", "candidate"
        task.save()
        with patch("portal.product_documents.render_candidate") as render:
            self.assertTrue(run_once())
            render.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.state, "WAITING_REVIEW", task.error_code)
        self.assertEqual(DocumentArtifact.objects.filter(task=task).count(), 1)
        self.assertEqual(DocumentArtifact.objects.get(task=task).pk, artifact.pk)

    def test_title_change_invalidates_candidate_and_preserves_file_title(self):
        task, artifact = self.rendered_task()
        original_title = artifact.render_evidence["document_title"]
        changed = self.owner_client.patch(f"/api/product/tasks/{task.pk}/", json_body(expected_version=task.version, title="新的隔离标题"), content_type="application/json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(self.approve(task, artifact).status_code, 409)
        artifact.refresh_from_db()
        self.assertEqual(artifact.render_evidence["document_title"], original_title)
        response = self.owner_client.get(f"/api/product/artifacts/{artifact.pk}/download/")
        self.assertEqual(b"".join(response.streaming_content), self.draft_content)

    def approve(self, task, artifact):
        task.refresh_from_db()
        return self.reviewer_client.post(f"/api/product/tasks/{task.pk}/decisions/", json_body(expected_version=task.version, target="artifact", target_id=str(artifact.pk), sha256=artifact.sha256, decision="approve", comment="仅隔离合同批准"), content_type="application/json")

    def test_full_isolated_approval_download_and_revocation(self):
        task, artifact = self.rendered_task()
        response = self.owner_client.get(f"/api/product/artifacts/{artifact.pk}/download/")
        self.assertEqual(b"".join(response.streaming_content), self.draft_content)
        self.assertEqual(self.approve(task, artifact).status_code, 409)
        verified = self.verify(task, artifact)
        self.assertEqual(verified.status_code, 200, verified.content)
        self.assertEqual(self.approve(task, artifact).status_code, 201)
        artifact.refresh_from_db()
        self.assertIsNotNone(effective_artifact_approval(artifact))
        response = self.owner_client.get(f"/api/product/artifacts/{artifact.pk}/download/")
        self.assertEqual(b"".join(response.streaming_content), self.final_content)
        with override_settings(PRODUCT_REVIEWER_IDS=()):
            response = self.owner_client.get(f"/api/product/artifacts/{artifact.pk}/download/")
            self.assertEqual(b"".join(response.streaming_content), self.draft_content)

    def test_missing_pages_failed_check_or_mutated_evidence_cannot_approve(self):
        task, artifact = self.rendered_task()
        self.assertEqual(self.verify(task, artifact, pages=[]).status_code, 400)
        checks = {key: True for key in CONTENT_CHECKS}
        checks["quantities"] = False
        self.assertEqual(self.verify(task, artifact, content_checks=checks).status_code, 200)
        self.assertEqual(self.approve(task, artifact).status_code, 409)
        task.refresh_from_db()
        Path(self.storage.name, artifact.render_evidence["pages"][0]["path"]).write_bytes(b"changed")
        self.assertEqual(self.verify(task, artifact).status_code, 409)

    def test_preview_and_verification_require_designated_reviewer(self):
        task, artifact = self.rendered_task()
        url = f"/api/product/artifacts/{artifact.pk}/preview/?page=1"
        self.assertEqual(self.owner_client.get(url).status_code, 404)
        self.assertEqual(self.other_client.get(url).status_code, 404)
        preview = self.reviewer_client.get(url)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("no-store", preview["Cache-Control"])
        self.assertTrue(b"".join(preview.streaming_content))
        detail = self.owner_client.get(f"/api/product/tasks/{task.pk}/").json()
        self.assertNotIn("draft_fallback", detail["artifacts"][0]["render_evidence"])
        self.assertNotIn("path", detail["artifacts"][0]["render_evidence"]["pages"][0])

    def test_changed_template_policy_or_input_invalidates_approval_evidence(self):
        task, artifact = self.rendered_task()
        self.assertEqual(self.verify(task, artifact).status_code, 200)
        with override_settings(PRODUCT_TEMPLATE_APPROVAL={**self.policy, "approval_ref": "changed-policy"}):
            self.assertEqual(self.approve(task, artifact).status_code, 409)
        task.refresh_from_db()
        new_input = self.input_payload()
        new_input["background"] = "changed"
        changed = self.owner_client.patch(f"/api/product/tasks/{task.pk}/", json_body(expected_version=task.version, input=new_input), content_type="application/json")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.approve(task, artifact).status_code, 409)
