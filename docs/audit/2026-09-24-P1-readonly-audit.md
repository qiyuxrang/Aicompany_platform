# 产品事业部 P1 只读代码审计报告

日期：2026-09-24｜模式：READ_ONLY_CODE_AUDIT｜执行者：WorkBuddy（潇潇）
基线：工作区当前状态（未提交/未修改任何文件）

**模式声明**：本次仅读取代码、测试与文档，未修改源码、migration、数据库、公共配置、SPEC 与项目级 EXECUTION_STATUS，未执行任何 git 写操作，未自动修复任何问题。

---

## 1. 当前架构摘要

**技术栈**（`pyproject.toml`、`backend/config/settings.py`、`compose.yaml`）
- Django 5.2 + DRF 3.16（`backend/portal`）＋ Django Admin；React 前端（`frontend/`，Vite/TS）；独立 FastAPI 模型网关（`model_gateway/`，18410，compose profile `models`）；独立 Worker 命令行（`portal.management.commands.run_product_worker`）。
- 数据库：生产强制独立 PostgreSQL（`PORTAL_DB_NAME`），本地无 PG 时降级 SQLite 且必须 `DEBUG=1`。
- 默认安全项：`SECRET_KEY ≥ 40` 强校验、非调试必须 HTTPS、HSTS、CSRF、`SESSION_COOKIE_HTTPONLY`、`Referrer-Policy: no-referrer`、DRF 默认 `IsAuthenticated` + 自定义 `PortalSessionAuthentication`。

**P1 文档线数据模型**（`backend/portal/product_models.py`）
```
DocumentTask(owner, reviewer, state×8, stage×6, version, input_version, blueprint_version,
             idempotency_key, payload_hash, fence, lease_until, attempt_count,
             pending_action, checkpoint)
  ├─ DocumentRevision(kind=input|blueprint|chapter|review|report, family, version,
  │                   payload, sha256, parent_sha256, input_hash, blueprint_hash, change_reason)
  ├─ DocumentSource(original_name, media_type, path, sha256, size, parsed, warnings, author_verification)
  ├─ DocumentAttempt(fence UNIQUE per task, action, status, model_calls, error_code)
  ├─ DocumentArtifact(version UNIQUE per task, family, path, sha256,
  │                   blueprint_hash, input_hash, review, render_evidence, template_hash, generation_hash)
  └─ DocumentApproval(revision XOR artifact, actor, decision, authorization(JSON), sha256)
DocumentReviewPolicy(pk=1, fingerprint, version)
```
关键约束：`(owner, idempotency_key)` 唯一、`version>0`、revision `(task, kind, family, version)` 唯一、attempt `(task, fence)` 唯一、artifact `(task, version)` 与 `(task, generation_hash)` 唯一、approval 目标 XOR 与 `(revision|artifact, actor, decision)` 唯一。

**P1 主流程实现（服务端权威链）**
| 环节 | 入口 | 关键实现 |
| --- | --- | --- |
| 任务创建与会话 | `product_api.tasks` / `conversations` / `task_conversation` | `Idempotency-Key` + `payload_hash`；`select_for_update` 锁 owner 行串行化 |
| 资料上传/导入 | `attach_source` / `attach_import_path` / `product_storage` | 私有根 + 路径逃逸防护 + 原子替换 + SHA-256 |
| 输入解析与存证 | `validate_input` / `preserve_input_provenance` / `resolve_input_issues` | 事实/推断/冲突/缺项分存，issue_hash 复核，来源归属校验 |
| 检索 | `product_retrieval` | `scope_hash` 调用前后双复验、响应逐字段白名单、正文 SHA-256 自校验、来源必须在授权 scope 内 |
| 蓝图与批准 | `validate_blueprint` / `approved_blueprint` / `decisions` | 不得移除用户条件；批准绑定 revision+sha256+actor+授权快照 |
| 执行 | `product_worker.claim_task/execute_claim` | lease + fence + attempt 上限 + 模型调用上限 + 预算账本（`product_budget`） |
| 分章与报告 | `chapters` / `report_chapters` / `content_document` | chapter 绑 input/blueprint hash，`family=technical-solution|feasibility` 独立版本流 |
| 内容检查 | `product_worker.content_checks` | 未引用来源、越蓝图范围、无据数字、数量不符、未决蓝图/输入项 → 一律不自动通过 |
| 渲染 | `product_documents` / `product_rendering` / `product_presentation` | 冻结资产逐文件哈希校验、子进程最小环境、Office 渲染产物签名与哈希校验、渲染后源文件不可变校验 |
| 同源交接 | `product_pair.pair_snapshot` | `report_id`+`sha256` 派生，`approval_inherited=False`，PPT 不重新解析 Word |
| 正式发布门禁 | `artifact_releasable` / `candidate_current` | 开关 + 渲染证据 + review.passed + 章节哈希全比对 + 必须最高版本 |
| 权限 | `product_user_allowed` / `reviewer_allowed` / `task_for` | 实时 DB 判定角色→模块；对象级 owner/reviewer；`source_permission_changed` 撤权即失效 |
| 审计 | `security.audit`（AuditEvent） | action/target/result/changes + `X-Request-ID` |

