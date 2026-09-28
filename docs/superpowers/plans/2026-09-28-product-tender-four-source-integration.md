# 产品事业部商机获取四来源接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将独立 Tender 四来源 V1 选择性接入 `Aicompany_platform` 产品事业部，使已登录的产品事业部用户在“商机获取”立即看到已采集的结构化公告和官方详情链接，点击刷新后由后台补采并显示状态；首次真实数据限固定三个自然日，真实采集与部署另行审批。

**Architecture:** 目标 `portal` 复用现有产品模块登录访问校验、审计、CSRF、迁移；Tender 公告、来源和批次独立于 Product `DocumentTask`。选择性移植四源、安全出站、幂等与失效即停；Web 刷新只入队、专用 Django 消费进程从数据库领取并回报状态，不新增消息中间件或周期性自动抓取。隔离 PG 中先验零外站及离线刷新闭环 A；真实三日首次采集与受控刷新 B、目标部署与消费者启用 C 分开审批。

**Tech Stack:** Django 5.2 / DRF / PostgreSQL，React / TypeScript / Vite，目标仓库已锁定的 uv / pnpm；复用 Django 管理命令的专用消费进程，不引入消息中间件、前端路由库或周期调度。

**Spec:** `docs/superpowers/specs/2026-09-28-product-tender-four-source-integration-design.md`（用户已确认；本计划和规格一起交付执行）。

## Global Constraints

- 目标仅为 `C:\Users\BJRunner\Desktop\Aicompany_platform`；现有 `main@43abfc7` 现场在 `backend/config/settings.py`、`backend/portal/product_knowledge_service.py`、其测试有用户未提交改动。已有 `docs/product-tender-four-source-design@2bacf99` **只是文档分支**，它不包含那些改动；实施前重新核实 `main` HEAD、工作区状态，并从当前真实主线建立新的仓库外隔离功能 worktree，不 stash/reset/checkout/覆盖主工作区，不直接提交 main。不从旧 `ai智能体平台` 分支 merge/cherry-pick 迁移。
- 范围仅 `ccgp_national`、`sx_jk_ecai`、`shxjkjt`、`csg_bidding`；其余 12 个和旧稿首批两站继续留台账，未知不判 PASS。`docs/SPEC.md` 是权威，实施时最小更正四源范围并注明旧验收未达成，不改既有需求编号/历史证据。
- 首次窗口固定 `Asia/Shanghai` **[2026-09-26 00:00:00, 2026-09-29 00:00:00)**（左闭右开），不是滚动 72 小时；没有可证明发布时间的记录不入库，不推断公告 ID/日期/地区。日期仅精确到天时保存真实精度，不虚构时分秒。采集时点若在窗口之后也不能改窗口。
- 初始采集、手动刷新两个环境开关默认 false；关闭时 GET 零出站、刷新 POST 明确拒绝且不入队，`--force`、`--preflight`、直接 Worker 不能越过总开关。A 只在隔离测试作用域打开开关并用离线 fake adapter 验消费者，绝不请求真实外站。商机读取、刷新和状态查询只复用现有 `product_user_allowed`，不设 Tender 角色、操作员名单或授权页面；过期来源/批次**不**自动接管，运维确认旧进程退出才可受控人工恢复并审计。
- 任何迁移、种子、账号及回归只对新的无宿主卷 tmpfs、仅回环端口 PostgreSQL 运行；任何 DB 参数不匹配则拒绝执行。禁用正式模型调用、真实外站采集、目标环境调度与部署；仅在隔离进程允许离线 fake adapter 消费批次；绝不复制独立版数据、demo 账号、源库或旧 `tender/migrations/0001–0009`。
- B 阶段真实访问、目标平台入库、官方链接点击和受控后台刷新须在 **A 完成后另获出站与环境授权**；点击刷新不能绕开首次三日窗口或启用未批准的增量边界。C 阶段目标部署、常驻 Tender 消费进程、正式库迁移及出站配置另行审批；不默认安装周期性自动采集。不绕过登录/验证码/403/TLS 或对外站并发压测。
- 测试遵循 RED→GREEN；单个来源异常不得伪装为空列表或成功；不完整扫描不推进边界。每个任务尾留验证结果和原子 commit（只在隔离功能分支）；主仓库 main 的状态前后对照记录。

