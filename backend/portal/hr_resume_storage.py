"""Private resume bytes. Callers must authorize the owning artifact first."""
import hashlib
import os
import uuid
from pathlib import Path

from django.conf import settings

from .product_storage import StorageError

MAX_BYTES = 2 * 1024 * 1024


def validate_file(name, content):
    if (not isinstance(name, str) or not name or len(name) > 200
            or any(ord(char) < 32 for char in name) or any(char in name for char in '/\\:')):
        raise StorageError('invalid_filename', '简历文件名无效。')
    suffix = Path(name).suffix.lower()
    if suffix not in {'.txt', '.docx', '.pdf'}:
        raise StorageError('unsupported_file', '仅支持 TXT、DOCX 和含文本的 PDF。')
    if not isinstance(content, bytes) or not content:
        raise StorageError('invalid_file', '文件为空或格式无效。')
    if len(content) > MAX_BYTES:
        raise StorageError('upload_too_large', '单份简历不得超过 2MiB。')
    return suffix


def _root():
    root = Path(getattr(settings, 'HR_STORAGE_ROOT', settings.BASE_DIR / '.runtime/hr-private'))
    return (root if root.is_absolute() else settings.BASE_DIR / root).resolve()


def _path(file_id):
    try:
        if str(uuid.UUID(str(file_id))) != file_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise StorageError('invalid_path', '简历文件标识无效。') from None
    root = _root()
    target = (root / file_id).resolve()
    if not target.is_relative_to(root):
        raise StorageError('invalid_path', '简历文件路径无效。')
    return target


def save_file(name, content):
    validate_file(name, content)
    file_id = str(uuid.uuid4())
    target = _path(file_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(content)
        os.replace(temporary, target)
    except OSError:
        raise StorageError('storage_unavailable', '简历存储失败。') from None
    finally:
        temporary.unlink(missing_ok=True)
    return {'file_id': file_id, 'sha256': hashlib.sha256(content).hexdigest(),
            'size': len(content), 'filename': name, 'version': 1}


def remove_file(file_id):
    try:
        _path(file_id).unlink(missing_ok=True)
    except OSError:
        raise StorageError('storage_cleanup_failed', '简历临时文件清理失败，需要运维处理。') from None


def read_file(file_id, expected_hash):
    try:
        with _path(file_id).open('rb') as stream:
            content = stream.read(MAX_BYTES + 1)
    except OSError:
        raise StorageError('artifact_missing', '简历文件不可读取。') from None
    if len(content) > MAX_BYTES or hashlib.sha256(content).hexdigest() != expected_hash:
        raise StorageError('artifact_hash_mismatch', '简历完整性校验失败。')
    return content