**部署与开关（fail-closed）**：`PRODUCT_*` 全默认关闭（P1、模型调用、正式发布、Office 渲染、检索），未配置时的阻断原因以真实 code 返回（`model_authorization_required`、`template_approval_required`、`retrieval_disabled` 等）。

---

## 2. 发现的问题

### A. 契约与状态登记类

**A-1（高）对话入口的成果范围契约与 SPEC 2026-09-23 范围调整冲突**
`docs/SPEC.md` 第 12 行范围调整记录明确「可研（PRD-009）、PPT（PRD-010）归入 P1，编号不变」。但代码与测试仍将其标注为 P2 未授权：
- `product_api.py:110-117` `_requested_output_states()` → feasibility/presentation 返回 `{"status":"not_started","code":"p2_not_authorized"}`
- `product_api.py:471-476`（`conversations`）与 `531-536`（`task_conversation`）blockers 硬编码 `"feasibility": "p2_not_authorized"`、`"presentation": "p2_not_authorized"`
- `tests/test_product_api.py:98-99` 将该行为断言为期望值（已被测试固化）

同时 `three_drafts` 动作（`product_api.py:672-680`、`product_three_drafts.py`）已经真实生成三件草稿，即执行层按 P1 处理、契约层仍按 P2 对外声明。

**A-2（高）前端已接线，但任务状态登记为 `FRONTEND_DEFERRED`**
`docs/SPEC.md` 第 168-174 行定义：`FRONTEND_DEFERRED` 特指「后端/核心能力已通过、但前端尚未接入」。而实际代码：
- `frontend/src/product/product-api.ts`：完整 P1 API 客户端（任务 CRUD、蓝图、章节、queue、下载、预览、逐页核验、上传）
- `frontend/src/product/DocumentWorkspace.tsx`：`/centers/product/documents` 工作台（含 `?task=`/`?artifact=` 深链）
- `frontend/src/product/DocumentWorkspace.test.tsx`、`frontend/src/centers/ProductWorkspace.test.tsx`、`App.test.tsx:37,139` 有对应测试与入口链接
- `App.tsx:830` 已把 `/centers/product/solution|feasibility|slides` 归并到 `/centers/product/documents`

即前端**已接入**，真实未完成的是**浏览器真实 E2E 验收**。`docs/product/T-P04/TASK_STATUS.md:10`、`T-P05/TASK_STATUS.md:14` 与 `EXECUTION_STATUS.md:33-34,87` 的登记口径与代码事实不符。

### B. 状态机与并发类

