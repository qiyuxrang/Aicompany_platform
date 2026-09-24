# 产品三件套单用户生成实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将产品事业部三件套改为单用户确认蓝图后自动生成技术方案 Word、可行性研究报告 Word 和 PPT，并移除报告二次审批、应用费用预算门禁，启用仅含指定 Logo 与文档主题的极简封面。

**Architecture:** 复用现有 Django 产品 API、持久 Worker、`three_drafts`、PPT 渲染、artifact 版本/hash/stale 链和 FastAPI 模型网关。蓝图确认仍写入精确 revision/hash 的 `DocumentApproval`，但允许所有者确认；确认后排队统一动作 `generate_outputs`，Worker 顺序生成两类独立章节、内容检查、两份 Word 和 PPT。模板仍由冻结资产包驱动，只修改封面与页眉规则。

**Tech Stack:** Python 3.13、Django 5.2、DRF、SQLite 隔离演示库、React 19、TypeScript、Vitest、FastAPI 模型网关、OOXML/Python 文档运行时、Microsoft Word/PowerPoint。

**Spec:** `docs/superpowers/specs/2026-09-24-product-single-user-generation-design.md`

## Global Constraints

- 新任务不要求 `reviewer`，仅任务所有者可以确认蓝图和生成三件套。
- 蓝图确认绑定当前 revision ID、SHA-256、输入 hash、操作者、授权版本和规则 hash。
- 用户确认蓝图后自动排队 `generate_outputs`；不再要求技术方案/可研报告内容审批，也不再单独排队 PPT。
- 删除 `PORTAL_PRODUCT_COST_POLICY` 的运行时门禁和预算账本，但保留 `PRODUCT_MAX_MODEL_CALLS=24`、attempt、超时、并发和 Token 日志。
- 两份 Word 的封面只含指定 Logo 与文档主题；Logo 源文件为 `C:\Users\BJRunner\Pictures\前端素材\图片1.png`，SHA-256 必须为 `9211d67a0a52e0445fb85e11c032eeec4ff6f3ad6870b8afac7a9d1a463f8a3e`。
- 运行时不得读取外部 Logo 路径；Logo 必须复制进冻结资产目录并登记 manifest hash。
- 删除全部文字页眉，保留 A4、模板现有页边距、目录、正文和底部居中页码。
- 不修改模型网关、`qwen-plus`、RAGFlow、招投标、正式发布和生产部署范围。
- 本轮只做定向测试、前端 typecheck/build 和一个代表性真实模型冒烟；不执行项目全量回归。
- 不新增依赖，不修改或提交 `.runtime/` 中的密钥。

## Review Focus

1. **重复确认同一蓝图**：必须幂等返回同一批准和当前任务，不能启动第二条 Worker 链；由 Task 1 测试。
2. **确认后输入或蓝图被修改**：旧确认与三件套必须失效，不能继续生成或显示 current；由 Task 1、Task 3 测试。
3. **某一类章节或 PPT 生成失败**：attempt 必须失败并保留已完成版本，重试不得重复已有模型调用/成果；由 Task 3 测试。
4. **外部 Logo 被替换或模板 hash 未同步**：`frozen_pack()` 必须 fail-closed；由 Task 5 测试。
5. **其他产品用户伪造任务 ID 或确认请求**：继续返回 404，不泄露任务是否存在；由 Task 1 测试。

---

## 文件结构与责任

- `backend/portal/product_api.py`：所有者蓝图确认、统一动作排队、任务 actions/blockers。
- `backend/portal/product_service.py`：所有者蓝图确认的当前性判定与对象权限。
- `backend/portal/product_worker.py`：移除预算预留，执行统一 `generate_outputs`。
- `backend/portal/product_three_drafts.py`：把“两份 Word”和“PPT”拆成可组合、不自行结束 attempt 的最小 helper。
- `backend/portal/product_pair.py`：PPT 来源链不再依赖报告审批。
- `frontend/src/product/DocumentWorkspace.tsx`：单用户主操作和简化后的状态展示。
- `frontend/src/product/DocumentWorkspace.test.tsx`：单用户交互合同。
- `backend/portal/product_assets/bj_docs/scripts/artifacts.py`：极简封面与无文字页眉渲染。
- `backend/portal/product_assets/bj_docs/assets/**`：Logo、Word 母版、policy、profile、manifest 冻结资产。
- `backend/portal/tests/`：后端契约、Worker、三件套、模板回归。

### Task 1: 所有者确认蓝图并原子排队统一生成动作

**Files:**
- Modify: `backend/portal/product_service.py:249-350`
- Modify: `backend/portal/product_api.py:165-245, 660-810`
- Test: `backend/portal/tests/test_product_api.py`

