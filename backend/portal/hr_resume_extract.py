"""Bounded local extraction; no model calls and no forced resume redaction."""
import io
import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from django.conf import settings

from .hr_resume_storage import validate_file
from .product_storage import StorageError

MAX_TEXT = 100000
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _xml(raw):
    upper = raw.upper()
    if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper:
        raise StorageError('invalid_file', '文档包含不支持的实体声明。')
    return ET.fromstring(raw)


def _docx(content):
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (len(entries) > 2000 or len(set(names)) != len(names)
                    or sum(entry.file_size for entry in entries) > 20 * 1024 * 1024
                    or '[Content_Types].xml' not in names or 'word/document.xml' not in names
                    or any('vba' in name.lower() or name.startswith('word/embeddings/') for name in names)):
                raise StorageError('invalid_file', 'DOCX 包无效、过大或包含嵌入内容。')
            for entry in entries:
                if entry.flag_bits & 1:
                    raise StorageError('invalid_file', '不支持加密文档。')
                if entry.filename.endswith('.rels'):
                    if entry.file_size > 1024 * 1024:
                        raise StorageError('invalid_file', '文档关系文件过大。')
                    relations = _xml(archive.read(entry))
                    if any(node.get('TargetMode') == 'External' for node in relations.iter()):
                        raise StorageError('invalid_file', '文档包含外部链接，请移除后上传。')
            if archive.getinfo('word/document.xml').file_size > 4 * 1024 * 1024:
                raise StorageError('invalid_file', '文档正文过大。')
            root = _xml(archive.read('word/document.xml'))
            paragraphs = [''.join(node.text or '' for node in para.iter(W + 't')) for para in root.iter(W + 'p')]
            return '\n'.join(text for text in paragraphs if text.strip())
    except (BadZipFile, ET.ParseError, KeyError, RuntimeError, ValueError):
        raise StorageError('invalid_file', 'DOCX 文件损坏或格式无效。') from None


def _pdf(content, *, pages=False):
    if not content.startswith(b'%PDF-'):
        raise StorageError('invalid_file', 'PDF 文件签名无效。')
    runtime = Path(settings.PRODUCT_DOCUMENT_PYTHON)
    if not runtime.is_absolute():
        runtime = settings.BASE_DIR / runtime
    if not runtime.is_file():
        raise StorageError('extractor_unavailable', 'PDF 文本运行时未配置。')
    script = Path(__file__).parent / 'hr_assets/extract_pdf.py'
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH'}}
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONNOUSERSITE='1')
    with tempfile.TemporaryDirectory(prefix='hr-extract-') as directory:
        source, output = Path(directory) / 'input.pdf', Path(directory) / 'text.json'
        source.write_bytes(content)
        try:
            command = [str(runtime), '-B', str(script), str(source), str(output)]
            if pages:
                command.append('--pages')
            result = subprocess.run(command,
                env=environment, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            limit = 16 * 1024 * 1024 if pages else 1024 * 1024
            if result.returncode or not output.is_file() or output.stat().st_size > limit:
                raise StorageError('invalid_file', 'PDF 无法解析、加密或超出限制。')
            return json.loads(output.read_text(encoding='utf-8'))['pages' if pages else 'text']
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
            raise StorageError('extractor_unavailable', 'PDF 提取失败或超时。') from None


def inspect_pdf(name, content):
    if validate_file(name, content) != '.pdf':
        raise StorageError('unsupported_file', '分页识别只接受 PDF。')
    return _pdf(content, pages=True)


def extract_text(name, content):
    suffix = validate_file(name, content)
    if suffix == '.txt':
        try:
            text = content.decode('utf-8-sig')
        except UnicodeError:
            raise StorageError('invalid_encoding', 'TXT 必须为 UTF-8。') from None
    else:
        text = _docx(content) if suffix == '.docx' else _pdf(content)
    if not isinstance(text, str) or '\x00' in text:
        raise StorageError('invalid_file', '简历正文格式无效。')
    if not text.strip():
        raise StorageError('text_unavailable', '无法提取文本；扫描件请转换为带文本的 PDF。')
    if len(text) > MAX_TEXT:
        raise StorageError('text_too_large', '简历正文超出限制，不进行截断。')
    return text
