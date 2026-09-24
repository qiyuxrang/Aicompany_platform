import hashlib
import json
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from django.test import SimpleTestCase, override_settings

from portal.product_documents import DocumentError, content_document, frozen_pack, render_candidate, render_draft, render_report_draft


class ProductDocumentTests(SimpleTestCase):
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

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
        policy_entry = next(entry for entry in manifest["files"]
                            if entry["path"] == "assets/document-format-policy.json")
        policy = json.loads((Path(__file__).parents[1] / "product_assets" / "bj_docs" /
                             policy_entry["path"]).read_text(encoding="utf-8"))
        self.assertEqual(policy["status"], "approved_interim_baseline")
        self.assertEqual(policy["typography"]["body"], {
            "font": "宋体", "size_pt": 12, "line_spacing_pt": 18,
        })
        self.assertIn("exact_page_margins_and_gutter", policy["pending_formal_template_confirmation"])

    def test_both_word_families_apply_frozen_interim_format(self):
        def paragraph_text(paragraph):
            return "".join(node.text or "" for node in paragraph.iter(self.W + "t"))

        with tempfile.TemporaryDirectory() as directory, override_settings(PRODUCT_STORAGE_ROOT=directory):
            for family in ("technical-solution", "feasibility"):
                artifact = render_report_draft(
                    self.task, self.input_revision, self.blueprint, [self.chapter], family)
                target = Path(directory) / artifact["path"]
                with ZipFile(target) as archive:
                    document = ET.fromstring(archive.read("word/document.xml"))
                    sections = list(document.iter(self.W + "sectPr"))
                    self.assertTrue(sections)
                    for section in sections:
                        size = section.find(self.W + "pgSz")
                        grid = section.find(self.W + "docGrid")
                        self.assertEqual((size.get(self.W + "w"), size.get(self.W + "h")),
                                         ("11906", "16838"))
                        self.assertEqual(grid.get(self.W + "linePitch"), "360")

                    paragraphs = list(document.iter(self.W + "p"))
                    body = next(item for item in paragraphs if "待核草稿：" in paragraph_text(item))
                    body_spacing = body.find(f"{self.W}pPr/{self.W}spacing")
                    body_run = body.find(self.W + "r")
                    self.assertEqual((body_spacing.get(self.W + "line"),
                                      body_spacing.get(self.W + "lineRule")), ("360", "exact"))
                    self.assertEqual(body_run.find(f"{self.W}rPr/{self.W}sz").get(self.W + "val"), "24")
                    self.assertEqual(body_run.find(f"{self.W}rPr/{self.W}rFonts").get(self.W + "eastAsia"), "宋体")

                    heading = next(item for item in paragraphs if paragraph_text(item) == "1 项目概述")
                    heading_run = heading.find(self.W + "r")
                    self.assertEqual(heading.find(f"{self.W}pPr/{self.W}jc").get(self.W + "val"), "center")
                    self.assertIsNone(heading.find(f"{self.W}pPr/{self.W}pStyle"))
                    self.assertEqual(heading_run.find(f"{self.W}rPr/{self.W}sz").get(self.W + "val"), "32")
                    self.assertIsNotNone(heading_run.find(f"{self.W}rPr/{self.W}b"))
                    self.assertNotIn("第一章", archive.read("word/document.xml").decode())

                    active_headers = []
                    for name in archive.namelist():
                        if name.startswith("word/header") and name.endswith(".xml"):
                            header = ET.fromstring(archive.read(name))
                            value = "".join(node.text or "" for node in header.iter(self.W + "t"))
                            if value:
                                active_headers.append((header, value))
                    self.assertTrue(any(value == "西安工业大学毕业设计（论文）"
                                        for _, value in active_headers))
                    normalized = next(header for header, value in active_headers
                                      if value == "西安工业大学毕业设计（论文）")
                    self.assertEqual(normalized.find(f".//{self.W}bottom").get(self.W + "val"), "double")
                    relationships = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
                    relationship_targets = {item.get("Id"): item.get("Target") for item in relationships}
                    relationship_id = sections[-1].find(self.W + "headerReference").get(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                    linked_header = ET.fromstring(archive.read("word/" + relationship_targets[relationship_id]))
                    self.assertEqual("".join(node.text or "" for node in linked_header.iter(self.W + "t")),
                                     "西安工业大学毕业设计（论文）")
                    chapter_headers = []
                    for section in sections:
                        first = next((item for item in section.findall(self.W + "headerReference")
                                      if item.get(self.W + "type") == "first"), None)
                        if first is None:
                            continue
                        target = relationship_targets[first.get(
                            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")]
                        header = ET.fromstring(archive.read("word/" + target))
                        chapter_headers.append("".join(node.text or "" for node in header.iter(self.W + "t")))
                    self.assertIn("项目概述", chapter_headers)
                    self.assertIn("待确认事项", chapter_headers)

                    if family == "technical-solution":
                        self.assertGreaterEqual(len(sections), 2)
                        self.assertIsNone(sections[0].find(self.W + "headerReference"))
                        self.assertIsNotNone(sections[-1].find(self.W + "headerReference"))
                        self.assertTrue(any(paragraph_text(item).startswith("表 1.1 ")
                                            for item in paragraphs))

    def test_feasibility_is_separate_draft_and_uses_its_own_template(self):
        content = content_document(self.task, self.input_revision, self.blueprint, [self.chapter], family="feasibility")
        self.assertEqual(content["family"], "feasibility")
        self.assertEqual(content["metadata"]["status"], "draft")
        self.assertTrue(any("未提供经核实的成本与收益依据" in item["text"] for item in content["pending"]))
        with tempfile.TemporaryDirectory() as directory, override_settings(PRODUCT_STORAGE_ROOT=directory):
            artifact = render_report_draft(self.task, self.input_revision, self.blueprint, [self.chapter], "feasibility")
            self.assertNotEqual(artifact["template_hash"], frozen_pack()["files"][0]["sha256"])
            with ZipFile(Path(directory) / artifact["path"]) as archive:
                self.assertIn("待核草稿", archive.read("word/document.xml").decode())

    def test_three_output_task_gets_distinct_word_titles(self):
        self.task.title = "园区安全接入与集中审计三件套（代表性草稿）"
        technical = content_document(self.task, self.input_revision, self.blueprint, [self.chapter])
        feasibility = content_document(
            self.task, self.input_revision, self.blueprint, [self.chapter], family="feasibility")
        self.assertEqual(technical["metadata"]["title"], "园区安全接入与集中审计技术方案（代表性草稿）")
        self.assertEqual(feasibility["metadata"]["title"], "园区安全接入与集中审计可行性研究报告（代表性草稿）")

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
