# 企业智能体平台

让产品、人事、工程和总经理在各自的工作台上处理业务：整理项目资料、生成方案、筛选简历、查看企业台账。账号、权限和首次改密统一管理，重要结论由人确认。

**先了解当前边界：** 平台已经包含业务页面、持久化流程和服务适配代码，但克隆仓库并不等于接通真实模型、RAGFlow 或企业原台账。未配置的服务会明确提示，不会用示例回答或虚构经营数字代替。

## 平台能做什么

| 工作台 | 主要工作 | 入口 |
| --- | --- | --- |
| 产品事业部 | 上传项目资料、审核蓝图、生成技术方案／可行性报告／PPT、知识库问答 | `/centers/product` |
| 人事部 | JD 生成、简历筛选、历史筛选记录 | `/centers/hr` |
| 工程部 | 按服务配置上传清单、生成内部成本草稿及查看任务状态 | `/centers/cost` |
| 经营台账人员 | 按授权录入、导入、提交和发布工程／财务／售前台账 | `/centers/business/ledgers` |
| 产品事业部售前人员 | 按授权录入和维护售前项目跟进数据 | `/centers/product/presales` |
| 总经理 | 只读工程／财务已发布台账和售前最新保存的工作数据，保留原系统入口 | `/centers/business` |
| 平台管理 | 分配账号与部门权限、配置模型、查看运行状态 | `/ops`、`/admin/` |

各部门和平台运维共用公司标识及按权限展示的侧栏，桌面内容与侧栏分别滚动，小屏支持折叠菜单。工程成本草稿依赖本地成本服务配置，定额审定与正式报价仍未开放；总经理的工程看板与工程部业务操作是不同入口。此次修复与验证见[全平台夜间交付记录](docs/PLATFORM_NIGHT_AUDIT_20260929.md)。

## 各部门怎么使用

### 产品事业部：从资料到交付物

产品事业部提供「全国商机看板」：关注各行业的信息化、数字化、智能化公开招采项目，通过行业、公告类型、地区三个下拉筛选查看商机概览、公告详情和原文。默认全部行业、全国、招标／采购公告，选择后自动更新；全国表示地区范围，不代表已覆盖全国所有公告。默认检查最近 30 天公开公告，历史公告保留，按榆林、陕西、周边及全国优先排序；支持跨公告项目归并、参与状态、个人已读/关注/不相关标记与公开附件证据。后台更新节奏由配置控制，页面显示真实来源状态与统计。新环境采集与定时开关默认关闭，来源核验后启用。Windows 本地启动脚本按配置同时启动 Web 与采集消费者，无需 Docker。分类规则与统计口径见[全国商机看板](docs/product/NATIONAL_OPPORTUNITY_BOARD.md)，启用、停止及恢复见[采集运行说明](docs/product/TENDER_INTEGRATION_RUNBOOK.md)。

新建项目，上传 **1 份设备清单和多份项目背景材料**，核对识别出的资料类型、设备与需求。背景材料可以是甲方调研结果、项目现状或建设要求。上传支持 PDF、Word、Excel、CSV／TXT 和常见图片；具体限制见[资料解析说明](docs/product/MULTIFORMAT_INTAKE.md)。

新建项目向导会分别管理设备清单和背景材料：开始生成前，必须有一份设备清单和至少一份背景材料。资料不完整时可先保存草稿，之后按类型补充。旧版手工项目保留原输入方式，不自动改写历史附件分类。

开始生成后，页面展示服务端实际进度。生成蓝图后先由项目所有者审核：填写修改意见可生成新版本；确认准确版本后，流程自动继续生成技术方案、可行性报告和 PPT 三件套草稿。资料、意见、批准记录和成果版本保持关联，不会覆盖成一个无法追溯的文件。

“知识库问答”是独立的多轮对话入口。页面从 RAGFlow 显示当前账号已授权的知识库名称与授权文档数量；后端检索获准的资料，再调用模型逐步生成回答并附上来源。流式显示的内容在来源核验完成前属于草稿，只有通过校验的完整回答才会保存到会话。没有检索结果时会直接说明；没有授权的资料、其他账号的对话和已撤销授权的历史不会返回给浏览器。

> 三件套草稿与正式交付不是同一状态。真实模型调用、文档渲染环境、模板批准和正式发布条件分别校验；尚未满足时展示实际阻塞原因。

### 人事部：招聘需求、JD 与简历

侧边栏提供 **JD 生成、简历筛选、历史筛选记录** 三个主要入口，原有转正业务保留在工作概览中。