**Interfaces:**
- Produces: `approved_blueprint(task) -> DocumentRevision | None`，以当前所有者的有效批准为准。
- Produces: `POST /api/product/tasks/{id}/decisions/`，`target=blueprint, decision=approve` 时由所有者确认并设置 `pending_action="generate_outputs"`。
- Consumes: 现有 `_decision_target()`、`approval_authorization()`、`require_version()`、fence/version 机制。

- [ ] **Step 1: 将测试 helper 改为所有者确认蓝图，并写失败测试**

在 `backend/portal/tests/test_product_api.py` 将 `approve_blueprint()` 改为使用 `owner_client`，并新增：

```python
def test_owner_confirms_current_blueprint_and_queues_all_outputs(self):
    task = self.create_task(reviewer=False)
    current = self.save_blueprint(task)

    response = self.owner_client.post(
        f"/api/product/tasks/{task['id']}/decisions/",
        json_body(
            expected_version=current["version"],
            target="blueprint",
            target_id=current["blueprint"]["id"],
            sha256=current["blueprint"]["sha256"],
            decision="approve",
            comment="确认当前蓝图并生成三件套",
        ),
        content_type="application/json",
    )

    self.assertEqual(response.status_code, 201, response.content)
    result = response.json()["task"]
    self.assertEqual(result["owner_id"], self.owner.pk)
    self.assertIsNone(result["reviewer_id"])
    self.assertEqual(result["state"], "QUEUED")
    self.assertEqual(result["pending_action"], "generate_outputs")
    self.assertIn("generate_outputs", result["actions"])
```

新增越权和幂等测试：

```python
def test_other_user_cannot_confirm_blueprint_and_owner_replay_is_idempotent(self):
    task = self.create_task(reviewer=False)
    current = self.save_blueprint(task)
    body = json_body(
        expected_version=current["version"], target="blueprint",
        target_id=current["blueprint"]["id"], sha256=current["blueprint"]["sha256"],
        decision="approve", comment="确认",
    )

    denied = self.other_client.post(
        f"/api/product/tasks/{task['id']}/decisions/", body,
        content_type="application/json",
    )
    first = self.owner_client.post(
        f"/api/product/tasks/{task['id']}/decisions/", body,
        content_type="application/json",
    )
    replay_body = json_body(**{**json.loads(body), "expected_version": first.json()["task"]["version"]})
    replay = self.owner_client.post(
        f"/api/product/tasks/{task['id']}/decisions/", replay_body,
        content_type="application/json",
    )

    self.assertEqual(denied.status_code, 404)
    self.assertEqual(first.status_code, 201)
    self.assertEqual(replay.status_code, 200)
    self.assertEqual(replay.json()["approval_id"], first.json()["approval_id"])
    self.assertEqual(DocumentApproval.objects.filter(task_id=task["id"], revision__kind="blueprint").count(), 1)
```

- [ ] **Step 2: 运行测试确认按旧双角色规则失败**

Run:

```powershell
uv run --env-file .runtime/local.env python backend/manage.py test portal.tests.test_product_api.ProductApiTests.test_owner_confirms_current_blueprint_and_queues_all_outputs portal.tests.test_product_api.ProductApiTests.test_other_user_cannot_confirm_blueprint_and_owner_replay_is_idempotent --verbosity 2
```

Expected: FAIL，所有者当前不能进入 `review=True` 路径或 `approved_blueprint()` 拒绝 owner approval。

- [ ] **Step 3: 最小修改所有者蓝图确认合同**

在 `product_service.py`：

```python
def approved_blueprint(task):
    if not task.blueprint_version:
        return None
    revision = task.revisions.filter(
        kind=DocumentRevision.Kind.BLUEPRINT,
        version=task.blueprint_version,
    ).first()
    current_input = task.revisions.filter(
        kind=DocumentRevision.Kind.INPUT,
        version=task.input_version,
    ).first()
    if not revision or not current_input or revision.input_hash != current_input.sha256:
        return None
    latest = revision.approvals.filter(actor_id=task.owner_id).select_related("actor").order_by("-created_at").first()
    return revision if (
        latest
        and latest.decision == DocumentApproval.Decision.APPROVE
        and product_user_allowed(latest.actor)
        and approval_current(task, latest)
    ) else None
```

将 `approval_authorization()` 的策略指纹改为单用户模式，不依赖 reviewer ID：

```python
return {
    "owner_grant_version": versions.get(task.owner_id),
    "actor_grant_version": versions.get(actor.pk),
    "policy_version": "single-owner-blueprint-v1",
    "rules_hash": current_rules,
}
```

在 `product_api.decisions()` 中仅对蓝图允许 owner 路径：

```python
if body["target"] == "blueprint":
    task = task_for(request.user, task_id, write=True)
    require_owner(task, request.user)
else:
    task = task_for(request.user, task_id, write=True, review=True)
```

蓝图批准分支改为：

