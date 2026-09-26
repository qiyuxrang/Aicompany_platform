"""One file per subprocess: bounded input/output, no network or inherited secrets."""
import json
import os
from pathlib import Path
import socket
import sys

# Imported by file path with -I; only this trusted directory is added to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from source_parsers.core import LIMITS, LimitReached, ParseError, Result


def deny_network(*args, **kwargs):
    raise OSError("Parser network access is disabled")


def main():
    socket.socket = deny_network
    socket.create_connection = deny_network
    socket.getaddrinfo = deny_network
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    source, output = Path(sys.argv[1]), Path(sys.argv[2])
    suffix = source.suffix.lower()
    if source.stat().st_size > 20 * 1024 * 1024: raise ParseError("upload_too_large", "文件超过 20 MB。")
    content = source.read_bytes()
    result = Result()
    result.meta["ocr_enabled"] = os.environ.get("PRODUCT_PARSER_OCR", "1") == "1"
    try:
        if len(sys.argv) == 4:
            from source_parsers.visual import preview
            preview(content, suffix, int(sys.argv[3]), output)
            return
        if suffix == ".docx":
            from source_parsers.office import docx
            docx(content, result)
        elif suffix in {".xlsx", ".xls"}:
            from source_parsers.office import xlsx, xls
            (xlsx if suffix == ".xlsx" else xls)(content, result)
        elif suffix == ".pdf":
            from source_parsers.visual import pdf
            pdf(content, result)
        else:
            from source_parsers.visual import image
            image(content, result, suffix=suffix)
    except LimitReached: pass
    output.write_text(json.dumps({"ok": True, "parsed": result.finish()}, ensure_ascii=False, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    try: main()
    except Exception as error:
        failure = {"ok": False, "code": getattr(error, "code", "parse_failed"),
                   "detail": getattr(error, "detail", "无法解析该文件，请检查文件是否损坏或重新导出。")}
        Path(sys.argv[2]).write_text(json.dumps(failure, ensure_ascii=False), encoding="utf-8")
        sys.exit(2)
