"""Parser contract and format-independent resource/OOXML guards."""
import io
import math
import re
from pathlib import PurePosixPath
from zipfile import ZipFile, BadZipFile

VERSION = "product-intake-1"
EXTENSIONS = (".csv", ".txt", ".pdf", ".docx", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
MEDIA = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xls": "application/vnd.ms-excel", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff"}
LIMITS = {"pages": 100, "ocr_pages": 12, "characters": 200000, "blocks": 5000, "rows": 10000, "columns": 100, "cells": 100000, "pixels": 24000000}


class ParseError(Exception):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(detail)


class LimitReached(Exception):
    pass


def location_label(location):
    parts = []
    if location.get("page"): parts.append(f"第 {location['page']} 页")
    if location.get("part"): parts.append(str(location["part"]))
    if location.get("sheet"): parts.append(f"工作表 {location['sheet']}")
    if location.get("range"): parts.append(location["range"])
    elif location.get("row"): parts.append(f"第 {location['row']} 行")
    if location.get("table"): parts.append(f"表格 {location['table']}")
    if location.get("paragraph"): parts.append(f"段落 {location['paragraph']}")
    if location.get("image"): parts.append(f"图片 {location['image']}")
    return " · ".join(parts) or "原文"


class Result:
    def __init__(self):
        self.blocks, self.items, self.warnings = [], [], []
        self.characters = 0
        self.ocr_count = 0
        self.truncated = False
        self.meta = {}

    def warn(self, code, detail, location=None, severity="review"):
        record = {"code": code, "detail": detail, "location": location or {}, "severity": severity}
        if record not in self.warnings and len(self.warnings) < 99:
            self.warnings.append(record)
        elif len(self.warnings) == 99:
            self.warnings.append({"code": "warnings_limited", "detail": "提示较多，仅展示前 99 项；请结合原文件逐项核对。", "location": {}, "severity": "review"})

    def add(self, text, location, kind="paragraph", **extra):
        text = str(text).replace("\x00", "").strip()
        if not text: return None
        if len(self.blocks) >= LIMITS["blocks"] or self.characters + len(text) > LIMITS["characters"]:
            self.truncated = True
            self.warn("extraction_limit", "已达到解析文本或内容块上限，剩余内容未提取；请拆分文件补充。", location, "partial")
            raise LimitReached()
        block = {"id": f"b{len(self.blocks) + 1}", "kind": kind, "text": text, "location": location,
                 "location_label": location_label(location), **extra}
        self.blocks.append(block)
        self.characters += len(text)
        return block

    def finish(self):
        ocr = any(block["kind"] == "ocr" for block in self.blocks)
        native = any(block["kind"] != "ocr" for block in self.blocks)
        if not self.blocks:
            self.warn("no_text", "未提取到可用文字。原文件已保留，请核对扫描清晰度或补充文字资料。")
        status = "failed" if not self.blocks else "partial" if self.truncated or any(w["severity"] == "partial" for w in self.warnings) else "needs_review" if any(w["severity"] == "review" for w in self.warnings) else "completed"
        return {"schema_version": 1, "parser_version": VERSION, "status": status,
                "method": "mixed" if ocr and native else "ocr" if ocr else "native",
                "blocks": self.blocks, "items": self.items, "warnings": self.warnings,
                "character_count": self.characters, "block_count": len(self.blocks),
                "truncated": self.truncated, "metadata": self.meta}