```python
task.pending_action = "generate_outputs"
task.stage = DocumentTask.Stage.WRITING
blocked = not settings.PRODUCT_MODEL_CALLS_ALLOWED
task.state = DocumentTask.State.WAITING_INPUT if blocked else DocumentTask.State.QUEUED
task.error_code = "model_authorization_required" if blocked else ""
task.fence += 1
```

`_task_actions()` 对任务所有者在 `WAITING_REVIEW/BLUEPRINT` 返回 `confirm_blueprint`；移除 `assign_reviewer` 和 reviewer 分支中的 `review_blueprint`。确认后运行中不再返回第二个生成动作。

- [ ] **Step 4: 运行定向 API 测试**

Run:

```powershell
uv run --env-file .runtime/local.env python backend/manage.py test portal.tests.test_product_api --verbosity 1
```

Expected: PASS。若旧双角色断言失败，只更新与新规范直接冲突的断言，不降低对象权限、stale 或版本校验。

- [ ] **Step 5: 提交**

```bash
git add backend/portal/product_service.py backend/portal/product_api.py backend/portal/tests/test_product_api.py
git commit -m "feat: allow owner blueprint confirmation"
```

### Task 2: 删除应用内费用预算门禁

**Files:**
- Modify: `backend/portal/product_worker.py:11-17, 96-125, 377-392`
- Modify: `backend/portal/product_api.py:211-236`
- Delete: `backend/portal/tests/test_product_budget.py`
- Modify: `backend/portal/tests/test_product_worker.py`
- Modify: `backend/config/settings.py:89-105`

**Interfaces:**
- Produces: `_model()` 在模型许可、技术调用上限和路由权限通过后直接调用网关。
- Removes: `reserve_call()`、`PRODUCT_COST_POLICY`、预算错误码。
- Preserves: `attempt.model_calls`、checkpoint calls、Token 日志、`PRODUCT_MAX_MODEL_CALLS`。

- [ ] **Step 1: 写无预算配置也会进入网关的失败测试**

用以下测试替换 `test_missing_cost_approval_blocks_before_any_outbound`：

```python
@override_settings(PRODUCT_COST_POLICY={})
@patch("portal.product_worker.generate_for_use")
def test_missing_cost_policy_does_not_block_model_call(self, model):
    model.return_value = {
        "content": json.dumps(self.blueprint_payload),
        "prompt_tokens": 10,
        "completion_tokens": 20,
    }
    run_once()
    self.task.refresh_from_db()
    self.assertEqual(self.task.error_code, "")
    model.assert_called_once()
    self.assertNotIn("budget", self.task.checkpoint)
```

- [ ] **Step 2: 运行测试确认旧预算门禁导致失败**

Run:

```powershell
uv run --env-file .runtime/local.env python backend/manage.py test portal.tests.test_product_worker.ProductWorkerTests.test_missing_cost_policy_does_not_block_model_call --verbosity 2
```

Expected: FAIL，当前返回 `budget_authorization_required` 且网关未调用。

- [ ] **Step 3: 删除预算调用和 blocker**

在 `product_worker.py` 删除：

```python
from .product_budget import reserve_call
reserve_call(task, route)
```

从 allowed/state error 集合中删除四个预算错误码。

在 `product_api._task_blockers()` 删除：

```python
elif not getattr(settings, "PRODUCT_COST_POLICY", {}):
    blockers[action] = {
        "code": "budget_authorization_required",
        "detail": "尚无覆盖当前模型路由的真实预算批准记录，不能发起调用。",
    }
```

在 `settings.py` 删除：

```python
PRODUCT_COST_POLICY = _product_configuration("PORTAL_PRODUCT_COST_POLICY")
```

删除 `test_product_budget.py`；保留 `product_budget.py` 一轮兼容期不被任何运行时代码引用，避免本批同时处理历史数据清理。最终全量测试前确认无调用者后再决定删除源文件。

- [ ] **Step 4: 运行 Worker 与 API blocker 定向测试**

Run:

```powershell
uv run --env-file .runtime/local.env python backend/manage.py test portal.tests.test_product_worker portal.tests.test_product_api --verbosity 1
```

Expected: PASS；任务 checkpoint 仍包含调用次数和 Token，不出现 budget ledger。

- [ ] **Step 5: 提交**

```bash
git add backend/config/settings.py backend/portal/product_worker.py backend/portal/product_api.py backend/portal/tests/test_product_worker.py
git rm backend/portal/tests/test_product_budget.py
git commit -m "refactor: remove product budget gate"
```

### Task 3: 实现 `generate_outputs` 单次 Worker 链

**Files:**
- Modify: `backend/portal/product_worker.py:300-400`
- Modify: `backend/portal/product_three_drafts.py`
- Modify: `backend/portal/product_pair.py`
- Modify: `backend/portal/product_api.py:670-715, 850-865`
- Test: `backend/portal/tests/test_product_three_drafts.py`
- Test: `backend/portal/tests/test_product_pair.py`
- Test: `backend/portal/tests/test_product_worker.py`

