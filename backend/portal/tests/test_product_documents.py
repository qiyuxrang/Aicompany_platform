import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from portal.product_documents import _diagram_evidence, _refresh_word_field_cache, DocumentError, content_document, frozen_pack, render_candidate, render_draft, render_report_draft
from portal.product_diagrams import validate_document_requirements
from portal.product_docx_layout import normalize_docx_layout
from portal.product_documents import _normalize_word_layout


class GeneratedWordLayoutTests(SimpleTestCase):
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def xml(self, *, end='end', section_text='', heading_name='H1', nested=''):
        return f'''<w:document xmlns:w="{self.W[1:-1]}"
          xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
          xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14">
        <w:body><w:p><w:r><w:t>Cover</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="TOC1"/></w:pPr>
          <w:r><w:fldChar w:fldCharType="begin"/></w:r>
          <w:r><w:instrText xml:space="preserve"> TO</w:instrText></w:r>
          <w:r><w:instrText>C \\o "1-2"</w:instrText></w:r>
          <w:r><w:fldChar w:fldCharType="separate"/></w:r>
          <w:r><w:t>Cached heading 1</w:t></w:r>{nested}
          <w:r><w:rPr><w:sz w:val="44"/></w:rPr><w:fldChar w:fldCharType="{end}"/></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/><w:sectPr><w:pgNumType w:start="1"/></w:sectPr></w:pPr>{section_text}</w:p>
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr>
          <w:bookmarkStart w:name="{heading_name}" w:id="7"/>
          <w:r><w:rPr><w:sz w:val="32"/></w:rPr><w:t>Authored heading</w:t></w:r>
          <w:bookmarkEnd w:id="7"/></w:p>
        <w:p><w:pPr><w:keepNext/></w:pPr><w:r><w:drawing><w14:test/></w:drawing></w:r></w:p>
        <w:p><w:r><w:t>Caption and retained note</w:t><w:br w:type="page"/></w:r></w:p>
        <w:sectPr><w:type w:val="nextPage"/></w:sectPr></w:body></w:document>'''.encode()

    def package(self, xml, *, duplicate=False):
        buffer = io.BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.comment = b"retained archive comment"
            archive.writestr("word/document.xml", xml)
            archive.writestr("word/media/image1.png", b"exact raster bytes")
            archive.writestr("word/_rels/document.xml.rels", b"exact relationship bytes")
            archive.writestr("word/styles.xml", b"exact styles bytes")
            if duplicate:
                archive.writestr("word/styles.xml", b"duplicate")
        return buffer.getvalue()

    def test_keeps_all_content_fields_bookmarks_media_sections_and_namespace_bindings(self):
        original = self.package(self.xml())
        normalized, evidence = normalize_docx_layout(original, ["H1"])
        with ZipFile(io.BytesIO(original)) as before, ZipFile(io.BytesIO(normalized)) as after:
            self.assertEqual(before.namelist(), after.namelist())
            self.assertEqual(before.comment, after.comment)
            for name in before.namelist():
                if name != "word/document.xml":
                    self.assertEqual(before.read(name), after.read(name))
            xml = after.read("word/document.xml")
            self.assertIn(b'xmlns:w14=', xml)
            self.assertIn(b'mc:Ignorable="w14"', xml)
            old, tree = ET.fromstring(before.read("word/document.xml")), ET.fromstring(xml)
            for tag in ("t", "instrText", "fldChar", "bookmarkStart", "bookmarkEnd", "drawing", "sectPr", "br"):
                self.assertEqual([ET.tostring(node) for node in old.iter(self.W + tag)],
                                 [ET.tostring(node) for node in tree.iter(self.W + tag)])
            paragraphs = list(tree.find(self.W + "body"))
            heading = next(p for p in paragraphs if p.find(self.W + "bookmarkStart") is not None)
            for flag in ("keepNext", "keepLines"):
                self.assertEqual(heading.find(f"{self.W}pPr/{self.W}{flag}").get(self.W + "val"), "1")
            self.assertEqual(heading.find(f"{self.W}r/{self.W}rPr/{self.W}sz").get(self.W + "val"), "32")
            tail = next(p for p in paragraphs if p.find(f"{self.W}r/{self.W}fldChar[@{self.W}fldCharType='end']") is not None)
            spacing = tail.find(f"{self.W}pPr/{self.W}spacing")
            self.assertEqual((spacing.get(self.W + "line"), spacing.get(self.W + "before")), ("20", "0"))
            section = next(p for p in paragraphs if p.find(f"{self.W}pPr/{self.W}sectPr") is not None)
            self.assertIsNone(section.find(f"{self.W}pPr/{self.W}pStyle"))
            self.assertEqual(section.find(f"{self.W}pPr/{self.W}keepNext").get(self.W + "val"), "0")
        self.assertEqual((evidence["heading_count"], evidence["toc_end_count"], evidence["compact_section_count"]), (1, 1, 1))
        self.assertEqual(evidence["normalized_sha256"], hashlib.sha256(normalized).hexdigest())
        self.assertEqual(evidence["visual_review"], "not_run")
        second, repeated = normalize_docx_layout(normalized, ["H1"])
        self.assertEqual(second, normalized)
        self.assertEqual(repeated["semantic_sha256"], evidence["semantic_sha256"])

    def test_nested_pageref_end_is_not_mistaken_for_outer_toc_end(self):
        nested = '''<w:r><w:fldChar w:fldCharType="begin"/></w:r>
          <w:r><w:instrText> PAGEREF H1 \\h </w:instrText></w:r>
          <w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>
          <w:r><w:fldChar w:fldCharType="end"/></w:r>'''
        normalized, evidence = normalize_docx_layout(self.package(self.xml(nested=nested)), ["H1"])
        self.assertEqual(evidence["toc_end_count"], 1)
        with ZipFile(io.BytesIO(normalized)) as archive:
            tree = ET.fromstring(archive.read("word/document.xml"))
        ends = tree.findall(f".//{self.W}fldChar[@{self.W}fldCharType='end']")
        self.assertEqual(len(ends), 2)
        self.assertIn("PAGEREF", "".join(node.text or '' for node in tree.iter(self.W + "instrText")))

    def test_invalid_field_boundaries_fail_without_producing_a_repaired_document(self):
        xml = self.xml()
        cases = [self.xml(end="begin"), xml.replace(b'w:fldCharType="separate"', b'w:fldCharType="end"'),
                 xml.replace(b'w:fldCharType="begin"', b'w:fldCharType="separate"'),
                 xml.replace(b' TO</w:instrText>', b' REF</w:instrText>'),
                 xml.replace(b'<w:fldChar w:fldCharType="end"/>', b'<w:fldChar w:fldCharType="end"/><w:t>Trailing content</w:t>')]
        for content in cases:
            with self.subTest(content=content[-60:]), self.assertRaises(ValueError):
                normalize_docx_layout(self.package(content), ["H1"])

    def test_section_content_or_missing_ambiguous_heading_is_not_silently_removed(self):
        for xml, headings in [(self.xml(section_text='<w:r><w:t>Retain this text</w:t></w:r>'), ["H1"]),
                              (self.xml(heading_name="Other"), ["H1"]), (self.xml(), ["H1", "H1"]),
                              (self.xml().replace(b'<w:bookmarkEnd', b'<w:bookmarkStart w:name="H1" w:id="8"/><w:bookmarkEnd'), ["H1"])]:
            # A duplicate bookmark within one paragraph is rejected separately.
            with self.subTest(headings=headings), self.assertRaises(ValueError):
                normalize_docx_layout(self.package(xml), headings)

    def test_xml_entities_malformed_xml_and_duplicate_zip_parts_fail_closed(self):
        for content in (b'<!DOCTYPE x [<!ENTITY boom "text">]>' + self.xml(), b'<broken', self.xml().decode().encode('utf-16')):
            with self.subTest(content=content[:30]), self.assertRaises(ValueError):
                normalize_docx_layout(self.package(content), ["H1"])
        with self.assertWarns(UserWarning):
            duplicate = self.package(self.xml(), duplicate=True)
        with self.assertRaises(ValueError):
            normalize_docx_layout(duplicate, ["H1"])

    def test_hash_mismatch_never_rewrites_generated_artifact_or_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "draft.docx"
            original = self.package(self.xml())
            target.write_bytes(original)
            quality = target.with_suffix(".quality.json")
            quality.write_bytes(b'{"output_sha256":"wrong","bookmark_map":{"heading":"H1"}}')
            old_quality = quality.read_bytes()
            with self.assertRaises(DocumentError):
                _normalize_word_layout(target, {"blocks": [{"id": "heading", "type": "heading"}]})
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(quality.read_bytes(), old_quality)

    def test_generated_and_normalized_hashes_remain_bound_in_quality_and_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "draft.docx"
            original = self.package(self.xml())
            target.write_bytes(original)
            quality = target.with_suffix(".quality.json")
            quality.write_text(json.dumps({"output_sha256": hashlib.sha256(original).hexdigest(),
                "content_sha256": "unchanged-content-version", "bookmark_map": {"heading": "H1"}}))
            evidence = _normalize_word_layout(target, {"blocks": [{"id": "heading", "type": "heading"}]})
            report = json.loads(quality.read_text())
            self.assertEqual(report["output_sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(report["layout_normalization"], evidence)
            self.assertEqual(evidence["generated_sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(report["content_sha256"], "unchanged-content-version")

    def test_quality_write_failure_restores_both_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "draft.docx"
            original = self.package(self.xml())
            target.write_bytes(original)
            quality = target.with_suffix(".quality.json")
            quality.write_text(json.dumps({"output_sha256": hashlib.sha256(original).hexdigest(),
                                          "bookmark_map": {"heading": "H1"}}))
            original_quality = quality.read_bytes()
            write_bytes = Path.write_bytes
            failures = []

            def fail_once(path, data):
                if path == quality and not failures:
                    failures.append(True)
                    raise OSError("synthetic quality promotion failure")
                return write_bytes(path, data)

            with patch.object(Path, "write_bytes", fail_once), self.assertRaises(DocumentError):
                _normalize_word_layout(target, {"blocks": [{"id": "heading", "type": "heading"}]})
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(quality.read_bytes(), original_quality)


class DiagramEvidenceTests(SimpleTestCase):
    def result(self):
        return {"rendered": 15, "engine": "mermaid-js-12.0.0", "sourcePersisted": False,
                "securityLevel": "strict", "blockedNetworkRequests": 0, "bundleSha256": "a" * 64,
                "manifestSha256": "b" * 64,
                "runtimeVersions": {"mermaid": "12.0.0", "katex": "0.18.2", "lodash-es": "4.18.1"}}

    def test_actual_bundle_and_loaded_versions_are_retained(self):
        evidence = _diagram_evidence(json.dumps(self.result()).encode(), 15)
        self.assertEqual(evidence["bundle_sha256"], "a" * 64)
        self.assertEqual(evidence["manifest_sha256"], "b" * 64)
        self.assertEqual(evidence["runtime_versions"]["katex"], "0.18.2")

    def test_unverified_bundle_missing_figures_or_network_use_fails_closed(self):
        for changes in ({"rendered": 0}, {"sourcePersisted": True}, {"securityLevel": "loose"},
                        {"blockedNetworkRequests": 1}, {"bundleSha256": "unknown"},
                        {"runtimeVersions": {"mermaid": "12.0.0", "katex": "0.16.47", "lodash-es": "4.18.1"}}):
            with self.subTest(changes=changes), self.assertRaises(DocumentError):
                _diagram_evidence(json.dumps({**self.result(), **changes}), 15)
        for output in (b"", b"{}", b"null", b"[]", b"not JSON"):
            with self.subTest(output=output), self.assertRaises(DocumentError):
                _diagram_evidence(output, 15)


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
                layout = artifact["render_evidence"]["layout_normalization"]
                self.assertGreater(layout["heading_count"], 2)
                self.assertEqual(layout["toc_end_count"], 1)
                self.assertEqual(layout["visual_review"], "not_run")
                target = Path(directory) / artifact["path"]
                quality = json.loads(target.with_suffix(".quality.json").read_text(encoding="utf-8"))
                self.assertEqual(quality["layout_normalization"], layout)
                self.assertEqual(quality["output_sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
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


@override_settings(PRODUCT_OFFICE_RENDER_ENABLED=True)
class WordFieldStagingTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="word-field-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / ("artifact-" + "a" * 90) / ("b" * 90)
        self.directory.mkdir(parents=True)
        self.target = self.directory / "draft.docx"
        self.target.write_bytes(b"original generated docx")
        self.quality_path = self.target.with_suffix(".quality.json")
        self.quality_path.write_text(json.dumps({"output_sha256": "original", "other_check": True}))
        self.original = self.target.read_bytes()
        self.original_quality = self.quality_path.read_bytes()
        self.stage = None

    def renderer(self, command, **kwargs):
        source, output = Path(command[4]), Path(command[5])
        self.stage = source.parent
        # Model COM's path restriction on every Word input/output, rather than
        # accepting the original long artifact path as a mocked success.
        self.assertEqual(source.read_bytes(), self.original)
        self.assertEqual(kwargs["cwd"], self.stage)
        self.assertNotEqual(source, self.target)
        for path in (source, output / "reviewed.docx", output / "document.pdf"):
            self.assertLess(len(str(path)), 260)
        output.mkdir()
        (output / "reviewed.docx").write_bytes(b"reviewed cached TOC docx")
        (output / "document.pdf").write_bytes(b"synthetic PDF evidence")
        (output / "page-001.png").write_bytes(b"synthetic PNG evidence")
        report = {"rendered": True, "toc_count": 1, "page_count": 2, "renderer": "Mock Word",
                  "pdf": str(output / "document.pdf"), "rendered_docx": str(output / "reviewed.docx"),
                  "images": [str(output / "page-001.png")]}
        (output / "render.json").write_text(json.dumps(report))
        return SimpleNamespace(returncode=0)

    def refresh(self, renderer=None):
        with patch("portal.product_documents.subprocess.run", side_effect=renderer or self.renderer):
            return _refresh_word_field_cache(self.target, Path("synthetic-python"), self.directory)

    def assert_not_promoted(self):
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.quality_path.read_bytes(), self.original_quality)
        if self.stage:
            self.assertFalse(self.stage.exists())

    def test_long_artifact_uses_short_office_paths_and_retains_render_evidence(self):
        self.assertGreaterEqual(len(str(self.target)), 260)
        result = self.refresh()
        self.assertEqual(result["status"], "completed")
        self.assertFalse(self.stage.exists())
        retained = self.directory / "word-field-refresh"
        report = json.loads((retained / "render.json").read_text())
        self.assertNotIn(str(self.stage), json.dumps(report))
        for filename in [report["pdf"], report["rendered_docx"], *report["images"]]:
            self.assertTrue(Path(filename).is_file())
        self.assertEqual(self.target.read_bytes(), b"reviewed cached TOC docx")
        quality = json.loads(self.quality_path.read_text())
        self.assertEqual(quality["output_sha256"], hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertTrue(quality["other_check"])
        self.assertEqual(quality["word_field_cache"]["toc_count"], 1)

    def test_failed_render_gates_preserve_target_quality_and_diagnostics(self):
        for failure in ("nonzero", "not_rendered", "missing_toc", "invalid_toc", "missing_reviewed",
                        "missing_report", "invalid_report"):
            with self.subTest(failure=failure):
                retained = self.directory / "word-field-refresh"
                if retained.exists():
                    shutil.rmtree(retained)

                def failed(command, **kwargs):
                    result = self.renderer(command, **kwargs)
                    output = Path(command[5])
                    report_path = output / "render.json"
                    report = json.loads(report_path.read_text())
                    if failure == "nonzero":
                        return SimpleNamespace(returncode=2)
                    if failure == "not_rendered":
                        report["rendered"] = False
                    elif failure == "missing_toc":
                        report.pop("toc_count")
                    elif failure == "invalid_toc":
                        report["toc_count"] = "invalid"
                    elif failure == "missing_reviewed":
                        (output / "reviewed.docx").unlink()
                    elif failure == "missing_report":
                        report_path.unlink()
                        return result
                    elif failure == "invalid_report":
                        report_path.write_text("invalid JSON diagnostic")
                        return result
                    report_path.write_text(json.dumps(report))
                    return result

                with self.assertRaises(DocumentError) as error:
                    self.refresh(failed)
                self.assertEqual(error.exception.code, "document_render_failed")
                self.assert_not_promoted()
                self.assertTrue((retained / "document.pdf").is_file())
                self.assertEqual(json.loads((retained / "refresh.json").read_text())["status"], "failed")

    def test_timeout_retains_partial_output_and_remaps_its_report(self):
        def timeout(command, **kwargs):
            self.renderer(command, **kwargs)
            raise subprocess.TimeoutExpired(command, 330)
        with self.assertRaises(DocumentError):
            self.refresh(timeout)
        self.assert_not_promoted()
        retained = self.directory / "word-field-refresh"
        report = json.loads((retained / "render.json").read_text())
        self.assertNotIn(str(self.stage), json.dumps(report))
        self.assertTrue(Path(report["pdf"]).is_file())

    def test_process_start_failure_does_not_promote(self):
        with self.assertRaises(DocumentError):
            self.refresh(lambda *args, **kwargs: (_ for _ in ()).throw(OSError("failed to start")))
        self.assert_not_promoted()
        self.assertTrue((self.directory / "word-field-refresh" / "refresh.json").is_file())

    def test_invalid_quality_does_not_run_office_or_promote(self):
        self.quality_path.write_text("invalid quality JSON")
        self.original_quality = self.quality_path.read_bytes()
        with patch("portal.product_documents.subprocess.run") as run:
            with self.assertRaises(DocumentError):
                _refresh_word_field_cache(self.target, Path("synthetic-python"), self.directory)
        run.assert_not_called()
        self.assert_not_promoted()

    def test_existing_render_evidence_is_never_overwritten(self):
        retained = self.directory / "word-field-refresh"
        retained.mkdir()
        marker = retained / "render.json"
        marker.write_bytes(b"prior evidence")
        with self.assertRaises(DocumentError):
            self.refresh()
        self.assertEqual(marker.read_bytes(), b"prior evidence")
        self.assert_not_promoted()

    def test_cleanup_failure_keeps_original_and_retained_diagnostics(self):
        real_temporary = tempfile.TemporaryDirectory
        owned = []

        class CleanupFailure:
            def __enter__(inner):
                inner.temporary = real_temporary(prefix="portal-word-")
                owned.append(inner.temporary)
                return inner.temporary.name

            def __exit__(inner, *args):
                inner.temporary.cleanup()
                raise OSError("simulated cleanup failure")

        with patch("portal.product_documents.tempfile.TemporaryDirectory", return_value=CleanupFailure()):
            with self.assertRaises(DocumentError):
                self.refresh()
        for item in owned:
            item.cleanup()
        self.assert_not_promoted()
        diagnostic = json.loads((self.directory / "word-field-refresh" / "refresh.json").read_text())
        self.assertEqual(diagnostic["reason"], "staging_cleanup_failed")
        self.assertTrue((self.directory / "word-field-refresh" / "document.pdf").is_file())

    def test_long_temp_root_fails_before_starting_office(self):
        real_temporary = tempfile.TemporaryDirectory

        class LongTemporary:
            def __enter__(inner):
                inner.temporary = real_temporary(dir=self.directory, prefix="portal-word-")
                return inner.temporary.name

            def __exit__(inner, *args):
                inner.temporary.cleanup()

        with patch("portal.product_documents.tempfile.TemporaryDirectory", return_value=LongTemporary()):
            with patch("portal.product_documents.subprocess.run") as run:
                with self.assertRaises(DocumentError):
                    _refresh_word_field_cache(self.target, Path("synthetic-python"), self.directory)
        run.assert_not_called()
        self.assert_not_promoted()
        self.assertFalse(list(self.directory.glob("portal-word-*")))
        diagnostic = json.loads((self.directory / "word-field-refresh" / "refresh.json").read_text())
        self.assertEqual(diagnostic["reason"], "office_path_too_long")

    def test_quality_promotion_failure_restores_both_original_files(self):
        write_bytes = Path.write_bytes
        failed = False

        def fail_once(path, data):
            nonlocal failed
            if path == self.quality_path and not failed:
                failed = True
                raise OSError("simulated quality write failure")
            return write_bytes(path, data)

        with patch.object(Path, "write_bytes", fail_once):
            with self.assertRaises(DocumentError):
                self.refresh()
        self.assertTrue(failed)
        self.assert_not_promoted()
        diagnostic = json.loads((self.directory / "word-field-refresh" / "refresh.json").read_text())
        self.assertEqual(diagnostic["reason"], "promotion_failed")

    @override_settings(PRODUCT_OFFICE_RENDER_ENABLED=False)
    def test_disabled_render_remains_explicitly_not_run(self):
        result = self.refresh()
        self.assertEqual(result, {"status": "not_run", "reason": "office_render_disabled"})
        self.assertFalse((self.directory / "word-field-refresh").exists())
        self.assert_not_promoted()
