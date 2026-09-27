# 生产化阶段实施报告

日期：2026-09-28

## 1. 结论

本轮获准执行的第一、第二、第四、第六、第七阶段已经固化到项目文件。平台代码侧已形成统一模型配置与选择、人事业务模型固化、部门经营台账流转、总经理只读已发布看板、生产配置检查和完整自动化回归闭环。

本报告中的“完成”是指仓库代码、迁移、契约和隔离测试完成，不等同于已在生产环境发布。真实模型、RAGFlow、工程同事系统、生产 PostgreSQL 并发、正式 TLS、备份恢复和最终用户签收仍是外部验收门槛；未接通时系统失败关闭，不生成虚假结果。

## 2. 分阶段成果

### 2.1 第一阶段：范围与生产基线

- 冻结部门边界、数据状态、权限矩阵、模型路由、文档格式和生产完成定义。
- 将真实模型、RAGFlow、工程系统和基础设施条件拆分为明确的外部门槛。
- 建立可逐项复核的生产候选验收矩阵和发布否决条件。

对应文件：

- `docs/production/PRODUCTION_BASELINE_20260928.md`
- `docs/production/ACCEPTANCE_MATRIX.md`
- `docs/production/EXTERNAL_GATES.md`

### 2.2 第二阶段：统一模型平台

- 管理员在 Django Admin 维护服务商、模型、业务路由、默认模型和可选模型。
- 可选模型支持按角色和用户授权；浏览器只接收业务显示名、不透明 UUID、能力、输出上限和配置版本。
- 服务商地址、密钥环境变量、远程模型标识和数据库主键不下发前端。
- 业务请求携带 `model_selection`；服务端在创建任务和执行调用时复验授权、能力和配置版本。
- 配置版本以平台密钥加盐，覆盖路由、模型、服务商和授权的实际运行字段；直接数据库更新也会使旧选择失效。
- 撤权后再恢复仍会推进用户授权版本，使撤权期间的在途输出被丢弃。
- 增加每用户／路由分钟限额、每模型门户并发上限和独立网关并发上限。
- 调用日志仅保存状态、耗时、Token、模型公开标识和配置版本，不保存提示词、输出正文或密钥。

安全模型列表接口：

```text
GET /api/models/routes/<route_code>/options/
```

### 2.3 第四阶段：人事业务生产化接线

- JD 生成和招聘平台适配使用 `hr_jd_draft` 路由，可选择管理员授权模型。
- 简历筛选批次使用 `hr_match_summary` 路由，并把模型 UUID 与配置版本固化在批次上。
- Worker 执行前重新检查模型授权与配置；解析和视觉提取仍走各自受控路由。
- JD、筛选执行页和历史记录展示业务模型名称，不展示敏感配置。
- 保持原有证据化评分、未知字段、敏感年龄不自动评分、用户隔离和 15 天保留规则。
- 为兼容现有非 UI 调用，后端暂时允许省略 `model_selection`；官方前端会主动提交选择。省略时采用执行时默认模型，此兼容口后续可在客户端全部升级后收紧。

### 2.4 第六阶段：部门台账与总经理看板

- 新增工程、财务、售前三类显式台账授权：录入、提交和发布分别控制。
- 部门人员可逐条新增、修改、删除，也可用 CSV 整表导入。
- 台账采用“草稿 → 已提交 → 已发布”状态；发布人可退回已提交版本。
- 所有写入携带 `expected_revision`；旧修订返回 409，避免静默覆盖。
- 每次有效变更生成不可变完整版本、校验和和审计事件。
- 总经理只读每个部门最新已发布版本；草稿、已提交版本和未授权历史不会进入正式指标。
- 总经理看板接口为 GET-only，已移除总经理个人 CSV 上传和个人快照回退，避免出现两套正式数据来源。
- 升级时旧个人快照记录不会删除，但不再进入正式看板；部门需核对真实数据后通过新流程重新发布，不能自动把旧个人快照提升为正式版本。
- 平台管理员只维护授权，不因管理员身份自动读取业务正文。
- 当前是单企业部署模型，每部门一个活动工作簿；单台账最多 2,000 条。

主要接口：