**Interfaces:**
- Produces: `generate_report_drafts(task, fence, attempt_id, input_revision, blueprint) -> list[DocumentArtifact]`，不结束 attempt。
- Produces: `generate_presentation_artifact(task, fence, attempt_id, input_revision, blueprint) -> DocumentArtifact`，不结束 attempt。
- Produces: `pending_action="generate_outputs"`，一个 attempt 完成三件套。
- Consumes: `_model()`、`validate_chapter()`、`content_checks()`、`save_report_content()`、现有 generation hash/idempotency。

- [ ] **Step 1: 将三件套测试改成单用户一次动作并先失败**

将 `test_worker_requires_two_content_approvals_before_editable_presentation` 替换为：

```python
@patch("portal.product_worker.generate_for_use")
def test_owner_blueprint_confirmation_generates_three_outputs_without_report_approval(self, model):
    responses = []
    for family in ("technical-solution", "feasibility"):
        responses.append(json.dumps({
            "chapter_id": "overview",
            "title": "项目概述",
            "paragraphs": [f"{family} 代表性正文。"],
            "source_ids": ["1"],
        }))
        responses.append(json.dumps({"passed": True, "issues": []}))
    model.side_effect = [
        {"content": value, "prompt_tokens": 10, "completion_tokens": 20}
        for value in responses
    ]
    task = self.create_task(reviewer=False)
    current = self.save_blueprint(task)
    approved = self.approve_blueprint(current)["task"]

    self.assertEqual(approved["pending_action"], "generate_outputs")
    self.assertTrue(run_once())

    outputs = self.owner_client.get(
        f"/api/product/tasks/{task['id']}/outputs/"
    ).json()["outputs"]
    self.assertEqual(
        {item["family"] for item in outputs},
        {"technical-solution", "feasibility", "presentation"},
    )
    self.assertTrue(all(item["current"] for item in outputs))
    self.assertFalse(DocumentApproval.objects.filter(task_id=task["id"], revision__kind="report").exists())
    record = DocumentTask.objects.get(pk=task["id"])
    self.assertEqual(record.state, "COMPLETED")
    self.assertEqual(record.pending_action, "")
```

新增部分失败重试测试：第一次 PPT renderer 抛 `presentation_unavailable`，第二次重试只重做 PPT，两个 Word generation hash 和版本数量不变。

- [ ] **Step 2: 运行三件套测试确认旧分步流程失败**

Run:

```powershell
uv run --env-file .runtime/local.env python backend/manage.py test portal.tests.test_product_three_drafts --verbosity 2
```

Expected: FAIL，旧 Worker 不认识 `generate_outputs`，PPT 仍要求 report approval。

- [ ] **Step 3: 提取可组合的 artifact helper**

在 `product_three_drafts.py`：

```python
def generate_report_drafts(task, fence, attempt_id, input_revision, blueprint):
    artifacts = []
    for family in ("technical-solution", "feasibility"):
        # 复用当前 generation hash、verified_artifact、REPORT revision 和 DocumentArtifact 创建逻辑
        artifacts.append(artifact)
    return artifacts


def generate_presentation_artifact(task, fence, attempt_id, input_revision, blueprint):
    # 复用当前 presentation generation hash 与 DocumentArtifact 创建逻辑
    return artifact
```

保留旧 `generate_three_drafts()` / `generate_presentation()` 作为薄包装，仅供历史 pending action 重试：调用 helper 后 `_finish()`。

- [ ] **Step 4: 让 PPT 来源链不依赖报告审批**

在 `product_pair.py` 删除 `effective_report_approval()` 门禁。`sources` 改成：

```python
sources = [{
    "family": report.family,
    "id": str(report.pk),
    "version": report.version,
    "sha256": report.sha256,
    "created_by": report.created_by_id,
    "created_at": report.created_at.isoformat(),
} for report in reports]
```

`pair_snapshot()` 仍必须检查两份 report 是当前 input/blueprint/chapters 派生，不能放松 stale 检查。

- [ ] **Step 5: 在 Worker 中生成两类独立章节并组合三件套**

新增小函数：

