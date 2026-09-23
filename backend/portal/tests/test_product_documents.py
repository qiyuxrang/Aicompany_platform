import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from django.test import SimpleTestCase, override_settings

from portal.product_documents import DocumentError, content_document, frozen_pack, render_candidate, render_draft, render_report_draft


class ProductDocumentTests(SimpleTestCase):
    def setUp(self):
        self.task = SimpleNamespace(pk=uuid.uuid4(), title="合成隔离技术方案")
        self.input_revision = SimpleNamespace(pk=uuid.uuid4(), sha256="1" * 64, payload={
            "requirements": "保持设备数量", "conditions": ["仅用隔离合成资料"],
            "items": [{"row_id": "r1", "name": "测试设备", "quantity": "2", "unit": "台"}], "sources": [], "issues": []})
        self.blueprint = SimpleNamespace(payload={"template_version": "frozen-original-v1", "missing": [], "conflicts": []})
        self.chapter = SimpleNamespace(sha256="2" * 64, payload={"chapter_id": "c1", "title": "项目概述", "source_ids": ["r1"], "paragraphs": ["本合成样例仅使用2台测试设备，不用于实际工程。"]})

    def test_frozen_assets_have_exact_hashes(self):
        manifest = frozen_pack()
        self.assertFalse(manifest["company_samples_copied"])
        self.assertEqual(manifest["formal_business_confirmation"], "blocked")

    def test_feasibility_is_separate_draft_and_uses_its_own_template(self):
        content = content_document(self.task, self.input_revision, self.blueprint, [self.chapter], family="feasibility")
        self.assertEqual(content["family"], "feasibility")
        self.assertEqual(content["metadata"]["status"], "draft")
        with tempfile.TemporaryDirectory() as directory, override_settings(PRODUCT_STORAGE_ROOT=directory):
            artifact = render_report_draft(self.task, self.input_revision, self.blueprint, [self.chapter], "feasibility")
            self.assertNotEqual(artifact["template_hash"], frozen_pack()["files"][0]["sha256"])
            with ZipFile(Path(directory) / artifact["path"]) as archive:
                self.assertIn("待核草稿", archive.read("word/document.xml").decode())

    def test_invalid_family_fails_closed(self):
        with self.assertRaises(DocumentError):
            content_document(self.task, self.input_revision, self.blueprint, [self.chapter], family="../escape")

    def test_content_has_draft_notice_and_preserves_input_quantity(self):
        content = content_document(self.task, self.input_revision, self.blueprint, [self.chapter])
        self.assertEqual(content["metadata"]["status"], "draft")
        self.assertIn("待核草稿", content["blocks"][0]["text"])
        self.assertEqual(next(block for block in content["blocks"] if block["type"] == "table")["rows"][0][2], "2")

    def test_missing_runtime_is_safe_and_no_external_call(self):
        with override_settings(PRODUCT_DOCUMENT_PYTHON="Z:/nonexistent-runtime/python.exe"):
            with self.assertRaises(DocumentError) as error:
                render_draft(self.task, self.input_revision, self.blueprint, [self.chapter])
        self.assertEqual(error.exception.code, "template_unavailable")

    def test_candidate_requires_explicit_matching_template_approval(self):
        with override_settings(PRODUCT_TEMPLATE_APPROVAL=None):
            with self.assertRaises(DocumentError) as error:
                render_candidate(self.task, self.input_revision, self.blueprint, [self.chapter])
        self.assertEqual(error.exception.code, "template_approval_required")
        with override_settings(PRODUCT_TEMPLATE_APPROVAL={
            "template_hash": "0" * 64, "approval_ref": "TEST-APPROVAL", "organization": "合成测试单位",
        }):
            with self.assertRaises(DocumentError) as error:
                render_candidate(self.task, self.input_revision, self.blueprint, [self.chapter])
        self.assertEqual(error.exception.code, "template_approval_required")

    def test_real_docx_is_private_immutable_draft_and_structurally_valid(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(PRODUCT_STORAGE_ROOT=directory):
            result = render_draft(self.task, self.input_revision, self.blueprint, [self.chapter])
            target = Path(directory) / result["path"]
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), result["sha256"])
            with ZipFile(target) as archive:
                document = archive.read("word/document.xml").decode()
                self.assertIn("待核草稿", document)
                self.assertIn("测试设备", document)
                self.assertIn("2台", document)
            report = json.loads(target.with_suffix(".quality.json").read_text(encoding="utf-8"))
            self.assertTrue(report["structural_pass"])
            self.assertFalse(result["render_evidence"]["verified"])
            before = target.read_bytes()
            second = render_draft(self.task, self.input_revision, self.blueprint, [self.chapter])
            self.assertNotEqual(result["path"], second["path"])
            self.assertEqual(target.read_bytes(), before)

    def test_real_candidate_uses_approved_identity_without_draft_marker(self):
        template_hash = next(entry["sha256"] for entry in frozen_pack()["files"] if entry["path"].endswith("template.docx"))
        approval = {"template_hash": template_hash, "approval_ref": "SYNTHETIC-APPROVAL-1", "organization": "合成测试单位"}
        with tempfile.TemporaryDirectory() as directory, override_settings(
                PRODUCT_STORAGE_ROOT=directory, PRODUCT_TEMPLATE_APPROVAL=approval):
            result = render_candidate(self.task, self.input_revision, self.blueprint, [self.chapter])
            target = Path(directory) / result["path"]
            self.assertEqual(target.name, "candidate.docx")
            self.assertEqual(result["render_evidence"]["status"], "candidate_unverified")
            self.assertFalse(result["render_evidence"]["verified"])
            self.assertEqual(result["render_evidence"]["template_approval"], approval)
            canonical = json.dumps(approval, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            self.assertEqual(result["render_evidence"]["template_approval_hash"], hashlib.sha256(canonical.encode()).hexdigest())
            with ZipFile(target) as archive:
                document = archive.read("word/document.xml").decode()
            self.assertIn("技术方案", document)
            self.assertIn("合成测试单位", document)
            self.assertNotIn("待核草稿", document)
            self.assertNotIn("编制单位待确认", document)
            self.assertNotIn("待产品负责人批准", document)
            self.assertNotIn("待确认事项", document)

    def test_candidate_rejects_unresolved_business_content(self):
        template_hash = next(entry["sha256"] for entry in frozen_pack()["files"] if entry["path"].endswith("template.docx"))
        approval = {"template_hash": template_hash, "approval_ref": "SYNTHETIC-APPROVAL-1", "organization": "合成测试单位"}
        self.blueprint.payload["missing"] = ["缺少网络边界"]
        with override_settings(PRODUCT_TEMPLATE_APPROVAL=approval):
            with self.assertRaises(DocumentError) as error:
                render_candidate(self.task, self.input_revision, self.blueprint, [self.chapter])
        self.assertEqual(error.exception.code, "candidate_content_unresolved")
