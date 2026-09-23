"""Run the representative P1 technical-solution chain in a new isolated SQLite DB.

No model or RAGFlow substitute is used. Missing external dependencies are
recorded as blocked while the independent manual-content path continues.
"""

import hashlib
import json
import os
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
EVIDENCE = ROOT / "docs" / "product" / "T-P05" / "evidence" / "representative-run-20260923-v2"


def sha256(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def expect(response, status):
    if response.status_code != status:
        try:
            body = response.json()
        except ValueError:
            body = response.content.decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {response.status_code}, expected {status}: {body}")
    return response.json()


def relative_copy(storage, record, destination):
    source = (storage / record["path"]).resolve()
    if not source.is_relative_to(storage) or not source.is_file() or sha256(source) != record["sha256"]:
        raise RuntimeError(f"render evidence is missing or changed: {record}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite evidence: {destination}")
    shutil.copyfile(source, destination)
    copied = sha256(destination)
    if copied != record["sha256"]:
        raise RuntimeError(f"copied evidence hash mismatch: {destination}")
    return {"path": destination.relative_to(ROOT).as_posix(), "sha256": copied, "bytes": destination.stat().st_size}


def main():
    import django

    django.setup()
    from django.conf import settings
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.core.management import call_command
    from django.test import Client, override_settings
    from portal.models import Role, User
    from portal.product_models import DocumentArtifact, DocumentAttempt, DocumentRevision, DocumentTask
    from portal.product_rendering import render_office
    from portal.product_storage import verified_artifact
    from portal.product_worker import run_once

    database = Path(settings.DATABASES["default"]["NAME"]).resolve()
    storage = Path(settings.PRODUCT_STORAGE_ROOT).resolve()
    runtime = (ROOT / ".runtime").resolve()
    if (
        settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3"
        or database.parent != runtime
        or not database.name.startswith("product-p1-tech-")
        or database.suffix != ".sqlite3"
        or storage.parent != runtime
        or not storage.name.startswith("product-p1-tech-private-")
        or not settings.DEBUG
        or not settings.PRODUCT_P1_ENABLED
        or settings.PRODUCT_MODEL_CALLS_ALLOWED
        or settings.PRODUCT_RETRIEVAL_ENABLED
        or settings.PRODUCT_FORMAL_RELEASE_ENABLED
        or settings.MODEL_GATEWAY_URL
        or settings.MODEL_GATEWAY_TOKEN
    ):
        raise SystemExit("Refusing to run outside a fresh isolated configuration.")
    for target in (database, storage, EVIDENCE):
        if target.exists():
            raise SystemExit(f"Refusing to overwrite existing acceptance state: {target}")

    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_portal", verbosity=0)
    role = Role.objects.get(code="product")
    password = secrets.token_urlsafe(32)
    owner = User.objects.create_user(
        username="p1_tech_owner", password=password,
        display_name="P1 技术方案代表性输入所有人", must_change_password=False,
    )
    reviewer = User.objects.create_user(
        username="p1_tech_reviewer", password=password,
        display_name="P1 技术方案代表性输入审核人", must_change_password=False,
    )
    owner.roles.add(role)
    reviewer.roles.add(role)
    settings.PRODUCT_REVIEWER_IDS = (reviewer.pk,)
    owner_client, reviewer_client = Client(), Client()
    expect(owner_client.post(
        "/api/login/", data=json.dumps({"username": owner.username, "password": password}),
        content_type="application/json",
    ), 200)
    expect(reviewer_client.post(
        "/api/login/", data=json.dumps({"username": reviewer.username, "password": password}),
        content_type="application/json",
    ), 200)

    inputs = EVIDENCE / "inputs"
    inputs.mkdir(parents=True)
    source_a = inputs / "代表性需求说明.txt"
    source_b = inputs / "代表性现网约束.txt"
    source_a.write_text(
        "代表性项目要求部署2台安全接入网关和1套集中审计节点。\n"
        "计划实施窗口为2026-10-15至2026-10-18。\n"
        "核心业务连续性要求是不停机切换。\n",
        encoding="utf-8",
    )
    source_b.write_text(
        "代表性现网边界由双链路组成。\n"
        "运维责任人和最终IP地址尚未确认。\n"
        "日志留存要求一处记载为180天，另一处记载为不少于6个月，口径待统一。\n",
        encoding="utf-8",
    )
    source_input_evidence = [
        {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size}
        for path in (source_a, source_b)
    ]

    input_payload = {
        "project": "园区安全接入与集中审计代表性项目",
        "requirements": "依据已授权的代表性资料形成可追溯技术方案草稿；未知项必须保留待确认标记。",
        "items": [
            {"row_id": "r-001", "name": "安全接入网关", "quantity": 2, "unit": "台"},
            {"row_id": "r-002", "name": "集中审计节点", "quantity": 1, "unit": "套"},
        ],
        "background": "本次只使用仓库内代表性输入，不含真实客户或公司项目资料。",
        "conditions": ["不得将推断写成事实", "冲突和缺项未关闭前只能生成待核草稿"],
    }
    created = expect(owner_client.post(
        "/api/product/tasks/", data=json.dumps({
            "title": "园区安全接入与集中审计技术方案（代表性草稿）",
            "input": input_payload, "reviewer_id": reviewer.pk,
        }), content_type="application/json", HTTP_IDEMPOTENCY_KEY="p1-technical-solution-representative-v1",
    ), 201)
    task_id = created["id"]

    uploaded_ids = []
    for path in (source_a, source_b):
        current = expect(owner_client.get(f"/api/product/tasks/{task_id}/"), 200)
        uploaded = expect(owner_client.post(
            f"/api/product/tasks/{task_id}/sources/",
            data={"expected_version": current["version"], "file": SimpleUploadedFile(path.name, path.read_bytes(), content_type="text/plain")},
        ), 201)
        uploaded_ids.append(uploaded["source_id"])

    statements = [
        ("fact", "资料明确要求部署2台安全接入网关和1套集中审计节点。", [uploaded_ids[0]]),
        ("inference", "基于双链路和不停机约束，建议并行建设并分步切换；该内容属于方案推断。", uploaded_ids),
        ("conflict", "日志留存存在180天与不少于6个月两种口径，正式实施前须统一。", [uploaded_ids[1]]),
        ("missing", "运维责任人和最终IP地址未提供，当前不得补造。", [uploaded_ids[1]]),
    ]
    for category, text, source_ids in statements:
        current = expect(owner_client.get(f"/api/product/tasks/{task_id}/"), 200)
        expect(owner_client.post(
            f"/api/product/tasks/{task_id}/statements/",
            data=json.dumps({"expected_version": current["version"], "category": category, "text": text, "source_ids": source_ids}),
            content_type="application/json",
        ), 200)

    current = expect(reviewer_client.get(f"/api/product/tasks/{task_id}/"), 200)
    resolutions = [{
        "issue_hash": item["issue_hash"], "category": item["category"],
        "reason": "审核确认分类和引用来源正确；冲突或缺项仅确认存在，不代表已解决。",
        "source_ids": item["source_ids"],
    } for item in current["input_issues"]]
    current = expect(reviewer_client.post(
        f"/api/product/tasks/{task_id}/input-review/",
        data=json.dumps({"expected_version": current["version"], "resolutions": resolutions}),
        content_type="application/json",
    ), 200)

    all_sources = ["r-001", "r-002", *uploaded_ids]
    blueprint_payload = {
        "purpose": "形成安全接入与集中审计实施技术方案待核草稿",
        "audience": "产品负责人、项目经理和实施审核人",
        "chapters": [
            {"id": "project-understanding", "title": "项目理解与边界", "scope": "事实、冲突和缺项边界", "source_ids": all_sources},
            {"id": "overall-design", "title": "总体技术方案", "scope": "明确区分事实与方案推断", "source_ids": all_sources},
            {"id": "implementation-plan", "title": "实施与切换计划", "scope": "依据实施窗口和连续性约束", "source_ids": all_sources},
            {"id": "acceptance-control", "title": "验收与风险控制", "scope": "数量、来源、冲突和缺项检查", "source_ids": all_sources},
        ],
        "conditions": [
            {"text": "不得将推断写成事实", "type": "human"},
            {"text": "冲突和缺项未关闭前只能生成待核草稿", "type": "program"},
        ],
        "missing": ["运维责任人和最终IP地址待业务确认"],
        "conflicts": ["日志留存180天与不少于6个月的口径待统一"],
        "template_version": "frozen-original-v1",
    }
    current = expect(owner_client.patch(
        f"/api/product/tasks/{task_id}/blueprint/",
        data=json.dumps({"expected_version": current["version"], "payload": blueprint_payload}),
        content_type="application/json",
    ), 200)
    approved = expect(reviewer_client.post(
        f"/api/product/tasks/{task_id}/decisions/",
        data=json.dumps({
            "expected_version": current["version"], "target": "blueprint",
            "target_id": current["blueprint"]["id"], "sha256": current["blueprint"]["sha256"],
            "decision": "approve", "comment": "批准当前精确蓝图版本用于代表性待核草稿；冲突和缺项不得视为关闭。",
        }), content_type="application/json",
    ), 201)["task"]

    chapters = [
        ("project-understanding", "项目理解与边界", [
            "本项目的已知设备范围为安全接入网关2台、集中审计节点1套。资料同时表明现网边界采用双链路。",
            "日志留存口径存在180天与不少于6个月两种记载；运维责任人和最终IP地址尚未提供，均作为待确认事项保留。",
        ]),
        ("overall-design", "总体技术方案", [
            "事实边界是双链路现网、2台安全接入网关和1套集中审计节点。",
            "方案建议采用并行建设和分步切换，以响应不停机约束；该表述是工程推断，须由项目审核人确认后实施。",
        ]),
        ("implementation-plan", "实施与切换计划", [
            "代表性资料给出的计划实施窗口为2026-10-15至2026-10-18。实施前先确认运维责任人、最终IP地址和日志留存口径。",
            "切换按准备、并行验证、分步迁移和回退确认执行；未确认事项不得在现场自行补造。",
        ]),
        ("acceptance-control", "验收与风险控制", [
            "数量核对范围为安全接入网关2台、集中审计节点1套；验收记录必须关联当前输入版本与来源哈希。",
            "内容检查必须分别核对事实、推断、冲突和缺项。冲突和缺项关闭前，本文件保持待核草稿状态。",
        ]),
    ]
    current = approved
    for chapter_id, title, paragraphs in chapters:
        current = expect(owner_client.post(
            f"/api/product/tasks/{task_id}/chapters/",
            data=json.dumps({
                "expected_version": current["version"], "chapter_id": chapter_id, "title": title,
                "paragraphs": paragraphs, "source_ids": all_sources,
            }), content_type="application/json",
        ), 201)["task"]

    queued = expect(owner_client.post(
        f"/api/product/tasks/{task_id}/queue/",
        data=json.dumps({"expected_version": current["version"], "action": "render"}),
        content_type="application/json",
    ), 200)
    if queued["state"] != "QUEUED" or not run_once():
        raise RuntimeError("real worker did not claim the queued render action")
    task = DocumentTask.objects.get(pk=task_id)
    artifact = DocumentArtifact.objects.get(task=task)
    original = verified_artifact(artifact)
    if sha256(original) != artifact.sha256:
        raise RuntimeError("persisted artifact hash mismatch")

    with override_settings(PRODUCT_OFFICE_RENDER_ENABLED=True):
        office = render_office(artifact.path, artifact.sha256)
    artifact.render_evidence = {**artifact.render_evidence, "draft_office_render": office}
    artifact.save(update_fields=["render_evidence"])

    artifacts_dir = EVIDENCE / "artifacts"
    render_dir = EVIDENCE / "word-render"
    final_record = relative_copy(storage, {"path": artifact.path, "sha256": artifact.sha256}, artifacts_dir / "园区安全接入与集中审计技术方案-代表性草稿-v1.docx")
    pdf_record = relative_copy(storage, office["pdf"], render_dir / "园区安全接入与集中审计技术方案-代表性草稿-v1.pdf")
    page_records = [
        {"page": page["page"], **relative_copy(storage, page, render_dir / f"page-{page['page']:03d}.png")}
        for page in office["pages"]
    ]

    detail = expect(owner_client.get(f"/api/product/tasks/{task_id}/"), 200)
    review = DocumentRevision.objects.get(task=task, kind="review")
    attempt = DocumentAttempt.objects.get(task=task, action="render")
    approval = task.approvals.get(revision_id=detail["blueprint"]["id"], decision="approve")

    probe = expect(owner_client.post(
        "/api/product/tasks/", data=json.dumps({
            "title": "RAGFlow 未配置阻塞探针", "input": input_payload, "reviewer_id": reviewer.pk,
        }), content_type="application/json", HTTP_IDEMPOTENCY_KEY="p1-ragflow-unconfigured-probe-v1",
    ), 201)
    expect(owner_client.post(
        f"/api/product/tasks/{probe['id']}/queue/",
        data=json.dumps({"expected_version": probe["version"], "action": "retrieve"}),
        content_type="application/json",
    ), 200)
    if not run_once():
        raise RuntimeError("retrieval blocker probe was not claimed")
    probe_task = DocumentTask.objects.get(pk=probe["id"])
    if probe_task.state != "WAITING_INPUT" or probe_task.error_code != "retrieval_disabled":
        raise RuntimeError("retrieval blocker probe did not preserve expected state")

    formal_body = expect(reviewer_client.post(
        f"/api/product/tasks/{task_id}/decisions/",
        data=json.dumps({
            "expected_version": detail["version"], "target": "artifact", "target_id": str(artifact.pk),
            "sha256": artifact.sha256, "decision": "approve", "comment": "该请求应被发布门禁拒绝。",
        }), content_type="application/json",
    ), 409)
    if formal_body.get("code") != "formal_release_blocked":
        raise RuntimeError("draft formal-release gate returned an unexpected result")

    chain = {
        "recorded_at": datetime.now(timezone.utc).isoformat(), "authority": "docs/SPEC.md",
        "boundary": {
            "database": str(database.relative_to(ROOT)), "storage": str(storage.relative_to(ROOT)),
            "representative_input_only": True, "formal_database_modified": False,
            "real_customer_data_used": False, "mock_model_used": False, "mock_ragflow_used": False,
        },
        "external_dependencies": {
            "ragflow": {"status": "BLOCKED_NOT_CONFIGURED", "probe_state": probe_task.state, "error_code": probe_task.error_code},
            "model": {"status": "NOT_VERIFIED_NOT_CONFIGURED", "calls": 0},
        },
        "task": {"id": str(task.pk), "state": task.state, "stage": task.stage, "version": task.version},
        "sources": [{
            "id": str(source.pk), "name": source.original_name, "sha256": source.sha256,
            "size": source.size, "parsed": source.parsed,
        } for source in task.sources.order_by("created_at")],
        "representative_input_files": source_input_evidence,
        "statements": detail["input"].get("statements", []),
        "issue_history": detail["input"].get("issue_history", []),
        "versions": [{
            "id": str(record.pk), "kind": record.kind, "family": record.family, "version": record.version,
            "sha256": record.sha256, "input_hash": record.input_hash, "blueprint_hash": record.blueprint_hash,
        } for record in task.revisions.order_by("version")],
        "blueprint_approval": {
            "id": str(approval.pk), "revision_id": str(approval.revision_id), "actor_id": approval.actor_id,
            "decision": approval.decision, "sha256": approval.sha256, "authorization": approval.authorization,
        },
        "review": {"id": str(review.pk), "sha256": review.sha256, "payload": review.payload},
        "worker_attempt": {
            "id": str(attempt.pk), "action": attempt.action, "status": attempt.status,
            "model_calls": attempt.model_calls, "fence": attempt.fence, "error_code": attempt.error_code,
        },
        "artifact": {
            "id": str(artifact.pk), "version": artifact.version, "path": artifact.path, "sha256": artifact.sha256,
            "input_hash": artifact.input_hash, "blueprint_hash": artifact.blueprint_hash,
            "review_id": str(artifact.review_id), "template_hash": artifact.template_hash,
            "generation_hash": artifact.generation_hash, "approved": False,
            "formal_approval_attempt": formal_body, "delivered_copy": final_record,
        },
        "word_render": {
            "renderer": "Microsoft Word", "status": office["status"], "verified": office["verified"],
            "generation_sha256": office["generation_sha256"], "rendered_docx": office["rendered_docx"],
            "page_count": office["page_count"], "pdf": pdf_record, "pages": page_records,
            "visual_review": "pending_manual_inspection",
        },
    }
    chain_path = EVIDENCE / "version-chain.json"
    chain_path.write_text(json.dumps(chain, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({
        "result": "CORE_PASS_WITH_EXTERNAL_BLOCKERS", "task_id": str(task.pk),
        "artifact": final_record, "page_count": office["page_count"],
        "chain": chain_path.relative_to(ROOT).as_posix(),
        "ragflow": chain["external_dependencies"]["ragflow"],
        "model": chain["external_dependencies"]["model"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()