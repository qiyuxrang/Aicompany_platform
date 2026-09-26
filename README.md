# 企业 AI 业务协同平台

面向产品、工程、人事与经营管理场景的企业级 AI 应用与协同门户。平台以任务为中心，通过统一账号与权限、业务工作台、文档生成与人工审核、版本留痕、模型网关和运维审计，将 AI 能力接入可控的业务交付流程。

项目采用 React + TypeScript 构建前端，Django/DRF 提供门户、权限和业务服务，FastAPI 承载独立模型网关，并支持 PostgreSQL 与 Docker Compose 部署。当前处于 Phase 1，已完成平台底座和产品文档链路的阶段性实现；真实模型、外部知识库与生产系统接入仍以授权和验收结果为准。

## 产品事业部工作台（2026-09-26）

产品部门入口 `/centers/product` 已整理为按项目持续推进的业务工作台：新建项目与附件上传、项目搜索、资料与底稿编辑、审核人指定、可视化蓝图确认、当前成果及历史版本。沿用原有持久任务、模型网关、Worker、权限和批准链；原专业工作台 `/centers/product/documents` 保留。

首页指标按当前账号授权范围统计，生成状态来自服务端。上传支持 PDF、DOCX、XLSX/XLS、CSV/TXT 与常见图片；原生提取与本地中文 OCR 保留来源定位、核对提示和解析历史。真实模型、外部知识库、Office 渲染与正式发布仍遵守既有授权条件。此分支未修改日常服务、真实账号或生产数据。

当前结构、brainstorm 与路由见 [产品事业部开发导览](docs/product/WORKBENCH_DEVELOPMENT_MAP.md)，本轮逐项验证、可复现步骤与尚未验证部分见 [工作台验证记录](docs/product/WORKBENCH_VALIDATION.md)。以下章节保留历史阶段记录，不能代替最新验收结论。

多格式解析的能力边界、版本模型、隔离运行环境、安装与容器资料迁移注意事项见 [多格式资料解析](docs/product/MULTIFORMAT_INTAKE.md)，截图和本轮验证见 [资料解析验收](docs/product/evidence/intake-20260926/README.md)。原工作台验证记录属于首轮历史基线；最新解析范围以此处为准。

## 当前进展

新增独立 FastAPI 模型网关及 Django Admin 配置入口：模型服务商、网关模型、业务用途绑定、只读调用日志。日常18210保留，网关18410仅监听本机；未配置真实厂商密钥，尚未验证真实推理，不代表部门AI业务已接入。配置、验证边界与回退见 [MODEL_GATEWAY](docs/MODEL_GATEWAY.md)。

当前界面默认浅色科技商务风，并支持顶部太阳／月亮图标一键切换日夜风格，记住当前浏览器选择，管理后台沿用同一偏好。日常18210刷新即可查看，账号密码不变。195项前端回归及110项颜色检查通过；使用、证据与回退见 [THEME_SELECTION](docs/THEME_SELECTION.md)，前次视觉改造见 [BUSINESS_LIGHT_UI](docs/BUSINESS_LIGHT_UI.md)。

## 四类工作台前端 V0.2（2026-09-21）

产品、工程、人事、总经理共16个可交互页面已交付，详见 [FRONTEND_WORKSPACES](docs/FRONTEND_WORKSPACES.md)。现有18210账号登录后，管理员从“业务页面预览”查看全部前端，业务人员从已授权入口进入；账号密码不变。前端177项、后端150项回归通过，隔离真实项目对照和内嵌浏览器实测已归档。当前是前端准备与导航，不代表生成、测算、招聘筛选或审批已接入；整体阶段状态仍为部分完成。

**阶段状态：部分完成。** 2026-09-21已完成获批四文件旧端桥接，在独立工作树、独立PostgreSQL和合成测试数据上通过真实双向HTTP身份/权限/只读项目对照，不再只是平台Mock。普通浏览器后台补验仍受连接条件阻塞，原经营实例尚未部署桥接。最新验收、回退及遗留项见 [PHASE1_CLOSURE](docs/PHASE1_CLOSURE.md)。浏览器SSO未实现且不在本轮范围；本阶段验证平台底座，不代表AI文档链路已验证。

## 本地启动

