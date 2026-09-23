# 产品事业部三种草稿迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有产品任务内产生技术方案 DOCX、可研 DOCX 和从两份报告派生的 PPTX 三种待审核草稿，并在隔离环境验证格式、版本和权限。

**Architecture:** 保留现有 DocumentTask、revision/approval、worker fence、对象权限及模型网关。新增按 family 分开的内容/工件标识和可研冻结资产；PPT 只消费两份精确内容版本。先做非覆盖备份恢复，再接入最小经过验证的旧工具闭包，最后测试三成果，不搬入 WorkBuddy 运行时。

**Tech Stack:** Python 3.13 Django 5.2/DRF/PostgreSQL，隔离 Python 3.12 文档运行时，React 19/Vitest/pnpm；原生 Word/PowerPoint 逐页渲染在可用的 Windows Office 环境验证。

**Spec:** `docs/superpowers/specs/2026-09-23-product-three-drafts-migration-design.md`

## Global Constraints

- 旧项目 `C:\Git_projects\bj_device_company` 全程只读；源 Word 与平台技术方案冻结文件不修改字节。新可研资产以 SHA-256 冻结，源仓库修改/未跟踪文件必须列入来源清单。
- 所有成果是待人工审核草稿，正式发布开关保持关闭；不传输公司真实资料，不使用曾暴露在聊天中的密钥，不复制 `模板文件/` 客户 PPTX。
- 禁止覆盖日常库、现有 `test_portal_phase1`、既有备份或私有工件；新隔离库、恢复库、存储目录、备份目录使用唯一名称并事前确认不存在。
- 不添加第二套身份/审计/审批/通用工作流；1000 字仅为合成验收案例，不固定总篇幅或旧 PPT 22 页数。
- 保留工作区已有未提交文件，只按本计划文件精确暂存并提交；不推送、不合并、不部署生产。

## Review Focus

1. PPT Master 运行时缺少 WorkBuddy/确认门禁时必须停止该路径，不能悄悄降级为旧引擎；Task 4 负向测试。
2. 输入或报告变化后旧 PPT 只能作为注明过期的历史查看，不允许冒充当前有效草稿下载；Task 4/5 测试。
3. 同名章节/块 ID 在两份报告间必须保留 `family:block_id`，不能覆盖；Task 3 测试。
4. 报告/演示稿越权访问和文件路径跨目录必须由服务端拒绝；Task 3/5 测试。
5. 可研模板或任一冻结依赖哈希不符时不得登记成功工件、旧版仍可读；Task 2/3 测试。

## 文件职责与执行前核对

- 原实现入口：`backend/portal/product_models.py`、`product_service.py`、`product_worker.py`、`product_documents.py`、`product_storage.py`、`product_api.py`、`product_release.py`、`frontend/src/product/DocumentWorkspace.tsx` 与 `product-api.ts`。`product_api.py` 现已约 1000 行；新增结果端点放独立 `backend/portal/product_outputs.py`，仅在 `backend/config/urls.py` 挂载；不扩大原文件。
- 新 `backend/portal/product_pair.py` 只负责两份内容及 PPT 来源/版本绑定；新 `backend/portal/product_presentation.py` 只负责本地 PPT 工具调用与产物校验。现有 `product_documents.py` 负责 family→冻结模板选择和 DOCX，不能变成第二个流程执行器。
- 新迁移在 `backend/portal/migrations/0010_*.py`（以 `0009` 后实际迁移图核对）；前端只复用既有产品任务页面组件，避免重写工作台。
- 旧 `skills/bj-docs/scripts/project_flow.py`、`pair_handoff.py`、`presentations.py` 和 `skills/bj-show/scripts/ppt-master.cjs` 先只读核对全部直接导入与许可；不得把源文件“复制过来”当成适配完成。首选复用已有已冻结脚本；若需新增依赖仅为文档运行时已知的 `python-pptx==1.0.2`，更新其固定清单并按许可证/哈希验证。PPT Master 若依赖未能脱离 WorkBuddy，阻断三成果整体完成，不改选另一引擎冒充原模式。
- 每个任务开始时重读本任务触及文件与其调用方；检查 Git 状态并记录用户原有改动。每次改代码遵循 RED→GREEN，最后只提交本任务新增/修改路径。若新发现设计冲突（如源流程必须借助宿主才能运行），停止该受阻路径、记录证据并请求设计变更，不臆造一个等价引擎。

