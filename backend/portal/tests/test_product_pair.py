import tempfile
from pathlib import Path

from django.test import override_settings

from portal.product_models import DocumentApproval, DocumentArtifact
from portal.product_pair import pair_snapshot, save_report_content
from portal.product_service import ProductError, append_revision, approval_authorization, digest
from portal.product_storage import verified_artifact
from .test_product_api import ProductApiTests
from .base import PortalTestCase


class PairDraftTests(PortalTestCase):
    setUp = ProductApiTests.setUp
    input_payload = ProductApiTests.input_payload
    create_task = ProductApiTests.create_task
    blueprint_payload = ProductApiTests.blueprint_payload
    save_blueprint = ProductApiTests.save_blueprint
    approve_blueprint = ProductApiTests.approve_blueprint
    def test_report_versions_are_separate_and_pair_detects_changed_input(self):
        created = self.create_task()
        task = self._task(self.save_blueprint(created))
        input_revision = task.revisions.get(kind="input", version=task.input_version)
        blueprint = task.revisions.get(kind="blueprint", version=task.blueprint_version)
        reports = []
        for version, (family, text, pending_id) in enumerate((("technical-solution", "技术路线", "P1"), ("feasibility", "条件评估", "P2")), 1):
            chapter = append_revision(task, "chapter", {"chapter_id": "overview", "title": "项目概述", "paragraphs": [text], "source_ids": ["1"]},
                                      input_hash=input_revision.sha256, blueprint_hash=blueprint.sha256,
                                      actor=self.owner, family=family, reason="test_lineage")
            report = save_report_content(task, family, {"blocks": [{"id": "same", "text": text, "source_ids": ["SINPUT"]}], "pending": [{"id": pending_id, "text": "同一待确认事项"}]}, input_hash=input_revision.sha256, blueprint_hash=blueprint.sha256, actor=self.owner)
            generation_hash = digest({"family": family, "input": input_revision.sha256, "blueprint": blueprint.sha256,
                                      "chapters": [chapter.sha256], "title": task.title})
            DocumentArtifact.objects.create(task=task, family=family, version=version, path=f"legacy/{family}.docx",
                sha256=str(version) * 64, input_hash=input_revision.sha256, blueprint_hash=blueprint.sha256,
                template_hash="a" * 64, generation_hash=generation_hash, render_evidence={"report_id": str(report.pk)})
            reports.append(report)
        first, second = reports
        with self.assertRaises(ProductError) as caught:
            pair_snapshot(task)
        self.assertEqual(caught.exception.code, "report_approval_required")
        for report in (first, second):
            DocumentApproval.objects.create(task=task, revision=report, actor=self.reviewer, decision="approve",
                                            sha256=report.sha256, authorization=approval_authorization(task, self.reviewer))
        snapshot = pair_snapshot(task)
        self.assertEqual({block["ref"] for block in snapshot["blocks"]}, {"technical-solution:same", "feasibility:same"})
        self.assertTrue(all(source["approval_id"] for source in snapshot["sources"]))
        self.assertEqual(snapshot["pending"], [{"text": "同一待确认事项",
                                                "refs": ["technical-solution:P1", "feasibility:P2"],
                                                "families": ["technical-solution", "feasibility"]}])
        self.assertNotEqual(first.pk, second.pk)
        later = append_revision(task, "input", {**input_revision.payload, "requirements": "已更改"}, actor=self.owner)
        task.input_version = later.version
        task.save(update_fields=["input_version"])
        with self.assertRaises(ProductError):
            pair_snapshot(task)

    def test_current_download_rejects_stale_and_foreign_access(self):
        created = self.create_task()
        saved = self.save_blueprint(created)
        self.approve_blueprint(saved)
        task = self._task(created)
        current = task.revisions.get(kind="input", version=task.input_version)
        with tempfile.TemporaryDirectory() as directory, override_settings(PRODUCT_STORAGE_ROOT=directory):
            path = Path(directory) / str(task.pk) / "artifacts" / "test.docx"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"draft")
            import hashlib
            report = save_report_content(task, "feasibility", {"blocks": [], "pending": []}, input_hash=current.sha256, blueprint_hash=task.revisions.get(kind="blueprint", version=task.blueprint_version).sha256, actor=self.owner)
            artifact = DocumentArtifact.objects.create(task=task, version=1, family="feasibility", path=path.relative_to(directory).as_posix(), sha256=hashlib.sha256(b"draft").hexdigest(), input_hash=current.sha256, blueprint_hash=report.blueprint_hash, template_hash="a" * 64, render_evidence={"report_id": str(report.pk)})
            url = f"/api/product/outputs/{artifact.pk}/download/"
            self.assertEqual(self.other_client.get(url).status_code, 404)
            self.assertEqual(self.owner_client.get(url).status_code, 409)
            response = self.owner_client.get(url + "?history=1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["X-Product-Artifact-Current"], "false")
            for closer in response._resource_closers:
                closer()
            response._resource_closers.clear()
            later = append_revision(task, "input", {**current.payload, "requirements": "已更改"}, actor=self.owner)
            task.input_version = later.version
            task.save(update_fields=["input_version"])
            self.assertEqual(self.owner_client.get(url).status_code, 409)
            self.assertTrue(verified_artifact(artifact).is_file())

    def _task(self, created):
        from portal.product_models import DocumentTask
        return DocumentTask.objects.get(pk=created["id"])
