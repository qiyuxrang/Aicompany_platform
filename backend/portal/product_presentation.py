"""Editable, source-bound business/technology PowerPoint draft."""

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
    manifest = output.with_suffix(".manifest.json")
    try:
        result = subprocess.run([str(_document_runtime()), "-B", str(SCRIPT), str(content), str(output)],
                                cwd=directory, env=_safe_environment(), capture_output=True,
                                timeout=int(getattr(settings, "PRODUCT_DOCUMENT_RENDER_TIMEOUT_SECONDS", 300)),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        raise DocumentError("presentation_unavailable") from None
    if result.returncode or not output.is_file() or not manifest.is_file():
        raise DocumentError("presentation_unavailable")
    try:
        quality = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise DocumentError("presentation_unavailable") from None
    if quality.get("quality_gate", {}).get("status") != "pass" or quality.get("missing_source_refs"):
        raise DocumentError("presentation_unavailable")
    return {"path": output.relative_to(root).as_posix(), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "template_hash": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            "render_evidence": {"kind": "draft", "status": "draft_unverified", "engine": quality["engine"],
                                "engine_version": quality["engine_version"], "design_profile": quality["design_profile"],
                                "quality_gate": quality["quality_gate"], "slides": quality["slides"],
                                "diagram_slides": quality["diagram_slides"], "native_charts": quality["native_charts"],
                                "editable_data_visuals": quality["editable_data_visuals"],
                                "visual_asset_count": quality.get("visual_asset_count", 0),
                                "layout_inventory": quality["layout_inventory"],
                                "dominant_layout_ratio": quality["dominant_layout_ratio"],
                                "source_block_coverage": {"total": quality["source_blocks"],
                                                          "mapped": quality["source_blocks_mapped"]},
                                "fact_boundary": quality["fact_boundary"],
                                "pair_hash": pair["sha256"], "source_versions": pair["sources"],
                                "block_refs": [{"ref": block["ref"], "source_ids": block.get("source_ids", [])} for block in pair["blocks"]],
                                "office_render": "not_run", "visual_review": "not_run", "business_approval": "blocked"}}