## 文件与接口地图

- 既有权威/入口：`docs/SPEC.md`；`frontend/src/centers/{config.ts,CenterWorkspace.tsx,ProductWorkspace.tsx}`（导航与 section）；`frontend/src/api.ts::apiRequest`（masked CSRF）；`backend/config/{settings.py,urls.py}`；`backend/portal/{models.py,security.py,product_service.py}`（产品授权）；`backend/portal/migrations/0023_hr_model_selection.py`（当前迁移叶，执行前重核）。
- 新领域文件：`backend/portal/tender_models.py`（模型、约束），`tender_sources/`（四源及其基类），`tender_{outbound,normalize,dedupe,service,worker,manual_refresh,recovery,api}.py`（只迁入实际依赖，其他分析/资质代码不因拷贝便利而接入），`tender_storage.py`（独立私有根与校验）、`tender_window.py`（固定窗口内候选日期判定）、`tender_consumer.py`（专用队列循环与存活心跳）；如选择复用独立版更多模块，先写明实际 import 图及非 S2/S3 输出为何必要。新迁移由当前目标模型生成，不能直接拷旧分支文件。
- 新 UI：`frontend/src/product/TenderOpportunities.tsx`、`tender-api.ts`、`tender-opportunities.css`（局部作用域）；如详情复杂，拆 `TenderOpportunityDetail.tsx`，不改全局 CSS。API 暴露 `/api/product/opportunities/`、`/api/product/opportunities/<int:id>/`、`/api/product/opportunities/options/`、`/api/product/opportunities/sources/`、`/api/product/opportunities/refresh/`（GET 状态及 POST 入队）、`/api/product/opportunities/refresh/<uuid:batch_id>/`（GET 批次）；任何附加路径必须列入测试与现有产品访问校验表，关闭时不能出站。
- 新测试：`backend/portal/tests/test_tender_{models,window,api,worker,recovery}.py`、`frontend/src/product/TenderOpportunities.test.tsx`、`frontend/src/centers/CenterWorkspace.test.tsx` 的精确补例；隔离双进程和浏览器证据放功能 worktree 的 `.runtime/tender-product/`，不提交凭据。

## Review Focus

1. 公告日期只含 `2026-09-26`、跨 UTC+8 零点或缺日期：Task 3 测试不能推断成虚构时分秒，边界准确，未知不入。
2. 消费进程未启动/心跳过期、执行中长任务、旧 worker 已失联或批次 15 分钟超时、用户连续点击：Task 4/5/6/7 验不生成无人消费队列、活跃批次 409 同 ID、旧 fence 拒写、不中途误判正常采集为离线及人工审计恢复。
3. 原始 URL 是 `javascript:`、外站重定向、同源假详情路径或附件 URL：Task 5 测在入库前拒绝、输出不会成为前端任意跳转；仅适配器核验的详情链接可点。
4. 非产品用户、产品模块撤权、管理员只预览、首次改密/禁用账号：Task 5/6/7 验所有 GET/POST 使用既有产品访问校验（无 Tender 专属角色）、后端立即拒绝、撤权后移除旧业务 DOM，预览零业务请求。
5. 首页-only 来源出现 3 天前但排在前列的旧公告、中央与地方某一栏不能扫到窗口下界：Task 3/4 验可单条可信入库却不得标三日完整、不得推进 checkpoint；另需注意 `run_all` 不应把同一运行起始时刻误作跨来源实际完成时刻。

---

### Task 1: 隔离基线与规范口径

**Files:** Modify `docs/SPEC.md` 的 4.5 范围描述；Create `docs/product/TENDER_FOUR_SOURCE_SCOPE.md`（旧稿与四源矩阵、验收记录入口）；测试日志仅存 `.runtime/tender-product/`。

**Interfaces:** Consumes `main` HEAD 与批准规格；Produces 四源首批口径与与旧设计的差异说明，其他 Task 均依此取范围。

