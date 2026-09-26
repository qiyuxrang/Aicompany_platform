import hashlib
import io
import tempfile
import json
import os
import subprocess
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
from zipfile import ZipFile

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from portal.hr_resume_extract import extract_text
from portal.hr_resume_storage import save_file, read_file
from portal.product_storage import StorageError


def docx(text='姓名：测试甲', extra=None):
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', (extra or {}).get('word/document.xml') or
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>SQL</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:body></w:document>')
        for name, value in (extra or {}).items():
            if name != 'word/document.xml':
                archive.writestr(name, value)
    return stream.getvalue()


class ResumeFileTests(SimpleTestCase):
    def test_txt_preserves_identity_and_docx_reads_table(self):
        text = '姓名：测试甲\n电话：13800000000\n技能：SQL'
        self.assertEqual(extract_text('resume.txt', text.encode()), text)
        self.assertEqual(extract_text('resume.docx', docx()), '姓名：测试甲\nSQL')

    def test_bad_inputs_fail_explicitly(self):
        cases = [('x.txt', b''), ('x.txt', b'\x00binary'), ('x.doc', b'old'),
                 ('../x.txt', b'text'), ('x.pdf', b'not-pdf'), ('x.docx', b'PKbad'),
                 ('x.txt', b'x' * (2 * 1024 * 1024 + 1)),
                 ('x.docx', docx(extra={'word/vbaProject.bin': b'macro'})),
                 ('x.docx', docx(extra={'word/_rels/document.xml.rels':
                    '<Relationships><Relationship TargetMode="External" Target="https://example.com"/></Relationships>'})),
                 ('x.docx', docx(extra={'word/document.xml': '<!DOCTYPE a [<!ENTITY a "x">]><a>&a;</a>'}))]
        for name, data in cases:
            with self.subTest(name=name), self.assertRaises(StorageError):
                extract_text(name, data)

    def test_real_pdf_text_extraction_with_existing_runtime(self):
        runtime = Path(settings.PRODUCT_DOCUMENT_PYTHON)
        if not runtime.is_file():
            self.skipTest('isolated document runtime unavailable')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'resume.pdf'
            subprocess.run([str(runtime), '-c',
                'import fitz,sys; d=fitz.open(); p=d.new_page(); '
                'p.insert_text((72,72),"Synthetic resume: SQL Python"); d.save(sys.argv[1])',
                str(target)], check=True, timeout=20)
            self.assertIn('SQL Python', extract_text('resume.pdf', target.read_bytes()))

    def test_pdf_process_does_not_inherit_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / 'python.exe'
            runtime.write_bytes(b'test')
            def run(command, **options):
                self.assertNotIn('PORTAL_MODEL_KEY_TEST', options['env'])
                self.assertNotIn('PORTAL_MODEL_GATEWAY_TOKEN', options['env'])
                Path(command[-1]).write_text(json.dumps({'text': 'resume'}), encoding='utf-8')
                return SimpleNamespace(returncode=0)
            with override_settings(PRODUCT_DOCUMENT_PYTHON=runtime), patch.dict(os.environ, {
                    'PORTAL_MODEL_KEY_TEST': 'test-secret', 'PORTAL_MODEL_GATEWAY_TOKEN': 'test-token'}), \
                    patch('portal.hr_resume_extract.subprocess.run', side_effect=run):
                self.assertEqual(extract_text('resume.pdf', b'%PDF-1.7 test'), 'resume')

    def test_private_file_hash_and_path_checks(self):
        with tempfile.TemporaryDirectory() as root, override_settings(HR_STORAGE_ROOT=Path(root)):
            stored = save_file('resume.txt', b'private')
            self.assertEqual(stored['sha256'], hashlib.sha256(b'private').hexdigest())
            self.assertEqual(read_file(stored['file_id'], stored['sha256']), b'private')
            for candidate in ('../outside', '/absolute', 'not-uuid'):
                with self.assertRaises(StorageError):
                    read_file(candidate, stored['sha256'])
            (Path(root) / stored['file_id']).write_bytes(b'changed')
            with self.assertRaises(StorageError):
                read_file(stored['file_id'], stored['sha256'])
