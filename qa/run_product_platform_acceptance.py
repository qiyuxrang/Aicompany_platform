"""Run the approved-blueprint output chain against an isolated acceptance database.

Required environment variables point Django at the isolated SQLite database and
private storage root.  This harness deliberately reuses persisted long-form
chapters and replaces only the external model review so that document rendering,
worker orchestration, authorization, output listing, and authenticated downloads
can be verified without a live model or RAGFlow endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402
from portal.product_models import DocumentArtifact, DocumentAttempt, DocumentTask  # noqa: E402
from portal.product_service import append_revision, current_revision  # noqa: E402
from portal.product_worker import run_once  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reset", action="store_true", help="Delete artifacts only in the configured isolated database")
    parser.add_argument("--compact-fixture", action="store_true",
                        help="Append deterministic ~3000/~5000-character report chapters in the isolated database")
    return parser.parse_args()


def append_compact_chapters(task: DocumentTask) -> None:
    """Create source-bounded short content for layout acceptance only."""
    input_revision = current_revision(task, "input")
    blueprint = current_revision(task, "blueprint")
    if input_revision is None or blueprint is None:
        raise RuntimeError("compact fixture requires current input and blueprint revisions")
    paragraphs = [
        "本节依据已上传资料说明当前范围、目标和约束。所有设备名称与数量均以输入清单为准，未提供的品牌、地址、责任人和商务数据不作推断；正式交付前仍须由项目负责人逐项复核来源、适用边界和实施条件。",
        "方案采用分阶段核对方法：先确认业务需求和现状，再校验技术依赖、接口边界与安全控制，随后形成实施步骤和验收要点。每项结论均保留来源关联，缺项与冲突进入待确认清单，不以自动生成内容替代人工决策。",
        "在执行层面，平台负责资料解析、内容组织、版本留痕和成果生成，使用人员负责蓝图审核、修改意见和结果确认。若前置条件未满足，流程保留在当前审核节点；修改与重新生成记录应支持追溯和复盘。",
        "项目实施应建立角色、权限、日志和数据保留规则，确保资料上传、任务执行、人工审核和文件下载均可核验。接口异常、模型超时和文件解析失败时，应记录错误原因并提供重试路径，避免影响已有版本。",
        "验收以功能闭环、内容完整、图文排版、访问控制和可恢复性为主要维度。测试环境先验证关键业务路径，再按实际部署条件补充性能、安全和运维检查；所有结论均应与输入资料和现场条件保持一致。",
    ]
    for family in ("technical-solution", "feasibility"):
        selected = paragraphs[:3] if family == "technical-solution" else paragraphs[:5]
        for chapter in blueprint.payload["chapters"]:
            payload = {
                "chapter_id": chapter["id"],
                "title": chapter["title"],
                "paragraphs": [f"{chapter['title']}：{paragraph}" for paragraph in selected],
                "source_ids": chapter["source_ids"],
            }
            append_revision(task, "chapter", payload, input_hash=input_revision.sha256,
                            blueprint_hash=blueprint.sha256, actor=task.owner,
                            family=family, reason="compact_acceptance_fixture")


def download(client: Client, url: str, target: Path) -> dict:
    response = client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"download failed: {url}: HTTP {response.status_code}")
    target.write_bytes(b"".join(response.streaming_content))
    return {"path": str(target.resolve()), "bytes": target.stat().st_size, "sha256": sha256(target)}


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    task = DocumentTask.objects.select_related("owner").get(pk=args.task_id)

    if args.compact_fixture:
        if DocumentTask.objects.count() != 1:
            raise RuntimeError("--compact-fixture is allowed only for a single-task isolated database")
        append_compact_chapters(task)

    if args.reset:
        if DocumentTask.objects.count() != 1:
            raise RuntimeError("--reset is allowed only for a single-task isolated acceptance database")
        DocumentArtifact.objects.filter(task=task).delete()
        DocumentAttempt.objects.filter(task=task, status="running").update(status="failed", error_code="acceptance_reset")
        task.state = "QUEUED"
        task.stage = "WRITING"
        task.pending_action = "generate_outputs"
        task.error_code = ""
        task.lease_until = None
        task.attempt_count = 0
        task.save()

    synthetic_review = {
        "status": "passed",
        "issues": [],
        "scope": "full",
        "full_document_human_review_required": False,
    }
    if task.state != "COMPLETED":
        with patch("portal.product_worker._review_family", return_value=synthetic_review):
            claimed = run_once()
        if not claimed:
            raise RuntimeError("worker did not claim the acceptance task")

    task.refresh_from_db()
    if task.state != "COMPLETED" or task.error_code:
        raise RuntimeError(f"worker failed: state={task.state}, error={task.error_code}")

    client = Client(HTTP_HOST="localhost")
    client.force_login(task.owner)
    session = client.session
    session["version"] = task.owner.session_version
    session.save()
    output_response = client.get(f"/api/product/tasks/{task.pk}/outputs/")
    if output_response.status_code != 200:
        raise RuntimeError(f"output API failed: HTTP {output_response.status_code}")
    outputs = output_response.json()["outputs"]
    families = {item["family"] for item in outputs}
    if families != {"technical-solution", "feasibility", "presentation"}:
        raise RuntimeError(f"unexpected output families: {sorted(families)}")
    if not all(item["current"] for item in outputs):
        raise RuntimeError("output API returned a stale artifact")

    filenames = {
        "technical-solution": "technical-solution.docx",
        "feasibility": "feasibility-report.docx",
        "presentation": "business-technology-presentation.pptx",
    }
    downloads = {}
    for item in outputs:
        family = item["family"]
        url = (
            f"/api/product/artifacts/{item['id']}/download/"
            if family == "technical-solution"
            else f"/api/product/outputs/{item['id']}/download/"
        )
        downloads[family] = download(client, url, output_dir / filenames[family])
        downloads[family]["api_url"] = url
        downloads[family]["artifact_sha256"] = item["sha256"]
        if downloads[family]["sha256"] != item["sha256"]:
            raise RuntimeError(f"download hash mismatch: {family}")

    artifacts = {
        artifact.family: {
            "id": str(artifact.pk),
            "version": artifact.version,
            "render_evidence": artifact.render_evidence,
        }
        for artifact in task.artifacts.order_by("version")
    }
    result = {
        "task_id": str(task.pk),
        "task_state": task.state,
        "task_stage": task.stage,
        "task_version": task.version,
        "external_model_calls": 0,
        "ragflow_calls": 0,
        "review_stub": "full-pass acceptance fixture",
        "api_outputs": outputs,
        "downloads": downloads,
        "artifacts": artifacts,
    }
    (output_dir / "platform-acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "task_state": task.state,
        "families": sorted(families),
        "downloads": downloads,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