**B-1（中高）`task_detail` PATCH 缺少 RUNNING 守卫，与同族端点不一致**
`product_service.py:475-477` `require_editable()` 只拒绝 `CANCELLED/COMPLETED`。`product_api.py:591-621` 的 PATCH 因此允许在 `RUNNING` 期间修改 `title/input`，进而调用 `invalidate_generation()`（`product_service.py:453-460`：state→DRAFT、`fence += 1`、`lease_until=None`）。
对照同族写端点均显式拒绝 `QUEUED/RUNNING`：`blueprint`(632)、`queue_task`(657)、`retry`(823)、`cancel`(800 仅拒 COMPLETED)、`assign_reviewer`(320)、`input_review`(362)、`input_statement`(379)、`chapters`(860)、`report_chapter`(`product_outputs.py:90`)。
后果：正在执行的 attempt 因 fence 失效被判 `lease_lost`，已计入 checkpoint 的模型调用与预算无法回收，任务被强制回落草稿。无脏数据（fence 保证），但产生预算浪费与状态抖动。

**B-2（中）`claim_task` 未使用 `skip_locked`，且候选查询无索引**
`product_worker.py:54-86`：`filter(Q(state="QUEUED") | Q(state="RUNNING", lease_until__lte=now)).order_by("created_at")` + `select_for_update()`。
- 未加 `skip_locked=True`：多 Worker 并发时第二个 Worker 会阻塞等待行锁，而非跳过已占用任务。
- 过滤/排序列 `state`、`lease_until`、`created_at` 在 `DocumentTask.Meta`（`product_models.py:48-52`）与 migration 0005–0011 中均无索引；Worker 以 2 秒轮询（`run_product_worker.py:23-24`），数据量增长后为全表扫描 + 排序。

**B-3（中低）唯一约束竞态未兜底，`decisions` 可能返回 500**
`product_api.py:736-754`：先查 `existing` 再 `DocumentApproval.objects.create(...)`。并发双击同一 decision 时，二者都可能通过 `existing` 检查，随后撞 `product_approval_revision_uq` / `product_approval_artifact_uq`（`product_models.py:168-177`）抛 `IntegrityError`；`product_endpoint`（`product_api.py:32-50`）只捕获 `ProductError/ParseError/StorageError`，故返回 500 而非幂等回放或 409。

**B-4（中低）`_finish` 静默失败导致 attempt 状态泄漏**
`product_worker.py:158-174`：当 `state != RUNNING` 或 lease 已过期时直接 `return False`，**不更新** `DocumentAttempt`；`execute_claim`（`372-388`）与 `_render`(263,285) 之外的部分调用点未检查返回值。任务若不再被领取，该 attempt 会长期停留 `running`，直到下次 `claim_task`（74 行）批量清理。

**B-5（中）异常分类丢失可观测性**
`product_worker.py:372-388`：`except Exception` 中，凡不在 39 项白名单内的 code 一律改写为 `execution_failed`，且不保留原始异常类型/堆栈。例如 `_guard`（39-51）内部调用 `task_for()` 抛出的 `ProductError("not_found")`、`ProductError("source_permission_changed")` 会被吞并成 `execution_failed`，排障时无法区分。

**B-6（低）`_task_actions` 空值保护缺口**
`product_api.py:198`：`input_revision.payload.get("issues")`。当 reviewer 访问一个 `input_version=0`（无 input revision，例如经 shell/未来迁移产生）的任务时抛 `AttributeError` → 500。同类调用点（176、182、246、283-287）均已做 None 判断，此处为唯一缺口。

### C. 读路径副作用与过期判定类

**C-1（中）读路径写库并争抢全局单行锁**
`product_service.py:249-264`：`reviewer_allowed()` 调用 `review_policy_version()`，后者 `@transaction.atomic` + `select_for_update()` + `get_or_create(pk=1)`，fingerprint 变化时还会 `save()`。
调用点包括 GET 路径：`_task_actions`（`product_api.py:192`）→ `_task_detail`（295）→ `task_detail` GET（584-587）与 `tasks` GET（546）。任何 reviewer 相关只读请求都会开启写事务并锁 `DocumentReviewPolicy` 单行，形成争用点与额外 WAL 写入。