- [ ] 读 `main` status、HEAD、迁移叶及 `git diff -- backend/config/settings.py backend/portal/product_knowledge_service.py backend/portal/tests/test_product_knowledge_service.py`（只读，禁止输出凭据）；检查当前隔离文档分支的规格是否已可在功能分支使用。若 main HEAD 变化先登记实际基线并审迁移图；从 main 建**新**仓库外功能 worktree，安全导入规格/计划（只复制文档提交，不合入其他分支的代码）；原主 checkout 的 status 快照存忽略目录。无原生 worktree tool 时用 git worktree；不复用 `aicompany-tender-design` 为业务改动分支。
- [ ] 仅在新 worktree 用 `uv sync --frozen`、`pnpm --dir frontend install --frozen-lockfile`；在私有临时 SQLite/PG 路径且模型/外站禁用时执行基线 `uv run python backend/manage.py test portal.tests --noinput`、`pnpm --dir frontend test`、`typecheck`、`build`。记录既有 FAIL/ERROR 与测试环境缺失原因，**不把 main 未提交变更的行为当成提交基线**。
- [ ] `docs/SPEC.md` 的 4.5 加一小段明确「本次接入 S1 采用四源、产品人员登录即可浏览及点击刷新后台采集（无 Tender 专属权限）、旧稿的两站受限待后续逐源验收，S2/S3 不在本批次」；`docs/product/TENDER_FOUR_SOURCE_SCOPE.md` 写 4/12 来源状态与固定窗口，以及另一仓库已通过证据只能作参考。检查旧稿仍作为历史设计可追溯。
- [ ] Run: `git diff --check && git status --short && git -C C:/Users/BJRunner/Desktop/Aicompany_platform status --short`；Expected: 仅新功能 worktree 有本任务文件，主 checkout 与记录的原三处未提交文件完全一致。Commit: `docs(tender): set four-source product scope`。

### Task 2: 目标 portal 领域模型、私有根与新迁移

**Files:** Create `backend/portal/tender_models.py`, `tender_storage.py`, `backend/portal/tests/test_tender_models.py`；Modify `backend/portal/models.py`, `backend/config/settings.py`；Generate `backend/portal/migrations/00NN_tender_product_*.py`（NN 由当前目标迁移图决定）。

**Interfaces:** Produces `TenderSource/TenderFetchRun/TenderManualRefresh/TenderConsumerHeartbeat/TenderRecoveryAudit/TenderSnapshot/TenderNotice/TenderNoticeVersion/TenderOpportunity/TenderOpportunityEvent` 及独立私有路径；公告与商机的 `publish_date` 和 `publish_precision`（`date|minute|second|unknown`，不靠虚构时间表示日精度）；Tasks 3–7 消费。依赖真实 `portal.User`，不能用旧 `tender` app 的外键。

- [ ] **RED:** `test_tender_models.py` 用 `PortalTestCase` 导入 `portal.models` 中的上述模型；断言 `_meta.app_label == 'portal'`、来源 `enabled=False` 默认、仅日期的 `publish_date/publish_precision` 可持久保存、`requested_by/operator` 指向 `portal.User`、消费者心跳有唯一固定实例记录（含最后更新时刻），同源双 RUNNING 和双活跃刷新写库触发 `IntegrityError`（各用内层 `transaction.atomic()` savepoint）；快照/公告 `(source,source_notice_id)` 业务唯一性按独立实现审查。Run: `uv run python backend/manage.py test portal.tests.test_tender_models --noinput`；Expected: 缺失模型/约束导致 RED，不是环境 DB 错误。
- [ ] **GREEN:** 从 `商机获取/tender/tender_models.py` 只移植本阶段必需模型/约束，把 `enabled` 默认改 false；若字段关联了分析/资质模型，明确为可空兼容或删掉未用关联，不能留孤儿 import。仅有日期时 `publish_at` 可空、`publish_date` 存可信原始日期，不能凭 `date` 假造时刻用于排序；私有快照原子写与路径/哈希校验优先复用目标 `product_storage` 底层原语，但 `TENDER_STORAGE_ROOT` 必须与 `PRODUCT_STORAGE_ROOT` 分离；不得把 Product 默认路径改掉。设置 `PORTAL_TENDER_INGESTION_ENABLED`、`PORTAL_TENDER_MANUAL_REFRESH_ENABLED` 默认 false；不设 Tender 专属用户名单。消费者心跳持久化于独立单例记录，重启覆盖旧心跳且不清除 RUNNING 批次。生成**目标仓库** `portal` 迁移，不拷旧迁移，核对依赖最新叶与 partial unique 索引。
- [ ] Run: 同定向测试 + `python backend/manage.py check`、`makemigrations --check --dry-run`（隔离配置）；Expected: 测试 PASS、无新增迁移。没有新隔离 PG 时先不运行真实 migrate，标 PG 未验。Commit: `feat(tender): add product-hosted models and disabled defaults`。

