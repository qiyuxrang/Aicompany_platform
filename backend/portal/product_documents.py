import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .product_storage import private_root
from .product_docx_layout import normalize_docx_layout
from .product_formal import has_repeated_filler, unsupported_financial_conclusion
from .product_diagrams import (
    SUBSECTION_TITLES,
    add_structural_blocks,
    inspect_docx_requirements,
    validate_document_requirements,
)


PACK = Path(__file__).resolve().parent / "product_assets" / "bj_docs"


class DocumentError(Exception):
    def __init__(self, code, *, diagnostic=None):
        self.code = code
        self.diagnostic = diagnostic or {}
        super().__init__(code)


def _company_name():
    try:
        policy = json.loads((PACK / "assets" / "document-format-policy.json").read_text(encoding="utf-8"))
        value = policy["company_identity"]["name"].strip()
        if not value or len(value) > 200:
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError):
        raise DocumentError("template_unavailable") from None


def _family_document_title(title, family):
    title = title.strip()
    # Workflow state belongs in the platform, not in the delivered document title.
    title = re.sub(r"[（(](?:代表性)?(?:草稿|待审稿)[）)]\s*$", "", title).strip()
    suffix = "技术方案" if family == "technical-solution" else "可行性研究报告"
    if suffix in title:
        return title
    if "三件套" in title:
        return title.replace("三件套", suffix, 1)
    return f"{title}（{suffix}）"


def frozen_pack(family="technical-solution"):
    if family not in {"technical-solution", "feasibility"}:
        raise DocumentError("template_unavailable")
    try:
        manifest = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            target = (PACK / entry["path"]).resolve()
            if not target.is_relative_to(PACK.resolve()) or hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
                raise DocumentError("template_unavailable")
        return manifest
    except (OSError, ValueError, KeyError, TypeError):
        raise DocumentError("template_unavailable") from None