**C-2（中）`outputs` 的 `current` 判定偏松，人工审核视图可能显示已过期草稿**
`product_outputs.py:20-34` `_current()`：
- `technical-solution`：仅比对 `input_hash`/`blueprint_hash`，**未比对章节哈希**；因此手工改章节（`chapters` 端点）后，旧技术方案草稿仍 `current=True`。
- `feasibility`：仅比对 `render_evidence["report_id"]`；通过 `report-chapters/` 修改可研章节不会改变 REPORT revision，旧可研草稿与 PPT 仍 `current=True`。
- 且 `product_outputs.py:82-105` `report_chapter()` 与 `chapters`(`product_api.py:881-891`) 不一致：未登记 `checkpoint.impact`、未把 state 复位为 DRAFT。
安全边界说明：正式门禁路径（`artifact_releasable` `product_service.py:335-361`、`candidate_current` `product_release.py:22-75`）会严格比对 `review.chapter_hashes`，因此**不会**误放行正式成果；风险集中在草稿列表的"是否当前有效"展示口径。

**C-3（低）GET 请求产生可被预取/重放写入的证据收据**
`product_api.py:923-953` `artifact_preview`（GET）会写入 `render_evidence.page_views` 收据，该收据是 `artifact_verification`（977-981）要求"必须先逐页打开鉴权预览"的依据。浏览器预取（prefetch）或被引导的 `<img>`/重放请求可产生未经人工真实查看的收据，削弱"逐页核对"证据强度（已设 `Cache-Control: private, no-store`，但不阻止主动请求）。

**C-4（低）`content_checks` 数字白名单范围过宽**
`product_worker.py:206`：`permitted_numbers` 由整个 `input_payload`（含 `background`、`requirements` 长文本）正则提取。输出中的数字只要在输入任意位置出现过即视为有据，无法识别"张冠李戴"（A 设备数量被写到 B 设备），仅能靠 217-231 行的同句式数量比对部分兜底。

**C-5（低）审计与业务非原子**
`security.audit`（`security.py:6-8`）直接 `create`。P1 中 `_audit` 均位于 `transaction.atomic()` 之外（`product_api.py:351,365,393,523,577,650,701,788` 等），属有意设计；但审计写失败会造成"业务已生效、无审计"且请求 500，缺少补偿或告警。

### D. 配置与运维观察项

- **D-1**：`PRODUCT_TEMPLATE_APPROVAL` / `PRODUCT_COST_POLICY` / `PRODUCT_RETRIEVAL_AUTHORIZATIONS` 以默认 `{}` 宽松解析（`settings.py:91-112`），与开关默认关闭组合后为 fail-closed，方向正确。
- **D-2**：`PRODUCT_LEASE_SECONDS=180`、`PRODUCT_MAX_ATTEMPTS=8`、`PRODUCT_MAX_MODEL_CALLS=24`、`PRODUCT_UPLOAD_MAX_BYTES=1MiB` 为硬编码阈值（`settings.py:81-84`），未经 D-06 批准；与 D-06 未解除的状态一致，但发布前需由用户确认取值口径。
- **D-3**：`Document*` 全部模型未注册 Django Admin（`admin.py` 无匹配），管理员无法在后台查看/应急处置 P1 任务与批准（符合"AI/管理员不得代替人批准"），运维可观测性依赖 `task-history` 接口与 `AuditEvent`。
- **D-4**：`PRODUCT_REVIEWER_IDS` 用 `value.isdigit()` 静默过滤非法值（`settings.py:78`）；失败方向安全（无审核人 → 无人可批准），但配置错误不会显式报错。

---

## 3. 风险等级汇总

