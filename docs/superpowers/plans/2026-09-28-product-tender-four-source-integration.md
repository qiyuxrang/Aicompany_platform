# 产品事业部商机获取四来源接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将独立 Tender 四来源 V1 选择性接入 `Aicompany_platform` 产品事业部，使授权用户在“商机获取”看到结构化公告并打开核验过的官方详情链接；首次真实数据仅限固定三个自然日，并与默认关闭的接入验收分开审批。

**Architecture:** 目标仓库的 `portal` 是唯一身份、权限、迁移、审计和 API 宿主，Tender 来源、快照、公告与商机是独立领域表，不写 Product `DocumentTask`。复用独立版安全出站/四个来源/幂等/租约逻辑，按本仓库修改门禁和 URL 校验；React 产品侧栏新增一个 section，不建新 center。先完成隔离 PG 上的无出站 A 验收，再将受控真实首次采集作为另行批准的 B 门槛，部署/调度 C 独立审批。

**Tech Stack:** Django 5.2 / DRF / PostgreSQL，React / TypeScript / Vite，目标仓库已锁定的 uv / pnpm；不引入新服务、前端路由库、定时器或公网依赖。

**Spec:** `docs/superpowers/specs/2026-09-28-product-tender-four-source-integration-design.md`（用户已确认；本计划和规格一起交付执行）。

## Global Constraints

- 目标仅为 `C:\Users\BJRunner\Desktop\Aicompany_platform`；现有 `main@43abfc7` 现场在 `backend/config/settings.py`、`backend/portal/product_knowledge_service.py`、其测试有用户未提交改动。已有 `docs/product-tender-four-source-design@2bacf99` **只是文档分支**，它不包含那些改动；实施前重新核实 `main` HEAD、工作区状态，并从当前真实主线建立新的仓库外隔离功能 worktree，不 stash/reset/checkout/覆盖主工作区，不直接提交 main。不从旧 `ai智能体平台` 分支 merge/cherry-pick 迁移。
- 范围仅 `ccgp_national`、`sx_jk_ecai`、`shxjkjt`、`csg_bidding`；其余 12 个和旧稿首批两站继续留台账，未知不判 PASS。`docs/SPEC.md` 是权威，实施时最小更正四源范围并注明旧验收未达成，不改既有需求编号/历史证据。
- 首次窗口固定 `Asia/Shanghai` **[2026-09-26 00:00:00, 2026-09-29 00:00:00)**（左闭右开），不是滚动 72 小时；没有可证明发布时间的记录不入库，不推断公告 ID/日期/地区。日期仅精确到天时保存真实精度，不虚构时分秒。采集时点若在窗口之后也不能改窗口。
- 初始采集、手动刷新开关默认 false；HTTP GET、预览、档案、`--force`、`--preflight`、直接 Worker 均零外站出站。需要显式授权的操作员/测试设置才可消费离线批次；过期来源/批次**不**自动接管，确认旧进程退出才可人工恢复并写审计。
- 任何迁移、种子、账号及回归只对新的无宿主卷 tmpfs、仅回环端口 PostgreSQL 运行；任何 DB 参数不匹配则拒绝执行。禁用正式模型调用、采集、调度、部署；绝不复制独立版数据、demo 账号、源库或旧 `tender/migrations/0001–0009`。
- B 阶段真实访问、在目标平台入库、官方链接点击须在 **A 完成后另获出站与环境授权**；本计划不以批准实施 A 代替 B 授权。C 阶段的部署、计划任务、正式库迁移须另行方案与批准。不绕过登录/验证码/403/TLS 或对外站并发压测。
- 测试遵循 RED→GREEN；单个来源异常不得伪装为空列表或成功；不完整扫描不推进边界。每个任务尾留验证结果和原子 commit（只在隔离功能分支）；主仓库 main 的状态前后对照记录。

## 文件与接口地图