```text
GET  /api/business/ledgers/permissions/
GET  /api/business/ledgers/<department>/
PATCH /api/business/ledgers/<department>/
POST /api/business/ledgers/<department>/records/
PUT|DELETE /api/business/ledgers/<department>/records/<project_id>/
POST /api/business/ledgers/<department>/import/
POST /api/business/ledgers/<department>/submit/
POST /api/business/ledgers/<department>/publish/
POST /api/business/ledgers/<department>/return/
GET  /api/business/ledgers/<department>/versions/
```

### 2.5 第七阶段：质量、安全与运维收口

- 新增只读生产配置检查命令，检查 DEBUG、HTTPS、PostgreSQL、平台密钥、主机名单、CSRF 来源、模型网关和启用路由。
- 检查结果区分代码／配置阻塞项和必须现场完成的外部门槛，不联网、不打印密钥。
- 补齐模型限流、并发、配置漂移、授权漂移、台账权限、并发修订和发布隔离测试。
- 修复 Windows 测试夹具清空 `SystemRoot` 导致 TLS 上下文无法创建的问题；网关 41 项协议与安全模拟全部通过。
- 部署、备份、恢复、回退和生产候选检查步骤继续由 `docs/DEPLOYMENT.md` 统一管理。

生产配置检查命令：

```powershell
.\scripts\manage.ps1 -AppEnvFile .runtime/validation.env check_production_readiness --json
```

## 3. 数据库迁移

全新隔离 SQLite 数据库已从零迁移成功，当前链尾为：

```text
0021_business_ledger_workflow
  → 0022_unified_model_selection
  → 0023_hr_model_selection
```

迁移漂移检查返回 `No changes detected`，Django 系统检查为 0 个问题。另从 0021 构造两条存量模型后升级到 0023，两条记录的公开 UUID 均非空且唯一。

## 4. 自动化验证

| 范围 | 结果 |
| --- | --- |
| 后端完整回归 | 发现 508 项，实际运行 507 项；`OK (skipped=4)` |
| 模型网关完整回归 | 41 项通过 |
| 前端组件与交互回归 | 25 个测试文件、235 项通过 |
| TypeScript 与生产构建 | `tsc --noEmit` 与 Vite build 通过；仅有单包大于 500 kB 的性能提示 |
| 新库迁移与迁移漂移 | 完整迁移至 0023；无遗漏迁移 |
| 真实密码哈希关键回归 | 登录、首次改密、模型授权、HR模型固化、台账与看板共 49 项通过；看板改为 GET-only 后台账相关 17 项再次通过 |
| 真实 Chromium 新台账流程 | 本机安装的 Microsoft Edge（Chromium）执行 11 条路径，全部通过，`page_errors=[]` |

所有自动化使用隔离数据库、合成账号和合成业务数据，不调用真实模型或 RAGFlow。

最终浏览器复跑证据位于 `.runtime/prd-completion/browser/0ee09443811c4a348887fcdd5d1f4d14/`，其中 `result.json` 记录浏览器、11 条检查、截图清单和空的页面异常列表。

## 5. 仍需现场完成的门槛

1. 配置获准的真实模型 HTTPS 地址、白名单、服务密钥、模型标识和费用限制，完成真实响应及并发压力验收。
2. 提供脱敏真实 JD、文本／DOCX／PDF／扫描 PDF 简历与人工基准，完成人事质量验收。
3. 在 RAGFlow 可访问后配置检索地址、令牌及用户到知识库／文档授权映射。
4. 由工程同事提供系统地址、一次身份兑换协议、角色字段和错误契约，完成工程入口接入。
5. 在正式 PostgreSQL 上执行台账双写并发、迁移、备份和隔离恢复演练。
6. 配置正式域名、TLS、反向代理可信链、监控告警接收人，完成用户验收后发布。

## 6. 明确未计入完成的功能

- 总经理 AI 经营分析当前只有模型选择预留，分析按钮未启用，不计作已上线。
- 真实模型质量、真实 RAGFlow 检索和工程外部系统没有被模拟成成功。
- 本轮没有宣称生产 PostgreSQL 并发、正式 TLS 或生产备份恢复已经通过。
