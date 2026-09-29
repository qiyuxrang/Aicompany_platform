import json
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote
from zipfile import ZipFile

from django.test import override_settings

from portal.product_models import DocumentApproval, DocumentArtifact, DocumentTask
from portal.product_presentation import render_presentation_draft
from portal.product_worker import _review_payload, run_once
from .test_product_pair import PairDraftTests
from .base import PortalTestCase, json_body


class ThreeDraftFlowTests(PortalTestCase):
    setUp = PairDraftTests.setUp
    input_payload = PairDraftTests.input_payload
    create_task = PairDraftTests.create_task
    blueprint_payload = PairDraftTests.blueprint_payload
    save_blueprint = PairDraftTests.save_blueprint
    approve_blueprint = PairDraftTests.approve_blueprint

    def test_business_technology_presentation_runtime_is_editable_and_source_bound(self):
        runtime = Path(__file__).resolve().parents[3] / ".runtime" / "product-documents-python" / "Scripts" / "python.exe"
        if not runtime.is_file():
            self.skipTest("isolated document runtime is not installed")
        pair = {
            "approval_inherited": False,
            "sha256": "c" * 64,
            "sources": [
                {"family": "technical-solution", "id": "1", "version": 1, "sha256": "a" * 64},
                {"family": "feasibility", "id": "2", "version": 1, "sha256": "b" * 64},
            ],
            "blocks": [
                {"ref": "technical-solution:H1", "type": "heading", "text": "总体技术架构", "source_ids": ["S1"]},
                {"ref": "technical-solution:P1", "type": "paragraph", "text": "平台接入、审计分析和运维处置形成可追溯流程。", "source_ids": ["S1"]},
                {"ref": "feasibility:H1", "type": "heading", "text": "实施可行性", "source_ids": ["S2"]},
                {"ref": "feasibility:P1", "type": "paragraph", "text": "项目按准备、并行验证、迁移和验收推进，最终结论待人工确认。", "source_ids": ["S2"]},
            ],
            "pending": [{"text": "最终实施窗口待确认。", "refs": ["technical-solution:P1", "feasibility:P1"]}],
        }
        with tempfile.TemporaryDirectory() as storage:
            with override_settings(PRODUCT_DOCUMENT_PYTHON=runtime, PRODUCT_STORAGE_ROOT=Path(storage),
                                   PRODUCT_DOCUMENT_RENDER_TIMEOUT_SECONDS=60):
                rendered = render_presentation_draft(SimpleNamespace(pk=uuid.uuid4(), title="合成园区安全项目"), pair)
            target = Path(storage) / rendered["path"]
            evidence = rendered["render_evidence"]
            self.assertEqual(evidence["engine"], "business-tech-pptx")
            self.assertEqual(evidence["engine_version"], "v4")
            self.assertEqual(evidence["quality_gate"]["status"], "pass")
            self.assertEqual(evidence["slides"], 5)
            self.assertGreaterEqual(evidence["diagram_slides"], 4)
            self.assertLessEqual(evidence["dominant_layout_ratio"], 0.25)
            self.assertEqual(evidence["native_charts"], 1)
            self.assertGreaterEqual(evidence["editable_data_visuals"], 4)
            self.assertGreaterEqual(evidence["source_block_coverage"]["total"],
                                    evidence["source_block_coverage"]["mapped"])
            self.assertEqual(evidence["source_mapping_scope"], "selected_summary_blocks")
            self.assertEqual(evidence["content_review"], "not_run")
            with ZipFile(target) as archive:
                names = archive.namelist()
                self.assertTrue(any(name.startswith("ppt/media/") for name in names))
                self.assertTrue(any(name.startswith("ppt/embeddings/") for name in names))
                slides = "".join(archive.read(name).decode("utf-8") for name in names
                                 if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
                self.assertIn("<p:sp>", slides)
                self.assertIn("技术方案总体架构", slides)
                self.assertIn("可行性判断与实施控制", slides)
                self.assertNotIn("待人工审核草稿", slides)

    def test_large_report_review_is_bounded_and_marked_sampled(self):
        created = self.create_task(reviewer=False)
        self.save_blueprint(created)
        task = DocumentTask.objects.get(pk=created["id"])
        input_revision = task.revisions.get(kind="input", version=task.input_version)
        blueprint = task.revisions.get(kind="blueprint", version=task.blueprint_version)
        chapter = SimpleNamespace(payload={
            "chapter_id": "overview", "title": "项目概述",
            "paragraphs": ["完整报告正文。" * 5000], "source_ids": ["1"],
        })

        payload, scope = _review_payload(
            task, input_revision, blueprint, [chapter], "feasibility"
        )

        self.assertEqual(scope, "sampled")
        self.assertEqual(payload["review_scope"], "sampled; full-document human review required")
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False)), 14500)
        self.assertGreater(payload["chapters"][0]["actual_characters"], 30000)

    @override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True)
    @patch("portal.product_presentation.render_presentation_draft")
    @patch("portal.product_three_drafts.render_report_draft")
    @patch("portal.product_worker.generate_for_use")
    def test_owner_blueprint_confirmation_generates_three_outputs_without_report_approval(self, model, render_report, render_presentation):
        responses = []
        for family in ("technical-solution", "feasibility"):
            responses.extend([
                json.dumps({"chapter_id": "overview", "title": "项目概述",
                            "paragraphs": [f"第{index}段" + "正文" * 5000 for index in range(5 if family == "technical-solution" else 7)], "source_ids": ["1"]}),
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
                    "render_evidence": {"status": "draft_unverified", "engine": "business-tech-pptx",
                                        "engine_version": "v3", "block_refs": [block["ref"] for block in pair["blocks"]],
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
        technical = DocumentArtifact.objects.get(task_id=task_id, family="technical-solution")
        technical_download = self.owner_client.get(f"/api/product/outputs/{technical.pk}/download/")
        self.assertEqual(technical_download.status_code, 200)
        self.assertEqual(b"".join(technical_download.streaming_content), b"PK-report-technical-solution")
        ppt = DocumentArtifact.objects.get(task_id=task_id, family="presentation")
        self.assertTrue(all(source["id"] and source["sha256"] for source in ppt.render_evidence["source_versions"]))
        self.assertTrue(all("approval_id" not in source for source in ppt.render_evidence["source_versions"]))
        with ZipFile(Path(self.storage.name) / ppt.path) as archive:
            slide = archive.read("ppt/slides/slide2.xml").decode()
            self.assertIn("technical-solution", slide)
            self.assertIn("technical-solution:", slide)
            self.assertIn("feasibility:", archive.read("ppt/slides/slide3.xml").decode())
        self.assertEqual(ppt.render_evidence["engine"], "business-tech-pptx")
        self.assertEqual(ppt.render_evidence["engine_version"], "v3")
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