- 既有权威/入口：`docs/SPEC.md`；`frontend/src/centers/{config.ts,CenterWorkspace.tsx,ProductWorkspace.tsx}`（导航与 section）；`frontend/src/api.ts::apiRequest`（masked CSRF）；`backend/config/{settings.py,urls.py}`；`backend/portal/{models.py,security.py,product_service.py}`（产品授权）；`backend/portal/migrations/0023_hr_model_selection.py`（当前迁移叶，执行前重核）。
- 新领域文件：`backend/portal/tender_models.py`（模型、约束），`tender_sources/`（四源及其基类），`tender_{outbound,normalize,dedupe,service,worker,manual_refresh,recovery,api}.py`（只迁入实际依赖，其他分析/资质代码不因拷贝便利而接入），`tender_storage.py`（独立私有根与校验）、`tender_window.py`（固定窗口内候选日期判定）；如选择复用独立版更多模块，先写明实际 import 图及非 S2/S3 输出为何必要。新迁移由当前目标模型生成，不能直接拷旧分支文件。
- 新 UI：`frontend/src/product/TenderOpportunities.tsx`、`tender-api.ts`、`tender-opportunities.css`（局部作用域）；如详情复杂，拆 `TenderOpportunityDetail.tsx`，不改全局 CSS。API 暴露 `/api/product/opportunities/`、`/api/product/opportunities/<int:id>/`、`/api/product/opportunities/options/`、`/api/product/opportunities/sources/`；任何从旧 Tender API 迁入的附加路径必须列入测试和鉴权表，关闭时不能出站。
- 新测试：`backend/portal/tests/test_tender_{models,window,api,worker,recovery}.py`、`frontend/src/product/TenderOpportunities.test.tsx`、`frontend/src/centers/CenterWorkspace.test.tsx` 的精确补例；隔离双进程和浏览器证据放功能 worktree 的 `.runtime/tender-product/`，不提交凭据。

## Review Focus

1. 公告日期只含 `2026-09-26`、跨 UTC+8 零点或缺日期：Task 3 测试不能推断成虚构时分秒，边界准确，未知不入。
2. 旧 worker 已失联但租约过期、批次 15 分钟超时、用户快速重复点击：Task 4/7 验第二进程零出站、409 同一批次 ID、旧 fence 拒写、人工审计恢复。
3. 原始 URL 是 `javascript:`、外站重定向、同源假详情路径或附件 URL：Task 5 测在入库前拒绝、输出不会成为前端任意跳转；仅适配器核验的详情链接可点。
4. 产品模块撤权、管理员只预览、首次改密/禁用账号、普通用户直访旧附件：Task 5/6/7 分别验后端立即 404/权限拒绝及页面移除业务 DOM，预览零业务请求。
5. 首页-only 来源出现 3 天前但排在前列的旧公告、中央与地方某一栏不能扫到窗口下界：Task 3/4 验可单条可信入库却不得标三日完整、不得推进 checkpoint；另需注意 `run_all` 不应把同一运行起始时刻误作跨来源实际完成时刻。

---

### Task 1: 隔离基线与规范口径

**Files:** Modify `docs/SPEC.md` 的 4.5 范围描述；Create `docs/product/TENDER_FOUR_SOURCE_SCOPE.md`（旧稿与四源矩阵、验收记录入口）；测试日志仅存 `.runtime/tender-product/`。

**Interfaces:** Consumes `main` HEAD 与批准规格；Produces 四源首批口径与与旧设计的差异说明，其他 Task 均依此取范围。

- [ ] 读 `main` status、HEAD、迁移叶及 `git diff -- backend/config/settings.py backend/portal/product_knowledge_service.py backend/portal/tests/test_product_knowledge_service.py`（只读，禁止输出凭据）；检查当前隔离文档分支的规格是否已可在功能分支使用。若 main HEAD 变化先登记实际基线并审迁移图；从 main 建**新**仓库外功能 worktree，安全导入规格/计划（只复制文档提交，不合入其他分支的代码）；原主 checkout 的 status 快照存忽略目录。无原生 worktree tool 时用 git worktree；不复用 `aicompany-tender-design` 为业务改动分支。
- [ ] 仅在新 worktree 用 `uv sync --frozen`、`pnpm --dir frontend install --frozen-lockfile`；在私有临时 SQLite/PG 路径且模型/外站禁用时执行基线 `uv run python backend/manage.py test portal.tests --noinput`、`pnpm --dir frontend test`、`typecheck`、`build`。记录既有 FAIL/ERROR 与测试环境缺失原因，**不把 main 未提交变更的行为当成提交基线**。
- [ ] `docs/SPEC.md` 的 4.5 加一小段明确「本次接入 S1 采用四源，旧稿的两站受限待后续逐源验收，S2/S3 不在本批次」；`docs/product/TENDER_FOUR_SOURCE_SCOPE.md` 写 4/12 来源状态与固定窗口，以及另一仓库已通过证据只能作参考。检查旧稿仍作为历史设计可追溯。
- [ ] Run: `git diff --check && git status --short && git -C C:/Users/BJRunner/Desktop/Aicompany_platform status --short`；Expected: 仅新功能 worktree 有本任务文件，主 checkout 与记录的原三处未提交文件完全一致。Commit: `docs(tender): set four-source product scope`。

