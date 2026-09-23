import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .product_storage import private_root


PACK = Path(__file__).resolve().parent / "product_assets" / "bj_docs"


class DocumentError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


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
    blocks = [{"id": "DRAFT_NOTICE", "type": "paragraph", "text": "待核草稿：未经正式内容、格式及产品负责人批准，不得作为正式方案使用。",
               "source_ids": ["SINPUT"], "requirement_ids": []}]
    for chapter in chapters:
        payload = chapter.payload
        stable = "C" + hashlib.sha256(payload["chapter_id"].encode()).hexdigest()[:24]
        cited = [mapped for original in payload["source_ids"] for mapped in source_map.get(original, [])] or ["SINPUT"]
        blocks.append({"id": stable, "type": "heading", "level": 1, "text": payload["title"], "source_ids": cited, "requirement_ids": requirement_ids})
        for index, paragraph in enumerate(payload["paragraphs"], start=1):
            blocks.append({"id": f"{stable}P{index}", "type": "paragraph", "text": paragraph, "source_ids": cited, "requirement_ids": requirement_ids})
    if facts.get("items"):
        rows = [[str(item.get(key, "")).strip() or "待确认" for key in ("row_id", "name", "quantity", "unit")]
                + [str(item.get("source_id", "人工录入"))] for item in facts["items"]]
        if family == "feasibility":
            for index, row in enumerate(rows, start=1):
                blocks.append({"id": f"INPUT_ITEM{index}", "type": "paragraph",
                               "text": f"输入清单（待核）：{row[1]}，数量 {row[2]} {row[3]}；原行号 {row[0]}；来源 {row[4]}。",
                               "source_ids": ["SINPUT"], "requirement_ids": requirement_ids})
        else:
            blocks.append({"id": "INPUT_TABLE", "type": "table", "prototype": "table6", "columns": ["原行号", "设备名称", "数量", "单位", "来源"],
                           "units": ["不适用"] * 5, "rows": rows, "caption": "表1 输入设备清单（待核）", "source_ids": ["SINPUT"], "requirement_ids": requirement_ids})
    pending = ["正式模板与样例、内容及格式批准尚未完成；本件始终为待核草稿。"]
    pending.extend(blueprint.payload.get("missing", []))
    pending.extend(blueprint.payload.get("conflicts", []))
    if facts.get("issues"):
        pending.append("输入资料存在缺项或冲突，请在平台逐项复核，不得按无问题发布。")
    blocks.append({"id": "PENDING_HEADING", "type": "heading", "level": 1, "text": "待确认事项", "source_ids": ["SINPUT"], "requirement_ids": []})
    for index, value in enumerate(pending, start=1):
        blocks.append({"id": f"PENDING_TEXT{index}", "type": "paragraph", "text": value, "source_ids": ["SINPUT"], "requirement_ids": []})
    return {"version": 1, "family": family,
            "metadata": {"id": "T" + str(task.pk).replace("-", ""), "title": task.title, "subtitle": "待核草稿 · 未获正式发布批准",
                         "date": timezone.localdate().isoformat(), "organization": "编制单位待确认", "status": "draft"},
            "sources": sources, "requirements": requirements,
             "pending": [{"id": f"PEND{index}", "text": value} for index, value in enumerate(pending, start=1)], "blocks": blocks}


def _document_runtime():
    runtime = Path(settings.PRODUCT_DOCUMENT_PYTHON)
    if not runtime.is_absolute():
        runtime = settings.BASE_DIR / runtime
    if not runtime.is_file():
        raise DocumentError("template_unavailable")
    return runtime.resolve()


def _safe_environment():
    environment = {key: value for key, value in os.environ.items() if key.upper() in
                   {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"}}
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONNOUSERSITE="1")
    return environment


def _render_document(task, chapters, manifest, runtime, document, filename, status, business_approval, extra_evidence=None):
    root = private_root().resolve()
    directory = (root / str(task.pk) / "artifacts" / uuid.uuid4().hex).resolve()
    if not directory.is_relative_to(root):
        raise DocumentError("template_unavailable")
    directory.mkdir(parents=True, exist_ok=False)
    content_path = directory / "content.json"
    target = directory / filename
    content_path.write_text(json.dumps(document, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    try:
        result = subprocess.run([str(runtime), "-B", str(PACK / "scripts" / "artifacts.py"), "docx", str(content_path), str(target)],
                                cwd=directory, env=_safe_environment(), capture_output=True, timeout=90,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0 or not target.is_file():
            raise DocumentError("document_validation_failed")
    except (OSError, subprocess.TimeoutExpired):
        raise DocumentError("document_render_failed") from None
    template = next(entry for entry in manifest["files"] if entry["path"] == f"assets/{document['family']}/template.docx")
    evidence = {"status": status, "verified": False,
                "structural_generation": "completed", "office_render": "not_run", "visual_review": "not_run", "business_approval": business_approval,
                "manifest_sha256": hashlib.sha256((PACK / "manifest.json").read_bytes()).hexdigest(),
                "chapter_hashes": {chapter.payload["chapter_id"]: chapter.sha256 for chapter in chapters}}
    evidence.update(extra_evidence or {})
    return {"path": target.relative_to(root).as_posix(), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "template_hash": template["sha256"], "render_evidence": evidence}


def render_report_draft(task, input_revision, blueprint, chapters, family):
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