在 JD 生成中，可用自然语言描述岗位、学历、薪资、福利和五险一金，也可继续编辑结构化字段。系统保留原始说明，未知条件标为待补充。通用 JD 核对确认后，可生成 BOSS 直聘、智联招聘等平台版本；平台适配只生成文案，不自动对外发布招聘信息。

简历筛选可以上传 JD，也可以选择已有的已确认 JD。核对 JD 正文和筛选条件后，点击“进行筛选”进入执行页，选择简历文件或文件夹。文件夹中的文件按批次上传，支持 TXT、DOCX 和含文本的 PDF，单份不超过 2 MiB。页面展示真实处理计数、证据、匹配结果与异常原因，最终取舍仍由人判断。年龄等信息不作为自动打分或排除依据。

**招聘资料暂存 15 天。** 招聘需求及其对话、JD 从需求创建时间计时；筛选批次及简历取批次与所属需求的较早截止时间，编辑和上传不会续期。到期即停止读取、修改和下载；HR Worker 定时删除关联记录与私有原文件。转正档案不在这项清理范围内。

### 总经理：企业台账 BI

总经理首页集中展示 BI 数据分析；财务部、产品事业部和工程部子页仅展示数据明细、筛选、来源、版本与更新状态，不再重复图表。财务盘点 XLS 与产品工作管控 XLS 的首次导入、字段映射和核验说明见 [总经理看板与源表导入](docs/product/MANAGER_SOURCE_BOARDS_20260929.md)。首次导入不创建账号、不修改密码或授权，工程部不填演示数据。

总经理登录后进入数据分析首页，集中查看财务与产品事业部的来源指标和条目分布；进入部门看板后查看项目明细、来源及更新状态，并按项目、状态或来源工作表筛选。工程数据尚未提供时保持空白。

工程和财务 BI 来自**部门最新已发布版本**。产品事业部人员在“售前数据录入”维护项目编号、名称、状态、跟进人员、预计金额（人民币元），也可补充跟进时间、项目定型、项目进展、项目概况、甲方对接人和成熟度。管理员需为录入账号明确授予“售前”台账录入权限；保存后总经理售前看板读取最新工作版本，打开页面时立即读取，页面保持可见时约每 5 秒刷新，并显式标注草稿／待发布状态。这不等于正式审核通过；总经理智能分析仍只使用已发布版本。原“草稿 → 已提交 → 已发布”流程仍可用于审核，发布人可将已提交版本退回修改；所有写入使用修订号防止并发覆盖，并保留不可变版本与审计记录。单部门最多 2,000 行、单次 CSV 最大 2 MiB。参考工作管控表中项目跟进字段建立的是手工录入界面，**不会自动导入原始 XLS**；该表含无表头列、不同统计口径和损坏公式，金额单位须由录入人员确认后按元填写。

总经理页面不提供台账修改入口；工程和财务草稿及已提交版本不会进入正式指标，售前工作数据会在总经理看板明确标注版本与状态。无台账数据时显示“—”，而不是零或演示数值。平台管理员可以配置台账授权，但不会因为管理员身份自动获得台账正文读取权限。

财务金额统一按人民币元计算。原始盘点表模式直接采用“实际应收结余”，分别展示年初应收、已实现回款和计划回款；缺失日期不推算逾期。旧版完整收款台账仍按“合同金额－已收金额”计算待收余额，按台账截止日期判断逾期。两种口径不混用，均不替代会计确认。当前流程不依赖财务、CRM 或工程数据接口，也不修改外部业务系统。

## 账号与登录

账号由管理员预先分配，不开放自行注册，也没有内置默认密码。首次登录会弹出新密码、确认密码两个输入框；保存后初始密码和旧会话失效，需要重新登录。

单部门账号登录后直接进入对应部门；具有多个部门角色的账号从统一工作台选择入口。平台管理员默认不获得部门资料权限，业务账号不能通过隐藏菜单或直接访问 URL 绕过后端校验。

平台管理员在 `/ops/maintenance?tab=audit` 审查平台审计记录，在 `/admin/` 维护账号、角色、模块及模型配置；模型调用日志可在管理后台只读查询。`/ops/usage` 展示员工成功登录和模块启动的使用统计，以及业务模型调用的状态与令牌计数。统计只来自真实审计及调用日志，不采集业务正文；模块启动不代表业务产出或员工绩效，管理员默认也无权读取部门业务资料。