---

### Task 1：非覆盖备份与恢复门禁

**Files:** Create `validation/p1_migration_backup.py`, `backend/portal/tests/test_p1_backup.py`; create ignored `backups/p1-three-drafts-<run-id>/` at runtime. Do not modify source repo.

**Interfaces:** Consumes current `.runtime/p1-validation.env`, `PRODUCT_STORAGE_ROOT`, both Git roots; produces `backup_manifest.json` (SHA-256 paths, HEAD, dirty-file listing), verified new restore database and new restore directory. Later tasks may run only after manifest verification succeeds.

- [ ] **Step 1: RED** — in `test_p1_backup.py` add stdlib temporary-directory checks: existing destination is refused without modifying marker bytes; missing private file fails verification; manifest never stores env values; mismatched SHA fails before restore. Run `uv run --env-file .runtime/local.env python backend/manage.py test portal.tests.test_p1_backup --noinput -v 2`, expect test failure for missing backup functions.
- [ ] **Step 2: GREEN** — implement `create_snapshot(destination, roots, storage_root, dump_path)` and `verify_snapshot(destination)` using `Path.mkdir(exist_ok=False)`, `hashlib.sha256`, `shutil.copy2`; reject links/path traversal and source/dest overlap. Preserve dirty tracked and untracked files plus base HEAD/diff in a restricted ignored directory; never include `.runtime/*.env`, credentials or key values in the manifest. For database snapshot use `pg_dump --format=custom --file=<new file>` via child env supplied by operator without printing it; require zero exit and nonempty dump. Do not expose its path via HTTP.
- [ ] **Step 3: Verify** — run focused tests; inspect actual `.runtime/p1-validation.env` **only for nonsecret DB name/host and storage path**, compare against running instance and source HEAD, snapshot P1 storage with no worker running. Restore with `createdb <new_unique_name>` and `pg_restore --exit-on-error -d <new_unique_name>` (never `--clean`); restore private files into new nonexistent directory, compare hashes and perform authorized read-only download against restored instance. Any failure stops Tasks 2–5; preserve original dump and error evidence. Do not use default `test_portal_phase1`.
- [ ] **Step 4: Commit** — `git add validation/p1_migration_backup.py backend/portal/tests/test_p1_backup.py && git commit -m "test: guard P1 backup and non-overwriting restore"`. Evidence manifest stays in ignored backup directory.

### Task 2：可研冻结模板及 DOCX 生成

**Files:** Add `backend/portal/product_assets/bj_docs/assets/feasibility/{template.docx,profile.json,prototypes.xml}` (source byte copy); modify existing `backend/portal/product_assets/bj_docs/manifest.json` only as a new version if current manifest contract permits, otherwise new feasibility manifest; modify `backend/portal/product_documents.py`, `backend/portal/product_assets/document-runtime.txt`; test `backend/portal/tests/test_product_documents.py`. Never modify existing technical-solution template/profile/prototypes.

**Interfaces:** Consumes task/input/revision/chapters; produces `render_report_draft(task, input_revision, blueprint, chapters, family)` returning existing path/hash/template_hash/render_evidence structure. Keep `render_draft(...)` as technical-solution wrapper for old callers. Only `family in ('technical-solution','feasibility')` accepted.

