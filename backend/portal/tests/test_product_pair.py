import tempfile
from pathlib import Path

from django.test import override_settings

from portal.product_models import DocumentArtifact
from portal.product_pair import pair_snapshot, save_report_content
from portal.product_service import ProductError, append_revision
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
        blueprint_hash = task.revisions.get(kind="blueprint", version=task.blueprint_version).sha256
        first = save_report_content(task, "technical-solution", {"blocks": [{"id": "same", "text": "技术路线", "source_ids": ["SINPUT"]}], "pending": []}, input_hash=input_revision.sha256, blueprint_hash=blueprint_hash, actor=self.owner)
        second = save_report_content(task, "feasibility", {"blocks": [{"id": "same", "text": "条件评估", "source_ids": ["SINPUT"]}], "pending": []}, input_hash=input_revision.sha256, blueprint_hash=blueprint_hash, actor=self.owner)
        self.assertEqual({block["ref"] for block in pair_snapshot(task)["blocks"]}, {"technical-solution:same", "feasibility:same"})
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
            response = self.owner_client.get(url)
            self.assertEqual(response.status_code, 200)
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
