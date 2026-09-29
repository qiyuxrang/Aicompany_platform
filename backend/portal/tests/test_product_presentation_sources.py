import json
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from django.conf import settings
from django.test import SimpleTestCase


SOURCE_MAX_BYTES = 16 * 1024 * 1024
BLOCK_MAX_COUNT = 10000
BACKEND = settings.BASE_DIR / "backend"
SCRIPT = BACKEND / "portal/product_assets/simple_presentation.py"
RUNTIME = settings.BASE_DIR / ".runtime/product-documents-python/Scripts/python.exe"


def sources():
    return [
        {"family": "technical-solution", "id": "technical", "version": 1, "sha256": "a" * 64},
        {"family": "feasibility", "id": "feasibility", "version": 1, "sha256": "b" * 64},
    ]


def source_data(blocks=None):
    return {
        "title": "客户项目来源真实性测试",
        "sources": sources(),
        "blocks": blocks or [
            {"ref": "technical-solution:H1", "type": "heading", "level": 1, "text": "客户技术方案总览"},
            {"ref": "feasibility:H1", "type": "heading", "level": 1, "text": "客户可行性研究总览"},
        ],
    }


class PresentationGeneratorTestCase(SimpleTestCase):
    def setUp(self):
        if not RUNTIME.is_file():
            self.skipTest("isolated document runtime is not installed")

    def write_source(self, directory, data, name="source.json"):
        target = Path(directory) / name
        target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return target

    def validate(self, source):
        return subprocess.run(
            [str(RUNTIME), "-B", str(SCRIPT), "--validate-source", str(source)],
            cwd=BACKEND, capture_output=True, text=True, timeout=60,
        )