### Task 3: 固定三日日期判定与四源候选有界扫描

**Files:** Create `backend/portal/tender_window.py`, `backend/portal/tests/test_tender_window.py`；Selectively copy `tender_sources/{base,ccgp_national,sx_jk_ecai,shxjkjt,csg_bidding}.py`, `tender_outbound.py`（另需注册文件仅按 import 图添加）。

**Interfaces:** Produces `FIRST_WINDOW_START/END`（北京时区半开区间）、`classify_window_date(value, *, precision, source_code)` → `inside|before|after|unknown`、`list_window_candidates(adapter, *, max_pages, max_candidates)` → 带 `refs/complete/reason/observed_bounds` 的结果；Task 4 Worker 使用结果的 `complete` 才推进 checkpoint。

- [ ] **RED:** 测时区转换（2026-09-25 15:59 UTC 拒、16:00 UTC 接受；2026-09-28 15:59 UTC 接受、16:00 UTC 拒）；北京日期 `2026-09-26` 可判入但保持 `date` 精度、不可存假 `00:00`；空值/无可靠来源 `unknown`。离线 fake adapter：只能首页时未见三天下界则 `complete=False`；全国双栏目一栏缺页或窗口下界则 `complete=False`，不请求上限外的页/详情；缺日期仅在候选上限内抓官方详情确认，无法确认保持 `unknown`。Run: `uv run python backend/manage.py test portal.tests.test_tender_window --noinput`；Expected: 缺接口或错误窗口导致 RED。
- [ ] **GREEN:** 仅复制四源适配器和安全出站必要文件；不把独立版默认 `list_notices(page=1)` 当作三日全量。全国 `list_incremental` 使用官方分页但不得只靠首次边界空引用结束；必要时用有界首轮扫描直到可信日期低于窗口下界或达上限。其他三源只有首页无法证明完整窗口则返回 `PARTIAL/NOT_VERIFIED`，可单条确认日期后入库。若网页缺发布日期，详情只能用现有 allowlist/HTTPS 和原文证据补证，不凭 ID/列表顺序/抓取时间猜。明确时区及日期精度元数据；不要把仅有日期写成带假时分的时间戳。
- [ ] Run: 窗口与四源离线 fixture 测试（复用独立工程 `tests/test_{ccgp_national,sx_jk_ecai,shxjkjt,csg_bidding}.py` 的代表性样本并替换 import）；Expected: 边界/受保护/部分扫描测试全绿，所有出站 mock 零真实网络。Commit: `feat(tender): bound three-day four-source discovery`。

### Task 4: 来源内幂等、硬关闭 Worker、专用消费者与人工恢复

**Files:** Create `backend/portal/tender_{normalize,dedupe,service,worker,manual_refresh,recovery,consumer}.py`, `backend/portal/tests/test_tender_{worker,recovery,consumer}.py`, `backend/portal/management/commands/{run_tender_worker,run_tender_consumer,confirm_tender_recovery}.py`；Modify `backend/config/settings.py` 仅必要配置。不以人工运行一次 `run_tender_refresh` 充当用户按钮的执行链。

**Interfaces:** Consumes Task 2 模型/私有根与 Task 3 `list_window_candidates`；Produces `run_source`、`enqueue(actor,source_codes)`、`execute_once(adapter_factory=None)`、`run_tender_consumer` 的独立 DB 队列循环/心跳、`confirm_source/confirm_batch`；Web 只入队与查本地进度，消费者后台顺序执行四源。固定三日首次采集与增量为显式不同模式；首次采集/可信边界未批准前 Web POST 不允许开启隐式增量；默认 Web GET 零出站。