- [ ] **Step 1: RED** — add test that original three technical template SHA-256 remain unchanged; feasibility source templates equal copied bytes, and corrupt feasibility asset fails closed before any file is registered; validate feasibility content family and `draft` status. Run `uv run --env-file .runtime/local.env python backend/manage.py test portal.tests.test_product_documents --noinput -v 2`, expect new tests fail before implementation.
- [ ] **Step 2: GREEN** — take only the three feasibility assets from the read-only source, list path/head/dirty status/hash in new manifest; extend `frozen_pack(family)` and `content_document(..., family)` instead of duplicating renderer. Pass `family` to already frozen `artifacts.py`, which chooses its template; preserve its original technical-solution hash verification and output structure. Validate independent `feasibility` prose via distinct rules; do not imply financial feasibility without evidence. Install `python-pptx` only in isolated document runtime if Task 4 actually selects a reviewed path; not in portal Python by default.
- [ ] **Step 3: Verify and commit** — rerun tests; render fixed synthetic feasibility DOCX with frozen runtime, inspect OOXML for source placeholders and draft labeling; confirm both template SHA-256 values remain unchanged. `git add` only feasibility assets/manifest/document module/test and any required runtime list, `git commit -m "feat: freeze feasibility Word draft assets"`.

### Task 3：双报告版本和私有下载

**Files:** Modify `backend/portal/product_models.py`, `product_worker.py`, `product_service.py` only where necessary; create `backend/portal/product_pair.py`, `backend/portal/product_outputs.py`, migration `backend/portal/migrations/0010_product_draft_families.py`; mount routes in `backend/config/urls.py`; test `backend/portal/tests/test_product_pair.py`, `test_product_api.py`, `test_product_worker.py`.

**Interfaces:** `save_report_content(task, family, payload, *, input_hash, blueprint_hash, actor)` and `latest_report_content(task, family)` separate families while reusing DocumentRevision/DocumentArtifact semantics; `pair_snapshot(task)` returns source IDs/versions/hashes for both families and rejects missing/stale report; `draft_download(request, artifact_id)` checks task permission/hash before streaming. Task 4 consumes `pair_snapshot` only, not DOCX files.

- [ ] **Step 1: RED** — tests: same `chapter_id` in two families coexists; two revisions each retain input/blueprint hash; changing input or one report makes pair stale; reviewer/owner can read own current draft, foreign user receives 403 and cannot infer filename; malformed relative path rejected; no generated draft is marked approved. Run focused Django tests, observe failures before schema/API changes.
- [ ] **Step 2: GREEN** — use minimal family field or typed revision payload after checking existing unique constraints; provide migration with default `technical-solution` for existing records and do not relabel old approvals for feasibility. Reuse worker claim/fence/attempt count/budget and `product_documents.render_report_draft` for two independent outputs; reserve separate generation hashes (`family` included), write temp artifact, verify SHA and register once. Route output API in new module with existing `product_endpoint`/`task_for` and `verified_artifact` checks; treat existing technical-only API as backward compatible.
- [ ] **Step 3: Verify and commit** — run `makemigrations --check --dry-run` and focused tests on new disposable PostgreSQL test DB (not `test_portal_phase1`); verify old technical-solution smoke test remains green. Commit only task paths: `git commit -m "feat: persist separate product report drafts"`.

### Task 4：同源 PPTX 草稿和过期门禁

**Files:** Create `backend/portal/product_presentation.py`; modify `backend/portal/product_pair.py` and `product_outputs.py` as required; add frozen PPT generation script(s)/assets under `backend/portal/product_assets/` only after source hashes/dependencies reviewed; test `backend/portal/tests/test_product_presentation.py` and `test_product_pair.py`.

**Interfaces:** `build_pair_handoff(task)` returns immutable source reference `family:block_id`/hash for both saved reports (all pending preserved, approval_inherited=False); `render_presentation_draft(task, pair)` returns private file path/hash/page/source metadata; `is_current_presentation(artifact)` checks latest input/report source hashes and access authorization. Task 5 consumes these as read-only outputs.

