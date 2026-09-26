"""Boundary for untrusted rich uploads: bounded, credential-free subprocesses."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore

from django.conf import settings
from .source_parsers.core import EXTENSIONS, MEDIA, Result, VERSION, location_label

_SLOTS = BoundedSemaphore(2)
SCRIPT = Path(__file__).resolve().parent / "source_parsers" / "cli.py"


def extraction_hash(parsed):
    return hashlib.sha256(json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def summary(parsed):
    return {"status": parsed.get("status", "completed"), "method": parsed.get("method", "native"),
            "parser_version": parsed.get("parser_version", "legacy-text"), "block_count": len(parsed.get("blocks", [])) if 'blocks' in parsed else len(parsed.get('items', [])) or int(bool(parsed.get('background'))),
            "item_count": len(parsed.get("items", [])), "character_count": parsed.get("character_count", len(parsed.get('background', ''))),
            "truncated": parsed.get("truncated", False), "metadata": parsed.get("metadata", {}),
            "extraction_hash": extraction_hash(parsed)}


def source_text(parsed):
    return "\n\n".join(f"[{block.get('location_label', location_label(block.get('location', {})))} · {block['id']}]\n{block['text']}" for block in parsed.get("blocks", [])) or parsed.get("background", "")


def parser_environment():
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1", OMP_NUM_THREADS="2",
               OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2", PRODUCT_PARSER_OCR="1" if settings.PRODUCT_OCR_ENABLED else "0")
    return env


def run_parser(name, content, page=None):
    from .product_storage import StorageError, private_root
    runtime = Path(settings.PRODUCT_PARSER_PYTHON).resolve()
    if not runtime.is_file():
        raise StorageError("parser_unavailable", "解析环境尚未安装，请运行 scripts/setup-product-intake.ps1 后重试。")
    if not _SLOTS.acquire(blocking=False):
        raise StorageError("parser_busy", "当前资料解析繁忙，请稍后重试；文件尚未重复保存。")
    try:
        staging = private_root() / ".parsing"
        staging.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=staging) as directory:
            root = Path(directory)
            source = root / ("source" + Path(name).suffix.lower())
            output = root / ("preview.png" if page is not None else "result.json")
            source.write_bytes(content)
            command = [str(runtime), "-I", "-B", str(SCRIPT), str(source), str(output)]
            if page is not None: command.append(str(page))
            try:
                process = subprocess.run(command, cwd=root, env=parser_environment(), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=settings.PRODUCT_PARSE_TIMEOUT_SECONDS,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired as error:
                raise StorageError("parse_timeout", "解析超过时间上限，未保存不完整结果；请拆分文件或缩小扫描图片后重试。") from error
            except OSError as error:
                raise StorageError("parser_unavailable", "资料解析进程无法启动，请检查解析环境。") from error
            if not output.is_file() or output.stat().st_size > 8 * 1024 * 1024:
                raise StorageError("parse_failed", "未取得完整解析结果，请检查文件或拆分后重试。")
            if page is not None and process.returncode == 0:
                data = output.read_bytes()
                if not data.startswith(b"\x89PNG\r\n\x1a\n"): raise StorageError("invalid_preview", "预览生成失败。")
                return data
            try: data = json.loads(output.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError) as error: raise StorageError("parse_failed", "解析结果无效。") from error
            if process.returncode or not data.get("ok"):
                raise StorageError(data.get("code", "parse_failed"), data.get("detail", "资料解析失败。"))
            parsed = data.get("parsed")
            if not isinstance(parsed, dict) or not isinstance(parsed.get("blocks"), list) or len(parsed["blocks"]) > 5000:
                raise StorageError("parse_failed", "解析结果结构无效。")
            return parsed
    finally: _SLOTS.release()


def parse_rich(name, content):
    parsed = run_parser(name, content)
    return name, MEDIA[Path(name).suffix.lower()], content, parsed, parsed["warnings"]
