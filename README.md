# 企业智能体平台

让产品、人事、工程和总经理在各自的工作台上处理业务：整理项目资料、生成方案、筛选简历、查看企业台账。账号、权限和首次改密统一管理，重要结论由人确认。

**先了解当前边界：** 平台已经包含业务页面、持久化流程和服务适配代码，但克隆仓库并不等于接通真实模型、RAGFlow 或企业原台账。未配置的服务会明确提示，不会用示例回答或虚构经营数字代替。

## 平台能做什么

| 工作台 | 主要工作 | 入口 |
| --- | --- | --- |
| 产品事业部 | 上传项目资料、审核蓝图、生成技术方案／可行性报告／PPT、知识库问答 | `/centers/product` |
| 人事部 | JD 生成、简历筛选、历史筛选记录 | `/centers/hr` |
| 工程部 | 统一登录、独立工程页面与原业务系统入口 | `/centers/cost` |
| 总经理 | 工程、财务、售前三类台账 BI 看板，保留原系统入口 | `/centers/business` |
| 平台管理 | 分配账号与部门权限、配置模型、查看运行状态 | `/ops`、`/admin/` |

工程部当前保留已有业务边界；本轮需求没有定义工程测算或项目执行细则，因此不会把尚未接入的工程功能标为完成。总经理的工程看板与工程部业务操作是不同入口。

## 各部门怎么使用

### 产品事业部：从资料到交付物

新建项目，上传 **1 份设备清单和多份项目背景材料**，核对识别出的资料类型、设备与需求。背景材料可以是甲方调研结果、项目现状或建设要求。上传支持 PDF、Word、Excel、CSV／TXT 和常见图片；具体限制见[资料解析说明](docs/product/MULTIFORMAT_INTAKE.md)。

新建项目向导会分别管理设备清单和背景材料：开始生成前，必须有一份设备清单和至少一份背景材料。资料不完整时可先保存草稿，之后按类型补充。旧版手工项目保留原输入方式，不自动改写历史附件分类。

开始生成后，页面展示服务端实际进度。生成蓝图后先由项目所有者审核：填写修改意见可生成新版本；确认准确版本后，流程自动继续生成技术方案、可行性报告和 PPT 三件套草稿。资料、意见、批准记录和成果版本保持关联，不会覆盖成一个无法追溯的文件。

“知识库问答”是独立的多轮对话入口。后端通过 RAGFlow 检索当前账号获准的资料，再调用模型回答并附上来源。没有检索结果时会直接说明；没有授权的资料、其他账号的对话和已撤销授权的历史不会返回给浏览器。

> 三件套草稿与正式交付不是同一状态。真实模型调用、文档渲染环境、模板批准和正式发布条件分别校验；尚未满足时展示实际阻塞原因。

### 人事部：招聘需求、JD 与简历

侧边栏提供 **JD 生成、简历筛选、历史筛选记录** 三个主要入口，原有转正业务保留在工作概览中。

在 JD 生成中，可用自然语言描述岗位、学历、薪资、福利和五险一金，也可继续编辑结构化字段。系统保留原始说明，未知条件标为待补充。通用 JD 核对确认后，可生成 BOSS 直聘、智联招聘等平台版本；平台适配只生成文案，不自动对外发布招聘信息。

简历筛选可以上传 JD，也可以选择已有的已确认 JD。核对 JD 正文和筛选条件后，点击“进行筛选”进入执行页，选择简历文件或文件夹。文件夹中的文件按批次上传，支持 TXT、DOCX 和含文本的 PDF，单份不超过 2 MiB。页面展示真实处理计数、证据、匹配结果与异常原因，最终取舍仍由人判断。年龄等信息不作为自动打分或排除依据。

**招聘资料暂存 15 天。** 招聘需求及其对话、JD 从需求创建时间计时；筛选批次及简历取批次与所属需求的较早截止时间，编辑和上传不会续期。到期即停止读取、修改和下载；HR Worker 定时删除关联记录与私有原文件。转正档案不在这项清理范围内。

### 总经理：企业台账 BI

总经理登录后进入企业台账，可切换工程、财务和售前三个看板，查看指标、状态分布和项目明细，并按项目或状态筛选。

当前 BI 数据来自**当前账号导入的 CSV 台账快照**。每个看板都提供空白模板；填写真实数据、选择台账截止日期后导入。一次导入一个部门的完整快照，最多 2,000 行、2 MiB。页面显示文件来源、台账截止日期和导入时间；没有数据时显示“—”，而不是零或演示数值。

财务金额统一按人民币元计算，待收余额为“合同金额－已收金额”；逾期按快照截止日期判断。该指标不替代会计确认口径。导入仅更新平台快照，不修改原业务系统，也不意味着旧系统已实现自动同步。

## 账号与登录

账号由管理员预先分配，不开放自行注册，也没有内置默认密码。首次登录会弹出新密码、确认密码两个输入框；保存后初始密码和旧会话失效，需要重新登录。

单部门账号登录后直接进入对应部门；具有多个部门角色的账号从统一工作台选择入口。平台管理员默认不获得部门资料权限，业务账号不能通过隐藏菜单或直接访问 URL 绕过后端校验。

## 本地启动

需要 Python 3.13、uv、Node.js 24 和 Corepack。依赖版本固定在 `uv.lock`、`frontend/package.json` 和 `frontend/pnpm-lock.yaml` 中。

在 Windows PowerShell 中，从仓库根目录运行：

```powershell
.\scripts\start-local.ps1
```

脚本安装依赖、构建前端、迁移数据库并启动 `http://127.0.0.1:8100`。首次运行会根据配置样例创建 `.runtime/local.env`，使用独立 SQLite 数据库和随机生成的平台密钥；不会覆盖企业原有数据库或启动其他项目。

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

Docker Compose 包含 PostgreSQL、Web 服务和 HR Worker；HR 文件使用独立持久卷。模型网关使用 `models` profile，产品 Worker 使用 `product` profile。启用对应功能前应完成环境变量、模型路由、私有文件卷和渲染依赖配置。详见[部署说明](docs/DEPLOYMENT.md)，不要将“容器启动成功”当作外部业务联调通过。

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

历史方案与阶段验收保留在 `docs/` 和 `deliverables/` 中，其中的旧端口、临时账号、测试数量和阶段状态不代表当前部署情况。
