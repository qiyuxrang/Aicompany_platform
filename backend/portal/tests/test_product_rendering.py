import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from portal.product_documents import DocumentError
from portal.product_rendering import render_office


class ProductRenderingTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime.exe"
        self.runtime.write_bytes(b"runtime")
        self.relative = Path("task") / "artifacts" / "artifact.docx"
        self.source = self.root / self.relative
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"PK\x03\x04synthetic-docx")
        self.sha256 = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.settings = override_settings(
            PRODUCT_STORAGE_ROOT=self.root,
            PRODUCT_DOCUMENT_PYTHON=self.runtime,
            PRODUCT_OFFICE_RENDER_ENABLED=True,
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def _successful_process(self, command, **kwargs):
        output = Path(command[5])
        output.mkdir(parents=True)
        rendered_docx = output / "reviewed.docx"
        pdf = output / "document.pdf"
        page = output / "page-001.png"
        rendered_docx.write_bytes(b"PK\x03\x04office-updated-docx")
        pdf.write_bytes(b"%PDF-1.7\nsynthetic")
        page.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
        report = {
            "structural_pass": True,
            "rendered": True,
            "visually_reviewed": False,
            "input_sha256": self.sha256,
            "page_count": 1,
            "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            "rendered_docx_sha256": hashlib.sha256(rendered_docx.read_bytes()).hexdigest(),
        }
        (output / "render.json").write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="渲染完成", stderr="")

    def test_disabled_by_default(self):
        with override_settings(PRODUCT_OFFICE_RENDER_ENABLED=False), patch("portal.product_rendering.subprocess.run") as run:
            with self.assertRaises(DocumentError) as error:
                render_office(self.relative.as_posix(), self.sha256)
        self.assertEqual(error.exception.code, "office_render_disabled")
        run.assert_not_called()

    def test_rejects_external_path_and_hash_mismatch(self):
        with patch("portal.product_rendering.subprocess.run") as run:
            with self.assertRaises(DocumentError) as error:
                render_office(str(self.source.resolve()), self.sha256)
            self.assertEqual(error.exception.code, "invalid_artifact_path")
            with self.assertRaises(DocumentError) as error:
                render_office(self.relative.as_posix(), "0" * 64)
            self.assertEqual(error.exception.code, "artifact_hash_mismatch")
        run.assert_not_called()

    @patch("portal.product_rendering.subprocess.run")
    def test_rendered_evidence_is_private_hashed_and_not_verified(self, run):
        run.side_effect = self._successful_process
        before = self.source.read_bytes()
        with patch.dict(os.environ, {"HTTP_PROXY": "http://invalid", "API_TOKEN": "secret"}):
            evidence = render_office(self.relative.as_posix(), self.sha256)
        self.assertEqual(evidence["status"], "rendered")
        self.assertFalse(evidence["verified"])
        self.assertEqual(evidence["generation_sha256"], self.sha256)
        self.assertEqual(evidence["generation"]["sha256"], self.sha256)
        self.assertEqual(evidence["docx_sha256"], evidence["rendered_docx"]["sha256"])
        self.assertEqual(evidence["page_count"], 1)
        self.assertEqual(len(evidence["pages"]), 1)
        self.assertEqual(evidence["pages"][0]["page"], 1)
        self.assertTrue(evidence["pdf"]["path"].startswith("office-renders/"))
        self.assertTrue(evidence["rendered_docx"]["differs_from_input"])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertNotIn(str(self.root.resolve()), json.dumps(evidence, ensure_ascii=False))
        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(Path(command[4]), self.source.resolve())
        self.assertEqual(command[3], "word")
        self.assertEqual(options["encoding"], "utf-8")
        self.assertNotIn("HTTP_PROXY", options["env"])
        self.assertNotIn("API_TOKEN", options["env"])

    @patch("portal.product_rendering.subprocess.run")
    def test_timeout_is_classified_without_mutating_source(self, run):
        run.side_effect = subprocess.TimeoutExpired(["office"], 100, output="正在启动")
        before = self.source.read_bytes()
        with self.assertRaises(DocumentError) as error:
            render_office(self.relative.as_posix(), self.sha256)
        self.assertEqual(error.exception.code, "office_render_timeout")
        self.assertEqual(self.source.read_bytes(), before)

    @patch("portal.product_rendering.subprocess.run")
    def test_nonzero_office_failure_is_classified(self, run):
        def fail(command, **kwargs):
            output = Path(command[5])
            output.mkdir(parents=True)
            (output / "render.json").write_text(json.dumps({
                "structural_pass": False, "rendered": False, "unverified_items": ["RuntimeError: synthetic failure"],
            }), encoding="utf-8")
            return SimpleNamespace(returncode=2, stdout="", stderr="中文失败")
        run.side_effect = fail
        with self.assertRaises(DocumentError) as error:
            render_office(self.relative.as_posix(), self.sha256)
        self.assertEqual(error.exception.code, "office_render_failed")

    @patch("portal.product_rendering.subprocess.run")
    def test_code_zero_with_empty_pdf_is_not_success(self, run):
        def empty_pdf(command, **kwargs):
            result = self._successful_process(command, **kwargs)
            (Path(command[5]) / "document.pdf").write_bytes(b"")
            return result
        run.side_effect = empty_pdf
        with self.assertRaises(DocumentError) as error:
            render_office(self.relative.as_posix(), self.sha256)
        self.assertEqual(error.exception.code, "office_render_invalid_output")

    @patch("portal.product_rendering.subprocess.run")
    def test_source_mutation_is_rejected(self, run):
        def mutate(command, **kwargs):
            Path(command[4]).write_bytes(b"PK\x03\x04mutated")
            return SimpleNamespace(returncode=2, stdout="", stderr="")
        run.side_effect = mutate
        with self.assertRaises(DocumentError) as error:
            render_office(self.relative.as_posix(), self.sha256)
        self.assertEqual(error.exception.code, "artifact_mutated")