模型路由已配置或调用次数为零，都不表示已接通真实模型。上线前须核实网关、服务商密钥、模型授权及业务路由，并完成获准环境的实际调用验收；没有权限或配置时保持明确的不可用状态。

## 本地启动

**已有环境升级：** 先停止旧服务与消费者并备份目标库，再同步依赖、执行迁移和历史商机分组回填。仅 `git pull` 或 `migrate` 不会给旧记录补上项目分组；操作顺序与完成标准见[商机功能升级检查清单](docs/DEPLOYMENT.md#商机功能升级检查清单)。

**另一台电脑演示已采集商机：** 拉取最新代码，完成本地环境初始化并停止旧服务后，运行 `.\scripts\import-tender-demo.ps1`，即可导入仓库附带的真实公开公告快照并启动看板。不会复制账号密码，不需要 RAGFlow；本次演示不自动联网采集。详细步骤与离线范围见[商机演示迁移](docs/product/TENDER_DEMO_TRANSFER.md)。

需要 Python 3.13、uv、Node.js 24 和 Corepack。依赖版本固定在 `uv.lock`、`frontend/package.json` 和 `frontend/pnpm-lock.yaml` 中。

在 Windows PowerShell 中，从仓库根目录运行：

```powershell
.\scripts\start-local.ps1
```

脚本安装依赖、构建前端、迁移数据库并启动 `http://127.0.0.1:8100`。首次运行会根据配置样例创建 `.runtime/local.env`，使用独立 SQLite 数据库和随机生成的平台密钥；不会覆盖企业原有数据库或启动其他项目。

若这台电脑已有可用的 `.venv` 和 `frontend/node_modules`，可在 PowerShell 7 中复用现有依赖启动，无需 Docker、uv 或 Corepack（仍需 Node.js 在 PATH 中）：

```powershell
.\scripts\start-local.ps1 -UseExistingDependencies
```

此模式会执行前端类型检查和构建，但不安装或同步依赖，也不自动安装产品资料解析、文档渲染运行时。依赖缺失时先按标准流程安装。RAGFlow 和真实模型未配置时可以使用基础平台；知识问答、生成等功能仍需各自服务就绪。

复用模式也可在同一份本地配置下执行管理命令，例如首次启动完成后创建管理员：

```powershell
.\scripts\start-local.ps1 -UseExistingDependencies -ManagementCommand bootstrap_admin,portal_admin
```

另开终端创建管理员：

```powershell
uv run --frozen --env-file .runtime/local.env python backend/manage.py bootstrap_admin portal_admin
```

按提示设置初始密码。首次登录改密后，在平台管理中创建部门账号、分配角色，按需配置模型与知识库。`Ctrl+C` 停止本地服务。

### 让长任务和自动清理持续运行

Web 服务与任务执行器是独立进程。使用同一份环境配置，在两个终端分别运行需要的执行器：

```powershell
# 人事：处理排队简历，并定期清理到期招聘资料
uv run --frozen --env-file .runtime/local.env python backend/manage.py run_hr_worker

# 产品：启用产品流程及相关模型权限后运行
uv run --frozen --env-file .runtime/local.env python backend/manage.py run_product_worker
```

HR Worker 启动时清理一次，此后定期清理。Worker 停止不影响到期访问拦截，但物理删除需要 Worker 恢复或运行下面的维护命令。清理失败会报告错误并保留可重试记录：

```powershell
uv run --frozen --env-file .runtime/local.env python backend/manage.py cleanup_hr_history --limit 100
```

不要把开发配置、验收账号或清理命令指向日常业务数据库。生产使用 PostgreSQL；SQLite 仅用于本地开发和隔离测试。

## 接入模型与知识库

平台的智能体执行层是**Django 持久任务与独立 Worker，加上 FastAPI 模型网关**，不是直接调用个人电脑里的 Codex、Pi 或其他交互式代理会话。

模型服务商、模型、用途路由在管理后台维护，密钥保留在服务端。产品蓝图、写作、审查与知识问答使用独立用途路由。配置步骤见[模型网关](docs/MODEL_GATEWAY.md)。

管理员还可以在“网关模型”列表上传不含密钥的模型 JSON 配置；导入模型默认停用。管理员启用模型并分别配置 `product_assistant`、`hr_assistant`、`engineering_assistant` 路由后，产品、人事、工程人员在各自“模型助手”中只选择获授权的公开模型 ID（附配置版本）并主动输入文本，服务端再次校验权限。它不直接读取业务资料，不代替产品生成、招聘审核或工程报价；没有可用路由时不会假装模型已接通。

RAGFlow 问答采用原生 `/api/v1/retrieval` 接口，配置与旧的蓝图检索适配器分开：

| 环境变量 | 用途 |
| --- | --- |
| `PORTAL_PRODUCT_KNOWLEDGE_ENABLED` | 设为 `1` 启用知识问答 |
| `PORTAL_PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED` | 设为 `1` 明确允许调用外部服务 |
| `PORTAL_PRODUCT_KNOWLEDGE_URL` | 受信任的 HTTPS RAGFlow 检索地址 |
| `PORTAL_PRODUCT_KNOWLEDGE_ALLOWED_URLS` | 允许访问的完整地址列表 |
| `PORTAL_PRODUCT_KNOWLEDGE_TOKEN_ENV` | 存放 RAGFlow API Token 的环境变量名，不是 Token 本身 |
| `PORTAL_PRODUCT_KNOWLEDGE_MODEL_ROUTE` | 回答模型用途路由，默认 `product_knowledge` |
| `PORTAL_PRODUCT_KNOWLEDGE_AUTHORIZATIONS` | 当前用户 ID 到知识库、文档 ID 的明确授权映射 |
| `PORTAL_PRODUCT_KNOWLEDGE_AUTHORIZATION_REVISION` | 可选的授权版本标识；变更后旧授权范围的对话不再展示 |

例如授权映射的形状为 `{"12":{"dataset_id":["document_id_1","document_id_2"]}}`。使用部署环境中的实际 ID；当前不接受通配符授权。入口地址须为 HTTPS、默认 443 端口、路径 `/api/v1/retrieval`，服务端拒绝重定向。配置好产品角色和专用模型路由后，页面会显示可用状态。

详细范围、保留期、台账统计和验收边界见[本轮需求对照](docs/PRD_COMPLETION_20260926.md)。

## 部署与测试

本轮修复、交叉复审、自动化验收与未完成的现场门槛见[部署前加固记录](docs/PREPRODUCTION_HARDENING_20260929.md)。历史测试数量不是当前提交的放行依据。

Docker Compose 包含 PostgreSQL、Web 服务和 HR Worker；HR 文件使用独立持久卷。模型网关使用 `models` profile，产品 Worker 使用 `product` profile。启用对应功能前应完成环境变量、模型路由、私有文件卷和渲染依赖配置。详见[部署说明](docs/DEPLOYMENT.md)，不要将“容器启动成功”当作外部业务联调通过。

已安装锁定依赖的 Windows 环境可执行 `./qa/run_preproduction_checks.ps1`，统一运行后端、迁移、网关、前端测试和构建。结果写入新的 `.runtime/preproduction-checks/` 目录；构建不会覆盖本地正在提供服务的 `frontend/dist`，也不会加载实际业务环境文件或执行生产迁移。

运行不读取现有环境文件的隔离后端测试：

```powershell
uv run --frozen python qa/run_prd_tests.py
```

运行前端测试与构建时，先进入前端目录，使 Corepack 使用该目录锁定的 pnpm 版本：

```powershell
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm test
corepack pnpm typecheck
corepack pnpm build
```

真实模型、RAGFlow、Office 渲染和企业旧台账的连通性，需要在获准环境使用实际服务单独验收。测试中的合成数据不会被当作企业经营数据。

## 文档导航

- [本轮需求对照与边界](docs/PRD_COMPLETION_20260926.md) · [产品开发导览](docs/product/WORKBENCH_DEVELOPMENT_MAP.md) · [人事模块](docs/hr/README.md)
- [多格式资料解析](docs/product/MULTIFORMAT_INTAKE.md) · [模型网关](docs/MODEL_GATEWAY.md)
- [部署与恢复](docs/DEPLOYMENT.md) · [账号与运维](docs/OPS_WORKSPACE.md) · [旧系统集成契约](docs/INTEGRATION_CONTRACT.md)
- [2026-09-28生产基线](docs/production/PRODUCTION_BASELINE_20260928.md) · [本轮实施报告](docs/production/IMPLEMENTATION_REPORT_20260928.md) · [生产候选验收矩阵](docs/production/ACCEPTANCE_MATRIX.md) · [外部验收门槛](docs/production/EXTERNAL_GATES.md)

历史方案与阶段验收保留在 `docs/` 和 `deliverables/` 中，其中的旧端口、临时账号、测试数量和阶段状态不代表当前部署情况。