### Task 2: 目标 portal 领域模型、私有根与新迁移

**Files:** Create `backend/portal/tender_models.py`, `tender_storage.py`, `backend/portal/tests/test_tender_models.py`；Modify `backend/portal/models.py`, `backend/config/settings.py`；Generate `backend/portal/migrations/00NN_tender_product_*.py`（NN 由当前目标迁移图决定）。

**Interfaces:** Produces `TenderSource/TenderFetchRun/TenderManualRefresh/TenderRecoveryAudit/TenderSnapshot/TenderNotice/TenderNoticeVersion/TenderOpportunity/TenderOpportunityEvent` 及独立私有路径；公告与商机的 `publish_date` 和 `publish_precision`（`date|minute|second|unknown`，不靠虚构时间表示日精度）；Tasks 3–7 消费。依赖真实 `portal.User`，不能用旧 `tender` app 的外键。

- [ ] **RED:** `test_tender_models.py` 用 `PortalTestCase` 导入 `portal.models` 中的上述模型；断言 `_meta.app_label == 'portal'`、来源 `enabled=False` 默认、仅日期的 `publish_date/publish_precision` 可持久保存、`requested_by/operator` 指向 `portal.User`、同源双 RUNNING 和双活跃刷新写库触发 `IntegrityError`（各用内层 `transaction.atomic()` savepoint）；快照/公告 `(source,source_notice_id)` 业务唯一性按独立实现审查。Run: `uv run python backend/manage.py test portal.tests.test_tender_models --noinput`；Expected: 缺失模型/约束导致 RED，不是环境 DB 错误。
- [ ] **GREEN:** 从 `商机获取/tender/tender_models.py` 只移植本阶段必需模型/约束，把 `enabled` 默认改 false；若字段关联了分析/资质模型，明确为可空兼容或删掉未用关联，不能留孤儿 import。仅有日期时 `publish_at` 可空、`publish_date` 存可信原始日期，不能凭 `date` 假造时刻用于排序；私有快照原子写与路径/哈希校验优先复用目标 `product_storage` 底层原语，但 `TENDER_STORAGE_ROOT` 必须与 `PRODUCT_STORAGE_ROOT` 分离；不得把 Product 默认路径改掉。设置 `PORTAL_TENDER_INGESTION_ENABLED`、`PORTAL_TENDER_MANUAL_REFRESH_ENABLED` 默认 false、名单默认空。生成**目标仓库** `portal` 迁移，不拷旧迁移，核对依赖最新叶与 partial unique 索引。
- [ ] Run: 同定向测试 + `python backend/manage.py check`、`makemigrations --check --dry-run`（隔离配置）；Expected: 测试 PASS、无新增迁移。没有新隔离 PG 时先不运行真实 migrate，标 PG 未验。Commit: `feat(tender): add product-hosted models and disabled defaults`。

### Task 3: 固定三日日期判定与四源候选有界扫描

**Files:** Create `backend/portal/tender_window.py`, `backend/portal/tests/test_tender_window.py`；Selectively copy `tender_sources/{base,ccgp_national,sx_jk_ecai,shxjkjt,csg_bidding}.py`, `tender_outbound.py`（另需注册文件仅按 import 图添加）。

