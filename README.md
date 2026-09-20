# 企业协同门户 · Phase 1 V0.1

**阶段状态：部分完成。** 可运行的个人账号、角色授权、工作台、四中心入口和受限 Django Admin 已实现；经营导航已到达原系统登录页。可信身份与只读数据只有平台端契约和合成测试，尚未完成真实旧系统接入。浏览器 SSO 未实现。本阶段验证平台底座，不代表 AI 文档链路已验证。

## 本地启动

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

- [范围、权限表、模块清单、页面结构](docs/PHASE1_SCOPE.md)
- [可信身份、撤权与只读接口契约](docs/INTEGRATION_CONTRACT.md)
- [实际验收报告与证据](docs/ACCEPTANCE_REPORT.md)
- [部署、启动、备份与恢复](docs/DEPLOYMENT.md)
- [独立审查及修复](docs/REVIEW_REPORT.md)
- [交接与待批准事项](docs/HANDOFF.md)

开源选型已经结束；保留现有 React + Django/DRF + Django Admin，未引入四项候选依赖，也未做 Unfold 改造。历史评估保留在 `docs/OPEN_SOURCE_REUSE_REVIEW.md`。
