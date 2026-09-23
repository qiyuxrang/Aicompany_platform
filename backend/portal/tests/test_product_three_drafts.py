from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from django.test import override_settings

from portal.product_models import DocumentArtifact, DocumentTask
from portal.product_worker import run_once
from .test_product_pair import PairDraftTests
from .base import PortalTestCase, json_body


class ThreeDraftFlowTests(PortalTestCase):
    setUp = PairDraftTests.setUp
    input_payload = PairDraftTests.input_payload
    create_task = PairDraftTests.create_task
    blueprint_payload = PairDraftTests.blueprint_payload
    save_blueprint = PairDraftTests.save_blueprint
    approve_blueprint = PairDraftTests.approve_blueprint

    def test_worker_creates_three_drafts_from_two_distinct_chapters(self):
        task = self.create_task()
        saved = self.save_blueprint(task)
        approved = self.approve_blueprint(saved)["task"]
        task_id = approved["id"]
        for family, prose in (("technical-solution", "合成技术路线经过人工复核。"),
                              ("feasibility", "合成可研仅列出备选路线与缺项。")):
            endpoint = "chapters/" if family == "technical-solution" else "report-chapters/"
            response = self.owner_client.post(f"/api/product/tasks/{task_id}/{endpoint}",
                json_body(expected_version=approved["version"], family=family, chapter_id="overview", title="项目概述", paragraphs=[prose], source_ids=["1"]),
                content_type="application/json")
            self.assertIn(response.status_code, (200, 201), response.content)
            approved = response.json().get("task", response.json())
        queued = self.owner_client.post(f"/api/product/tasks/{task_id}/queue/",
            json_body(expected_version=approved["version"], action="three_drafts"), content_type="application/json")
        self.assertEqual(queued.status_code, 200, queued.content)
        self.assertTrue(run_once())
        result = self.owner_client.get(f"/api/product/tasks/{task_id}/outputs/")
        self.assertEqual(result.status_code, 200)
        outputs = result.json()["outputs"]
        self.assertEqual({item["family"] for item in outputs}, {"technical-solution", "feasibility", "presentation"})
        self.assertTrue(all(item["current"] and item["draft"] for item in outputs))
        ppt = DocumentArtifact.objects.get(task_id=task_id, family="presentation")
        with ZipFile(Path(self.storage.name) / ppt.path) as archive:
            slide = archive.read("ppt/slides/slide2.xml").decode()
            self.assertIn("合成技术路线", slide)
            self.assertIn("technical-solution:", slide)
        self.assertEqual(ppt.render_evidence["engine"], "python-pptx-simple-draft")
        self.assertIn("feasibility:", ZipFile(Path(self.storage.name) / ppt.path).read("ppt/slides/slide3.xml").decode())
        self.assertEqual(self.other_client.get(f"/api/product/tasks/{task_id}/outputs/").status_code, 404)
        current = DocumentTask.objects.get(pk=task_id)
        from portal.product_service import append_revision
        input_revision = current.revisions.get(kind="input", version=current.input_version)
        later = append_revision(current, "input", {**input_revision.payload, "requirements": "来源已更改"}, actor=self.owner)
        current.input_version = later.version
        current.save(update_fields=["input_version"])
        self.assertEqual(self.owner_client.get(f"/api/product/outputs/{ppt.pk}/download/").status_code, 409)
        self.assertTrue((Path(self.storage.name) / ppt.path).is_file())
