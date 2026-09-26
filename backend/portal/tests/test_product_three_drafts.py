import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote
from zipfile import ZipFile

from django.test import override_settings

from portal.product_models import DocumentApproval, DocumentArtifact, DocumentTask
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

    @override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True)
    @patch("portal.product_presentation.render_presentation_draft")
    @patch("portal.product_three_drafts.render_report_draft")
    @patch("portal.product_worker.generate_for_use")
    def test_owner_blueprint_confirmation_generates_three_outputs_without_report_approval(self, model, render_report, render_presentation):
        responses = []
        for family in ("technical-solution", "feasibility"):
            responses.extend([
                json.dumps({"chapter_id": "overview", "title": "项目概述",
                            "paragraphs": [f"{family} 代表性正文。"], "source_ids": ["1"]}),
                json.dumps({"passed": True, "issues": []}),
            ])
        model.side_effect = [{"content": value, "prompt_tokens": 10, "completion_tokens": 20} for value in responses]

        def report_result(task, input_revision, blueprint, chapters, family):
            path = Path(self.storage.name) / str(task.pk) / f"{family}.docx"
            path.parent.mkdir(parents=True, exist_ok=True)
            content = b"PK-report-" + family.encode()
            path.write_bytes(content)
            import hashlib
            return {"path": path.relative_to(self.storage.name).as_posix(),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "template_hash": "b" * 64, "render_evidence": {"status": "draft_unverified"}}

        def presentation_result(task, pair):
            path = Path(self.storage.name) / str(task.pk) / "presentation.pptx"
            with ZipFile(path, "w") as archive:
                archive.writestr("ppt/slides/slide2.xml", "technical-solution: representative")
                archive.writestr("ppt/slides/slide3.xml", "feasibility: representative")
            import hashlib
            return {"path": path.relative_to(self.storage.name).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "template_hash": "d" * 64,
                    "render_evidence": {"status": "draft_unverified", "engine": "python-pptx-simple-draft",
                                        "engine_version": "v1", "block_refs": [block["ref"] for block in pair["blocks"]],
                                        "source_versions": pair["sources"], "pair_hash": pair["sha256"]}}

        render_report.side_effect = report_result
        render_presentation.side_effect = presentation_result
        task = self.create_task(reviewer=False)
        current = self.save_blueprint(task)
        approved = self.approve_blueprint(current)["task"]
        task_id = approved["id"]
        self.assertEqual(approved["pending_action"], "generate_outputs")
        self.assertTrue(run_once())

        current = self.owner_client.get(f"/api/product/tasks/{task_id}/").json()
        self.assertEqual(current["state"], "COMPLETED", current.get("error_code"))
        self.assertEqual({item["family"] for item in current["reports"]}, {"technical-solution", "feasibility"})
        self.assertTrue(all(report["current"] for report in current["reports"]))
        self.assertTrue(all(not report["approved"] for report in current["reports"]))
        result = self.owner_client.get(f"/api/product/tasks/{task_id}/outputs/")
        self.assertEqual(result.status_code, 200)
        outputs = result.json()["outputs"]
        self.assertFalse(DocumentApproval.objects.filter(task_id=task_id, revision__kind="report").exists())
        record = DocumentTask.objects.get(pk=task_id)
        self.assertEqual(record.state, "COMPLETED", record.error_code)
        self.assertEqual(record.pending_action, "")
        self.assertEqual({item["family"] for item in outputs}, {"technical-solution", "feasibility", "presentation"})
        self.assertTrue(all(item["current"] and item["draft"] for item in outputs))
        self.assertTrue(all(item["content_approved"] is False for item in outputs if item["family"] != "presentation"))
        ppt = DocumentArtifact.objects.get(task_id=task_id, family="presentation")
        self.assertTrue(all(source["id"] and source["sha256"] for source in ppt.render_evidence["source_versions"]))
        self.assertTrue(all("approval_id" not in source for source in ppt.render_evidence["source_versions"]))
        with ZipFile(Path(self.storage.name) / ppt.path) as archive:
            slide = archive.read("ppt/slides/slide2.xml").decode()
            self.assertIn("technical-solution", slide)
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
