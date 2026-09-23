"""Editable, source-bound simplified PowerPoint draft; not PPT Master."""

import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

from django.conf import settings

from .product_documents import DocumentError, _document_runtime, _safe_environment
from .product_storage import private_root

SCRIPT = Path(__file__).resolve().parent / "product_assets" / "simple_presentation.py"


def render_presentation_draft(task, pair):
    if pair.get("approval_inherited") is not False or not pair.get("blocks") or not pair.get("sources"):
        raise DocumentError("presentation_unavailable")
    root = private_root().resolve()
    directory = root / str(task.pk) / "artifacts" / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    content = directory / "pair.json"
    content.write_text(json.dumps({"title": task.title, **pair}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    output = directory / "draft.pptx"
    try:
        result = subprocess.run([str(_document_runtime()), "-B", str(SCRIPT), str(content), str(output)],
                                cwd=directory, env=_safe_environment(), capture_output=True, timeout=90,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        raise DocumentError("presentation_unavailable") from None
    if result.returncode or not output.is_file():
        raise DocumentError("presentation_unavailable")
    return {"path": output.relative_to(root).as_posix(), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "template_hash": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            "render_evidence": {"kind": "draft", "status": "draft_unverified", "engine": "python-pptx-simple-draft",
                                "pair_hash": pair["sha256"], "source_versions": pair["sources"],
                                "office_render": "not_run", "visual_review": "not_run", "business_approval": "blocked"}}