```python
def _generate_family(task_id, fence, attempt_id, task, input_revision, blueprint, family):
    from .product_service import validate_chapter

    chapters = current_chapters(task, input_revision.sha256, blueprint.sha256, family)
    for chapter in blueprint.payload["chapters"]:
        if chapter["id"] in chapters:
            continue
        payload = _model(
            task_id,
            fence,
            attempt_id,
            settings.PRODUCT_WRITING_ROUTE,
            {
                "action": "chapter",
                "family": family,
                "approved_blueprint": {
                    "purpose": blueprint.payload["purpose"],
                    "audience": blueprint.payload["audience"],
                    "conditions": blueprint.payload["conditions"],
                    "chapter": chapter,
                },
                "input": model_input(task, input_revision.payload, chapter["source_ids"]),
                "chapter": chapter,
                "schema": {
                    "chapter_id": chapter["id"],
                    "title": chapter["title"],
                    "paragraphs": ["string"],
                    "source_ids": [],
                },
            },
        )
        if payload.get("chapter_id") != chapter["id"] or payload.get("title") != chapter["title"]:
            raise ExecutionError("invalid_model_output")
        validate_chapter(payload)
        if not set(payload["source_ids"]) <= set(chapter["source_ids"]):
            raise ExecutionError("invalid_model_output")
        chapters[chapter["id"]] = _store(
            task_id,
            fence,
            "chapter",
            payload,
            input_revision.sha256,
            blueprint.sha256,
            family=family,
        )
    ordered = [chapters.get(item["id"]) for item in blueprint.payload["chapters"]]
    if not ordered or any(item is None for item in ordered):
        raise ExecutionError("chapters_incomplete")
    return ordered
```

将 `_store()` 签名扩展为：

```python
def _store(task_id, fence, kind, payload, input_hash, blueprint_hash="", family="technical-solution"):
    task = _guard(task_id, fence)
    record = append_revision(
        task,
        kind,
        payload,
        input_hash=input_hash,
        blueprint_hash=blueprint_hash,
        family=family,
        reason=f"worker_{kind}",
    )
    # 保留现有 checkpoint、lease、blueprint_version 更新逻辑
    return record
```

`execute_claim()` 新分支：

```python
if task.pending_action == "generate_outputs":
    from .product_three_drafts import generate_presentation_artifact, generate_report_drafts

    for family in ("technical-solution", "feasibility"):
        chapters = _generate_family(
            task_id, fence, attempt_id, task, input_revision, blueprint, family,
        )
        checked = content_checks(input_revision.payload, blueprint.payload, chapters)
        reviewer = _model(
            task_id,
            fence,
            attempt_id,
            settings.PRODUCT_REVIEW_ROUTE,
            {
                "action": "independent_review",
                "family": family,
                "input": model_input(
                    task,
                    input_revision.payload,
                    [source for chapter in blueprint.payload["chapters"] for source in chapter["source_ids"]],
                ),
                "conditions": blueprint.payload["conditions"],
                "chapters": [chapter.payload for chapter in chapters],
                "schema": {"passed": "boolean", "issues": ["string"]},
            },
        )
        if (
            set(reviewer) != {"passed", "issues"}
            or type(reviewer["passed"]) is not bool
            or not isinstance(reviewer["issues"], list)
        ):
            raise ExecutionError("invalid_model_output")
        checked["model_review"] = {
            "status": "passed" if reviewer["passed"] and not reviewer["issues"] else "issues_found",
            "issues": reviewer["issues"],
        }
        checked["chapter_hashes"] = {
            chapter.payload["chapter_id"]: chapter.sha256 for chapter in chapters
        }
        checked["passed"] = not checked["issues"] and checked["model_review"]["status"] == "passed"
        _store(
            task_id,
            fence,
            "review",
            checked,
            input_revision.sha256,
            blueprint.sha256,
            family=family,
        )
    generate_report_drafts(task, fence, attempt_id, input_revision, blueprint)
    generate_presentation_artifact(task, fence, attempt_id, input_revision, blueprint)
    return _finish(task_id, fence, attempt_id, "COMPLETED", "FINAL_REVIEW")
```

`_guard()`、`_queue()`、`retry()` 的 action 集合加入 `generate_outputs`。

- [ ] **Step 6: 运行 Worker、pair、三件套定向测试**

Run:

```powershell
uv run --env-file .runtime/local.env python backend/manage.py test portal.tests.test_product_worker portal.tests.test_product_pair portal.tests.test_product_three_drafts --verbosity 1
```

Expected: PASS；无 report approval 记录也能生成 PPT，stale/权限/历史下载测试仍通过。

- [ ] **Step 7: 提交**

```bash
git add backend/portal/product_worker.py backend/portal/product_three_drafts.py backend/portal/product_pair.py backend/portal/product_api.py backend/portal/tests/test_product_worker.py backend/portal/tests/test_product_pair.py backend/portal/tests/test_product_three_drafts.py
git commit -m "feat: generate product outputs after blueprint confirmation"
```

### Task 4: 简化产品前端为单用户主流程

**Files:**
- Modify: `frontend/src/product/DocumentWorkspace.tsx:430-520`
- Modify: `frontend/src/product/product-api.ts`
- Modify: `frontend/src/product/DocumentWorkspace.test.tsx`

**Interfaces:**
- Consumes: 后端 action `confirm_blueprint`、状态 `QUEUED/RUNNING/COMPLETED`、三类 outputs。
- Produces: 按钮“确认蓝图并生成三件套”，调用现有 decisions endpoint。

