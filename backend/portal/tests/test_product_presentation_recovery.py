import json
import hashlib
import subprocess
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from portal.product_documents import DocumentError
from portal.product_presentation import _valid_manifest, render_presentation_draft
from portal.product_worker import _failure_diagnostic
from portal.product_models import DocumentArtifact, DocumentTask
from portal.product_worker import run_once

from .base import PortalTestCase, json_body
from . import test_product_api as product_api_tests


def source_pair(paragraph_count=1):
    blocks = []
    for family in ("technical-solution", "feasibility"):
        blocks.append({"ref": family + ":H1", "type": "heading", "level": 1,
                       "text": "合成验收项目", "source_ids": ["S1"]})
        blocks.extend({"ref": f"{family}:P{index}", "type": "paragraph",
                       "text": "设备与需求以输入清单为准，实施条件需要人工核实。", "source_ids": ["S1"]}
                      for index in range(paragraph_count))
    return {"approval_inherited": False, "sha256": "c" * 64,
            "sources": [{"family": family, "id": str(index), "version": 1, "sha256": "a" * 64}
                        for index, family in enumerate(("technical-solution", "feasibility"))],
            "blocks": blocks, "pending": []}


class PresentationDiagnosticTests(SimpleTestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.runtime = Path(self.storage.name) / "runtime.exe"
        self.runtime.touch()
        self.override = override_settings(PRODUCT_STORAGE_ROOT=Path(self.storage.name),
                                          PRODUCT_DOCUMENT_PYTHON=self.runtime)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.task = SimpleNamespace(pk=uuid.uuid4(), title="合成项目")

    def assert_failure(self, run, reason, stage):
        with patch("portal.product_presentation.subprocess.run", run):
            with self.assertRaises(DocumentError) as caught:
                render_presentation_draft(self.task, source_pair())
        self.assertEqual(caught.exception.code, "presentation_unavailable")
        diagnostic = _failure_diagnostic(caught.exception)
        self.assertEqual(diagnostic["reason"], reason)
        self.assertEqual(diagnostic["stage"], stage)
        self.assertNotIn("private-token", json.dumps(diagnostic))
        return diagnostic

    def test_timeout_and_launch_errors_do_not_retain_subprocess_content(self):
        for error, reason in [(subprocess.TimeoutExpired("private-token", 1, stderr=b"private-token"), "timeout"),
                              (OSError("private-token"), "launch_failed")]:
            with self.subTest(reason=reason):
                def fail(*args, **kwargs):
                    raise error
                self.assert_failure(fail, reason, "presentation_render")

    def test_known_failures_are_classified_without_saving_stderr(self):
        for message, reason in [("invalid slide source", "invalid_source"),
                                ("presentation source limit exceeded", "source_limit"),
                                ("presentation quality gate failed", "quality_failed"),
                                ("private-token", "process_failed")]:
            with self.subTest(reason=reason):
                def fail(*args, **kwargs):
                    return SimpleNamespace(returncode=1, stderr=("private-token\nValueError: " + message).encode())
                diagnostic = self.assert_failure(fail, reason, "presentation_render")
                self.assertEqual(diagnostic["returncode"], 1)

    def test_missing_and_invalid_manifest_are_controlled_errors(self):
        for payload, reason in [(None, "missing_output"), ([], "invalid_manifest"), ({}, "invalid_manifest")]:
            with self.subTest(reason=reason, payload=payload):
                def finish(command, **kwargs):
                    if payload is not None:
                        target = Path(command[-1])
                        target.write_bytes(b"fixture")
                        target.with_suffix(".manifest.json").write_text(json.dumps(payload), encoding="utf-8")
                    return SimpleNamespace(returncode=0, stderr=b"")
                self.assert_failure(finish, reason, "presentation_manifest")

    def test_runtime_missing_has_safe_diagnostic(self):
        self.runtime.unlink()
        with self.assertRaises(DocumentError) as caught:
            render_presentation_draft(self.task, source_pair())
        self.assertEqual(_failure_diagnostic(caught.exception)["reason"], "runtime_missing")

    def test_untrusted_diagnostic_fields_are_not_retained(self):
        for details in [{"stage": "private-token", "reason": "private-token", "stderr": "private-token"},
                        {"stage": [], "reason": {}, "returncode": True}]:
            with self.subTest(details=details):
                diagnostic = _failure_diagnostic(DocumentError("presentation_unavailable", diagnostic=details))
                self.assertEqual(set(diagnostic), {"exception_type", "frames", "updated_at"})

    def test_manifest_types_and_source_accounting_fail_closed(self):
        pair = source_pair()
        refs = [block["ref"] for block in pair["blocks"]]
        manifest = {"engine": "business-tech-pptx", "engine_version": "v3", "design_profile": "dark",
                    "fact_boundary": "source-bound", "slides": 5, "diagram_slides": 4, "native_charts": 1,
                    "editable_data_visuals": 4, "visual_asset_count": 1, "source_blocks": 4,
                    "source_blocks_mapped": 1, "mapped_source_refs": refs[:1], "omitted_source_refs": refs[1:],
                    "missing_source_refs": [], "quality_gate": {"status": "pass"},
                    "layout_inventory": {"cover": 1, "summary": 4}, "dominant_layout_ratio": 0.8,
                    "source_mapping_scope": "selected_summary_blocks", "content_review": "not_run"}
        self.assertTrue(_valid_manifest(manifest, pair))
        for key, value in [("missing_source_refs", False), ("slides", "5"), ("source_blocks_mapped", 4),
                           ("source_blocks", True), ("dominant_layout_ratio", float("nan")),
                           ("omitted_source_refs", []), ("mapped_source_refs", ["unknown:P1"]),
                           ("content_review", "passed"), ("layout_inventory", {"cover": 3})]:
            with self.subTest(key=key, value=value):
                self.assertFalse(_valid_manifest({**manifest, key: value}, pair))


class PresentationRuntimeRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.runtime = settings.BASE_DIR / ".runtime/product-documents-python/Scripts/python.exe"
        if not self.runtime.is_file():
            self.skipTest("isolated document runtime is not installed")
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.override = override_settings(PRODUCT_STORAGE_ROOT=Path(self.storage.name),
                                          PRODUCT_DOCUMENT_PYTHON=self.runtime,
                                          PRODUCT_DOCUMENT_RENDER_TIMEOUT_SECONDS=60)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_small_long_and_pending_report_pairs_render_with_source_notes(self):
        for paragraphs, pending in [(1, []), (247, []), (20, [{"text": "实施窗口待确认", "refs": ["technical-solution:P0"]}])]:
            with self.subTest(paragraphs=paragraphs, pending=bool(pending)):
                pair = source_pair(paragraphs)
                pair["pending"] = pending
                result = render_presentation_draft(SimpleNamespace(pk=uuid.uuid4(), title="合成验收项目"), pair)
                evidence = result["render_evidence"]
                self.assertEqual(evidence["quality_gate"]["status"], "pass")
                self.assertEqual(evidence["source_block_coverage"]["total"], len(pair["blocks"]))
                self.assertLessEqual(evidence["source_block_coverage"]["mapped"], len(pair["blocks"]))
                self.assertEqual(evidence["source_mapping_scope"], "selected_summary_blocks")
                self.assertEqual(evidence["content_review"], "not_run")
                if paragraphs == 247:
                    self.assertLess(evidence["source_block_coverage"]["mapped"], len(pair["blocks"]))
                self.assertEqual(evidence["status"], "draft_unverified")
                with ZipFile(Path(self.storage.name) / result["path"]) as archive:
                    notes = "".join(archive.read(name).decode("utf-8") for name in archive.namelist()
                                    if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml"))
                    self.assertIn("technical-solution:P0", notes)
                    self.assertIn("feasibility:P0", notes)

    def test_oversized_source_is_rejected_without_unbounded_rendering(self):
        pair = source_pair(5000)
        with self.assertRaises(DocumentError) as caught:
            render_presentation_draft(SimpleNamespace(pk=uuid.uuid4(), title="合成验收项目"), pair)
        self.assertEqual(_failure_diagnostic(caught.exception)["reason"], "source_limit")


class PresentationRetryTests(PortalTestCase):
    setUp = product_api_tests.ProductApiTests.setUp
    input_payload = product_api_tests.ProductApiTests.input_payload
    create_task = product_api_tests.ProductApiTests.create_task
    blueprint_payload = product_api_tests.ProductApiTests.blueprint_payload
    save_blueprint = product_api_tests.ProductApiTests.save_blueprint
    approve_blueprint = product_api_tests.ProductApiTests.approve_blueprint

    @override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True)
    @patch("portal.product_presentation.render_presentation_draft")
    @patch("portal.product_three_drafts.render_report_draft")
    @patch("portal.product_worker.generate_for_use")
    def test_ppt_failure_retry_preserves_and_reuses_both_word_artifacts(self, model, render_report, render_presentation):
        responses = []
        for count in (5, 7):
            responses.extend([{"chapter_id": "overview", "title": "项目概述", "source_ids": ["1"],
                               "paragraphs": [f"第{index}段" + "合成正文" * 2500 for index in range(count)]},
                              {"passed": True, "issues": []}])
        responses.extend([{"passed": True, "issues": []}] * 2)
        model.side_effect = [{"content": json.dumps(value), "prompt_tokens": 10, "completion_tokens": 20}
                             for value in responses]

        def report(task, input_revision, blueprint, chapters, family):
            target = Path(self.storage.name) / str(task.pk) / f"{family}.docx"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"synthetic-report-" + family.encode())
            return {"path": target.relative_to(self.storage.name).as_posix(),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "template_hash": "a" * 64,
                    "render_evidence": {"status": "draft_unverified"}}

        def presentation(task, pair):
            if render_presentation.call_count == 1:
                raise DocumentError("presentation_unavailable", diagnostic={"stage": "presentation_render", "reason": "source_limit"})
            target = Path(self.storage.name) / str(task.pk) / "presentation.pptx"
            target.write_bytes(b"synthetic-presentation")
            return {"path": target.relative_to(self.storage.name).as_posix(),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "template_hash": "b" * 64,
                    "render_evidence": {"status": "draft_unverified", "pair_hash": pair["sha256"], "source_versions": pair["sources"]}}

        render_report.side_effect = report
        render_presentation.side_effect = presentation
        task = self.approve_blueprint(self.save_blueprint(self.create_task(reviewer=False)))["task"]
        self.assertTrue(run_once())
        record = DocumentTask.objects.get(pk=task["id"])
        self.assertEqual(record.state, "FAILED")
        self.assertEqual(record.error_code, "presentation_unavailable")
        self.assertEqual(record.checkpoint["last_failure"]["reason"], "source_limit")
        saved = list(DocumentArtifact.objects.filter(task=record).values_list("pk", "sha256"))
        self.assertEqual(len(saved), 2)
        outputs = self.owner_client.get(f"/api/product/tasks/{record.pk}/outputs/").json()["outputs"]
        self.assertTrue(all(item["current"] for item in outputs))
        response = self.owner_client.post(f"/api/product/tasks/{record.pk}/retry/",
                                          json_body(expected_version=record.version), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(run_once())
        record.refresh_from_db()
        self.assertEqual(record.state, "COMPLETED", record.error_code)
        self.assertEqual(render_report.call_count, 2)
        self.assertEqual(model.call_count, 6)
        self.assertEqual(DocumentArtifact.objects.filter(task=record).count(), 3)
        self.assertEqual(set(DocumentArtifact.objects.filter(pk__in=[identifier for identifier, _ in saved])
                             .values_list("pk", "sha256")), set(saved))