| 编号 | 问题 | 风险等级 | 是否阻断 P1 主线 |
| --- | --- | --- | --- |
| A-1 | 对话契约仍称可研/PPT 属 P2 未授权（与 SPEC 冲突，测试已固化） | 高 | 不阻断执行，**影响验收口径** |
| A-2 | 前端已接入却登记 `FRONTEND_DEFERRED`（状态与事实不符） | 高 | 不阻断执行，**影响验收可信度** |
| B-1 | `task_detail` PATCH 缺 RUNNING 守卫 | 中高 | 否（fence 保证无脏数据，浪费预算） |
| B-2 | `claim_task` 无 `skip_locked` + 无索引 | 中 | 否（单 Worker 场景无影响） |
| B-3 | approval 唯一约束竞态返回 500 | 中低 | 否 |
| B-4 | `_finish` 失败不落 attempt 状态 | 中低 | 否 |
| B-5 | 异常分类被吞并为 `execution_failed` | 中 | 否（可观测性） |
| B-6 | `_task_actions` 空值缺口 → 500 | 低 | 否（需异常数据形态） |
| C-1 | GET 写库 + 全局单行锁争用 | 中 | 否（并发规模相关） |
| C-2 | `outputs.current` 偏松 + `report_chapter` 缺 impact/状态复位 | 中 | 否（正式门禁仍严格） |
| C-3 | GET 预览收据可被预取/重放 | 低 | 否（证据强度） |
| C-4 | 数字白名单范围过宽 | 低 | 否（质量口径） |
| C-5 | 审计与业务非原子 | 低 | 否 |
| D-1~D-4 | 配置解析、硬编码阈值、Admin 未注册、静默过滤 | 观察项 | 否 |

**未发现**：P1 路径上的越权读取/写入（对象级 owner/reviewer 判定 + 撤权即时失效已覆盖）、路径穿越、绕过 CSRF 的写操作、AI 代批、批准重放复活旧版本、正式成果在开关关闭时被放行。

---

## 4. 受影响文件 / 函数

| 编号 | 文件 | 函数 / 位置 |
| --- | --- | --- |
| A-1 | `backend/portal/product_api.py` | `_requested_output_states`(110-117)、`conversations`(471-476)、`task_conversation`(531-536) |
| A-1 | `backend/portal/tests/test_product_api.py` | `test_conversation_*` 断言(98-99) |
| A-2 | `frontend/src/product/product-api.ts`、`DocumentWorkspace.tsx`、`DocumentWorkspace.test.tsx`、`src/App.tsx`(830) | 全部（已接线事实） |
| A-2 | `docs/product/T-P04/TASK_STATUS.md`(10)、`T-P05/TASK_STATUS.md`(14)、`deliverables/企业平台SSD_V1_20260923/EXECUTION_STATUS.md`(33,34,87) | 状态登记（**不在本次修改范围**） |
| B-1 | `backend/portal/product_service.py` | `require_editable`(475-477)、`invalidate_generation`(453-460) |
| B-1 | `backend/portal/product_api.py` | `task_detail` PATCH(588-621) |
| B-2 | `backend/portal/product_worker.py`；`backend/portal/product_models.py` | `claim_task`(54-86)；`DocumentTask.Meta`(48-52) |
| B-3 | `backend/portal/product_api.py`；`backend/portal/product_models.py` | `decisions`(724-789)；`DocumentApproval.Meta`(162-178) |
| B-4 | `backend/portal/product_worker.py` | `_finish`(158-174)、调用点(263,285,317,329,358,388) |
| B-5 | `backend/portal/product_worker.py` | `execute_claim` except 分支(372-388) |
| B-6 | `backend/portal/product_api.py` | `_task_actions`(198) |
| C-1 | `backend/portal/product_service.py` | `reviewer_allowed`(249-252)、`review_policy_version`(255-264) |
| C-2 | `backend/portal/product_outputs.py`；`backend/portal/product_api.py` | `_current`(20-34)、`report_chapter`(80-105)；`chapters`(852-893) |
| C-3 | `backend/portal/product_api.py` | `artifact_preview`(923-953)、`artifact_verification`(977-981) |
| C-4 | `backend/portal/product_worker.py` | `content_checks`(199-237，重点 206) |
| C-5 | `backend/portal/security.py` | `audit`(6-8) |
| D-2 | `backend/config/settings.py` | 81-84、91-112 |

---

## 5. 建议修复方式（仅建议，未实施）

> 说明：B-1、B-2（索引）、C-1、C-2 属行为/性能变更，按 SPEC 第 7 节需先确认 Feature Batch 范围与可修改文件（D-09 未批准）；A-1、A-2 属文档与契约口径，需用户确认后处理。