class PresentationSourceValidationTests(PresentationGeneratorTestCase):
    def test_source_byte_limit_accepts_exact_boundary_and_rejects_next_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            data = {**source_data(), "padding": ""}
            encoded = json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode()
            data["padding"] = "x" * (SOURCE_MAX_BYTES - len(encoded))
            encoded = json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode()
            self.assertEqual(len(encoded), SOURCE_MAX_BYTES)
            source = Path(directory) / "boundary.json"
            source.write_bytes(encoded)
            self.assertEqual(self.validate(source).returncode, 0)

            source.write_bytes(encoded + b" ")
            rejected = self.validate(source)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("presentation source limit exceeded", rejected.stderr)

    def test_block_count_accepts_10000_and_rejects_10001(self):
        blocks = [
            {"ref": f"{family}:P{index}", "type": "paragraph", "text": "来源摘录"}
            for family in ("technical-solution", "feasibility")
            for index in range(5000)
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_source(directory, source_data(blocks))
            self.assertEqual(self.validate(source).returncode, 0)
            blocks.append({"ref": "technical-solution:OVER", "type": "paragraph", "text": "超限"})
            source = self.write_source(directory, source_data(blocks), "over.json")
            rejected = self.validate(source)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("presentation source limit exceeded", rejected.stderr)

    def test_source_and_block_types_fail_closed(self):
        valid = source_data()
        invalid_values = [
            {**valid, "sources": "not-a-list"},
            {**valid, "sources": [sources()[0], "not-an-object"]},
            {**valid, "blocks": "not-a-list"},
            {**valid, "blocks": ["not-an-object"]},
            {**valid, "blocks": [{"ref": "technical-solution:X", "type": "unknown", "text": "x"}]},
            {**valid, "blocks": [valid["blocks"][0], dict(valid["blocks"][0])]},
            {**valid, "blocks": [{"ref": "technical-solution:H", "type": "heading", "level": True, "text": "x"}]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            for index, data in enumerate(invalid_values):
                with self.subTest(index=index):
                    source = self.write_source(directory, data, f"invalid-{index}.json")
                    rejected = self.validate(source)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("invalid slide source", rejected.stderr)

    def test_mapping_distinguishes_used_omitted_and_unknown_refs(self):
        code = """
import json
from pptx import Presentation
from portal.product_assets import simple_presentation as generator
prs = Presentation()
generator.add_slide(prs, 'test', 'source', 1, 'test',
                    ['technical-solution:USED', 'unknown:REF', 'technical-solution:USED'])
print(json.dumps(generator.source_mapping(
    prs,
    {'technical-solution:USED', 'technical-solution:OMITTED'},
    {'technical-solution:USED', 'technical-solution:OMITTED'},
), sort_keys=True))
"""
        completed = subprocess.run(
            [str(RUNTIME), "-B", "-c", code], cwd=BACKEND,
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {
            "mapped_source_refs": ["technical-solution:USED", "unknown:REF"],
            "omitted_source_refs": ["technical-solution:OMITTED"],
            "missing_source_refs": ["unknown:REF"],
        })


class PresentationSourceManifestTests(PresentationGeneratorTestCase):
    def test_many_chapters_keep_body_content_and_fallback_text_does_not_overlap(self):
        blocks = []
        for family in ("technical-solution", "feasibility"):
            for index in range(6):
                blocks.extend([
                    {"ref": f"{family}:H{index}", "type": "heading", "level": 1, "text": f"章节{index}"},
                    {"ref": f"{family}:P{index}", "type": "paragraph", "text": f"{family} 正文结论{index}。"},
                ])
        with tempfile.TemporaryDirectory() as directory:
            source = self.write_source(directory, source_data(blocks))
            target = Path(directory) / "summary.pptx"
            completed = subprocess.run([str(RUNTIME), "-B", str(SCRIPT), str(source), str(target)],
                                       cwd=BACKEND, capture_output=True, text=True, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads(target.with_suffix(".manifest.json").read_text(encoding="utf-8"))
            mapped = set(manifest["mapped_source_refs"])
            self.assertEqual(manifest["quality_gate"]["status"], "pass")
            for family in ("technical-solution", "feasibility"):
                self.assertIn(f"{family}:P0", mapped)
                self.assertIn(f"{family}:P3", mapped)
                self.assertGreater(manifest["body_coverage"][family]["mapped"], 0)
            with ZipFile(target) as archive:
                ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
                      "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
                for page in (3, 5):
                    root = ET.fromstring(archive.read(f"ppt/slides/slide{page}.xml"))
                    boxes = []
                    for shape in root.findall(".//p:sp", ns):
                        if not "".join(_root_text(shape)).strip():
                            continue
                        transform = shape.find("p:spPr/a:xfrm", ns)
                        if transform is None:
                            continue
                        offset, extent = transform.find("a:off", ns), transform.find("a:ext", ns)
                        boxes.append(tuple(int(value) for value in (
                            offset.get("x"), offset.get("y"), extent.get("cx"), extent.get("cy"))))
                    for index, (x, y, w, h) in enumerate(boxes):
                        for bx, by, bw, bh in boxes[index + 1:]:
                            self.assertFalse(min(x + w, bx + bw) > max(x, bx)
                                             and min(y + h, by + bh) > max(y, by),
                                             f"overlapping text on slide {page}")

    def test_five_page_summary_uses_real_content_counts_and_refs(self):
        blocks = []
        for family, title, prefix in (
                ("technical-solution", "客户技术方案总览", "客户技术项目摘录"),
                ("feasibility", "客户可行性研究总览", "客户可行性项目摘录")):
            blocks.append({"ref": f"{family}:H1", "type": "heading", "level": 1, "text": title})
            blocks.extend({"ref": f"{family}:P{index}", "type": "paragraph",
                           "text": f"{prefix}{index}，仅来自当前报告。"}
                          for index in range(8))
            blocks.append({"ref": f"{family}:DRAFT_NOTICE1", "type": "paragraph", "text": "草稿声明"})
            blocks.append({"ref": f"{family}:PENDING_TEXT1", "type": "paragraph", "text": "待确认内容"})
        blocks.extend([
            {"ref": "technical-solution:F1", "type": "figure", "caption": "客户网络结构图", "text": ""},
            {"ref": "technical-solution:F2", "type": "figure", "caption": "客户部署结构图", "text": ""},
            {"ref": "feasibility:F1", "type": "figure", "caption": "客户实施条件图", "text": ""},
        ])
        data = source_data(blocks)

        with tempfile.TemporaryDirectory() as directory:
            source = self.write_source(directory, data)
            target = Path(directory) / "summary.pptx"
            completed = subprocess.run(
                [str(RUNTIME), "-B", str(SCRIPT), str(source), str(target)],
                cwd=BACKEND, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads(target.with_suffix(".manifest.json").read_text(encoding="utf-8"))
            with ZipFile(target) as archive:
                slide_names = [name for name in archive.namelist()
                               if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
                slide_roots = [ET.fromstring(archive.read(name)) for name in slide_names]
                slide_text = "".join(text for root in slide_roots for text in _root_text(root))
                picture_count = sum(1 for root in slide_roots for element in root.iter()
                                    if element.tag.endswith("}pic"))
                notes_text = "".join(
                    text
                    for name in archive.namelist()
                    if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml")
                    for text in _xml_text(archive.read(name))
                )
                chart_values = [
                    float(value.text)
                    for name in archive.namelist()
                    if name.startswith("ppt/charts/chart") and name.endswith(".xml")
                    for value in ET.fromstring(archive.read(name)).findall(
                        ".//{http://schemas.openxmlformats.org/drawingml/2006/chart}val/"
                        "{http://schemas.openxmlformats.org/drawingml/2006/chart}numRef/"
                        "{http://schemas.openxmlformats.org/drawingml/2006/chart}numCache/"
                        "{http://schemas.openxmlformats.org/drawingml/2006/chart}pt/"
                        "{http://schemas.openxmlformats.org/drawingml/2006/chart}v"
                    )
                ]

        all_refs = {block["ref"] for block in blocks}
        eligible = {ref for ref in all_refs
                    if not ref.split(":", 1)[1].startswith(("DRAFT_NOTICE", "PENDING_"))}
        mapped = set(manifest["mapped_source_refs"])
        self.assertEqual(manifest["slides"], 5)
        self.assertEqual(manifest["quality_gate"]["status"], "pass")
        self.assertEqual(manifest["source_mapping_scope"], "selected_summary_blocks")
        self.assertEqual(manifest["content_review"], "not_run")
        self.assertEqual(manifest["source_blocks"], len(eligible))
        self.assertEqual(manifest["source_blocks_mapped"], len(eligible & mapped))
        self.assertEqual(set(manifest["omitted_source_refs"]), eligible - mapped)
        self.assertEqual(set(manifest["missing_source_refs"]), mapped - all_refs)
        self.assertTrue(manifest["omitted_source_refs"])
        self.assertEqual(manifest["missing_source_refs"], [])
        self.assertIn("technical-solution:P0", mapped)
        self.assertIn("feasibility:P0", mapped)
        self.assertIn("technical-solution:P0", notes_text)
        self.assertIn("feasibility:P0", notes_text)
        for field in ("visual_asset_count", "source_blocks", "source_blocks_mapped"):
            self.assertIs(type(manifest[field]), int)
        self.assertEqual(manifest["visual_asset_count"], picture_count)
        self.assertEqual(manifest["native_charts"], 1)

        self.assertIn("客户技术方案总览", slide_text)
        self.assertIn("客户可行性研究总览", slide_text)
        self.assertIn("3 张", slide_text)
        for platform_assumption in ("35 张", "统一登录", "智能体编排", "资料上传", "风险控制矩阵"):
            self.assertNotIn(platform_assumption, slide_text)
        self.assertEqual(chart_values, [2.0, 1.0])


def _xml_text(content):
    return _root_text(ET.fromstring(content))


def _root_text(root):
    return [element.text or "" for element in root.iter() if element.tag.endswith("}t")]