后续补验见 [PHASE1_FOLLOWUP](docs/PHASE1_FOLLOWUP.md)：最终旧端真实联调、27次并发回调以及过期票据清理已验证；门户后端现为143项回归通过。整体仍不包含普通浏览器全量验收、正式上线或浏览器SSO。

2026-09-21新增管理员运维工作台（六页），说明见 [OPS_WORKSPACE](docs/OPS_WORKSPACE.md)。日常预览仍为 `http://127.0.0.1:18210/`，使用 `.runtime/ops-validation.env`；原8100和8018未重启/重配。后续获批桥接验收仅用18310/18318，完成后已停止并撤销测试凭据，不能把这两个地址当作持续开放的业务环境。不要向日常库执行验收清理脚本；`bjrunner` 账号和密码保持不变。

需要本机已有 Python 3.13、uv、Node 24、pnpm；容器部署另需 Docker Compose。版本以 uv.lock、frontend/pnpm-lock.yaml、Dockerfile 为准。

```powershell
.\scripts\start-local.ps1
```

脚本建立独立 SQLite 开发环境并绑定 `http://127.0.0.1:8100`，不修改旧项目。首次另开终端创建管理员（交互输入密码，不存在默认账号密码）：

```powershell
uv run --env-file .runtime/local.env python backend/manage.py bootstrap_admin portal_admin
```

管理员首次登录须修改初始密码；之后重新登录，从工作台进入 `/admin/` 管理账号、角色、模块及映射。管理员默认没有业务模块授权。`Ctrl+C` 停止本地服务。

本次验收实际运行的是独立 PostgreSQL 链路，不是 SQLite：`127.0.0.1:8100` → 手工容器 `enterprise-portal-phase1-db` / 回环 `55438`。该链路使用 `.runtime/validation.env`，**不要与 local.env 的 SQLite 混用**。若使用该现成验证实例，管理员创建命令改用 `--env-file .runtime/validation.env`。验收专用账号在验证结束后停用，不作为默认业务账号。

## 检查与测试

```powershell
uv run --env-file .runtime/validation.env python backend/manage.py check
uv run --env-file .runtime/validation.env python backend/manage.py test portal.tests --verbosity 2
pnpm --dir frontend test
pnpm --dir frontend typecheck
pnpm --dir frontend build
```

数据库测试会创建 `test_portal_phase1`，只可在已隔离的验证库运行。HTTP 验收脚本依赖一次性 QA 凭据，账号停用后不可直接重复使用；补验须先安排新一轮受控测试账号。不要把合成测试结果当作真实经营集成。

在本次隔离PG验证库补验，可依次运行 `validation/prepare_qa.py --rotate-closed-fixtures`、`validation/http_acceptance.py`、`validation/close_qa.py`（均使用 `uv run --env-file .runtime/validation.env python` 前缀）。轮换仅接受已经关闭的完整QA账号组，不覆盖业务用户；失败时也必须执行close_qa关闭临时账号。

## 交付文档

2026-09-22 SDD增量：产品P1持久任务入口为`/centers/product/documents`，默认关闭，仅面向获准隔离验证；不改变既有账号密码。当前状态、实测证据与阻塞见`deliverables/企业平台SDD_20260921/EXECUTION_STATUS.md`，启动及恢复见同目录`P1_RUNBOOK.md`。合成模型测试不代表真实模型链路或正式Word验收通过。

- [范围、权限表、模块清单、页面结构](docs/PHASE1_SCOPE.md)
- [可信身份、撤权与只读接口契约](docs/INTEGRATION_CONTRACT.md)
- [实际验收报告与证据](docs/ACCEPTANCE_REPORT.md)
- [部署、启动、备份与恢复](docs/DEPLOYMENT.md)
- [独立审查及修复](docs/REVIEW_REPORT.md)
- [交接与待批准事项](docs/HANDOFF.md)

开源选型已经结束；保留现有 React + Django/DRF + Django Admin，未引入四项候选依赖，也未做 Unfold 改造。历史评估保留在 `docs/OPEN_SOURCE_REUSE_REVIEW.md`。

## 下一轮审批状态

本地阶段基线：`8f6db91254e060118a9b51212df72297f373bf52`（未推送）。旧端实施先审阅 `docs/INTEGRATION_APPROVAL.md`，批准后才在隔离worktree修改；后台普通浏览器补验清单为 `docs/ADMIN_BROWSER_ACCEPTANCE.md`。当前两项均未新增验收通过结论，整体仍为部分完成。