- [ ] **RED:** patch `TenderOutbound.fetch` 一调用即失败；来源误设 enabled=True 时 `run_source`、`run_all`、`preflight_source`、`run_tender_worker --force/--preflight`、`run_tender_consumer --once` 和直接 `execute_once` 在两个开关关闭时均零出站/不领取；隔离测试 override 两开关后，独立消费者领 `QUEUED` 并顺序处理 fake 四源，状态/计数入库；空闲进程每 ≤10 秒更新心跳，执行中长任务也低频更新心跳，超过 15 秒不续跳视为失联，正常退出立即撤销心跳；进程意外退出时允许最多 15 秒的短暂误判，已入队批次必须在状态查询上报告排队超时而不是永久等待。过期 RUNNING 即使新消费者重启也不能接管；旧 fence 拒写，确认旧进程退出后审计恢复。Run: `uv run python backend/manage.py test portal.tests.test_tender_worker portal.tests.test_tender_recovery portal.tests.test_tender_consumer --noinput`；Expected: 门禁/消费者缺失导致 RED。
- [ ] **GREEN:** 选择性移植 normalize/dedupe/service/worker/manual_refresh/recovery，审计用 `portal.security.audit`、存储用 Task 2 私有根；`requested_by` 为现有产品用户，无 Tender 操作员名单。入口及直接 Worker 均查总采集开关，刷新批次另查手动开关；`--force` 不得绕过。专用 `run_tender_consumer` 管理命令循环空闲心跳和 `execute_once`，按现有 Product Worker 的轮询范式却**不复用其过期自动抢占**；不中断活动运行写库，低频 heartbeat 用独立 DB 连接/线程维持并在进程正常退出时撤销心跳，失败则不认领新任务，不能误判长任务离线；意外退出留下的短时有效心跳可能导致一个排队批次，状态查询须显示超时/恢复提示而非无限转圈。确认旧进程停止的恢复命令使用 `--confirm-stopped --operator-id --reason`，核对运维身份、过期与相关源状态并审计。Worker 在详情入库前校验固定窗口；不完整扫描不推进 checkpoint，复跑业务幂等。核查 `run_all(now=...)` 跨源实际完成时刻。
- [ ] Run: 三组定向测试和已有 Product Worker 定向测试；Expected: 零外网、独立进程模拟能消费/更新状态、过期不自动接管、关闭开关零出站。Commit: `feat(tender): add guarded consumer and fail-closed recovery`。

### Task 5: 现有产品登录校验的公告/刷新 API 与官方详情链接

**Files:** Create `backend/portal/tender_api.py`, `tender_access.py`, `backend/portal/tests/test_tender_api.py`；Modify `backend/config/urls.py`；如四源归一实际引用未迁文件，在此按测试证据补最小必要文件。

**Interfaces:** Produces `/api/product/opportunities/`（分页/筛选）、`/<id>/`（来源/版本/链接）、`/options/`、`/sources/`、`/refresh/`（GET 最近活跃批次/消费者可用性、POST 入队）、`/refresh/<uuid:batch_id>/`（GET 进度）；前端 Task 6 消费 JSON，保持 `id,project_name,project_code,region,purchaser,budget,publish_at,publish_date,publish_precision,bid_deadline,status,source,original_url,current_version,first_seen_at` 的单一契约；仅日期时 `publish_at=null`、`publish_date=YYYY-MM-DD`、`publish_precision=date`；精确到分钟/秒时返回可换算时刻及原始精度。