def safe_zip(content, required):
    from defusedxml import ElementTree as ET
    try:
        archive = ZipFile(io.BytesIO(content))
        entries = archive.infolist()
        if len(entries) > 1500: raise ParseError("archive_limit", "文档内部文件过多，请拆分文档。")
        total = 0
        names = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if entry.filename in names or path.is_absolute() or ".." in path.parts or "\\" in entry.filename or "\x00" in entry.filename or entry.flag_bits & 1:
                raise ParseError("unsafe_archive", "文档包含无效路径、重复条目或加密内容。")
            names.add(entry.filename)
            total += entry.file_size
            if entry.file_size > 16 * 1024 * 1024 or total > 64 * 1024 * 1024 or (entry.file_size > 1024 * 1024 and entry.file_size > 300 * max(1, entry.compress_size)):
                raise ParseError("archive_limit", "文档展开体积或压缩比超过安全上限。")
            if "vbaproject" in entry.filename.lower() or "/embeddings/" in entry.filename.lower() or entry.filename.endswith(".exe"):
                raise ParseError("active_content", "文档包含宏或嵌入执行对象，请移除后另存为普通文档。")
        if required not in names or "[Content_Types].xml" not in names:
            raise ParseError("format_mismatch", "文件内容与扩展名不一致，请重新导出文档。")
        # All XML, including relationships and shared strings, passes a DTD/entity
        # rejecting parser before any third-party OOXML reader sees the package.
        for name in names:
            if name.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(name), forbid_dtd=True, forbid_entities=True, forbid_external=True)
        return archive
    except ParseError:
        if 'archive' in locals(): archive.close()
        raise
    except Exception as error:
        if 'archive' in locals(): archive.close()
        raise ParseError("invalid_document", "文档损坏、包含不安全 XML 或不属于受支持的 Office 格式。") from error


ALIASES = {"row_id": {"序号", "顺序", "行号", "编号", "row_id"}, "name": {"设备名称", "设备材料名称", "设备名", "材料名称", "名称", "name"}, "quantity": {"数量", "设备数量", "quantity"}, "unit": {"单位", "计量单位", "unit"}}


def table_items(result, rows):
    """Promote only explicit equipment table headers; never infer OCR columns."""
    columns = None
    seen = set()
    for block in rows:
        cells = block["cells"]
        values = [str(value).strip() for value in cells]
        headers = {key: [i for i, value in enumerate(values) if re.sub(r"\s", "", value) in aliases] for key, aliases in ALIASES.items()}
        if headers["name"] and headers["quantity"]:
            if any(len(indexes) > 1 for indexes in headers.values()):
                result.warn("ambiguous_table_header", "表头包含重名字段，未自动转为设备清单。", block["location"])
                columns = None
            else:
                columns = {key: indexes[0] if indexes else None for key, indexes in headers.items()}
            continue
        if columns is None: continue
        if values and values[0] in {"合计", "总计", "小计", "说明", "备注"}:
            columns = None
            continue
        def cell(key):
            index = columns[key]
            value = values[index] if index is not None and index < len(values) else ""
            # Formulas/cell errors remain in the source block, not factual quantities.
            return "" if value.startswith("=") or value in {"#REF!", "#N/A", "#VALUE!", "#DIV/0!"} else value
        item = {key: cell(key) for key in ALIASES}
        if (item['name'] and not any(item[key] for key in ('row_id', 'quantity', 'unit'))
                and re.match(r'^(?:[（(][一二三四五六七八九十百0-9]+[）)]|[一二三四五六七八九十百]+[、.．])', item['name'])):
            seen.clear()
            continue
        item.update(source_row=block["location"].get("row", 0), source_location=block["location"], block_id=block["id"])
        if not any(item[key] for key in ALIASES): continue
        for key in ("name", "quantity", "unit"):
            if not item[key]: result.warn("missing_" + key, {"name": "设备名称缺失", "quantity": "设备数量缺失或需核对公式", "unit": "设备单位缺失"}[key], block["location"])
        if item["row_id"] and item["row_id"] in seen: result.warn("duplicate_row_id", "本表原始行号重复，已保留并使用独立来源标识。", block["location"])
        if item["row_id"]: seen.add(item["row_id"])
        try:
            if item["quantity"] and (not math.isfinite(float(item["quantity"])) or float(item["quantity"]) < 0): raise ValueError
        except (ValueError, OverflowError):
            result.warn("invalid_quantity", "数量不是有效的非负数，请核对原单元格。", block["location"])
        result.items.append(item)
