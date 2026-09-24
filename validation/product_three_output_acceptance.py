"""Run the representative P1 three-output quality chain.

Uses a fresh isolated SQLite database and private storage. It never calls a
model or RAGFlow and never treats representative approvals as business signoff.
"""

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
RUN_VERSION = os.environ.get("P1_THREE_RUN_VERSION", "v1")
EVIDENCE_TASK = os.environ.get("P1_THREE_EVIDENCE_TASK", "T-P06")
if EVIDENCE_TASK not in {"T-P06", "T-P08"}:
    raise SystemExit("Unsupported three-output evidence task.")
EVIDENCE = ROOT / "docs" / "product" / EVIDENCE_TASK / "evidence" / f"representative-run-20260924-{RUN_VERSION}"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expect(response, status):
    if response.status_code != status:
        try:
            body = response.json()
        except ValueError:
            body = response.content.decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {response.status_code}, expected {status}: {body}")
    return response.json()


def copy_checked(source, destination, expected=None):
    source = Path(source).resolve()
    if not source.is_file() or (expected and sha256(source) != expected):
        raise RuntimeError(f"missing or changed evidence source: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite evidence: {destination}")
    shutil.copyfile(source, destination)
    actual = sha256(destination)
    if expected and actual != expected:
        raise RuntimeError(f"copied evidence hash mismatch: {destination}")
    return {"path": destination.relative_to(ROOT).as_posix(), "sha256": actual, "bytes": destination.stat().st_size}