- [ ] **Step 1: RED** — tests: duplicate IDs remain separately addressable by family; changed report/permission invalidates current PPT and current-download returns 409 while old file remains historical; oversized slide or untrusted source reference fails without registering artifact; missing PPT Master/host-only dependency yields explicit blocker and never silently calls `presentations.py` or `artifacts pptx`.
- [ ] **Step 2: Choose verified tool path** — inspect full imports/licensing and read-only run of source `ppt-master.cjs` (no `--prepare`/download) to establish whether PPT Master 6.4.0 can run headless under platform process with actual integrity/design gates. If not, stop PPT implementation and record concrete blocker; request human design approval before alternative engine. If yes, freeze only required files, hashes, licenses and isolated runtime; transform pair source IDs to approved deck plan/selected content (not raw DOCX), export editable PPTX to new private path. Never copy client `模板文件/*.pptx`. This is an explicit decision gate: until this tool path is verified, executable PPT adapter steps cannot honestly be specified; do not invent an equivalent engine.
- [ ] **Step 3: GREEN & verify** — execute source-bound synthetic two-report handoff, construct short deck without padding to 22 slides, open/edit text and render every page with available PowerPoint. Verify no cut-off/garbling, slide IDs/refs, actual PPTX hash, stale download denial and retained history. Run focused tests + Office evidence script; commit only successful paths as `feat: derive review-only PPT from paired reports`. If tool unavailable, commit no misleading PPT success implementation; write blocker evidence under `qa/` and report partial completion.

### Task 5：现有产品页展示和端到端验收

**Files:** Modify `frontend/src/product/DocumentWorkspace.tsx` (extract only touched UI block into `frontend/src/product/PairDrafts.tsx` if needed), `product-api.ts`, relevant `.test.tsx`; create `validation/p1_three_drafts_acceptance.py` for isolation run; write `qa/p1-three-drafts/RESULTS.md` only after running. Do not touch unrelated workspaces.

**Interfaces:** Reads Task 3/4 output endpoints, displays each family, version, SHA, draft/blocked/stale status; no client-supplied pathname as download target. Backend remains sole authority for download/permissions.

- [ ] **Step 1: RED** — frontend test creates synthetic task response and asserts three distinct draft cards, family/version/source label, stale PPT warning and disabled current download; errors not displayed as empty successful output. API negative tests verify forged task/artifact ID and missing or changed source hash rejected.
- [ ] **Step 2: GREEN** — add minimal task page controls; preserve original technical-only flow. With isolated Postgres, isolated product storage and explicitly non-secret configuration, run worker and browser for 两份约 1000 字的报告 + one source-bound PPT; save SHA/Office page screenshots and failure logs **outside Git by default**. Do not call real model unless independently approved cost/exfiltration scope + valid replacement key are verified; synthetic/manual-content acceptance must be labeled as such.
- [ ] **Step 3: Regress** — run frontend typecheck/tests/build; isolated PG with unique new test DB run `portal.tests` including concurrency; `makemigrations --check --dry-run`; model-gateway 35 tests. Restore backup to another new DB+private path and compare hashes. Check source repository before/after hashes and original technical template. Record actual pass, skip, blocker and browser evidence. Do not claim full three-output acceptance if Task 4 blocked.
- [ ] **Step 4: Commit** — commit exact frontend/validation paths and sanitized acceptance report using `git add <explicit paths> && git commit -m "test: verify three review-only product drafts"`; no credentials, raw input, backups, PDF/Word/PPT sample files in Git without separate review.

## Self-review and execution gate

Trace spec requirements: backup→Task 1; immutable Word→Task 2; double-report/data permissions→Task 3; PPT/version/integrity→Task 4; browser/quality/whole-suite→Task 5. Each Review Focus condition is covered by the owning task's RED test. Do not silently pass Task 4 if PPT Master cannot be validated: report partial result and propose an explicit design revision. Task 1 actual database restore is a destructive-risk operation on **new** resources; require non-overwrite checks and operator confirmation of target identity before execution. The tasks share interfaces; implement serially.
