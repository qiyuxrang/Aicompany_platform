import importlib.util
import os
import subprocess
from io import BytesIO
from pathlib import Path
from unittest import TestCase, skipIf
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parents[1] / "product_assets" / "bj_docs" / "scripts"
DOCUMENT_RUNTIME = ROOT / ".runtime" / "product-documents-python" / "Scripts" / "python.exe"
DELEGATED = "BJ_DOCS_CHART_SAFETY_DELEGATED"
OOXML = None
if importlib.util.find_spec("lxml") is not None:
    SPEC = importlib.util.spec_from_file_location("bj_docs_ooxml", SCRIPTS / "ooxml.py")
    OOXML = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(OOXML)

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def zipped(parts):
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as package:
        for name, payload in parts.items():
            package.writestr(name, payload)
    return output.getvalue()


@skipIf(OOXML is None, "lxml unavailable here; suite delegated to document runtime")
class ProductOfficeChartSafetyTests(TestCase):
    def workbook_parts(self):
        return {
            "[Content_Types].xml": f"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
                <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
                <Default Extension="xml" ContentType="application/xml"/>
                <Override PartName="/xl/workbook.xml" ContentType="{OOXML.WORKBOOK_CONTENT_TYPE}"/>
                <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
            </Types>""".encode(),
            "_rels/.rels": f"""<Relationships xmlns="{REL_NS}">
                <Relationship Id="rId1" Type="{OFFICE_REL}/officeDocument" Target="xl/workbook.xml"/>
            </Relationships>""".encode(),
            "xl/workbook.xml": f'<workbook xmlns="{SHEET_NS}"/>'.encode(),
            "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{SHEET_NS}"><sheetData/></worksheet>'.encode(),
        }

    def presentation_parts(self, workbook, chart_package=True):
        chart_link = f'<Relationship Id="rId2" Type="{OFFICE_REL}/package" Target="../embeddings/chart.xlsx"/>'
        slide_links = f'<Relationship Id="rId1" Type="{OFFICE_REL}/chart" Target="../charts/chart1.xml"/>'
        if not chart_package:
            slide_links += chart_link
            chart_link = ""
        return {
            "[Content_Types].xml": f"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
                <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
                <Default Extension="xml" ContentType="application/xml"/>
                <Default Extension="xlsx" ContentType="{OOXML.EMBEDDED_XLSX_CONTENT_TYPE}"/>
                <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
                <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
                <Override PartName="/ppt/charts/chart1.xml" ContentType="{OOXML.CHART_CONTENT_TYPE}"/>
            </Types>""".encode(),
            "_rels/.rels": f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" Type="{OFFICE_REL}/officeDocument" Target="ppt/presentation.xml"/></Relationships>'.encode(),
            "ppt/presentation.xml": b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
            "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" Type="{OFFICE_REL}/slide" Target="slides/slide1.xml"/></Relationships>'.encode(),
            "ppt/slides/slide1.xml": b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
            "ppt/slides/_rels/slide1.xml.rels": f'<Relationships xmlns="{REL_NS}">{slide_links}</Relationships>'.encode(),
            "ppt/charts/chart1.xml": b'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"/>',
            "ppt/charts/_rels/chart1.xml.rels": f'<Relationships xmlns="{REL_NS}">{chart_link}</Relationships>'.encode(),
            "ppt/embeddings/chart.xlsx": zipped(workbook),
        }

    def audit(self, workbook, chart_package=True):
        return OOXML.audit_package(self.presentation_parts(workbook, chart_package), "presentation")

    def test_allows_macro_free_internal_chart_workbook(self):
        self.assertTrue(self.audit(self.workbook_parts()))

    def test_rejects_formula_and_external_relationship(self):
        formula = self.workbook_parts()
        formula["xl/worksheets/sheet1.xml"] = f'<worksheet xmlns="{SHEET_NS}"><sheetData><row><c><f>1+1</f></c></row></sheetData></worksheet>'.encode()
        external = self.workbook_parts()
        external["xl/_rels/workbook.xml.rels"] = f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId9" Type="{OFFICE_REL}/externalLink" Target="https://example.invalid/data.xlsx" TargetMode="External"/></Relationships>'.encode()
        for label, workbook, message in (
            ("formula", formula, "formula forbidden"),
            ("external", external, "external relationship"),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, message):
                self.audit(workbook)

    def test_rejects_formula_in_content_typed_renamed_worksheet(self):
        workbook = self.workbook_parts()
        workbook["[Content_Types].xml"] = workbook["[Content_Types].xml"].replace(b"sheet1.xml", b"sheet1.dat")
        workbook["xl/worksheets/sheet1.dat"] = f'<worksheet xmlns="{SHEET_NS}"><sheetData><row><c><f>WEBSERVICE("https://example.invalid")</f></c></row></sheetData></worksheet>'.encode()
        del workbook["xl/worksheets/sheet1.xml"]
        workbook["xl/_rels/workbook.xml.rels"] = f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" Type="{OFFICE_REL}/worksheet" Target="worksheets/sheet1.dat"/></Relationships>'.encode()
        with self.assertRaisesRegex(ValueError, "formula forbidden"):
            self.audit(workbook)

    def test_rejects_active_content_types_and_relationships_for_every_kind(self):
        active_type = {
            "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/ppt/payload.bin" ContentType="application/vnd.ms-office.vbaProject"/></Types>',
            "ppt/payload.bin": b"VBA",
        }
        active_relationship = {
            "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/ppt/payload.bin" ContentType="application/octet-stream"/></Types>',
            "_rels/.rels": f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" Type="{OFFICE_REL}/vbaProject" Target="ppt/payload.bin"/></Relationships>'.encode(),
            "ppt/payload.bin": b"VBA",
        }
        for kind in ("word", "sheet", "presentation"):
            with self.subTest(kind=kind, vector="content_type"), self.assertRaisesRegex(ValueError, "unsafe active content type"):
                OOXML.audit_package(active_type, kind)
            with self.subTest(kind=kind, vector="relationship"), self.assertRaisesRegex(ValueError, "unsafe active relationship"):
                OOXML.audit_package(active_relationship, kind)

    def test_rejects_macro_nested_ole_and_active_content(self):
        macro = self.workbook_parts()
        macro["[Content_Types].xml"] = macro["[Content_Types].xml"].replace(
            OOXML.WORKBOOK_CONTENT_TYPE.encode(),
            b"application/vnd.ms-excel.sheet.macroEnabled.main+xml",
        )
        ole = self.workbook_parts()
        ole["xl/embeddings/oleObject1.bin"] = b"OLE"
        active = self.workbook_parts()
        active["xl/activeX/activeX1.bin"] = b"ACTIVE"
        for label, workbook in (("macro", macro), ("ole", ole), ("active", active)):
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.audit(workbook)

    def test_rejects_non_chart_package_embedding(self):
        with self.assertRaisesRegex(ValueError, "unsafe presentation embedded package"):
            self.audit(self.workbook_parts(), chart_package=False)

    def test_word_policy_still_rejects_embedded_xlsx(self):
        with self.assertRaisesRegex(ValueError, "unsafe package part"):
            OOXML.audit_package({
                "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xlsx" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"/></Types>',
                "word/embeddings/chart.xlsx": zipped(self.workbook_parts()),
            }, "word")

    def test_rejects_nested_compression_bomb(self):
        workbook = self.workbook_parts()
        workbook["xl/media/repeated.bin"] = b"0" * (1024 * 1024)
        with self.assertRaisesRegex(ValueError, "compression ratio"):
            self.audit(workbook)

    def test_recursive_uncompressed_budget_includes_embedded_workbook(self):
        workbook = self.workbook_parts()
        presentation = self.presentation_parts(workbook)
        outer_size = sum(map(len, presentation.values()))
        nested_size = sum(map(len, workbook.values()))
        with patch.object(OOXML, "MAX_PACKAGE_UNCOMPRESSED", outer_size + nested_size - 1):
            with self.assertRaisesRegex(ValueError, "package too large"):
                OOXML.audit_package(presentation, "presentation")


class ProductOfficeChartSafetyRuntimeTests(TestCase):
    def test_document_runtime_safety_suite(self):
        if OOXML is not None:
            self.skipTest("lxml available; safety suite runs directly")
        if os.environ.get(DELEGATED) == "1":
            self.fail("document runtime does not provide lxml")
        if not DOCUMENT_RUNTIME.is_file():
            self.skipTest(f"lxml unavailable and document runtime missing: {DOCUMENT_RUNTIME}")
        environment = os.environ.copy()
        environment[DELEGATED] = "1"
        result = subprocess.run(
            [str(DOCUMENT_RUNTIME), "-B", "-m", "unittest", "portal.tests.test_product_office_chart_safety"],
            cwd=ROOT / "backend",
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