- [ ] **RED:** 用 `PortalTestCase` 现有产品账号（不新建 Tender Role）测所有 GET/POST 可用；退出登录、关闭 product 模块/撤权、必须改密或停用账号全部拒绝，管理员预览不调用 API；POST 要求 masked CSRF。关闭开关或消费者心跳距今 >15 秒时 POST 返回可读 `refresh_unavailable` 且零队列新增，GET 仍可读已入库公告；在线且开关开启时 POST 仅入队返回 `202 + batch_id + QUEUED`，已有 QUEUED/RUNNING 时无论心跳先返 `409 + 同 batch_id`；再次查询可得 `QUEUED/RUNNING/SUCCESS/PARTIAL/FAILED/INTERRUPTED`，消费者失联导致的 QUEUED 超过有界期限（按 15 分钟批次上限）要显示 `INTERRUPTED`/恢复提示但不擅改库中状态或自动接管，中断/失联不永久转圈。`javascript:`、外域、同域假路径或附件 URL 不得成为 `original_url`；5000+ 采购单位不截断。Run: `uv run python backend/manage.py test portal.tests.test_tender_api --noinput`；Expected: 缺接口/CSRF/关闭保护导致 RED。
- [ ] **GREEN:** 所有端点统一调用现有 `portal.product_service.product_user_allowed`，不增加 Tender 用户权限层；POST 复用目标 masked CSRF 和 Task 4 的唯一活跃批次逻辑。新入队时检查双开关、Task 2 的单例消费者心跳 ≤15 秒且四源名单固定；已活跃批次优先返回 409 + 原 ID，未启用时返回 `503 refresh_unavailable` 不入队；GET 批次从本地 DB 读取结果/最近成功时刻，RUNNING 租约超时或消费者失联且排队超过 15 分钟仅**展示** `INTERRUPTED` 与运维恢复提示，不暗中接管或更改持久状态。四源入库前核验 HTTPS 官方详情 URL 与实际正文，API 只输出已核验持久链接，不展示原始 HTML/未经核验附件；来源失败不混淆空列表。不接资质原件接口。
- [ ] Run: 本 API 测试 + 现有 `portal.tests.test_product_workspace`；Expected: 产品访问、CSRF、刷新开关/在线条件、官方链接与状态语义全绿。Commit: `feat(tender): expose authorized product opportunities`。

### Task 6: 产品页「商机获取」与可点原始公告

**Files:** Modify `frontend/src/centers/{config.ts,CenterWorkspace.tsx,ProductWorkspace.tsx}`；Create `frontend/src/product/{TenderOpportunities.tsx,tender-api.ts,tender-opportunities.css,TenderOpportunities.test.tsx}`；仅必要时 Create `TenderOpportunityDetail.tsx`。

**Interfaces:** Consumes Task 5 JSON；Produces `/centers/product/opportunities` 与详情深链（例如 `/centers/product/opportunities?notice=<id>`；URL 恢复列表筛选时继续保留 query 参数），无顶层 `/centers/tender`。

- [ ] **RED:** RTL/Vitest 测产品菜单进入即显示现存结构化公告，不触发采集；官方 URL 是 `<a target="_blank" rel="noopener noreferrer">`。点击刷新：可用时 POST 仅一次并依批次 ID 查进度，409 接续同批次，不可用显示原因且不无限排队；QUEUED/RUNNING 显示状态与可离开后重进恢复，SUCCESS/PARTIAL/FAILED/INTERRUPTED 后停止轮询、重取公告/来源状态并展示来源新增数/失败码，撤权或请求中断立即清空业务 DOM。管理员 preview 不调用 Tender API；产品旧深链保持。Run: `pnpm --dir frontend test -- TenderOpportunities` 与 `pnpm --dir frontend test -- CenterWorkspace`；Expected: 缺刷新状态流导致 RED。
- [ ] **GREEN:** 在 ProductWorkspace 分发 section 并增一个产品菜单项；局部 CSS + `apiRequest`（平台 masked CSRF），不复制独立 demo cookie helper、不在页面打开时抓取；刷新触发一次 POST，按返回批次 ID 适度轮询本地 DB，已有活跃批次可恢复查看，离开页面取消轮询、终态停轮询并重载列表，失败展示可读提示。未知字段不猜、正文纯文本，来源筛选不截断；只有核验 URL 才渲染原文链接，管理员预览只渲染静态说明。
- [ ] Run: `pnpm --dir frontend test && pnpm --dir frontend typecheck && pnpm --dir frontend build`；Expected: 全量前端绿色、无 TS 错误且 Product/HR 菜单不退化。Commit: `feat(tender): add opportunities inside product workbench`。