- [ ] **Step 1: 更新 mock task actions 并写失败交互测试**

将测试 fixture actions 精简为：

```typescript
actions: [
  "edit", "add_source", "review_input", "add_statement",
  "queue_retrieve", "queue_blueprint", "save_blueprint",
  "confirm_blueprint", "cancel", "retry",
],
```

新增测试：

```typescript
it("同一用户确认蓝图后直接排队生成三件套", async () => {
  current = task({
    state: "WAITING_REVIEW",
    stage: "BLUEPRINT",
    reviewer_id: null,
    actions: ["confirm_blueprint"],
  });
  await openTask();
  fill("确认说明", "确认当前结构并生成三件套");
  await click("确认蓝图并生成三件套");

  expect(writes().at(-1)?.path).toBe("/api/product/tasks/task-1/decisions/");
  expect(bodyOfLastWrite()).toEqual({
    expected_version: 2,
    target: "blueprint",
    target_id: "blueprint-1",
    sha256: "blueprint-sha",
    decision: "approve",
    comment: "确认当前结构并生成三件套",
  });
  expect(screen.queryByLabelText("指定审核人 ID")).toBeNull();
  expect(screen.queryByText("结构化内容审核")).toBeNull();
  expect(screen.queryByRole("button", { name: /生成 PPT/ })).toBeNull();
});
```

- [ ] **Step 2: 运行前端定向测试确认失败**

Run:

```powershell
pnpm --dir frontend test -- DocumentWorkspace.test.tsx
```

Expected: FAIL，当前页面仍显示审核人和多个审批/生成按钮。

- [ ] **Step 3: 最小简化 UI**

在 `DocumentWorkspace.tsx`：

- 删除 `assignedReviewer`、`assignmentReason`、报告审批和 artifact 审批相关本地状态及控件；
- 将“审核意见”改为“确认说明”；
- 单一按钮：

```tsx
<button
  disabled={disabled("confirm_blueprint") || !task.blueprint}
  onClick={() => decision("blueprint", "approve")}
>
  确认蓝图并生成三件套
</button>
```

- 删除“生成正文”“生成 Word 草稿”“生成两份 Word”“从已批准内容生成 PPT”按钮；
- 保留 outputs 卡片、历史下载、stale、重试和取消；
- 报告状态显示“已生成/已过期”，不再显示“已批准/待审核”；
- 状态提示明确“生成完成仍为草稿，不代表正式发布”。

- [ ] **Step 4: 运行前端定向测试、类型检查和构建**

Run:

```powershell
pnpm --dir frontend test -- DocumentWorkspace.test.tsx
pnpm --dir frontend typecheck
pnpm --dir frontend build
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/product/DocumentWorkspace.tsx frontend/src/product/product-api.ts frontend/src/product/DocumentWorkspace.test.tsx
git commit -m "feat: simplify product generation workflow"
```

### Task 5: 极简 Logo 封面和无文字页眉

**Files:**
- Modify binary: `backend/portal/product_assets/bj_docs/assets/technical-solution/template.docx`
- Modify binary: `backend/portal/product_assets/bj_docs/assets/feasibility/template.docx`
- Replace: `backend/portal/product_assets/bj_docs/assets/company/company-logo.png`
- Modify: `backend/portal/product_assets/bj_docs/assets/document-format-policy.json`
- Modify: `backend/portal/product_assets/bj_docs/assets/technical-solution/profile.json`
- Modify: `backend/portal/product_assets/bj_docs/assets/feasibility/profile.json`
- Modify: `backend/portal/product_assets/bj_docs/manifest.json`
- Modify: `backend/portal/product_assets/bj_docs/scripts/artifacts.py`
- Test: `backend/portal/tests/test_product_documents.py`

**Interfaces:**
- Consumes: `metadata.title` 已由 `_family_document_title()` 生成带文档类型的主题。
- Produces: Word 封面只有冻结 Logo 与 `metadata.title`，无其他固定文本和文字页眉。

- [ ] **Step 1: 写模板结构失败测试**

在 `test_product_documents.py` 新增：

```python
def test_word_cover_contains_only_logo_and_document_topic_without_text_header(self):
    for family, expected_title in (
        ("technical-solution", "合成方案（技术方案）"),
        ("feasibility", "合成方案（可行性研究报告）"),
    ):
        result = render_report_draft(
            self.task, self.input_revision, self.blueprint, [self.chapter], family,
        )
        target = Path(self.storage.name) / result["path"]
        with ZipFile(target) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
            headers = "".join(
                archive.read(name).decode("utf-8")
                for name in archive.namelist()
                if name.startswith("word/header") and name.endswith(".xml")
            )
            media = [name for name in archive.namelist() if name.startswith("word/media/")]
        self.assertIn(expected_title, document)
        self.assertEqual(len(media), 1)
        self.assertNotIn("西安工业大学毕业设计（论文）", headers)
        self.assertNotIn("陕西省一二三数字信息技术有限公司", document)
        self.assertNotIn("建设单位", document)
        self.assertNotIn("编制单位", document)
```