def content_document(task, input_revision, blueprint, chapters, family="technical-solution"):
    if family not in {"technical-solution", "feasibility"}:
        raise DocumentError("template_unavailable")
    if blueprint.payload.get("template_version") != "frozen-original-v1":
        raise DocumentError("template_unavailable")
    facts = input_revision.payload
    source_map = {}
    sources = [{"id": "SINPUT", "text": f"输入版本{input_revision.pk}；SHA256={input_revision.sha256}；人工资料，真实性待审核。"}]
    for index, item in enumerate([*facts.get("items", []), *facts.get("sources", []), *facts.get("knowledge_sources", [])], start=1):
        source_id = f"S{index}"
        original_id = str(item.get("id", item.get("row_id", "")))
        source_map.setdefault(original_id, []).append(source_id)
        sources.append({"id": source_id, "text": json.dumps(item, ensure_ascii=False, allow_nan=False)})
    conditions = facts.get("conditions", [])
    requirements = [{"id": f"R{index}", "text": value} for index, value in enumerate(conditions, start=1)]
    if facts.get("requirements", "").strip():
        requirements.append({"id": "RINPUT", "text": facts["requirements"]})
    requirement_ids = [item["id"] for item in requirements]
    blocks = []
    chapter_count = len(chapters)
    omitted_financial_claims = False
    for chapter_index, chapter in enumerate(chapters):
        payload = chapter.payload
        stable = "C" + hashlib.sha256(payload["chapter_id"].encode()).hexdigest()[:24]
        cited = [mapped for original in payload["source_ids"] for mapped in source_map.get(original, [])] or ["SINPUT"]
        blocks.append({"id": stable, "type": "heading", "level": 1, "text": payload["title"], "source_ids": cited, "requirement_ids": requirement_ids})
        for index, paragraph in enumerate(payload["paragraphs"], start=1):
            if family == "feasibility" and unsupported_financial_conclusion(paragraph):
                omitted_financial_claims = True
                continue
            subsection = SUBSECTION_TITLES[family][(index - 1) % len(SUBSECTION_TITLES[family])]
            blocks.append({"id": f"{stable}S{index}", "type": "heading", "level": 2, "text": subsection,
                           "source_ids": cited, "requirement_ids": requirement_ids})
            blocks.append({"id": f"{stable}P{index}", "type": "paragraph", "text": paragraph, "source_ids": cited, "requirement_ids": requirement_ids})
        add_structural_blocks(
            blocks,
            family,
            cited,
            requirement_ids,
            chapter_index=chapter_index,
            chapter_count=chapter_count,
        )
    if facts.get("items"):
        rows = [[str(item.get(key, "")).strip() or "待确认" for key in ("row_id", "name", "quantity", "unit")]
                + [str(item.get("source_id", "人工录入"))] for item in facts["items"]]
        if family == "feasibility":
            for index, row in enumerate(rows, start=1):
                blocks.append({"id": f"INPUT_ITEM{index}", "type": "paragraph",
                               "text": f"输入清单：{row[1]}，数量 {row[2]} {row[3]}；原行号 {row[0]}；来源 {row[4]}。",
                               "source_ids": ["SINPUT"], "requirement_ids": requirement_ids})
        else:
            blocks.append({"id": "INPUT_TABLE", "type": "table", "prototype": "table6", "columns": ["原行号", "设备名称", "数量", "单位", "来源"],
                           "units": ["不适用"] * 5, "rows": rows, "caption": "表1 输入设备清单", "source_ids": ["SINPUT"], "requirement_ids": requirement_ids})
    pending = []
    if family == "feasibility":
        pending.append("未提供经核实的成本与收益依据，不形成经济成本、收益或回报结论。")
        if omitted_financial_claims:
            pending.append("可研正文包含未经核实的投资或回报数值结论，已拒绝纳入交付；请补充并确认对应依据。")
    pending.extend(blueprint.payload.get("missing", []))
    pending.extend(blueprint.payload.get("conflicts", []))
    if facts.get("issues"):
        pending.append("输入资料存在缺项或冲突，请在平台逐项复核，不得按无问题发布。")
    if pending:
        blocks.append({"id": "PENDING_HEADING", "type": "heading", "level": 1, "text": "待确认事项", "source_ids": ["SINPUT"], "requirement_ids": []})
        for index, value in enumerate(pending, start=1):
            blocks.append({"id": f"PENDING_TEXT{index}", "type": "paragraph", "text": value, "source_ids": ["SINPUT"], "requirement_ids": []})
    return {"version": 1, "family": family,
            "metadata": {"id": "T" + str(task.pk).replace("-", ""),
                         "title": _family_document_title(task.title, family),
                         "subtitle": "技术方案" if family == "technical-solution" else "可行性研究报告",
                         "date": timezone.localdate().isoformat(), "organization": _company_name(), "status": "draft"},
            "sources": sources, "requirements": requirements,
             "pending": [{"id": f"PEND{index}", "text": value} for index, value in enumerate(pending, start=1)], "blocks": blocks}


def _document_runtime():
    runtime = Path(settings.PRODUCT_DOCUMENT_PYTHON)
    if not runtime.is_absolute():
        runtime = settings.BASE_DIR / runtime
    if not runtime.is_file():
        raise DocumentError("template_unavailable")
    return runtime.resolve()


def _mermaid_runtime():
    configured = str(getattr(settings, "PRODUCT_MERMAID_NODE", "node")).strip()
    runtime = configured if Path(configured).is_absolute() else shutil.which(configured)
    if not runtime or not Path(runtime).is_file():
        raise DocumentError("template_unavailable")
    return Path(runtime).resolve()


def _mermaid_chromium():
    configured = str(getattr(settings, "PRODUCT_MERMAID_CHROMIUM", "")).strip()
    candidates = [configured] if configured else []
    for command in ("chromium", "chromium-browser", "google-chrome", "msedge"):
        found = shutil.which(command)
        if found:
            candidates.append(found)
    if os.name == "nt":
        for root in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
            if root:
                candidates.extend([str(Path(root) / "Microsoft/Edge/Application/msedge.exe"),
                                   str(Path(root) / "Google/Chrome/Application/chrome.exe")])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise DocumentError("template_unavailable")