- **A-1**：将 feasibility/presentation 的 `code` 改为表达真实阻断原因（如 `model_not_authorized` 或 `three_drafts_draft_only`），或新增 `output_scope: "p1"` 字段；同步更新 `tests/test_product_api.py:98-99` 与前端 `ConversationTaskResult` 类型；**不改 SPEC**。修改前需确认 105 项回归的期望值变更范围。
- **A-2**：由用户在任务级 TASK_STATUS 中改用更精确的标记（如 `FRONTEND_INTEGRATED / BROWSER_E2E_NOT_RUN`），或在 TASK_STATUS 补写"前端已接入、浏览器 E2E 未执行"的说明；不得据此把 Browser E2E 或 P1 记为 PASS。
- **B-1**：在 `task_detail` PATCH 中补 `if task.state in {"QUEUED","RUNNING"}: raise ProductError("invalid_state", ...)`，与同族端点对齐；或明确文档化为"允许运行期取消式编辑"并补测试固化语义。
- **B-2**：`claim_task` 加 `select_for_update(skip_locked=True)`（PG）；新增索引 `Index(fields=["state","created_at"])` 与 `Index(fields=["state","lease_until"])`（需 D-06 批准后另开批次）。
- **B-3**：捕获 `IntegrityError`，按既有 `decision_superseded` 语义返回 409，或改写为幂等回放响应；可在 `product_endpoint` 增加 `IntegrityError → 409/幂等` 的统一兜底。
- **B-4**：`_finish` 返回 False 时同步将该 attempt 标记为 `cancelled/lease_lost`；所有调用点统一检查返回值。
- **B-5**：保留原始 code（如写入 `checkpoint["last_error"]` 或 `attempt.error_code` 的附加字段），审计 changes 记录异常类名；避免用 `execution_failed` 覆盖可辨错误。
- **B-6**：`input_revision is not None and input_revision.payload.get("issues")`；并给 `product_endpoint` 增加结构化兜底（记 request_id + 返回统一错误体）。
- **C-1**：将 policy fingerprint/version 计算改为进程内 memo 或仅在 fingerprint 变化时落库；GET 路径避免写事务。
- **C-2**：`_current` 对 technical-solution 复用 `candidate_current` 的 chapter_hashes 比对；`report_chapter` 补 `checkpoint.impact` 登记与 state 复位，与 `chapters` 对齐。
- **C-3**：把预览收据写入改为 POST（或对 `page_views` 收据附加"真实用户交互"约束），并在验收文档中写明该证据强度边界。
- **C-4**：`permitted_numbers` 按章节被授权 `source_ids` 限定范围。
- **C-5**：关键批准路径把审计并入业务事务；读路径保留异步但增加失败告警。
- **D-2**：发布前由用户确认 lease/attempt/模型调用/上传上限取值，并登记为已批准阈值。

---

## 6. 建议补充的测试

现有 P1 相关测试规模：`test_product_api` 28、`test_product_worker` 16、`test_product_retrieval` 17、`test_product_documents` 9、`test_product_increment` 7、`test_product_release_flow` 7、`test_product_budget` 7、`test_product_rendering` 7、`test_product_rules` 3、`test_product_retrieval_flow` 3、`test_product_pair` 2、`test_product_three_drafts` **1**、`test_product_concurrency` **1**（`skipUnless(postgresql)`）。