补 frozen asset 测试：

```python
policy = json.loads((PACK / "assets/document-format-policy.json").read_text(encoding="utf-8"))
self.assertEqual(
    policy["company_identity"]["logo_sha256"],
    "9211d67a0a52e0445fb85e11c032eeec4ff6f3ad6870b8afac7a9d1a463f8a3e",
)
for relative in (
    "assets/company/company-logo.png",
    "assets/technical-solution/template.docx",
    "assets/feasibility/template.docx",
    "assets/document-format-policy.json",
):
    entry = next(item for item in manifest["files"] if item["path"] == relative)
    target = PACK / relative
    self.assertEqual(entry["bytes"], target.stat().st_size)
    self.assertEqual(entry["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
```

- [ ] **Step 2: 运行文档测试确认旧封面和页眉导致失败**

Run:

```powershell
uv run --env-file .runtime/local.env python backend/manage.py test portal.tests.test_product_documents --verbosity 2
```

Expected: FAIL，当前文档包含旧文字页眉、重复公司名称或可研封面字段。

- [ ] **Step 3: 替换冻结 Logo**

执行：

```powershell
Copy-Item -LiteralPath "C:\Users\BJRunner\Pictures\前端素材\图片1.png" `
  -Destination "backend\portal\product_assets\bj_docs\assets\company\company-logo.png" -Force
```

立即校验 SHA-256 必须为：

```text
9211d67a0a52e0445fb85e11c032eeec4ff6f3ad6870b8afac7a9d1a463f8a3e
```

不匹配则停止，不更新 manifest。

- [ ] **Step 4: 修改 Word 生成器**

在 `artifacts.py`：

- `add_cover_identity()` 只在标题前插入 Logo，删除技术方案额外公司名称段落；
- 不再创建 `ordinary_header_id` 和 chapter header；
- 将 `configure_section_header()` 改为删除模板已有的全部 `headerReference/titlePg`，不增加新 header；
- 保留 footer page field；
- 章节分节只复制无 header 的 section 属性。

最小接口：

```python
def clear_section_headers(section):
    for node in section.findall("w:headerReference", NS) + section.findall("w:titlePg", NS):
        section.remove(node)
```

在 `_write_word()` 对全部 section 调用 `clear_section_headers()`，删除 `add_header_part()` 的运行时调用和章标题页眉重建逻辑。

- [ ] **Step 5: 将两个母版压缩为极简封面占位结构**

使用现有文档运行时执行一次受控 OOXML 更新：

- 技术方案母版正文前置段落只保留 `{{title}}`、目录域/“目录”、`{{body}}`；
- 可研母版删除 `{{subtitle}}`、`建设单位：{{owner}}`、`编制单位：{{organization}}`、`{{date}}`，保留 `{{title}}`、目录域/“目录”、`{{body}}`；
- 保留母版原有 section/page/footer、TOC field 和 paragraph styles；
- 不重新创建空白 Word 文档，避免丢失目录域和样式原型。

生成后以 OOXML 断言：每个母版恰好包含一个 `{{title}}`、一个 `{{body}}` 和 TOC field，不含上述删除字段。

- [ ] **Step 6: 更新 policy、profile 和 manifest hash**

`document-format-policy.json` 修改：

```json
{
  "status": "approved_interim_baseline_v3",
  "company_identity": {
    "logo_sha256": "9211d67a0a52e0445fb85e11c032eeec4ff6f3ad6870b8afac7a9d1a463f8a3e",
    "cover_logo_width_mm": 50,
    "placement": "centered_above_title"
  },
  "cover": {
    "fields": ["logo", "document_topic"],
    "document_topic": "task_title_with_family_suffix"
  },
  "header_footer": {
    "ordinary_header": null,
    "chapter_opening_header": null,
    "page_number": {"position": "bottom_center"}
  }
}
```

保留其他版式规则。重新计算并写入：

- 两个 template 的 `profile.json.template_sha256`；
- manifest 中 Logo、两个 template、policy 的 `bytes` 与 `sha256`；
- manifest 自身不保存自己的 hash。

- [ ] **Step 7: 运行文档定向测试和真实 Office 打开检查**

Run:

```powershell
uv run --env-file .runtime/local.env python backend/manage.py test portal.tests.test_product_documents portal.tests.test_product_rendering --verbosity 1
```

然后生成一份技术方案和一份可研代表性 DOCX，使用 Word COM 只读打开并断言页数大于 0；将渲染输出放入 `.runtime/`，不提交。

Expected: PASS；两份 Word 封面只有 Logo 和主题，无文字页眉，页码和目录域仍存在。

- [ ] **Step 8: 提交**

```bash
git add backend/portal/product_assets/bj_docs backend/portal/tests/test_product_documents.py
git commit -m "feat: apply minimal product document cover"
```

### Task 6: 端到端定向验证、状态更新和 checkpoint

**Files:**
- Modify: `docs/product/T-R01/TASK_STATUS.md`
- Create: `docs/product/T-R01/evidence/SINGLE_USER_GENERATION_SMOKE_20260924.md`
- Modify: `deliverables/企业平台SSD_V1_20260923/EXECUTION_STATUS.md`

**Interfaces:**
- Consumes: Tasks 1–5 的 API、Worker、UI 和模板成果。
- Produces: 一个脱敏代表性任务的真实模型/浏览器/Office 证据。

- [ ] **Step 1: 运行批准范围内的定向自动化验证**

Run:

```powershell
uv run --env-file .runtime/local.env python backend/manage.py check
uv run --env-file .runtime/local.env python backend/manage.py makemigrations --check --dry-run
uv run --env-file .runtime/local.env python backend/manage.py test `
  portal.tests.test_product_api `
  portal.tests.test_product_worker `
  portal.tests.test_product_pair `
  portal.tests.test_product_three_drafts `
  portal.tests.test_product_documents `
  portal.tests.test_product_rendering `
  --verbosity 1
pnpm --dir frontend test -- DocumentWorkspace.test.tsx
pnpm --dir frontend typecheck
pnpm --dir frontend build
```

