from pathlib import Path
from urllib.parse import unquote
from zipfile import ZipFile

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

    def test_worker_requires_two_content_approvals_before_editable_presentation(self):
        task = self.create_task()
        saved = self.save_blueprint(task)
        current = self.approve_blueprint(saved)["task"]
        task_id = current["id"]
        for family, prose in (("technical-solution", "合成技术路线经过人工复核。"),
                              ("feasibility", "合成可研仅列出备选路线与缺项。")):
            endpoint = "chapters/" if family == "technical-solution" else "report-chapters/"
            response = self.owner_client.post(f"/api/product/tasks/{task_id}/{endpoint}",
                json_body(expected_version=current["version"], family=family, chapter_id="overview", title="项目概述", paragraphs=[prose], source_ids=["1"]),
                content_type="application/json")
            self.assertIn(response.status_code, (200, 201), response.content)
            current = response.json().get("task", response.json())
        queued = self.owner_client.post(f"/api/product/tasks/{task_id}/queue/",
            json_body(expected_version=current["version"], action="three_drafts"), content_type="application/json")
        self.assertEqual(queued.status_code, 200, queued.content)
        self.assertTrue(run_once())

        current = self.owner_client.get(f"/api/product/tasks/{task_id}/").json()
        outputs = self.owner_client.get(f"/api/product/tasks/{task_id}/outputs/").json()["outputs"]
        self.assertEqual({item["family"] for item in outputs}, {"technical-solution", "feasibility"})
        self.assertEqual({item["family"] for item in current["reports"]}, {"technical-solution", "feasibility"})
        self.assertTrue(all(report["current"] for report in current["reports"]))
        self.assertTrue(all(not report["approved"] for report in current["reports"]))
        blocked = self.owner_client.post(f"/api/product/tasks/{task_id}/queue/",
            json_body(expected_version=current["version"], action="presentation"), content_type="application/json")
        self.assertEqual(blocked.status_code, 409, blocked.content)
        self.assertEqual(blocked.json()["code"], "report_approval_required")

        for report in current["reports"]:
            approved = self.reviewer_client.post(f"/api/product/tasks/{task_id}/decisions/",
                json_body(expected_version=current["version"], target="report", target_id=report["id"],
                          sha256=report["sha256"], decision="approve", comment=f"批准 {report['family']} 结构化内容"),
                content_type="application/json")
            self.assertEqual(approved.status_code, 201, approved.content)
            current = approved.json()["task"]
        self.assertTrue(all(report["approved"] for report in current["reports"]))
        self.assertIn("queue_presentation", self.owner_client.get(f"/api/product/tasks/{task_id}/").json()["actions"])

        queued = self.owner_client.post(f"/api/product/tasks/{task_id}/queue/",
            json_body(expected_version=current["version"], action="presentation"), content_type="application/json")
        self.assertEqual(queued.status_code, 200, queued.content)
        self.assertTrue(run_once())
        result = self.owner_client.get(f"/api/product/tasks/{task_id}/outputs/")
        self.assertEqual(result.status_code, 200)
        outputs = result.json()["outputs"]
        self.assertEqual({item["family"] for item in outputs}, {"technical-solution", "feasibility", "presentation"})
        self.assertTrue(all(item["current"] and item["draft"] for item in outputs))
        self.assertTrue(all(item["content_approved"] for item in outputs if item["family"] != "presentation"))
        ppt = DocumentArtifact.objects.get(task_id=task_id, family="presentation")
        self.assertTrue(all(source["approval_id"] for source in ppt.render_evidence["source_versions"]))
        with ZipFile(Path(self.storage.name) / ppt.path) as archive:
            slide = archive.read("ppt/slides/slide2.xml").decode()
            self.assertIn("合成技术路线", slide)
            self.assertIn("technical-solution:", slide)
            self.assertIn("feasibility:", archive.read("ppt/slides/slide3.xml").decode())
        self.assertEqual(ppt.render_evidence["engine"], "python-pptx-simple-draft")
        self.assertEqual(ppt.render_evidence["engine_version"], "v1")
        self.assertTrue(ppt.render_evidence["block_refs"])
        self.assertEqual(self.other_client.get(f"/api/product/tasks/{task_id}/outputs/").status_code, 404)
        current_record = DocumentTask.objects.get(pk=task_id)
        from portal.product_service import append_revision
        technical_chapter = current_record.revisions.filter(kind="chapter", family="technical-solution").order_by("-version").first()
        append_revision(current_record, "chapter", {**technical_chapter.payload, "paragraphs": ["技术方案章节已修改。"]},
                        input_hash=technical_chapter.input_hash, blueprint_hash=technical_chapter.blueprint_hash,
                        actor=self.owner, family="technical-solution", reason="test_stale_chapter")
        chapter_outputs = {item["family"]: item for item in self.owner_client.get(f"/api/product/tasks/{task_id}/outputs/").json()["outputs"]}
        self.assertFalse(chapter_outputs["technical-solution"]["current"])
        self.assertTrue(chapter_outputs["technical-solution"]["stale"])
        self.assertTrue(chapter_outputs["feasibility"]["current"])
        self.assertFalse(chapter_outputs["presentation"]["current"])
        technical_artifact = DocumentArtifact.objects.filter(task_id=task_id, family="technical-solution").order_by("-version").first()
        technical_url = f"/api/product/artifacts/{technical_artifact.pk}/download/"
        self.assertEqual(self.owner_client.get(technical_url).status_code, 409)
        technical_history = self.owner_client.get(technical_url + "?history=1")
        self.assertEqual(technical_history["X-Product-Artifact-Current"], "false")
        b"".join(technical_history.streaming_content)
        for closer in technical_history._resource_closers:
            closer()
        technical_history._resource_closers.clear()
        chapter_reports = {item["family"]: item for item in self.owner_client.get(f"/api/product/tasks/{task_id}/").json()["reports"]}
        self.assertFalse(chapter_reports["technical-solution"]["current"])
        self.assertTrue(chapter_reports["feasibility"]["current"])
        input_revision = current_record.revisions.get(kind="input", version=current_record.input_version)
        later = append_revision(current_record, "input", {**input_revision.payload, "requirements": "来源已更改"},
                                actor=self.owner, reason="test_stale_input")
        current_record.input_version = later.version
        current_record.save(update_fields=["input_version"])
        stale_task = self.owner_client.get(f"/api/product/tasks/{task_id}/").json()
        self.assertTrue(all(not report["current"] for report in stale_task["reports"]))
        history_timeline = self.owner_client.get(f"/api/product/tasks/{task_id}/history/").json()["timeline"]
        ppt_history = next(item for item in history_timeline if item["event"] == "artifact" and item["id"] == str(ppt.pk))
        self.assertFalse(ppt_history["current"])
        self.assertTrue(ppt_history["stale"])
        self.assertEqual(self.owner_client.get(f"/api/product/outputs/{ppt.pk}/download/").status_code, 409)
        self.assertEqual(self.owner_client.get(f"/api/product/outputs/{ppt.pk}/download/?history=yes").status_code, 409)
        self.assertEqual(self.other_client.get(f"/api/product/outputs/{ppt.pk}/download/?history=1").status_code, 404)
        history = self.owner_client.get(f"/api/product/outputs/{ppt.pk}/download/?history=1")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history["X-Product-Artifact-Current"], "false")
        self.assertIn("历史草稿-已过期", unquote(history["Content-Disposition"]))
        b"".join(history.streaming_content)
        for closer in history._resource_closers:
            closer()
        history._resource_closers.clear()
        self.assertTrue((Path(self.storage.name) / ppt.path).is_file())
