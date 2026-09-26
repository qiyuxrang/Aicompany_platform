"""Parser boundary tests. Real native readers; OCR mocked except browser acceptance."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from PIL import Image
from portal.source_parsers import visual
from portal.source_parsers.core import Result, ParseError, LimitReached, safe_zip, LIMITS
from portal.source_parsers.office import docx, xlsx
from portal.tests.intake_fixtures import docx as word_fixture, xlsx as sheet_fixture, pdf as pdf_fixture


def image_bytes(format='PNG'):
    output = io.BytesIO()
    Image.new('RGB', (300, 100), 'white').save(output, format)
    return output.getvalue()


class ParserBoundaryTests(unittest.TestCase):
    def test_native_pdf_never_uses_ocr(self):
        result = Result()
        with patch.object(visual, 'ocr', side_effect=AssertionError('native text must not use OCR')):
            visual.pdf(pdf_fixture(), result)
        self.assertEqual(result.finish()['method'], 'native')
        self.assertIn('Native PDF', result.blocks[0]['text'])

    def test_image_signature_must_match_declared_format(self):
        with self.assertRaises(ParseError) as caught: visual.read_image(image_bytes('JPEG'), '.png')
        self.assertEqual(caught.exception.code, 'format_mismatch')

    def test_empty_image_is_failed_not_completed(self):
        result = Result()
        with patch.object(visual, '_ENGINE', return_value=(None, None)):
            visual.image(image_bytes(), result)
        self.assertEqual(result.finish()['status'], 'failed')
        self.assertEqual(result.items, [])

    def test_ocr_low_confidence_is_evidence_not_equipment_quantity(self):
        result = Result()
        output = [([[0, 0], [100, 0], [100, 20], [0, 20]], '配电柜 2台', .65)]
        with patch.object(visual, '_ENGINE', return_value=(output, None)):
            visual.image(image_bytes(), result)
        self.assertEqual(result.finish()['status'], 'needs_review')
        self.assertEqual(result.blocks[0]['confidence'], .65)
        self.assertEqual(result.items, [])
        self.assertIn('low_ocr_confidence', {w['code'] for w in result.warnings})

    def test_ocr_limit_returns_partial_without_calling_engine(self):
        result = Result(); result.add('已经提取的原文', {'page': 1}); result.ocr_count = LIMITS['ocr_pages']
        with patch.object(visual, '_ENGINE') as engine:
            visual.image(image_bytes(), result)
            engine.assert_not_called()
        self.assertEqual(result.finish()['status'], 'partial')
        self.assertIn('ocr_limit', {w['code'] for w in result.warnings})

    def test_disabled_ocr_preserves_file_as_unread(self):
        result = Result(); result.meta['ocr_enabled'] = False
        with patch.object(visual, '_ENGINE') as engine:
            visual.image(image_bytes(), result); engine.assert_not_called()
        self.assertEqual(result.finish()['status'], 'failed')
        self.assertIn('ocr_unavailable', {w['code'] for w in result.warnings})

    def test_multiframe_image_is_explicitly_partial(self):
        output = io.BytesIO()
        first = Image.new('RGB', (300, 100), 'white'); second = Image.new('RGB', (300, 100), 'black')
        first.save(output, 'TIFF', save_all=True, append_images=[second])
        result = Result()
        with patch.object(visual, '_ENGINE', return_value=([([[0, 0], [100, 0], [100, 20], [0, 20]], 'first frame', .99)], None)):
            visual.image(output.getvalue(), result, suffix='.tif')
        self.assertEqual(result.finish()['status'], 'partial')

    def test_extraction_character_limit_does_not_report_success(self):
        result = Result(); result.add('first block', {'page': 1})
        with self.assertRaises(LimitReached): result.add('x' * LIMITS['characters'], {'page': 2})
        self.assertTrue(result.finish()['truncated'])
        self.assertEqual(result.finish()['status'], 'partial')

    def test_zip_expansion_ratio_rejected_before_xml_parse(self):
        with self.assertRaises(ParseError) as caught:
            safe_zip(word_fixture(extras={'word/oversize.xml': '<x>' + 'A' * (2 * 1024 * 1024) + '</x>'}), 'word/document.xml')
        self.assertEqual(caught.exception.code, 'archive_limit')

    def test_pdf_page_limit_checked_before_ocr(self):
        import pypdfium2 as pdfium
        output = io.BytesIO()
        with pdfium.PdfDocument.new() as document:
            for _ in range(101): document.new_page(100, 100).close()
            document.save(output)
        with patch.object(visual, 'ocr', side_effect=AssertionError), self.assertRaises(ParseError) as caught:
            visual.pdf(output.getvalue(), Result())
        self.assertEqual(caught.exception.code, 'page_limit')

    def test_underreported_xlsx_dimension_does_not_lose_rows(self):
        from zipfile import ZipFile
        from portal.tests.intake_fixtures import package
        with ZipFile(io.BytesIO(sheet_fixture())) as archive:
            parts = {name: archive.read(name).replace(b'A1:D3', b'A1:A1') for name in archive.namelist()}
        result = Result(); xlsx(package(parts), result)
        self.assertEqual(len(result.items), 3)
        self.assertEqual(result.items[1]['name'], '控制柜')

    def test_external_relationship_not_fetched(self):
        rel = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" TargetMode="External" Target="https://example.invalid/private" Type="hyperlink"/></Relationships>'
        result = Result(); docx(word_fixture(extras={'word/_rels/document.xml.rels': rel}), result)
        self.assertIn('external_link_ignored', {w['code'] for w in result.warnings})
        self.assertEqual(result.finish()['status'], 'completed')


if __name__ == '__main__': unittest.main(verbosity=2)
