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
from portal.product_diagrams import validate_document_requirements


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
        self.assertEqual(policy["status"], "approved_format_baseline_v4")
        self.assertEqual(policy["company_identity"]["logo_sha256"],
                         "9211d67a0a52e0445fb85e11c032eeec4ff6f3ad6870b8afac7a9d1a463f8a3e")
        root = Path(__file__).parents[1] / "product_assets" / "bj_docs"
        for relative in ("assets/company/company-logo.png", "assets/technical-solution/template.docx",
                         "assets/feasibility/template.docx", "assets/document-format-policy.json"):
            entry = next(item for item in manifest["files"] if item["path"] == relative)
            target = root / relative
            self.assertEqual(entry["bytes"], target.stat().st_size)
            self.assertEqual(entry["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(policy["typography"]["body"], {
            "font": "宋体", "size_pt": 12, "line_spacing_pt": 18,
        })
        self.assertEqual(policy["page"]["margins_mm"], {
            "top": 25.4, "bottom": 25.4, "left": 25.4, "right": 25.4, "gutter": 0,
        })
        self.assertNotIn("exact_page_margins_and_gutter", policy["pending_formal_template_confirmation"])

    def test_both_word_families_apply_frozen_interim_format(self):
        def paragraph_text(paragraph):
            return "".join(node.text or "" for node in paragraph.iter(self.W + "t"))

        with tempfile.TemporaryDirectory() as directory, override_settings(PRODUCT_STORAGE_ROOT=directory):
            second_chapter = SimpleNamespace(
                sha256="3" * 64,
                payload={"chapter_id": "c2", "title": "Additional chapter",
                         "source_ids": ["r1"], "paragraphs": ["Additional content."]},
            )
            for family in ("technical-solution", "feasibility"):
                artifact = render_report_draft(
                    self.task, self.input_revision, self.blueprint,
                    [self.chapter, second_chapter], family)
                target = Path(directory) / artifact["path"]
                with ZipFile(target) as archive:
                    document = ET.fromstring(archive.read("word/document.xml"))
                    sections = list(document.iter(self.W + "sectPr"))
                    self.assertTrue(sections)
                    for section in sections:
                        size = section.find(self.W + "pgSz")
                        margins = section.find(self.W + "pgMar")
                        grid = section.find(self.W + "docGrid")
                        self.assertEqual((size.get(self.W + "w"), size.get(self.W + "h")),
                                         ("11906", "16838"))
                        self.assertEqual(grid.get(self.W + "linePitch"), "360")
                        self.assertEqual(
                            tuple(margins.get(self.W + name) for name in ("top", "bottom", "left", "right", "gutter")),
                            ("1440", "1440", "1440", "1440", "0"),
                        )

                    paragraphs = list(document.iter(self.W + "p"))
                    body = next(item for item in paragraphs if "本合成样例仅使用2台测试设备" in paragraph_text(item))
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
                    self.assertIsNone(heading.find(f"{self.W}pPr/{self.W}pageBreakBefore"))
                    self.assertGreaterEqual(len(document.findall(
                        f".//{self.W}br[@{self.W}type='page']")), 1)
                    self.assertNotIn("第一章", archive.read("word/document.xml").decode())

                    headers = "".join(archive.read(name).decode("utf-8") for name in archive.namelist()
                                      if name.startswith("word/header") and name.endswith(".xml"))
                    footers = "".join(archive.read(name).decode("utf-8") for name in archive.namelist()
                                      if name.startswith("word/footer") and name.endswith(".xml"))
                    self.assertIn("技术方案" if family == "technical-solution" else "可行性研究报告", headers)
                    self.assertIn("PAGE", footers)
                    if family == "technical-solution":
                        # The input table follows the chapter owning it, so its
                        # chapter number may change when the approved blueprint
                        # gains or reorders chapters.  Keep the format contract
                        # strict without freezing a stale semantic position.
                        self.assertTrue(any(
                            paragraph_text(item).startswith("表 ")
                            and paragraph_text(item).endswith("输入设备清单")
                            for item in paragraphs
                        ))

    def test_word_cover_contains_only_logo_and_document_topic_without_text_header(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(PRODUCT_STORAGE_ROOT=directory):
            for family, expected_title in (("technical-solution", "合成隔离技术方案"),
                                           ("feasibility", "合成隔离技术方案（可行性研究报告）")):
                result = render_report_draft(self.task, self.input_revision, self.blueprint, [self.chapter], family)
                with ZipFile(Path(directory) / result["path"]) as archive:
                    document = archive.read("word/document.xml").decode("utf-8")
                    headers = "".join(archive.read(name).decode("utf-8") for name in archive.namelist()
                                      if name.startswith("word/header") and name.endswith(".xml"))
                    media = [name for name in archive.namelist() if name.startswith("word/media/")]
                self.assertIn(expected_title, document)
                self.assertGreaterEqual(len(media), 16 if family == "technical-solution" else 21)
                self.assertNotIn("西安工业大学毕业设计（论文）", headers)
                self.assertIn("技术方案" if family == "technical-solution" else "可行性研究报告", headers)
                self.assertNotIn("陕西省一二三数字信息技术有限公司", document)
                self.assertNotIn("建设单位", document)
                self.assertNotIn("编制单位", document)

    def test_feasibility_is_separate_draft_and_uses_its_own_template(self):
        content = content_document(self.task, self.input_revision, self.blueprint, [self.chapter], family="feasibility")
        self.assertEqual(content["family"], "feasibility")
        self.assertEqual(content["metadata"]["status"], "draft")
        self.assertTrue(any("未提供经核实的成本与收益依据" in item["text"] for item in content["pending"]))
        with tempfile.TemporaryDirectory() as directory, override_settings(PRODUCT_STORAGE_ROOT=directory):
            artifact = render_report_draft(self.task, self.input_revision, self.blueprint, [self.chapter], "feasibility")
            self.assertNotEqual(artifact["template_hash"], frozen_pack()["files"][0]["sha256"])
            with ZipFile(Path(directory) / artifact["path"]) as archive:
                self.assertNotIn("待核草稿", archive.read("word/document.xml").decode())

    def test_three_output_task_gets_distinct_word_titles(self):
        self.task.title = "园区安全接入与集中审计三件套（代表性草稿）"
        technical = content_document(self.task, self.input_revision, self.blueprint, [self.chapter])
        feasibility = content_document(
            self.task, self.input_revision, self.blueprint, [self.chapter], family="feasibility")
        self.assertEqual(technical["metadata"]["title"], "园区安全接入与集中审计技术方案")
        self.assertEqual(feasibility["metadata"]["title"], "园区安全接入与集中审计可行性研究报告")

    def test_reports_enforce_diagram_counts_h2_and_no_mermaid_source(self):
        for family, expected in (("technical-solution", 15), ("feasibility", 20)):
            content = content_document(self.task, self.input_revision, self.blueprint, [self.chapter], family=family)
            structure = validate_document_requirements(content)
            self.assertEqual(structure["figure_count"], expected)
            self.assertGreaterEqual(structure["heading2_count"], expected + 1)
            self.assertEqual(structure["toc_depth"], 2)
            serialized = json.dumps(content, ensure_ascii=False)
            self.assertNotIn("flowchart LR", serialized)
            self.assertNotIn("classDef", serialized)

    def test_diagrams_are_embedded_across_business_chapters(self):
        chapters = [
            SimpleNamespace(
                sha256=str(index) * 64,
                payload={
                    "chapter_id": f"c{index}",
                    "title": f"业务章节{index}",
                    "source_ids": ["r1"],
                    "paragraphs": [f"章节{index}的来源约束说明。"],
                },
            )
            for index in range(1, 6)
        ]
        for family, expected_per_chapter in (("technical-solution", 3), ("feasibility", 4)):
            content = content_document(
                self.task, self.input_revision, self.blueprint, chapters, family=family)
            self.assertFalse(any(block.get("id") == "DIAGRAMS_HEADING" for block in content["blocks"]))
            self.assertFalse(any(
                block.get("type") == "heading"
                and block.get("level") == 1
                and block.get("text") in {"架构与流程图解", "可行性分析图解"}
                for block in content["blocks"]
            ))
            counts = [0] * len(chapters)
            active_chapter = -1
            for block in content["blocks"]:
                if block.get("type") == "heading" and block.get("level") == 1:
                    if block.get("text", "").startswith("业务章节"):
                        active_chapter += 1
                    else:
                        active_chapter = len(chapters)
                elif block.get("type") == "figure" and active_chapter < len(chapters):
                    counts[active_chapter] += 1
            self.assertEqual(counts, [expected_per_chapter] * len(chapters))

    def test_invalid_family_fails_closed(self):
        with self.assertRaises(DocumentError):
            content_document(self.task, self.input_revision, self.blueprint, [self.chapter], family="../escape")

    def test_content_omits_draft_notice_and_preserves_input_quantity(self):
        content = content_document(self.task, self.input_revision, self.blueprint, [self.chapter])
        self.assertEqual(content["metadata"]["status"], "draft")
        self.assertFalse(any("待核草稿" in block.get("text", "") for block in content["blocks"]))
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
                document_xml = archive.read("word/document.xml")
                document = document_xml.decode()
                self.assertNotIn("待核草稿", document)
                self.assertIn("测试设备", document)
                self.assertIn("2台", document)
                tree = ET.fromstring(document_xml)
                picture_paragraphs = [
                    paragraph for paragraph in tree.iter(self.W + "p")
                    if paragraph.find(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing") is not None
                ]
                self.assertGreaterEqual(len(picture_paragraphs), 16)
                for paragraph in picture_paragraphs[1:]:
                    spacing = paragraph.find(f"{self.W}pPr/{self.W}spacing")
                    self.assertNotEqual(spacing.get(self.W + "lineRule"), "exact")
                    self.assertEqual(
                        paragraph.find(f"{self.W}pPr/{self.W}jc").get(self.W + "val"), "center")
            report = json.loads(target.with_suffix(".quality.json").read_text(encoding="utf-8"))
            self.assertTrue(report["structural_pass"])
            self.assertEqual(result["render_evidence"]["document_structure"]["figure_count"], 15)
            self.assertEqual(result["render_evidence"]["document_structure"]["toc_depth"], 2)
            self.assertEqual(result["render_evidence"]["diagram_generation"]["engine"], "mermaid-js-12.0.0")
            self.assertTrue(result["render_evidence"]["diagram_generation"]["color"])
            self.assertFalse(result["render_evidence"]["diagram_generation"]["source_persisted"])
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