建议补充（按优先级）：
1. `test_product_concurrency.py`：**幂等键并发独占**已有 1 项；补 ① `claim_task` 双 Worker 并发领取只产生一个 attempt（需 PG，验证 `skip_locked` 行为）；② 并发 `decisions` 同 decision 只落一条 approval 且第二个请求返回 409/幂等（对应 B-3）；③ lease 过期后抢占不产生双写。
2. `test_product_three_drafts.py`：现仅 1 项最小链。补 ① 仅一个 family 章节完整 → `chapters_incomplete` 且不产 artifact；② 输入变更后重跑 `three_drafts` 产生新版本且旧版本保留；③ PPT 逐页来源块与 `technical-solution:`/`feasibility:` 前缀绑定断言（已有部分）；④ presentation 过期后 `draft_download` 返回 409。
3. `test_product_pair.py`：补 `pair_snapshot` 在 REPORT 版本变化 / pending 项变化时的哈希差异断言。
4. 新增（对应 B-1）：`task_detail` PATCH 在 `RUNNING` 状态下的行为断言（当前无覆盖）。
5. 新增（对应 B-6）：构造 `input_version=0` 的 reviewer 视角详情请求，断言不返回 500。
6. 新增（对应 B-4/B-5）：`_finish` 在 state 非 RUNNING / lease 过期时，断言 attempt 终态非 `running`；断言未白名单异常被归类时保留可辨 code。
7. 新增（对应 C-2）：手工修改 technical-solution 章节与 feasibility 章节后，断言 `outputs.current` 为 `False`（当前语义会返回 True）。
8. 新增（对应 C-3）：未打开预览页即提交 `artifact_verification` 必须 409（已有 `test_page_hashes_without_preview_cannot_mark_verified`）；补"预取式 GET 预览 + 立即核验"的证据强度边界用例或明确记录为已知边界。
9. 新增（对应 C-4）：跨设备数量错配（把 A 数量写入 B 章节）应产生 `unsupported_number` 或 `quantity_mismatch`。
10. 新增（对应 A-1）：`conversations` 契约字段与 SPEC P1 范围一致性断言（当前断言的是 `p2_not_authorized`，修正后需同步）。
11. 环境维度：明确 `test_product_concurrency` / `test_integration_postgres` 在 SQLite 环境必然 SKIP，**不得**以 SQLite 结果声称并发/行锁语义已验证（T-P05 的 "1 SKIP" 很可能即此项，建议在 TEST_RESULTS 中显式标注 skip 名称与原因）。

---

## 7. 是否会影响当前产品 P1 主线

**结论：不阻断 P1 主线推进，但有两项影响 P1 发布验收口径的问题需要先处理或书面澄清。**

- **不阻断**：P1 当前登记为 R3 `BLOCKED` / T-P05 `CORE_PASS`（`EXECUTION_STATUS.md:34,87,129`），主 blocker 是授权类 D-01（真实模型/外发）、D-02（正式母版/Office 环境）、D-03（人员授权与审批策略）。本次 B/C/D 类发现均为实现层质量与健壮性问题，**不改变**"后端草稿核心链已跑通、正式成果与 P1 均不得记 PASS"的既有结论。
- **需要先处理/澄清**：
  - **A-1**：对外契约仍声明可研/PPT 属 P2 未授权，与 SPEC 范围调整直接冲突，且已被测试固化。若 P1 发布验收要覆盖三件套，该契约与验收文档口径必须一致。
  - **A-2**：前端已接线的代码事实与 `FRONTEND_DEFERRED` 登记不符，直接影响"前端是否接入"的判定，而 SPEC 第 176-183 行把"前端业务流程 / 浏览器真实 E2E"列为 P1 发布验收前置项。
  - **C-2**：草稿列表的"当前有效"标记偏松，可能在人工审核环节把已过期草稿当作当前稿；正式门禁本身仍严格，故为口径问题而非放行问题。
- **不影响**：`frontend` 类型检查/测试/构建、后端 105 项回归的既有通过结论不因本次只读审计改变（本次未运行任何测试，也未修改任何文件）。
- **建议顺序**：先由用户确认 A-1/A-2 的口径与登记修正方式（属文档与契约，需 D-09 范围确认）→ 再按 Feature Batch 处理 B-1/B-6（小改、收益明确）→ B-2/B-3/B-4/C-1/C-2 视 D-06 与批次安排。**在 P1 发布验收前按 SPEC 第 7 节不得新增功能特性。**

---

## 附：本次审计未覆盖范围

- 未运行任何测试或命令（不修改数据库、不创建测试库），所有结论来自静态阅读。
- 未审计 `frontend/` 全部交互细节（仅核对 P1 接线事实）、`hr_*`、`operations`、`integration`、`work_summary`、`model_gateway/` 内部实现（非 P1 主线）。
- 未核对 `deliverables/` 内历史包的逐条验收结论与证据文件实体（仅引用其状态指针）。
- 未验证 `backups/p1-three-drafts-20260923_084212_8dd98387/` 的可恢复性（不执行恢复操作）。