### Task 7: 新隔离 PG 与浏览器 A 验收，B/C 留授权闸口

**Files:** Create `docs/product/TENDER_INTEGRATION_ACCEPTANCE.md`, `docs/product/TENDER_INTEGRATION_RUNBOOK.md`；临时脚本/日志放 `.runtime/tender-product/`；若测试发现业务缺陷，先补同模块 RED，再最小修复。

**Interfaces:** Consumes Tasks 1–6；Produces经日志证明的 A 裁决与明确 B/C 待授权清单。

- [ ] 新起与日常 DB 无关的 postgres tmpfs 容器，`docker inspect` 确认**无挂载**及 `127.0.0.1` 回环端口；随机口令仅忽略目录；白名单配置明确 DB 名/host/port，任何参数不匹配即拒运行；禁用采集与模型。对空库运行 `migrate --plan`, `migrate`, `check`, `makemigrations --check --dry-run`；验证新部分唯一索引和原 Product 表结构未受破坏。不可安全隔离时标 PG `NOT_VERIFIED`，不退到业务库。
- [ ] **RED→GREEN for discovered regressions:** 在不同 Python 进程/PG 连接上，以离线 fake adapter 模拟产品用户 POST→队列→独立消费者领取→状态终结→API 查询→合成公告新增可见，以及重复 POST 409 同 ID、来源/批次 15 分钟过期不可自动接管、旧 fence 拒写、人工审计恢复。分别杀掉空闲/运行中的消费者，心跳过期 POST 不入新队列；长时间执行仍正确续跳、不误报离线；重启仅领未开始 QUEUED，不抢过期 RUNNING。先加复现测试再修根因。`PORTAL_TENDER_*` 默认关闭，只在离线测试隔离进程中 override True，保存命令/计数/状态，零公网调用。
- [ ] 在隔离账号/合成库启动仅回环 Web + Chromium：产品用户进页面立即可见已有公告、详情/可信原文链接；采集关闭时刷新明确「未启用」且零入队，隔离开关开启时点击刷新并由后台 fake adapter 消费，页面进度从排队至终态、列表增量可见，再次点击不重复商机；非产品用户被拒、管理员 preview 零业务请求、撤权清除业务 DOM，5000+ 合成采购单位可完整筛选。**不实际点击外站链接**；B 授权后核验真实网页。原产品深链照旧。
- [ ] 跑 `uv run python backend/manage.py test portal.tests --noinput`、`pnpm --dir frontend test/typecheck/build`（完整命令分别运行）、`python backend/manage.py check`、迁移检查；逐项记录失败类别和与 Task 1 基线的差异，不以定向通过宣称全量通过。无 PG 容器时文档逐项 `NOT_VERIFIED`；容器实验后删除凭据并销毁 tmpfs，不能触碰日常库。
- [ ] 验收文档划分 A=目标平台关闭真实采集接入与离线刷新闭环、B=固定三日受控真实公告加真实后台按钮刷新、C=目标环境部署/消费者启用；B 另批公网及隔离环境授权，逐源有界核验真实日期与原文且不得把不完整窗口标全量；C 审备份、存储、消费者心跳/启动/告警、开关、出站及回退，正式按钮未连上消费者时必须标不可用；不默认加周期调度。Run: `git diff --check && git status --short`，对照 main 原三处未提交文件仍未变。Commit: `docs(tender): record product integration acceptance gates`（只在功能分支，绝不自动 push/merge/deploy）。

## 完成判定与交接

A 通过只报告「目标平台产品事业部的商机获取已在隔离环境接入，真实采集关闭；刷新按钮离线闭环通过」。B 单独批准后，固定窗口内至少一条真实公告完成「可信发布日期→匹配详情→结构化商机→官方原文→后台刷新补采/复跑不重复」方可报告真实数据目标通过；无合格公告记 `NOT_VERIFIED`。C 的正式部署、消费者运行与启用按钮另行批准；不默认周期采集，不因 A/B 通过自动触发。