def _safe_environment():
    environment = {key: value for key, value in os.environ.items() if key.upper() in
                   {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"}}
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONNOUSERSITE="1")
    return environment


def _diagram_evidence(output, figure_count):
    try:
        result = json.loads(output)
        versions = {"mermaid": "12.0.0", "katex": "0.18.2", "lodash-es": "4.18.1"}
        bundle_hash = result["bundleSha256"]
        manifest_hash = result["manifestSha256"]
        if (result["rendered"] != figure_count or result["engine"] != "mermaid-js-12.0.0"
                or result["sourcePersisted"] is not False or result["securityLevel"] != "strict"
                or result["blockedNetworkRequests"] != 0
                or not isinstance(bundle_hash, str) or re.fullmatch(r"[0-9a-f]{64}", bundle_hash) is None
                or not isinstance(manifest_hash, str) or re.fullmatch(r"[0-9a-f]{64}", manifest_hash) is None
                or any(result["runtimeVersions"].get(name) != version for name, version in versions.items())):
            raise ValueError
        return {"engine": result["engine"], "color": True, "source_persisted": False,
                "figure_count": figure_count, "bundle_sha256": bundle_hash,
                "manifest_sha256": manifest_hash,
                "runtime_versions": result["runtimeVersions"], "security_level": "strict",
                "blocked_network_requests": 0}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise DocumentError("document_validation_failed") from None


def _refresh_word_field_cache(target, runtime, directory):
    """Refresh Word fields in an owned short path before promoting the DOCX."""
    if not getattr(settings, "PRODUCT_OFFICE_RENDER_ENABLED", False):
        return {"status": "not_run", "reason": "office_render_disabled"}
    timeout = int(getattr(settings, "PRODUCT_OFFICE_RENDER_TIMEOUT_SECONDS", 300))
    retained = directory / "word-field-refresh"
    diagnostic = {"status": "failed", "reason": "staging_failed"}
    try:
        # Never replace evidence from an earlier attempt in this artifact.
        retained.mkdir(exist_ok=False)
    except OSError:
        raise DocumentError("document_render_failed") from None
    try:
        original = target.read_bytes()
        quality_path = target.with_suffix(".quality.json")
        original_quality = quality_path.read_bytes()
        quality = json.loads(original_quality)
        if not isinstance(quality, dict):
            raise ValueError("Invalid quality report")
        # mkdtemp's private directory is transient; all Office paths, including
        # the renderer's own reviewed copy and PDF, stay below MAX_PATH.
        with tempfile.TemporaryDirectory(prefix="portal-word-") as temporary:
            stage = Path(temporary).resolve()
            source = stage / "input.docx"
            output = stage / "render"
            if any(len(str(path)) >= 260 for path in
                   (source, output / "reviewed.docx", output / "document.pdf")):
                diagnostic["reason"] = "office_path_too_long"
                raise ValueError("Office staging path exceeds MAX_PATH")
            shutil.copy2(target, source)
            command = [str(runtime), "-B", str(PACK / "scripts" / "office_render.py"),
                       "word", str(source), str(output), "--timeout", str(timeout)]

            def retained_paths(value):
                if isinstance(value, dict):
                    return {key: retained_paths(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [retained_paths(item) for item in value]
                if isinstance(value, str):
                    return value.replace(str(output), str(retained)).replace(
                        str(source), str(target)).replace(str(stage), str(retained))
                return value

            try:
                diagnostic["reason"] = "renderer_failed"
                result = subprocess.run(
                    command, cwd=stage, env=_safe_environment(), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=timeout + 30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                diagnostic["returncode"] = result.returncode
            finally:
                # Preserve partial output even when the child times out or fails.
                # This destination was exclusively created by this attempt.
                if output.is_dir():
                    shutil.copytree(output, retained, dirs_exist_ok=True)
                    report_path = retained / "render.json"
                    if report_path.is_file():
                        try:
                            report = json.loads(report_path.read_text(encoding="utf-8"))
                        except (UnicodeError, ValueError):
                            pass  # Preserve malformed output for review; it cannot pass.
                        else:
                            report_path.write_text(json.dumps(retained_paths(report), ensure_ascii=False),
                                                   encoding="utf-8")
            report_path = retained / "render.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("Invalid Office report")

            reviewed = retained / "reviewed.docx"
            if (result.returncode != 0 or report.get("rendered") is not True
                    or int(report.get("toc_count", 0)) < 1 or not reviewed.is_file()):
                raise ValueError("Word field refresh did not pass")
            cache = {"status": "completed", "renderer": report.get("renderer", "Microsoft Word"),
                     "toc_count": int(report["toc_count"]),
                     "page_count": int(report.get("page_count", 0))}
            quality["output_sha256"] = hashlib.sha256(reviewed.read_bytes()).hexdigest()
            quality["word_field_cache"] = cache
            refreshed_quality = json.dumps(quality, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            diagnostic["reason"] = "staging_cleanup_failed"
        # Cleanup must succeed before either delivered file is changed.
        if stage.exists():
            raise OSError("Office staging cleanup left its owned directory")
        completed = {"status": "completed", "returncode": result.returncode,
                     "temporary_directory_removed": True}
        # No diagnostic I/O may fail after the delivered files are promoted.
        (retained / "refresh.json").write_text(json.dumps(completed), encoding="utf-8")
        diagnostic["reason"] = "promotion_failed"
        try:
            shutil.copy2(reviewed, target)
            quality_path.write_bytes(refreshed_quality)
        except OSError:
            target.write_bytes(original)
            quality_path.write_bytes(original_quality)
            raise
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError, subprocess.TimeoutExpired):
        try:
            (retained / "refresh.json").write_text(json.dumps(diagnostic), encoding="utf-8")
        except OSError:
            pass  # Existing renderer output still remains available for review.
        raise DocumentError("document_render_failed") from None
    return {**cache, "cached_result": True}


def _normalize_word_layout(target, document):
    """Bind authored headings and empty TOC boundaries before Office refresh."""
    quality_path = target.with_suffix(".quality.json")
    original = target.read_bytes()
    original_quality = quality_path.read_bytes()
    try:
        quality = json.loads(original_quality)
        if quality["output_sha256"] != hashlib.sha256(original).hexdigest():
            raise ValueError("Generated output hash mismatch")
        bookmarks = quality["bookmark_map"]
        heading_names = [bookmarks[block["id"]] for block in document["blocks"] if block["type"] == "heading"]
        normalized, evidence = normalize_docx_layout(original, heading_names)
        quality["output_sha256"] = evidence["normalized_sha256"]
        quality["layout_normalization"] = evidence
        refreshed = json.dumps(quality, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        try:
            target.write_bytes(normalized)
            quality_path.write_bytes(refreshed)
        except OSError:
            target.write_bytes(original)
            quality_path.write_bytes(original_quality)
            raise
        return evidence
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        raise DocumentError("document_validation_failed") from None


def _render_document(task, chapters, manifest, runtime, document, filename, status, business_approval, extra_evidence=None):
    root = private_root().resolve()
    directory = (root / str(task.pk) / "artifacts" / uuid.uuid4().hex).resolve()
    if not directory.is_relative_to(root):
        raise DocumentError("template_unavailable")
    directory.mkdir(parents=True, exist_ok=False)
    content_path = directory / "content.json"
    target = directory / filename
    structural = validate_document_requirements(document)
    content_path.write_text(json.dumps(document, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    try:
        diagram_environment = _safe_environment()
        diagram_environment["PORTAL_CHROMIUM_PATH"] = str(_mermaid_chromium())
        diagrams = subprocess.run([str(_mermaid_runtime()), str(PACK.parent / "mermaid-runtime" / "render.mjs"), str(content_path)],
                                  cwd=directory, env=diagram_environment, capture_output=True,
                                  timeout=int(getattr(settings, "PRODUCT_DOCUMENT_RENDER_TIMEOUT_SECONDS", 300)),
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if diagrams.returncode != 0:
            raise DocumentError("document_validation_failed")
        diagram_evidence = _diagram_evidence(diagrams.stdout, structural["figure_count"])
        result = subprocess.run([str(runtime), "-B", str(PACK / "scripts" / "artifacts.py"), "docx", str(content_path), str(target)],
                                cwd=directory, env=_safe_environment(), capture_output=True,
                                timeout=int(getattr(settings, "PRODUCT_DOCUMENT_RENDER_TIMEOUT_SECONDS", 300)),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0 or not target.is_file():
            raise DocumentError("document_validation_failed")
        layout = _normalize_word_layout(target, document)
        field_refresh = _refresh_word_field_cache(target, runtime, directory)
        structural = inspect_docx_requirements(target, structural)
    except (OSError, subprocess.TimeoutExpired):
        raise DocumentError("document_render_failed") from None
    template = next(entry for entry in manifest["files"] if entry["path"] == f"assets/{document['family']}/template.docx")
    evidence = {"status": status, "verified": False,
                "structural_generation": "completed", "office_render": field_refresh["status"],
                "word_field_cache": field_refresh, "visual_review": "not_run", "business_approval": business_approval,
                "layout_normalization": layout,
                "manifest_sha256": hashlib.sha256((PACK / "manifest.json").read_bytes()).hexdigest(),
                "chapter_hashes": {chapter.payload["chapter_id"]: chapter.sha256 for chapter in chapters},
                "document_structure": structural,
                "diagram_generation": diagram_evidence}
    evidence.update(extra_evidence or {})
    return {"path": target.relative_to(root).as_posix(), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "template_hash": template["sha256"], "render_evidence": evidence}


def render_report_draft(task, input_revision, blueprint, chapters, family):
    paragraphs = [paragraph for chapter in chapters for paragraph in chapter.payload.get("paragraphs", [])]
    if has_repeated_filler(paragraphs):
        raise DocumentError("repeated_body_filler")
    if family == "feasibility" and any(unsupported_financial_conclusion(paragraph) for paragraph in paragraphs):
        raise DocumentError("investment_evidence_required")
    manifest = frozen_pack(family)
    runtime = _document_runtime()
    document = content_document(task, input_revision, blueprint, chapters, family)
    return _render_document(task, chapters, manifest, runtime, document, "draft.docx", "draft_unverified", "blocked")


def render_draft(task, input_revision, blueprint, chapters):
    return render_report_draft(task, input_revision, blueprint, chapters, "technical-solution")


def _template_approval(manifest):
    approval = getattr(settings, "PRODUCT_TEMPLATE_APPROVAL", None)
    template = next((entry for entry in manifest["files"] if entry["path"] == "assets/technical-solution/template.docx"), None)
    if not isinstance(approval, dict) or template is None:
        raise DocumentError("template_approval_required")
    template_hash = approval.get("template_hash")
    approval_ref = approval.get("approval_ref")
    organization = approval.get("organization")
    if (not isinstance(template_hash, str) or len(template_hash) != 64
            or any(character not in "0123456789abcdef" for character in template_hash.lower())
            or template_hash.lower() != template["sha256"]):
        raise DocumentError("template_approval_required")
    for value in (approval_ref, organization):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200 or any(ord(character) < 32 for character in value):
            raise DocumentError("template_approval_required")
    return {"template_hash": template["sha256"], "approval_ref": approval_ref.strip(), "organization": organization.strip()}


def _candidate_document(task, input_revision, blueprint, chapters, approval):
    if blueprint.payload.get("missing") or blueprint.payload.get("conflicts") or input_revision.payload.get("issues"):
        raise DocumentError("candidate_content_unresolved")
    document = content_document(task, input_revision, blueprint, chapters)
    document["metadata"].update(subtitle="技术方案", organization=approval["organization"], status="reviewed")
    document["sources"].append({"id": "STEMPLATE", "text": f"模板批准引用={approval['approval_ref']}；组织={approval['organization']}"})
    document["pending"] = []
    document["blocks"] = [block for block in document["blocks"]
                          if block["id"] != "DRAFT_NOTICE" and block["id"] != "PENDING_HEADING" and not block["id"].startswith("PENDING_TEXT")]
    first_heading = next((index for index, block in enumerate(document["blocks"]) if block["type"] == "heading"), -1)
    document["blocks"].insert(first_heading + 1, {
        "id": "ORGANIZATION", "type": "paragraph", "text": f"编制单位：{approval['organization']}",
        "source_ids": ["STEMPLATE"], "requirement_ids": [],
    })
    for block in document["blocks"]:
        if block["id"] == "INPUT_TABLE":
            block["caption"] = "表1 输入设备清单"
    return document


def render_candidate(task, input_revision, blueprint, chapters):
    manifest = frozen_pack()
    approval = _template_approval(manifest)
    runtime = _document_runtime()
    document = _candidate_document(task, input_revision, blueprint, chapters, approval)
    approval_hash = hashlib.sha256(json.dumps(
        approval, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return _render_document(
        task, chapters, manifest, runtime, document, "candidate.docx", "candidate_unverified", "pending",
        {"template_approval": approval, "template_approval_hash": approval_hash,
         "template_approval_hash_method": "sha256-canonical-json-v1"},
    )