Expected: 0 failure / 0 error；不运行其余全量测试。

- [ ] **Step 2: 备份隔离演示数据库并重启服务**

备份：

```powershell
Copy-Item .runtime/product-demo-fast.sqlite3 `
  .runtime/product-demo-fast.before-single-user.sqlite3 -Force
```

使用现有 `.runtime/local.env`、18320 门户、18410 网关和产品 Worker。确认两个 `/health` 均为 200。

- [ ] **Step 3: 执行单用户真实 Chromium 冒烟**

使用 `p1_three_owner`：

1. 登录产品事业部；
2. 创建一个新的脱敏代表性任务，不指定审核人；
3. 上传 TXT/CSV 脱敏资料；
4. 请求 `qwen-plus` 生成蓝图；
5. 保存并点击“确认蓝图并生成三件套”；
6. 等待任务 `COMPLETED`；
7. 确认三类 current artifact；
8. 下载技术方案 DOCX、可研 DOCX、PPTX；
9. 校验文件非空、OOXML `PK` 签名和 SHA-256。

不得使用第二账号，不执行正式发布批准。

- [ ] **Step 4: 使用 Microsoft Office 打开三件套**

- Word COM 只读打开两份 DOCX，确认封面 Logo 和主题可见、旧学校页眉不存在、目录和正文存在；
- PowerPoint COM 只读打开 PPTX，页数大于 0；
- 关闭 Office，不保存文件，不修改 artifact hash。

- [ ] **Step 5: 写脱敏 Evidence 和状态**

Evidence 必须记录：

- 代码 checkpoint；
- 模型名称 `qwen-plus`，不记录 Key/Base URL；
- 三类模型路由调用状态、耗时和 Token 是否存在；
- 三件套文件大小/SHA-256；
- Word/PPT 打开结果；
- 未运行的全量回归、T-G04、RAGFlow、业务签认和生产验收。

`T-R01` 状态仍为 `NOT_VERIFIED`，但增加“单用户真实模型三件套冒烟完成”。

- [ ] **Step 6: 最终轻量复核**

Run:

```powershell
git diff --check
git status --short
uv run --env-file .runtime/local.env python backend/manage.py check
pnpm --dir frontend typecheck
pnpm --dir frontend build
```

Expected: 仅 Evidence/状态文件待提交，运行服务健康。

- [ ] **Step 7: 提交并停止**

```bash
git add docs/product/T-R01/TASK_STATUS.md docs/product/T-R01/evidence/SINGLE_USER_GENERATION_SMOKE_20260924.md deliverables/企业平台SSD_V1_20260923/EXECUTION_STATUS.md
git commit -m "docs: record single-user product generation checkpoint"
```

停止，不合并、不推送、不执行全量回归。

## 计划自检

- 规范覆盖：单用户蓝图确认、自动三件套、取消报告审批、取消预算门禁、极简 Logo 封面、无文字页眉、普通页边距、定向验证均有对应 Task。
- 数据兼容：未删除 reviewer/approval schema，旧数据可读；新逻辑只改变当前蓝图确认和新成果生成。
- 类型一致：统一动作始终命名 `generate_outputs`；前端 action 始终命名 `confirm_blueprint`；两类可组合 helper 始终为 `generate_report_drafts()` 与 `generate_presentation_artifact()`。
- 安全边界：对象权限、版本/hash、stale、模型调用上限、网关权限和日志保留。
- 无新增依赖，无运行时读取桌面 Logo，无密钥写入 Git。
