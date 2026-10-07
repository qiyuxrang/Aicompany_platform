import hashlib
import importlib.util
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from zipfile import ZipFile

from django.test import SimpleTestCase, override_settings

from portal.product_documents import content_document
from portal.product_models import DocumentApproval, DocumentArtifact, DocumentTask
from portal.product_pair import output_current
from portal.product_service import append_revision, approval_authorization, digest
from portal.product_storage import StorageError, verified_artifact

from .base import PortalTestCase


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD = f"{{{WORD_NS}}}"
ET.register_namespace("w", WORD_NS)
VERIFIER_PATH = Path(__file__).resolve().parents[3] / "qa" / "verify_product_deliverables.py"
VERIFIER_SPEC = importlib.util.spec_from_file_location("agent_platform_quality_verifier", VERIFIER_PATH)
QUALITY_VERIFIER = importlib.util.module_from_spec(VERIFIER_SPEC)
VERIFIER_SPEC.loader.exec_module(QUALITY_VERIFIER)


def _paragraph(text="", *, style=None, outline=None):
    paragraph = ET.Element(WORD + "p")
    if style or outline is not None:
        properties = ET.SubElement(paragraph, WORD + "pPr")
        if style:
            ET.SubElement(properties, WORD + "pStyle", {WORD + "val": style})
        if outline is not None:
            ET.SubElement(properties, WORD + "outlineLvl", {WORD + "val": str(outline)})
    if text:
        run = ET.SubElement(paragraph, WORD + "r")
        ET.SubElement(run, WORD + "t").text = text
    return paragraph


def _write_docx(path, body_text):
    document = ET.Element(WORD + "document")
    body = ET.SubElement(document, WORD + "body")
    body.append(_paragraph("合成封面" * 100))
    body.append(_paragraph("合成目录" * 100, style="TOC1"))
    body.append(_paragraph("合成正文标题", outline=0))
    body.append(_paragraph("合成小节标题", outline=1))
    body.append(_paragraph())
    body.append(_paragraph(body_text))
    table = ET.SubElement(body, WORD + "tbl")
    row = ET.SubElement(table, WORD + "tr")
    cell = ET.SubElement(row, WORD + "tc")
    cell.append(_paragraph("合成附表" * 1000))
    body.append(_paragraph("附录 A 合成附表", outline=0))
    body.append(_paragraph("合成附录正文" * 1000))
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", ET.tostring(document, encoding="utf-8", xml_declaration=True))


