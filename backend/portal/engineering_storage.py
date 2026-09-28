"""Private engineering files; optionally set PORTAL_ENGINEERING_STORAGE_ROOT."""
import hashlib
import os
import shutil
import uuid
import zipfile
from pathlib import Path

from django.conf import settings

from .product_storage import StorageError


DEFAULT_MAX_BYTES = 20 * 1024 * 1024
MAX_RESULT_BYTES = 200 * 1024 * 1024


def _configured(name, environment, default):
    value = getattr(settings, name, None)
    return value if value is not None else os.environ.get(environment, default)


def upload_max_bytes():
    try:
        value = int(_configured("ENGINEERING_UPLOAD_MAX_BYTES",
                                "PORTAL_ENGINEERING_UPLOAD_MAX_BYTES", DEFAULT_MAX_BYTES))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_BYTES
    return min(max(value, 1), DEFAULT_MAX_BYTES)


def private_root():
    value = _configured("ENGINEERING_STORAGE_ROOT", "PORTAL_ENGINEERING_STORAGE_ROOT",
                        settings.BASE_DIR / ".runtime" / "engineering-private")
    root = Path(value)
    return (root if root.is_absolute() else settings.BASE_DIR / root).resolve()


def _job_root(job_id):
    try:
        identifier = str(uuid.UUID(str(job_id)))
    except (ValueError, TypeError, AttributeError):
        raise StorageError("invalid_path", "工程任务文件标识无效。") from None
    root = private_root()
    target = (root / identifier).resolve()
    if not target.is_relative_to(root):
        raise StorageError("invalid_path", "工程任务存储路径无效。")
    return target


def _resolve_relative(relative):
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise StorageError("invalid_path", "工程任务文件路径无效。")
    path = Path(relative)
    if path.is_absolute():
        raise StorageError("invalid_path", "工程任务文件路径无效。")
    root = private_root()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise StorageError("invalid_path", "工程任务文件路径无效。")
    return target


def read_upload(upload):
    name = upload.name or ""
    if (not isinstance(name, str) or not name or len(name) > 200
            or Path(name).name != name or any(ord(char) < 32 for char in name)
            or any(char in name for char in "/\\:")):
        raise StorageError("invalid_filename", "工程清单文件名无效。")
    if Path(name).suffix.lower() != ".xlsx":
        raise StorageError("unsupported_file", "仅支持 .xlsx 工程清单。")
    limit = upload_max_bytes()
    if getattr(upload, "size", 0) > limit:
        raise StorageError("upload_too_large", "单份工程清单不得超过 20MiB。")
    chunks = []
    size = 0
    for chunk in upload.chunks(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise StorageError("upload_too_large", "单份工程清单不得超过 20MiB。")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise StorageError("invalid_file", "工程清单不能为空。")
    return name, content


def save_inputs(job_id, files):
    directory = _job_root(job_id) / "inputs"
    try:
        directory.mkdir(parents=True, exist_ok=False)
        stored = []
        for name, content in files:
            target = directory / f"{uuid.uuid4()}.xlsx"
            temporary = target.with_suffix(".tmp")
            try:
                with temporary.open("xb") as stream:
                    stream.write(content)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            stored.append({
                "name": name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "storage_path": target.relative_to(private_root()).as_posix(),
            })
        return stored
    except (OSError, FileExistsError) as error:
        raise StorageError("storage_unavailable", "工程私有存储写入失败。") from error


def remove_job(job_id):
    target = _job_root(job_id)
    if not target.exists():
        return
    try:
        shutil.rmtree(target)
    except OSError as error:
        raise StorageError("storage_cleanup_failed", "工程私有文件清理失败，需要运维处理。") from error


def verified_input(item):
    try:
        target = _resolve_relative(item["storage_path"])
        expected_hash = item["sha256"]
        expected_size = item["size"]
    except (KeyError, TypeError):
        raise StorageError("invalid_input_record", "工程任务输入记录无效。") from None
    digest = hashlib.sha256()
    size = 0
    try:
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > upload_max_bytes():
                    raise StorageError("input_too_large", "工程任务输入文件超过限制。")
                digest.update(chunk)
    except OSError:
        raise StorageError("input_missing", "工程任务输入文件不可读取。") from None
    if size != expected_size or digest.hexdigest() != expected_hash:
        raise StorageError("input_hash_mismatch", "工程任务输入文件完整性校验失败。")
    return target


def work_directory(job_id, fence):
    job_root = _job_root(job_id)
    target = job_root / "work" / str(fence)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise StorageError("storage_unavailable", "工程任务工作目录不可用。") from error
    resolved = target.resolve()
    if not resolved.is_relative_to(job_root):
        raise StorageError("invalid_path", "工程任务工作目录无效。")
    return resolved


def verified_cli_output(raw_path, expected_hash, work_root):
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise StorageError("invalid_result", "测算程序返回了无效成果路径。")
    target = Path(raw_path).resolve()
    root = Path(work_root).resolve()
    if not target.is_relative_to(root) or target.suffix.lower() != ".xlsx" or not target.is_file():
        raise StorageError("invalid_result", "测算程序成果不在受控工作目录中。")
    digest = hashlib.sha256()
    size = 0
    try:
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > MAX_RESULT_BYTES:
                    raise StorageError("result_too_large", "测算成果超过受控大小。")
                digest.update(chunk)
    except OSError:
        raise StorageError("result_missing", "测算成果文件不可读取。") from None
    if digest.hexdigest() != expected_hash:
        raise StorageError("result_hash_mismatch", "测算成果完整性校验失败。")
    return target


def persist_result(job_id, fence, sources):
    if not 1 <= len(sources) <= 2:
        raise StorageError("invalid_result", "测算成果数量与输入不一致。")
    job_root = _job_root(job_id)
    result_dir = job_root / "results"
    try:
        result_dir.mkdir(parents=True, exist_ok=True)
        result_dir = result_dir.resolve()
        if not result_dir.is_relative_to(job_root):
            raise StorageError("invalid_path", "测算成果存储路径无效。")
        if len(sources) == 1:
            filename = "成本测算内部草稿.xlsx"
            target = result_dir / f"internal-draft-{fence}.xlsx"
            temporary = target.with_suffix(".tmp")
            with sources[0].open("rb") as source, temporary.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
        else:
            filename = "成本测算内部草稿.zip"
            target = result_dir / f"internal-drafts-{fence}.zip"
            temporary = target.with_suffix(".tmp")
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
                for index, source in enumerate(sources, start=1):
                    archive.write(source, f"{index}-成本测算内部草稿.xlsx")
        os.replace(temporary, target)
    except OSError as error:
        raise StorageError("storage_unavailable", "测算成果写入私有存储失败。") from error
    finally:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise StorageError("storage_unavailable", "测算成果写入私有存储失败。") from error
    return {
        "path": target.relative_to(private_root()).as_posix(),
        "filename": filename,
        "sha256": digest.hexdigest(),
        "size": size,
    }


def verified_result(job):
    target = _resolve_relative(job.result_path)
    digest = hashlib.sha256()
    size = 0
    try:
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
    except OSError:
        raise StorageError("result_missing", "测算成果文件不可读取。") from None
    if size != job.result_size or digest.hexdigest() != job.result_sha256:
        raise StorageError("result_hash_mismatch", "测算成果完整性校验失败。")
    return target