**Interfaces:** Produces `FIRST_WINDOW_START/END`（北京时区半开区间）、`classify_window_date(value, *, precision, source_code)` → `inside|before|after|unknown`、`list_window_candidates(adapter, *, max_pages, max_candidates)` → 带 `refs/complete/reason/observed_bounds` 的结果；Task 4 Worker 使用结果的 `complete` 才推进 checkpoint。

- [ ] **RED:** 测时区转换（2026-09-25 15:59 UTC 拒、16:00 UTC 接受；2026-09-28 15:59 UTC 接受、16:00 UTC 拒）；北京日期 `2026-09-26` 可判入但保持 `date` 精度、不可存假 `00:00`；空值/无可靠来源 `unknown`。离线 fake adapter：只能首页时未见三天下界则 `complete=False`；全国双栏目一栏缺页或窗口下界则 `complete=False`，不请求上限外的页/详情；缺日期仅在候选上限内抓官方详情确认，无法确认保持 `unknown`。Run: `uv run python backend/manage.py test portal.tests.test_tender_window --noinput`；Expected: 缺接口或错误窗口导致 RED。
- [ ] **GREEN:** 仅复制四源适配器和安全出站必要文件；不把独立版默认 `list_notices(page=1)` 当作三日全量。全国 `list_incremental` 使用官方分页但不得只靠首次边界空引用结束；必要时用有界首轮扫描直到可信日期低于窗口下界或达上限。其他三源只有首页无法证明完整窗口则返回 `PARTIAL/NOT_VERIFIED`，可单条确认日期后入库。若网页缺发布日期，详情只能用现有 allowlist/HTTPS 和原文证据补证，不凭 ID/列表顺序/抓取时间猜。明确时区及日期精度元数据；不要把仅有日期写成带假时分的时间戳。
- [ ] Run: 窗口与四源离线 fixture 测试（复用独立工程 `tests/test_{ccgp_national,sx_jk_ecai,shxjkjt,csg_bidding}.py` 的代表性样本并替换 import）；Expected: 边界/受保护/部分扫描测试全绿，所有出站 mock 零真实网络。Commit: `feat(tender): bound three-day four-source discovery`。

### Task 4: 来源内幂等、硬关闭 Worker 与人工恢复

**Files:** Create `backend/portal/tender_{normalize,dedupe,service,worker,manual_refresh,recovery}.py`, `backend/portal/tests/test_tender_{worker,recovery}.py`, `backend/portal/management/commands/{run_tender_worker,run_tender_refresh,confirm_tender_recovery}.py`；Modify `backend/config/settings.py` 仅必要配置。

**Interfaces:** Consumes Task 2 模型/私有根与 Task 3 `list_window_candidates`；Produces `run_source`、`execute_once`、`confirm_source/confirm_batch`，其中 Web 只入队。窗口阶段与常规增量分不同显式调用，默认永不从 Web GET 抓取。

- [ ] **RED:** 定向测试 patch `TenderOutbound.fetch` 为一调用即抛异常；在来源误设 enabled=True 时测试 `run_source`、`run_all`、`preflight_source`、`run_tender_worker --force/--preflight`、`run_tender_refresh --once` 均无出站/不领取；失效来源、批次在租约过期后仍 RUNNING 且第二次无法接管；重复入队返回原批次 ID；15 分钟过期不自动 PARTIAL，旧 fence `_finish` 不能覆盖 checkpoint/健康。Run: `uv run python backend/manage.py test portal.tests.test_tender_worker portal.tests.test_tender_recovery --noinput`；Expected: 缺命令/门禁/恢复导致 RED。
- [ ] **GREEN:** 从独立版选择性移植 normalize/dedupe/service/worker/manual_refresh/recovery，替换审计为 `portal.security.audit`、存储为 Task 2 私有根、操作者为目标平台授权名单；入口和直接 Worker 先检总采集开关，手动刷新还需第二开关。`--force` 不得绕过；恢复命令须显式 `--confirm-stopped --operator-id --reason`，检查有权用户、已过期且所有相关来源不再 RUNNING，审计与 fence 同事务。Worker 中有界窗口候选日期校验**发生在详情入库前**，不完整扫描不得推进 checkpoint；重复真实详情业务幂等。检视 `run_all(now=...)` 是否以统一开始时刻误覆盖逐源真实完成时刻，必要时修正并以测试覆盖。
- [ ] Run: 定向测试、目标仓库已有 Product Worker 定向测试；Expected: 无网络调用、所有状态与计数按预期。Commit: `feat(tender): enforce closed gates and fail-closed recovery`。

