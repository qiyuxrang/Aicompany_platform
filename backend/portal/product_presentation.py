"""Editable, source-bound business/technology PowerPoint draft."""

import hashlib
import json
import math
import os
import subprocess
import uuid
from pathlib import Path

from django.conf import settings

from .product_documents import DocumentError, _document_runtime, _safe_environment
from .product_storage import private_root

SCRIPT = Path(__file__).resolve().parent / "product_assets" / "simple_presentation.py"


def _valid_manifest(quality, pair):
    if not isinstance(quality, dict):
        return False
    text_fields = ("engine", "engine_version", "design_profile", "fact_boundary")
    counts = ("slides", "diagram_slides", "native_charts", "editable_data_visuals", "visual_asset_count",
              "source_blocks", "source_blocks_mapped")
    references = ("mapped_source_refs", "omitted_source_refs", "missing_source_refs")
    if (any(not isinstance(quality.get(key), str) or not 1 <= len(quality[key]) <= 500 for key in text_fields)
            or any(type(quality.get(key)) is not int or not 0 <= quality[key] <= 10000 for key in counts)
            or any(not isinstance(quality.get(key), list) or len(quality[key]) > 10000
                   or any(not isinstance(ref, str) or not 1 <= len(ref) <= 200 for ref in quality[key])
                   or len(set(quality[key])) != len(quality[key]) for key in references)):
        return False
    gate = quality.get("quality_gate")
    layouts = quality.get("layout_inventory")
    ratio = quality.get("dominant_layout_ratio")
    if (not isinstance(gate, dict) or gate.get("status") not in ("pass", "fail")
            or not isinstance(layouts, dict) or not layouts
            or any(not isinstance(key, str) or type(value) is not int or value < 0 for key, value in layouts.items())
            or not quality["slides"] or sum(layouts.values()) != quality["slides"]
            or type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 <= ratio <= 1
            or quality.get("source_mapping_scope") != "selected_summary_blocks"
            or quality.get("content_review") != "not_run"):
        return False
    all_refs = {block["ref"] for block in pair["blocks"]}
    eligible = {block["ref"] for block in pair["blocks"]
                if block.get("type") in {"heading", "paragraph", "table", "figure"}
                and not block["ref"].split(":", 1)[-1].startswith(("DRAFT_NOTICE", "PENDING_"))}
    mapped = set(quality["mapped_source_refs"])
    return (quality["source_blocks"] == len(eligible)
            and quality["source_blocks_mapped"] == len(eligible & mapped)
            and set(quality["omitted_source_refs"]) == eligible - mapped
            and set(quality["missing_source_refs"]) == mapped - all_refs)


def render_presentation_draft(task, pair):
    if pair.get("approval_inherited") is not False or not pair.get("blocks") or not pair.get("sources"):
        raise DocumentError("presentation_unavailable", diagnostic={"stage": "presentation_source", "reason": "invalid_source"})
    try:
        runtime = _document_runtime()
    except DocumentError:
        raise DocumentError("presentation_unavailable", diagnostic={"stage": "presentation_runtime", "reason": "runtime_missing"}) from None
    root = private_root().resolve()
    directory = root / str(task.pk) / "artifacts" / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    content = directory / "pair.json"
    content.write_text(json.dumps({"title": task.title, **pair}, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    output = directory / "draft.pptx"
    manifest = output.with_suffix(".manifest.json")
    try:
        result = subprocess.run([str(runtime), "-B", str(SCRIPT), str(content), str(output)],
                                cwd=directory, env=_safe_environment(), capture_output=True,
                                timeout=int(getattr(settings, "PRODUCT_DOCUMENT_RENDER_TIMEOUT_SECONDS", 300)),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise DocumentError("presentation_unavailable", diagnostic={"stage": "presentation_render", "reason": "timeout"}) from None
    except OSError:
        raise DocumentError("presentation_unavailable", diagnostic={"stage": "presentation_render", "reason": "launch_failed"}) from None
    if result.returncode:
        reason = "process_failed"
        stderr = result.stderr or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        known_errors = {"presentation source limit exceeded": "source_limit", "invalid slide source": "invalid_source",
                        "presentation quality gate failed": "quality_failed"}
        last_line = stderr.rstrip().splitlines()[-1] if stderr.strip() else ""
        for message, code in known_errors.items():
            if last_line == "ValueError: " + message:
                reason = code
                break
        raise DocumentError("presentation_unavailable", diagnostic={"stage": "presentation_render", "reason": reason, "returncode": result.returncode})
    if not output.is_file() or not manifest.is_file():
        raise DocumentError("presentation_unavailable", diagnostic={"stage": "presentation_manifest", "reason": "missing_output"})
    try:
        quality = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        raise DocumentError("presentation_unavailable", diagnostic={"stage": "presentation_manifest", "reason": "invalid_manifest"}) from None
    if not _valid_manifest(quality, pair):
        raise DocumentError("presentation_unavailable", diagnostic={"stage": "presentation_manifest", "reason": "invalid_manifest"})
    if quality["quality_gate"].get("status") != "pass" or quality["missing_source_refs"]:
        raise DocumentError("presentation_unavailable", diagnostic={"stage": "presentation_quality", "reason": "quality_failed"})
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
                                "source_mapping_scope": quality["source_mapping_scope"],
                                "omitted_source_blocks": len(quality["omitted_source_refs"]),
                                "content_review": quality["content_review"],
                                "fact_boundary": quality["fact_boundary"],
                                "pair_hash": pair["sha256"], "source_versions": pair["sources"],
                                "block_refs": [{"ref": block["ref"], "source_ids": block.get("source_ids", [])}
                                               for block in pair["blocks"] if block["ref"] in set(quality["mapped_source_refs"])],
                                "office_render": "not_run", "visual_review": "not_run", "business_approval": "blocked"}}
