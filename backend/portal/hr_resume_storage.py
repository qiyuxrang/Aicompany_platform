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
        target = _path(file_id)
        target.unlink(missing_ok=True)
        target.with_suffix('.delete').unlink(missing_ok=True)
    except OSError:
        raise StorageError('storage_cleanup_failed', '简历临时文件清理失败，需要运维处理。') from None


def defer_file_removal(file_id):
    """Durable, content-free retry marker for a rolled-back upload only."""
    try:
        _path(file_id).with_suffix('.delete').write_text('', encoding='ascii')
    except OSError as error:
        # Surface this failure; the age-based orphan sweep remains a second path.
        raise StorageError('storage_cleanup_failed', '无法记录文件删除重试，请修复存储。') from error


def read_file(file_id, expected_hash):
    try:
        with _path(file_id).open('rb') as stream:
            content = stream.read(MAX_BYTES + 1)
    except OSError:
        raise StorageError('artifact_missing', '简历文件不可读取。') from None
    if len(content) > MAX_BYTES or hashlib.sha256(content).hexdigest() != expected_hash:
        raise StorageError('artifact_hash_mismatch', '简历完整性校验失败。')
    return content


def cleanup_orphan_files(edge, *, limit=100):
    """Reap old UUID files left by interrupted uploads/rollbacks, never live refs.

    At most limit unreferenced expired files are attempted; directory enumeration
    is read-only. Fresh in-flight upload files cannot be mistaken for orphans.
    """
    from .hr_screening_models import ResumeArtifact
    root = _root()
    result = {'removed': 0, 'failures': []}
    if not root.exists():
        return result
    attempted = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if attempted >= limit:
                    break
                name = entry.name
                deferred = name.endswith('.delete')
                identifier = name[:-7] if deferred else name[:-4] if name.endswith('.tmp') else name
                try:
                    if str(uuid.UUID(identifier)) != identifier:
                        continue
                except ValueError:
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    if not deferred and entry.stat(follow_symlinks=False).st_mtime > edge.timestamp():
                        continue
                    if ResumeArtifact.objects.filter(file_id=identifier).exists():
                        continue
                    attempted += 1
                    # Only canonical root-local UUID and UUID.tmp names; no paths from uploads.
                    if deferred:
                        remove_file(identifier)
                    else:
                        (root / name).unlink(missing_ok=True)
                    result['removed'] += 1
                except FileNotFoundError:
                    continue
                except (OSError, StorageError):
                    result['failures'].append({'kind': 'orphan_file', 'id': identifier,
                                               'code': 'storage_cleanup_failed'})
    except OSError as error:
        raise StorageError('storage_cleanup_failed', '招聘私有文件目录清理失败，需要重试。') from error
    return result