### Task 5: 产品授权的商机 API 与官方详情链接

**Files:** Create `backend/portal/tender_api.py`, `tender_access.py`, `backend/portal/tests/test_tender_api.py`；Modify `backend/config/urls.py`；如四源归一实际引用未迁文件，在此按测试证据补最小必要文件。

**Interfaces:** Produces `/api/product/opportunities/`（分页/筛选）、`/<id>/`（来源/版本/链接）、`/options/`、`/sources/`；前端 Task 6 消费 JSON，保持 `id,project_name,project_code,region,purchaser,budget,publish_at,publish_date,publish_precision,bid_deadline,status,source,original_url,current_version,first_seen_at` 的单一契约；仅日期时 `publish_at=null`、`publish_date=YYYY-MM-DD`、`publish_precision=date`；精确到分钟/秒时返回可换算时刻及原始精度。

- [ ] **RED:** 复用 `portal.tests.base.PortalTestCase` 构造明确 product Module+Role 的产品用户/无权用户/管理员；授权列表/详情 200、取消 Role/停用模块立刻 404、必须改密拒绝、管理员预览不因此获得 API 权；`javascript:`、外域、同域假路径、附件 URL 不得成为 `original_url`，有效官方 HTTPS 详情 URL 返回且新标签打开不由服务端重定向。筛选多于 5000 个采购单位不截断；来源失败与真正无新公告分别返回明确状态。Run: `uv run python backend/manage.py test portal.tests.test_tender_api --noinput`；Expected: URL 404/无链接校验导致 RED。
- [ ] **GREEN:** 用 `product_user_allowed`（目标已有 `portal.product_service`）鉴权产品阅读，任何对象查询前先鉴权；工作台菜单不能代替 API 权限。入库前在四源适配器核验原始详情 URL 与对应 ID/HTML 标题，API 只读已验证持久 URL，必要时按 source 的允许详情路径复验；绝不把外站原始 HTML 直插 DOM 或让 `source_links` 漏出未经核验的关联 URL。提供规范化来源健康与查询状态，关闭时刷新写接口 404/不可用，GET 零出站；无资质原件上传/下载入口。
- [ ] Run: 本 API 测试 + 现有 `portal.tests.test_product_workspace`；Expected: 权限、筛选、官方链接/错误语义全绿。Commit: `feat(tender): expose authorized product opportunities`。

### Task 6: 产品页「商机获取」与可点原始公告

**Files:** Modify `frontend/src/centers/{config.ts,CenterWorkspace.tsx,ProductWorkspace.tsx}`；Create `frontend/src/product/{TenderOpportunities.tsx,tender-api.ts,tender-opportunities.css,TenderOpportunities.test.tsx}`；仅必要时 Create `TenderOpportunityDetail.tsx`。

**Interfaces:** Consumes Task 5 JSON；Produces `/centers/product/opportunities` 与详情深链（例如 `/centers/product/opportunities?notice=<id>`；URL 恢复列表筛选时继续保留 query 参数），无顶层 `/centers/tender`。

- [ ] **RED:** RTL/Vitest 测菜单“商机获取”出现在 product sections，进入后列出结构化字段、未知字段文案与真实 URL 的 `<a target="_blank" rel="noopener noreferrer">`；模拟空库、来源失败、请求中断和撤权时不保留旧业务 DOM；管理员 preview 不能调用 Tender API；产品现有项目/成果导航与 `/centers/product/documents` 深链仍可用。Run: `pnpm --dir frontend test -- TenderOpportunities` 与 `pnpm --dir frontend test -- CenterWorkspace`；Expected: 缺入口/组件导致 RED。
- [ ] **GREEN:** 在 ProductWorkspace 内分发 section，在中心 config 中添加一个 section 与现有图标映射；以产品 CSS 作用域包裹，从平台 `apiRequest` 读取后端，不复制独立 demo cookie CSRF helper/global styles、不自动刷新/自动采集；正文只渲染纯文本。保持来源筛选选项完整、loading/error/empty 分离；详情从用户所选条目跳转并可返回，原文链接仅在已校验 URL 出现时渲染；管理员预览只渲染静态说明不读 API。
- [ ] Run: `pnpm --dir frontend test && pnpm --dir frontend typecheck && pnpm --dir frontend build`；Expected: 全量前端绿色、无 TS 错误且 Product/HR 菜单不退化。Commit: `feat(tender): add opportunities inside product workbench`。

