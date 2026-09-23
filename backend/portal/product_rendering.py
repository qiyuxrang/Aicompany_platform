import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

from django.conf import settings

from .product_documents import PACK, DocumentError, _document_runtime, _safe_environment, frozen_pack
from .product_storage import private_root


def _sha256(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _private_docx(artifact_path, artifact_sha256):
    if not isinstance(artifact_path, (str, os.PathLike)) or not isinstance(artifact_sha256, str):
        raise DocumentError("invalid_artifact_path")
    relative = Path(os.fspath(artifact_path))
    expected = artifact_sha256.lower()
    if (relative.is_absolute() or not relative.parts or ".." in relative.parts or "\x00" in str(relative)
            or relative.suffix.lower() != ".docx" or len(expected) != 64
            or any(character not in "0123456789abcdef" for character in expected)):
        raise DocumentError("invalid_artifact_path")
    root = private_root().resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise DocumentError("invalid_artifact_path")
    if not target.is_file():
        raise DocumentError("artifact_missing")
    actual = _sha256(target)
    if actual != expected:
        raise DocumentError("artifact_hash_mismatch")
    return root, target, actual


def _unchanged(target, expected):
    if not target.is_file() or _sha256(target) != expected:
        raise DocumentError("artifact_mutated")


def _report(directory):
    try:
        value = json.loads((directory / "render.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise DocumentError("office_render_invalid_output") from None
    if not isinstance(value, dict):
        raise DocumentError("office_render_invalid_output")
    return value


def _output(path, signature):
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise DocumentError("office_render_invalid_output")
    with path.open("rb") as stream:
        if stream.read(len(signature)) != signature:
            raise DocumentError("office_render_invalid_output")
    return _sha256(path)


def _relative_evidence(root, path, sha256):
    target = path.resolve()
    if not target.is_relative_to(root):
        raise DocumentError("office_render_invalid_output")
    return {"path": target.relative_to(root).as_posix(), "sha256": sha256}


def render_office(artifact_path, artifact_sha256):
    if not getattr(settings, "PRODUCT_OFFICE_RENDER_ENABLED", False):
        raise DocumentError("office_render_disabled")
    try:
        manifest = frozen_pack()
        runtime = _document_runtime()
    except DocumentError:
        raise DocumentError("office_render_unavailable") from None
    root, source, source_sha256 = _private_docx(artifact_path, artifact_sha256)
    parent = (root / "office-renders").resolve()
    if not parent.is_relative_to(root):
        raise DocumentError("office_render_unavailable")
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / uuid.uuid4().hex
    command = [str(runtime), "-B", str(PACK / "scripts" / "office_render.py"), "word", str(source), str(directory), "--timeout", "90"]
    try:
        result = subprocess.run(
            command, cwd=parent, env=_safe_environment(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=100,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        _unchanged(source, source_sha256)
        raise DocumentError("office_render_timeout") from None
    except OSError:
        _unchanged(source, source_sha256)
        raise DocumentError("office_render_unavailable") from None
    _unchanged(source, source_sha256)
    if result.returncode != 0:
        try:
            report = _report(directory)
        except DocumentError:
            raise DocumentError("office_render_failed") from None
        failure = json.dumps(report.get("unverified_items", []), ensure_ascii=False).lower()
        raise DocumentError("office_render_timeout" if "timed out" in failure else "office_render_failed")
    report = _report(directory)
    if report.get("rendered") is not True or report.get("structural_pass") is not True:
        raise DocumentError("office_render_failed")
    page_count = report.get("page_count")
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
        raise DocumentError("office_render_invalid_output")
    pdf = directory / "document.pdf"
    rendered_docx = directory / "reviewed.docx"
    pages = [directory / f"page-{index:03}.png" for index in range(1, page_count + 1)]
    if set(pages) != set(directory.glob("page-*.png")):
        raise DocumentError("office_render_invalid_output")
    pdf_sha256 = _output(pdf, b"%PDF-")
    rendered_docx_sha256 = _output(rendered_docx, b"PK")
    page_hashes = [_output(page, b"\x89PNG\r\n\x1a\n") for page in pages]
    if report.get("input_sha256") != source_sha256:
        raise DocumentError("office_render_invalid_output")
    if report.get("pdf_sha256") not in (None, pdf_sha256) or report.get("rendered_docx_sha256") not in (None, rendered_docx_sha256):
        raise DocumentError("office_render_invalid_output")
    office_script = next(entry for entry in manifest["files"] if entry["path"] == "scripts/office_render.py")
    return {
        "status": "rendered",
        "verified": False,
        "docx_sha256": rendered_docx_sha256,
        "generation_sha256": source_sha256,
        "generation": _relative_evidence(root, source, source_sha256),
        "page_count": page_count,
        "pdf": _relative_evidence(root, pdf, pdf_sha256),
        "pages": [{"page": index, **_relative_evidence(root, page, sha256)}
                  for index, (page, sha256) in enumerate(zip(pages, page_hashes), start=1)],
        "rendered_docx": {
            **_relative_evidence(root, rendered_docx, rendered_docx_sha256),
            "differs_from_input": rendered_docx_sha256 != source_sha256,
        },
        "visual_review": "not_run",
        "office_render_script_sha256": office_script["sha256"],
    }
