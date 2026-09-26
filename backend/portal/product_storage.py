import csv
import hashlib
import io
import os
import uuid
from pathlib import Path

from django.conf import settings
from .source_parsers.core import EXTENSIONS


class StorageError(Exception):
    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def private_root():
    root = Path(getattr(settings, "PRODUCT_STORAGE_ROOT", settings.BASE_DIR / ".runtime" / "product-private"))
    return root if root.is_absolute() else settings.BASE_DIR / root


def parse_upload(upload):
    name = upload.name or ""
    if not name or "\x00" in name or Path(name).name != name or "/" in name or "\\" in name:
        raise StorageError("invalid_filename", "文件名无效。")
    suffix = Path(name).suffix.lower()
    if suffix not in EXTENSIONS:
        raise StorageError("unsupported_file", "支持 PDF、DOCX、XLSX/XLS、CSV/TXT 和常见图片；旧 DOC 或含宏文件请另存为 DOCX/XLSX。")
    limit = int(getattr(settings, "PRODUCT_UPLOAD_MAX_BYTES", 1024 * 1024))
    content = upload.read(limit + 1)
    if len(content) > limit:
        raise StorageError("upload_too_large", "文件超过允许大小。")
    return parse_source_content(name, content)


def parse_source_content(name, content):
    suffix = Path(name).suffix.lower()
    if suffix not in EXTENSIONS:
        raise StorageError("unsupported_file", "该文件格式不受支持，请使用 PDF、DOCX、XLSX/XLS 或常见图片。")
    if len(content) > int(getattr(settings, "PRODUCT_UPLOAD_MAX_BYTES", 20 * 1024 * 1024)):
        raise StorageError("upload_too_large", "文件超过允许大小。")
    if suffix not in {".csv", ".txt"}:
        from .product_intake import parse_rich
        if not content: raise StorageError("invalid_file", "文件内容为空。")
        return parse_rich(name, content)
    if not content or b"\x00" in content:
        raise StorageError("invalid_file", "文件为空或不是文本文件。")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise StorageError("invalid_encoding", "文件必须使用 UTF-8 编码。") from error
    if suffix == ".txt":
        return name, "text/plain", content, {"background": text}, []
    return name, "text/csv", content, {"items": _parse_csv(text)}, _csv_warnings(text)


def read_import_path(value):
    if not isinstance(value, str) or not value or len(value) > 2000 or "\x00" in value:
        raise StorageError("invalid_import_path", "文件路径无效。")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise StorageError("invalid_import_path", "文件路径必须是绝对路径。")
    roots = [Path(root).resolve() for root in getattr(settings, "PRODUCT_IMPORT_ROOTS", ())]
    try:
        target = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise StorageError("import_path_missing", "文件路径不存在。") from error
    if not roots or not any(target.is_relative_to(root) for root in roots):
        raise StorageError("import_path_not_allowed", "文件路径不在允许的导入目录中。")
    if not target.is_file():
        raise StorageError("import_path_not_file", "路径必须指向文件。")
    limit = int(getattr(settings, "PRODUCT_UPLOAD_MAX_BYTES", 1024 * 1024))
    try:
        if target.stat().st_size > limit:
            raise StorageError("upload_too_large", "文件超过允许大小。")
        with target.open("rb") as stream:
            content = stream.read(limit + 1)
    except OSError as error:
        raise StorageError("import_path_unreadable", "文件路径无法读取。") from error
    if len(content) > limit:
        raise StorageError("upload_too_large", "文件超过允许大小。")
    return parse_source_content(target.name, content)


def _csv_rows(text):
    try:
        return list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as error:
        raise StorageError("invalid_csv", "CSV 格式无效。") from error


def _parse_csv(text):
    rows = _csv_rows(text)
    if not rows:
        raise StorageError("invalid_csv", "CSV 缺少表头。")
    aliases = {
        "row_id": {"row_id", "序号", "行号"},
        "name": {"name", "设备名称", "名称"},
        "quantity": {"quantity", "数量"},
        "unit": {"unit", "单位"},
    }
    headers = [cell.strip() for cell in rows[0]]
    columns = {key: next((index for index, value in enumerate(headers) if value in names), None) for key, names in aliases.items()}
    if columns["name"] is None:
        raise StorageError("invalid_csv", "CSV 必须包含设备名称列。")
    items = []
    for row_index, row in enumerate(rows[1:], start=2):
        def cell(key):
            index = columns[key]
            return row[index].strip() if index is not None and index < len(row) else ""
        if not any(value.strip() for value in row):
            continue
        items.append({
            "row_id": cell("row_id"),
            "name": cell("name"),
            "quantity": cell("quantity"),
            "unit": cell("unit"),
            "source_row": row_index,
        })
    return items


def _csv_warnings(text):
    items = _parse_csv(text)
    warnings = []
    seen = set()
    for item in items:
        row_id = item["row_id"]
        if row_id and row_id in seen:
            warnings.append({"code": "duplicate_row_id", "row": item["source_row"]})
        if row_id:
            seen.add(row_id)
        if not item["unit"]:
            warnings.append({"code": "missing_unit", "row": item["source_row"]})
        if not item["name"]:
            warnings.append({"code": "continuation_or_missing_name", "row": item["source_row"]})
    return warnings


def write_source(task_id, original_name, content):
    source_id = uuid.uuid4()
    suffix = Path(original_name).suffix.lower()
    relative = Path(str(task_id)) / "sources" / f"{source_id}{suffix}"
    target = (private_root() / relative).resolve()
    root = private_root().resolve()
    if not target.is_relative_to(root):
        raise StorageError("invalid_path", "存储路径无效。")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return source_id, relative.as_posix(), hashlib.sha256(content).hexdigest()


def remove_relative(relative):
    try:
        resolve_relative(relative).unlink(missing_ok=True)
    except StorageError:
        pass


def resolve_relative(relative):
    path = Path(relative)
    if path.is_absolute() or "\x00" in str(relative):
        raise StorageError("invalid_path", "文件路径无效。")
    root = private_root().resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise StorageError("invalid_path", "文件路径无效。")
    return target


def verified_artifact(artifact):
    target = resolve_relative(artifact.path)
    if not target.is_file():
        raise StorageError("artifact_missing", "成果文件不存在。")
    checksum = hashlib.sha256()
    with target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    if checksum.hexdigest() != artifact.sha256:
        raise StorageError("artifact_hash_mismatch", "成果文件完整性校验失败。")
    return target