### Task 7: 新隔离 PG 与浏览器 A 验收，B/C 留授权闸口

**Files:** Create `docs/product/TENDER_INTEGRATION_ACCEPTANCE.md`, `docs/product/TENDER_INTEGRATION_RUNBOOK.md`；临时脚本/日志放 `.runtime/tender-product/`；若测试发现业务缺陷，先补同模块 RED，再最小修复。

**Interfaces:** Consumes Tasks 1–6；Produces经日志证明的 A 裁决与明确 B/C 待授权清单。

- [ ] 新起与日常 DB 无关的 postgres tmpfs 容器，`docker inspect` 确认**无挂载**及 `127.0.0.1` 回环端口；随机口令仅忽略目录；白名单配置明确 DB 名/host/port，任何参数不匹配即拒运行；禁用采集与模型。对空库运行 `migrate --plan`, `migrate`, `check`, `makemigrations --check --dry-run`；验证新部分唯一索引和原 Product 表结构未受破坏。不可安全隔离时标 PG `NOT_VERIFIED`，不退到业务库。
- [ ] **RED→GREEN for discovered regressions:** 在真正不同的 Python 进程/PG 连接上，以离线 fake adapter 模拟来源领取、过期、旧进程已退出、审计恢复及 stale fence；批次模拟 15 分钟超时、第二进程不能领取/零出站/重复 POST 409 同 ID、人工恢复和旧结果拒写；先在被测分支加复现测试，失败才改共享根因。`PORTAL_TENDER_*` 两开关维持关闭，只有离线测试作用域内 override True。保存命令、计数与结果，不把独立版/另一仓库的 PG 结果移植成通过。
- [ ] 在隔离账号和合成公告库启动仅回环本地 Web + Chromium：产品用户点击侧栏可见商机列表、详情、可信官方链接属性和日期精度；无权限直达 API 404、管理员 preview 不请求业务 API、撤权后页面移除业务 DOM，5000 个合成采购单位的原生下拉完整显示且末项可筛选。**浏览器不得真的点官方链接向外站发请求**；B 授权时再逐条核验真实网页。产品项目成果的现有深链照旧。
- [ ] 跑 `uv run python backend/manage.py test portal.tests --noinput`、`pnpm --dir frontend test/typecheck/build`（完整命令分别运行）、`python backend/manage.py check`、迁移检查；逐项记录失败类别和与 Task 1 基线的差异，不以定向通过宣称全量通过。无 PG 容器时文档逐项 `NOT_VERIFIED`；容器实验后删除凭据并销毁 tmpfs，不能触碰日常库。
- [ ] 验收文档划分 A=目标仓库关闭采集接入、B=固定三天受控真实公告、C=发布/调度；B 的执行须另批真正的公网/目标环境授权、按四源合法低频有界运行与网站日期/详情核验，若不足窗口下界明确 `PARTIAL/NOT_VERIFIED` 而不推进边界；C 需要独立备份/存储/监控/回退/部署验收。Run: `git diff --check && git status --short`，对照 main 原三处未提交文件仍未变。Commit: `docs(tender): record product integration acceptance gates`（只在功能分支，绝不自动 push/merge/deploy）。

## 完成判定与交接

本计划在 A 通过时只允许报告「目标平台产品事业部的商机获取已在隔离环境接入，采集关闭」。只有 B 独立批准并且固定窗口内至少一条真实公告在目标平台通过「来源真实发布日期→匹配详情→结构化商机→浏览器点击官方原文→重复运行不重复业务记录」才可说“点击可见真实招标信息”完成；无合格公告写 `NOT_VERIFIED`。C 的生产发布和无人值守采集始终是另一个决策，不因 A/B 通过自动触发。