class AgentFormalQualityDocumentTests(SimpleTestCase):
    def test_final_docx_gate_counts_only_nonblank_chapter_body_at_each_formal_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            for family, minimum in (("technical", 50000), ("feasibility", 70000)):
                with self.subTest(family=family):
                    path = Path(directory) / f"{family}.docx"
                    _write_docx(path, "正文" * ((minimum - 2) // 2) + "正")
                    result = QUALITY_VERIFIER.inspect_docx(
                        path, minimum_characters=minimum, minimum_figures=0,
                    )
                    self.assertEqual(result["characters_non_whitespace"], minimum - 1)
                    self.assertFalse(result["length_passed"])
                    self.assertTrue(any(issue.startswith("characters_below_target:") for issue in result["issues"]))

    def test_repeated_body_filler_cannot_meet_the_formal_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repeated.docx"
            filler = "合成重复段落内容" * 4000
            _write_docx(path, filler + filler)
            result = QUALITY_VERIFIER.inspect_docx(path, minimum_characters=50000, minimum_figures=0)
        self.assertFalse(result["length_passed"], "重复段落不能充当正式正文篇幅")

    def test_corrupt_word_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            docx = Path(directory) / "corrupt.docx"
            docx.write_bytes(b"synthetic damaged Word file")
            self.assertFalse(QUALITY_VERIFIER.inspect_docx(docx, minimum_characters=50000, minimum_figures=0)["passed"])

    @skipUnless(importlib.util.find_spec("pptx"), "python-pptx is unavailable in the configured .venv")
    def test_corrupt_powerpoint_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pptx = Path(directory) / "corrupt.pptx"
            pptx.write_bytes(b"synthetic damaged PowerPoint file")
            self.assertFalse(QUALITY_VERIFIER.inspect_pptx(pptx)["passed"])

    def test_artifact_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.docx"
            artifact.write_bytes(b"synthetic artifact")
            with override_settings(PRODUCT_STORAGE_ROOT=root):
                with self.assertRaises(StorageError) as error:
                    verified_artifact(SimpleNamespace(path="artifact.docx", sha256="0" * 64))
            self.assertEqual(error.exception.code, "artifact_hash_mismatch")

    def test_missing_investment_evidence_rejects_generated_economic_conclusions(self):
        claim = "合成测算：投资成本100万元，预计回报率30%。"
        input_revision = SimpleNamespace(
            pk=1, sha256="a" * 64,
            payload={"project": "合成项目", "requirements": "合成需求", "items": [], "sources": [],
                     "knowledge_sources": [], "conditions": [], "issues": []},
        )
        blueprint = SimpleNamespace(
            sha256="b" * 64,
            payload={"template_version": "frozen-original-v1", "conditions": [], "missing": [], "conflicts": []},
        )
        chapter = SimpleNamespace(payload={
            "chapter_id": "overview", "title": "合成可研", "source_ids": [], "paragraphs": [claim],
        })
        document = content_document(SimpleNamespace(pk="synthetic", title="合成项目"), input_revision,
                                    blueprint, [chapter], "feasibility")
        self.assertTrue(any("不形成经济成本、收益或回报结论" in item["text"] for item in document["pending"]))
        contains_unsupported_claim = claim in "\n".join(block.get("text", "") for block in document["blocks"])
        self.assertFalse(contains_unsupported_claim, "unsupported economic claim remains in feasibility output")


@override_settings(PRODUCT_P1_ENABLED=True)
class AgentFormalQualityVersionTests(PortalTestCase):
    def test_older_artifact_version_is_not_current(self):
        owner = self.create_user("agent-quality-version-owner", "product")
        task = DocumentTask.objects.create(
            owner=owner, title="合成版本核验", idempotency_key="agent-quality-version",
            payload_hash=digest({"synthetic": True}), state="WAITING_REVIEW", stage="BLUEPRINT",
        )
        input_revision = append_revision(task, "input", {
            "project": "合成项目", "requirements": "合成要求", "items": [], "conditions": [],
        }, actor=owner)
        blueprint = append_revision(task, "blueprint", {
            "purpose": "合成方案", "audience": "测试", "conditions": [], "missing": [], "conflicts": [],
            "chapters": [{"id": "overview", "title": "合成概述", "scope": "合成范围", "source_ids": []}],
            "template_version": "frozen-original-v1",
        }, input_hash=input_revision.sha256, actor=owner)
        task.input_version = input_revision.version
        task.blueprint_version = blueprint.version
        task.save(update_fields=["input_version", "blueprint_version"])
        DocumentApproval.objects.create(
            task=task, revision=blueprint, actor=owner, decision="approve", sha256=blueprint.sha256,
            authorization=approval_authorization(task, owner),
        )
        append_revision(task, "chapter", {
            "chapter_id": "overview", "title": "合成概述", "paragraphs": ["合成正文"], "source_ids": [],
        }, input_hash=input_revision.sha256, blueprint_hash=blueprint.sha256, family="technical-solution", actor=owner)
        old = DocumentArtifact.objects.create(
            task=task, version=1, family="technical-solution", path="synthetic-old.docx", sha256="1" * 64,
            input_hash=input_revision.sha256, blueprint_hash=blueprint.sha256,
            template_hash="2" * 64, generation_hash=digest({"synthetic_version": 1}),
        )
        DocumentArtifact.objects.create(
            task=task, version=2, family="technical-solution", path="synthetic-current.docx", sha256="3" * 64,
            input_hash=input_revision.sha256, blueprint_hash=blueprint.sha256,
            template_hash="2" * 64, generation_hash=digest({"synthetic_version": 2}),
        )
        self.assertFalse(output_current(task, old))