def main():
    import django

    django.setup()
    from django.conf import settings
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.core.management import call_command
    from django.test import Client, override_settings
    from portal.models import Role, User
    from portal.product_documents import PACK, _document_runtime, _safe_environment
    from portal.product_models import DocumentArtifact, DocumentAttempt, DocumentRevision, DocumentTask
    from portal.product_rendering import render_office
    from portal.product_storage import private_root, verified_artifact
    from portal.product_worker import run_once
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "testserver"]

    database = Path(settings.DATABASES["default"]["NAME"]).resolve()
    storage = Path(settings.PRODUCT_STORAGE_ROOT).resolve()
    runtime_root = (ROOT / ".runtime").resolve()
    if (
        settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3"
        or database.parent != runtime_root
        or not database.name.startswith("product-p1-three-")
        or database.suffix != ".sqlite3"
        or storage.parent != runtime_root
        or not storage.name.startswith("product-p1-three-private-")
        or not settings.DEBUG
        or not settings.PRODUCT_P1_ENABLED
        or settings.PRODUCT_MODEL_CALLS_ALLOWED
        or settings.PRODUCT_RETRIEVAL_ENABLED
        or settings.PRODUCT_FORMAL_RELEASE_ENABLED
        or settings.MODEL_GATEWAY_URL
        or settings.MODEL_GATEWAY_TOKEN
        or not _document_runtime().is_file()
    ):
        raise SystemExit("Refusing to run outside a fresh isolated configuration.")
    for target in (database, storage, EVIDENCE):
        if target.exists():
            raise SystemExit(f"Refusing to overwrite existing acceptance state: {target}")

    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_portal", verbosity=0)
    role = Role.objects.get(code="product")
    password = secrets.token_urlsafe(32)
    owner = User.objects.create_user(username="p1_three_owner", password=password,
        display_name="P1 三件套代表性输入所有人", must_change_password=False)
    reviewer = User.objects.create_user(username="p1_three_reviewer", password=password,
        display_name="P1 三件套代表性内容审核人", must_change_password=False)
    owner.roles.add(role)
    reviewer.roles.add(role)
    settings.PRODUCT_REVIEWER_IDS = (reviewer.pk,)
    owner_client, reviewer_client = Client(), Client()
    expect(owner_client.post("/api/login/", data=json.dumps({"username": owner.username, "password": password}), content_type="application/json"), 200)
    expect(reviewer_client.post("/api/login/", data=json.dumps({"username": reviewer.username, "password": password}), content_type="application/json"), 200)

    inputs = EVIDENCE / "inputs"
    inputs.mkdir(parents=True)
    source_a = inputs / "代表性建设需求.txt"
    source_b = inputs / "代表性约束与缺项.txt"
    source_a.write_text(
        "园区拟部署2台安全接入网关和1套集中审计节点。\n"
        "目标是统一远程接入控制、日志汇聚和审计追溯。\n"
        "实施窗口为2026-10-15至2026-10-18，要求业务不中断。\n",
        encoding="utf-8",
    )
    source_b.write_text(
        "现网为双链路，允许并行验证后分步切换。\n"
        "运维责任人和最终IP地址尚未确认。\n"
        "日志留存口径存在180天与不少于6个月两种记载。\n"
        "未提供经核实的建设成本、运维成本或收益测算资料。\n",
        encoding="utf-8",
    )
    input_files = [{"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size}
                   for path in (source_a, source_b)]
    input_payload = {
        "project": "园区安全接入与集中审计代表性项目",
        "requirements": "形成可追溯的技术方案、可行性研究报告和汇报简版；未知项不得补造。",
        "items": [
            {"row_id": "r-001", "name": "安全接入网关", "quantity": 2, "unit": "台"},
            {"row_id": "r-002", "name": "集中审计节点", "quantity": 1, "unit": "套"},
        ],
        "background": "仅使用仓库内代表性输入，不含真实客户资料。",
        "conditions": ["不得将推断写成事实", "缺少成本或收益依据时不得形成经济结论"],
    }
    current = expect(owner_client.post("/api/product/tasks/", data=json.dumps({
        "title": "园区安全接入与集中审计三件套（代表性草稿）",
        "input": input_payload, "reviewer_id": reviewer.pk,
    }), content_type="application/json", HTTP_IDEMPOTENCY_KEY=f"p1-three-representative-{RUN_VERSION}"), 201)
    task_id = current["id"]

    uploaded_ids = []
    for path in (source_a, source_b):
        current = expect(owner_client.get(f"/api/product/tasks/{task_id}/"), 200)
        uploaded = expect(owner_client.post(f"/api/product/tasks/{task_id}/sources/", data={
            "expected_version": current["version"],
            "file": SimpleUploadedFile(path.name, path.read_bytes(), content_type="text/plain"),
        }), 201)
        uploaded_ids.append(uploaded["source_id"])

    statements = [
        ("fact", "设备范围为安全接入网关2台、集中审计节点1套。", [uploaded_ids[0]]),
        ("inference", "建议并行建设、验证后分步切换；该内容属于方案推断。", uploaded_ids),
        ("conflict", "日志留存存在180天与不少于6个月两种口径。", [uploaded_ids[1]]),
        ("missing", "运维责任人、最终IP以及成本收益资料均未提供。", [uploaded_ids[1]]),
    ]
    for category, text, source_ids in statements:
        current = expect(owner_client.get(f"/api/product/tasks/{task_id}/"), 200)
        expect(owner_client.post(f"/api/product/tasks/{task_id}/statements/", data=json.dumps({
            "expected_version": current["version"], "category": category, "text": text, "source_ids": source_ids,
        }), content_type="application/json"), 200)
    current = expect(reviewer_client.get(f"/api/product/tasks/{task_id}/"), 200)
    resolutions = [{"issue_hash": item["issue_hash"], "category": item["category"],
                    "reason": "仅确认分类和来源，冲突与缺项并未关闭。", "source_ids": item["source_ids"]}
                   for item in current["input_issues"]]
    current = expect(reviewer_client.post(f"/api/product/tasks/{task_id}/input-review/", data=json.dumps({
        "expected_version": current["version"], "resolutions": resolutions,
    }), content_type="application/json"), 200)

    all_sources = ["r-001", "r-002", *uploaded_ids]
    chapter_specs = [
        ("necessity", "现状与建设必要性"),
        ("alternatives", "备选技术路线"),
        ("conditions", "实施条件与技术方案"),
        ("risks", "风险与控制"),
        ("economics", "经济资料边界"),
    ]
    blueprint = {
        "purpose": "形成同源、独立论证且可追溯的三件套待核草稿",
        "audience": "产品负责人、项目经理和实施审核人",
        "chapters": [{"id": key, "title": title, "scope": title, "source_ids": all_sources} for key, title in chapter_specs],
        "conditions": [
            {"text": "不得将推断写成事实", "type": "human"},
            {"text": "缺少成本或收益依据时不得形成经济结论", "type": "program"},
        ],
        "missing": ["运维责任人、最终IP地址、建设成本、运维成本和收益依据待确认"],
        "conflicts": ["日志留存180天与不少于6个月的口径待统一"],
        "template_version": "frozen-original-v1",
    }
    current = expect(owner_client.patch(f"/api/product/tasks/{task_id}/blueprint/", data=json.dumps({
        "expected_version": current["version"], "payload": blueprint,
    }), content_type="application/json"), 200)
    current = expect(reviewer_client.post(f"/api/product/tasks/{task_id}/decisions/", data=json.dumps({
        "expected_version": current["version"], "target": "blueprint", "target_id": current["blueprint"]["id"],
        "sha256": current["blueprint"]["sha256"], "decision": "approve",
        "comment": "批准精确蓝图用于代表性草稿；不代表业务或正式发布签认。",
    }), content_type="application/json"), 201)["task"]

    technical = {
        "necessity": ["现网双链路需要统一远程接入控制与集中审计，设备事实范围为2台网关和1套审计节点。"],
        "alternatives": ["技术路线A为现网旁挂并行验证，路线B为窗口内直接切换；建议A属于工程推断，须人工确认。"],
        "conditions": ["按准备、并行验证、分步迁移和回退确认实施；最终IP和运维责任人确认前不得进入正式切换。"],
        "risks": ["日志留存口径冲突会影响存储配置和验收，必须在正式实施前统一。"],
        "economics": ["技术方案不承担经济结论；当前未取得经核实的成本或收益依据。"],
    }
    feasibility = {
        "necessity": ["统一接入控制和审计追溯具有建设必要性；该判断仅基于已提供的控制目标，不扩大为收益结论。"],
        "alternatives": ["比较旁挂并行验证与直接切换两种路线。前者回退条件较清晰，后者实施更集中；最终选择待人工确认。"],
        "conditions": ["可实施条件包括双链路、明确窗口和允许并行验证；未满足条件包括责任人、最终IP与留存口径确认。"],
        "risks": ["主要风险为切换中断、配置口径不一致和责任边界缺失；以分步验证、回退和人工确认控制。"],
        "economics": ["当前没有经核实的建设成本、运维成本或收益数据，因此不形成投资回报、成本节约或收益结论。"],
    }
    for family, content in (("technical-solution", technical), ("feasibility", feasibility)):
        endpoint = "chapters/" if family == "technical-solution" else "report-chapters/"
        for chapter_id, title in chapter_specs:
            response = expect(owner_client.post(f"/api/product/tasks/{task_id}/{endpoint}", data=json.dumps({
                "expected_version": current["version"], "family": family, "chapter_id": chapter_id,
                "title": title, "paragraphs": content[chapter_id], "source_ids": all_sources,
            }), content_type="application/json"), 201)
            current = response["task"]

    queued = expect(owner_client.post(f"/api/product/tasks/{task_id}/queue/", data=json.dumps({
        "expected_version": current["version"], "action": "three_drafts",
    }), content_type="application/json"), 200)
    if queued["state"] != "QUEUED" or not run_once():
        raise RuntimeError("report draft worker did not complete")
    current = expect(owner_client.get(f"/api/product/tasks/{task_id}/"), 200)
    reports = {item["family"]: item for item in current["reports"]}
    if set(reports) != {"technical-solution", "feasibility"} or any(item["approved"] for item in reports.values()):
        raise RuntimeError("independent report content versions were not created as unapproved")
    task = DocumentTask.objects.get(pk=task_id)
    report_payloads = {family: task.revisions.get(pk=item["id"]).payload for family, item in reports.items()}
    if report_payloads["technical-solution"] == report_payloads["feasibility"]:
        raise RuntimeError("feasibility content copied technical solution payload")
    if not any("未提供经核实的成本与收益依据" in item["text"] for item in report_payloads["feasibility"]["pending"]):
        raise RuntimeError("feasibility economic evidence boundary missing")

    artifacts = {item.family: item for item in task.artifacts.all()}
    if set(artifacts) != {"technical-solution", "feasibility"}:
        raise RuntimeError("presentation was generated before content approvals")
    word_artifacts = {
        "technical_solution": artifacts["technical-solution"],
        "feasibility": artifacts["feasibility"],
    }
    word_renders = {}
    with override_settings(PRODUCT_OFFICE_RENDER_ENABLED=True):
        for key, artifact in word_artifacts.items():
            verified_artifact(artifact)
            word_renders[key] = render_office(artifact.path, artifact.sha256)
            artifact.render_evidence = {
                **artifact.render_evidence,
                "draft_office_render": word_renders[key],
            }
            artifact.save(update_fields=["render_evidence"])

    for family in ("technical-solution", "feasibility"):
        report = reports[family]
        approved = expect(reviewer_client.post(f"/api/product/tasks/{task_id}/decisions/", data=json.dumps({
            "expected_version": current["version"], "target": "report", "target_id": report["id"],
            "sha256": report["sha256"], "decision": "approve",
            "comment": f"批准 {family} 结构化内容版本用于代表性 PPT 草稿；不代表正式业务签认。",
        }), content_type="application/json"), 201)
        current = approved["task"]
    if not all(item["approved"] for item in current["reports"]):
        raise RuntimeError("report content approvals are not effective")

    queued = expect(owner_client.post(f"/api/product/tasks/{task_id}/queue/", data=json.dumps({
        "expected_version": current["version"], "action": "presentation",
    }), content_type="application/json"), 200)
    if queued["state"] != "QUEUED" or not run_once():
        raise RuntimeError("presentation worker did not complete")
    task.refresh_from_db()
    presentation = task.artifacts.get(family="presentation")
    presentation_path = verified_artifact(presentation)
    with ZipFile(presentation_path) as archive:
        if "ppt/slides/slide1.xml" not in archive.namelist() or len([name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]) < 3:
            raise RuntimeError("editable PPTX slide package is incomplete")
        combined = "".join(archive.read(name).decode("utf-8") for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
        for source in presentation.render_evidence["source_versions"]:
            if source["family"] not in combined or source["sha256"] not in combined or source["approval_id"] not in combined:
                raise RuntimeError("PPT slide provenance footer is incomplete")
        duplicate_pending = "正式模板与样例、内容及格式批准尚未完成；本件始终为待核草稿。"
        if combined.count(duplicate_pending) != 1 or "technical-solution:PEND1" not in combined or "feasibility:PEND1" not in combined:
            raise RuntimeError("PPT pending items were not deduplicated with source refs")

    ppt_render_private = private_root().resolve() / "office-renders" / ("powerpoint-" + uuid.uuid4().hex)
    command = [str(_document_runtime()), "-B", str(PACK / "scripts" / "office_render.py"), "powerpoint",
               str(presentation_path), str(ppt_render_private), "--timeout", "120"]
    rendered = subprocess.run(command, cwd=private_root(), env=_safe_environment(), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=135,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if rendered.returncode != 0:
        raise RuntimeError("PowerPoint render failed: " + rendered.stdout[-2000:] + rendered.stderr[-2000:])
    ppt_render = json.loads((ppt_render_private / "render.json").read_text(encoding="utf-8"))
    if ppt_render.get("rendered") is not True or ppt_render.get("structural_pass") is not True or ppt_render.get("page_count", 0) < 3:
        raise RuntimeError(f"invalid PowerPoint render evidence: {ppt_render}")
    ppt_render_record = {
        "status": "rendered", "renderer": ppt_render["renderer"], "page_count": ppt_render["page_count"],
        "structural_pass": True,
        "pdf": {"path": Path(ppt_render["pdf"]).resolve().relative_to(private_root().resolve()).as_posix(),
                "sha256": sha256(ppt_render["pdf"])},
        "pages": [{"page": index,
                   "path": Path(path).resolve().relative_to(private_root().resolve()).as_posix(),
                   "sha256": sha256(path)}
                  for index, path in enumerate(ppt_render["images"], start=1)],
        "visual_review": "not_run", "business_approval": "blocked",
    }
    presentation.render_evidence = {**presentation.render_evidence, "draft_office_render": ppt_render_record}
    presentation.save(update_fields=["render_evidence"])

    final_dir = EVIDENCE / "artifacts"
    def delivered_word(key, destination):
        artifact = word_artifacts[key]
        rendered_docx = word_renders[key]["rendered_docx"]
        record = copy_checked(storage / rendered_docx["path"], destination, rendered_docx["sha256"])
        record.update({
            "office_fields_refreshed": True,
            "source_artifact_id": str(artifact.pk),
            "source_artifact_sha256": artifact.sha256,
            "source_artifact_path": artifact.path,
            "rendered_docx_differs_from_source": rendered_docx["differs_from_input"],
        })
        return record

    technical_copy = delivered_word("technical_solution",
        final_dir / f"园区安全接入与集中审计技术方案-代表性草稿-{RUN_VERSION}.docx")
    feasibility_copy = delivered_word("feasibility",
        final_dir / f"园区安全接入与集中审计可行性研究报告-代表性草稿-{RUN_VERSION}.docx")
    presentation_copy = copy_checked(presentation_path,
        final_dir / f"园区安全接入与集中审计汇报简版-代表性草稿-{RUN_VERSION}.pptx", presentation.sha256)
    word_render_evidence = {}
    for key, label in (("technical_solution", "技术方案"), ("feasibility", "可行性研究报告")):
        word_render = word_renders[key]
        word_dir = EVIDENCE / f"word-render-{key.replace('_', '-')}"
        word_pdf = copy_checked(storage / word_render["pdf"]["path"],
            word_dir / f"{label}-{RUN_VERSION}.pdf", word_render["pdf"]["sha256"])
        word_pages = [
            {"page": item["page"], **copy_checked(
                storage / item["path"], word_dir / f"page-{item['page']:03d}.png", item["sha256"])}
            for item in word_render["pages"]
        ]
        word_render_evidence[key] = {
            "renderer": "Microsoft Word", "status": word_render["status"],
            "page_count": word_render["page_count"], "pdf": word_pdf, "pages": word_pages,
            "rendered_docx_sha256": word_render["rendered_docx"]["sha256"],
            "rendered_docx_differs_from_source": word_render["rendered_docx"]["differs_from_input"],
            "visual_review": "pending_manual_inspection",
        }
    ppt_dir = EVIDENCE / "ppt-render"
    ppt_pdf = copy_checked(Path(ppt_render["pdf"]), ppt_dir / f"汇报简版-{RUN_VERSION}.pdf")
    ppt_pages = [{"page": index, **copy_checked(Path(path), ppt_dir / f"slide-{index:03d}.png")}
                 for index, path in enumerate(ppt_render["images"], start=1)]

    current = expect(owner_client.get(f"/api/product/tasks/{task_id}/"), 200)
    outputs = expect(owner_client.get(f"/api/product/tasks/{task_id}/outputs/"), 200)["outputs"]
    if len(outputs) != 3 or not all(item["current"] for item in outputs):
        raise RuntimeError("three current outputs were not exposed")
    formal_attempts = {}
    for family in ("technical-solution", "feasibility", "presentation"):
        artifact = task.artifacts.get(family=family)
        response = expect(reviewer_client.post(f"/api/product/tasks/{task_id}/decisions/", data=json.dumps({
            "expected_version": current["version"], "target": "artifact", "target_id": str(artifact.pk),
            "sha256": artifact.sha256, "decision": "approve", "comment": "应由正式发布门禁拒绝。",
        }), content_type="application/json"), 409)
        if response.get("code") != "formal_release_blocked":
            raise RuntimeError("formal artifact approval gate returned unexpected result")
        formal_attempts[family] = response

    history = expect(owner_client.get(f"/api/product/tasks/{task_id}/history/"), 200)
    task = DocumentTask.objects.get(pk=task_id)
    chain = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "authority": "docs/SPEC.md",
        "boundary": {
            "database": database.relative_to(ROOT).as_posix(), "storage": storage.relative_to(ROOT).as_posix(),
            "representative_input_only": True, "formal_database_modified": False, "real_customer_data_used": False,
            "model_calls": 0, "ragflow_calls": 0, "business_signoff": "blocked",
        },
        "task": {"id": str(task.pk), "state": task.state, "stage": task.stage, "version": task.version},
        "representative_input_files": input_files,
        "sources": [{"id": str(source.pk), "name": source.original_name, "sha256": source.sha256,
                     "size": source.size, "uploaded_by": source.uploaded_by_id,
                     "author_verification": source.author_verification}
                    for source in task.sources.order_by("created_at")],
        "input": {"version": task.input_version, "sha256": task.revisions.get(kind="input", version=task.input_version).sha256},
        "blueprint": {"version": task.blueprint_version, "sha256": task.revisions.get(kind="blueprint", version=task.blueprint_version).sha256},
        "versions": [{"id": str(record.pk), "kind": record.kind, "family": record.family,
                      "version": record.version, "sha256": record.sha256, "parent_sha256": record.parent_sha256,
                      "reason": record.change_reason, "input_hash": record.input_hash, "blueprint_hash": record.blueprint_hash}
                     for record in task.revisions.order_by("created_at", "pk")],
        "approvals": [{"id": str(record.pk), "target_id": str(record.revision_id or record.artifact_id),
                       "decision": record.decision, "actor_id": record.actor_id, "sha256": record.sha256,
                       "comment": record.comment, "authorization": record.authorization}
                      for record in task.approvals.order_by("created_at", "pk")],
        "artifacts": [{"id": str(artifact.pk), "family": artifact.family, "version": artifact.version,
                       "sha256": artifact.sha256, "generation_hash": artifact.generation_hash,
                       "input_hash": artifact.input_hash, "blueprint_hash": artifact.blueprint_hash,
                       "template_hash": artifact.template_hash, "render_evidence": artifact.render_evidence}
                      for artifact in task.artifacts.order_by("version")],
        "worker_attempts": [{"id": str(attempt.pk), "action": attempt.action, "status": attempt.status,
                             "model_calls": attempt.model_calls, "fence": attempt.fence, "error_code": attempt.error_code}
                            for attempt in task.attempts.order_by("start_at")],
        "delivered": {"technical_solution_word": technical_copy,
                      "feasibility_word": feasibility_copy, "presentation": presentation_copy},
        "word_renders": word_render_evidence,
        "ppt_render": {"renderer": ppt_render["renderer"], "status": "rendered",
                       "page_count": ppt_render["page_count"], "pdf": ppt_pdf, "pages": ppt_pages,
                       "visual_review": "pending_manual_inspection", "quality_claim": "not_ppt_master"},
        "formal_artifact_approval_attempts": formal_attempts,
        "outputs": outputs,
        "history": {"task_version": history["task_version"], "event_count": len(history["timeline"])},
    }
    chain_path = EVIDENCE / "version-chain.json"
    chain_path.write_text(json.dumps(chain, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({
        "result": "CORE_PASS_WITH_EXTERNAL_AND_HUMAN_BLOCKERS",
        "task_id": str(task.pk), "technical_solution_word": technical_copy,
        "feasibility_word": feasibility_copy, "presentation": presentation_copy,
        "technical_word_pages": word_renders["technical_solution"]["page_count"],
        "feasibility_word_pages": word_renders["feasibility"]["page_count"],
        "ppt_pages": ppt_render["page_count"],
        "chain": chain_path.relative_to(ROOT).as_posix(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
