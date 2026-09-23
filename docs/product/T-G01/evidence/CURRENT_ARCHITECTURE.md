# T-G01 当前产品 P1 架构基线

## 证据头

- 日期：2026-09-23
- 任务：T-G01
- 规范：`docs/SPEC.md` V1.0
- 代码基线：`feature/phase1-portal` @ `7eec970764ad7be3b1e6eaa5e0bf4b414f811fdf`
- 执行者：Codex
- 结论：核心后端链已存在且隔离回归通过；当前未有 Portal、Model Gateway 或 RAGFlow 运行实例可供真实依赖验证。

## 当前调用链

1. React `DocumentWorkspace` 通过 `/api/product/` 调用 Django/DRF。
2. `product_api.py` 以服务端 session 身份、产品模块权限和对象所有者/审核人身份控制任务。
3. `DocumentTask` 保存状态、阶段、版本、lease、fence、持久动作和 checkpoint。
4. `DocumentRevision` 分存 input、blueprint、chapter、review 和 report，每版保存 SHA-256 及 input/blueprint hash。
5. `run_product_worker` 调用 `product_worker.run_once()`；Worker 用 lease/fence 拒绝过期写入，以 `DocumentAttempt` 留存执行状态。
6. 模型调用经 `portal.model_gateway.generate_for_use()` 到独立 FastAPI `/v1/generate`，带路由权限、费用上限、超时和调用证据。
7. 检索经 `product_retrieval.retrieve_for_task()` 执行 `portal-retrieval-v1` 合同，在调用前后重新校验 owner/reviewer scope。
8. Word 草稿由冻结 `bj_docs` 资产包和隔离 Python 3.12 运行时生成；正式候选额外要求母版批准、Office 渲染、逐页人工核验与成稿批准。
9. `DocumentArtifact` 保存文件路径、artifact hash、template hash、generation hash、来源版本 hash 和 render evidence；下载前再验文件 hash 和对象权限。

## 已存数据与治理能力

| 能力 | 现状 | 依据 |
| --- | --- | --- |
| DocumentTask | EXISTS | 状态、阶段、版本、lease、fence、checkpoint |
| input revision | EXISTS | `DocumentRevision.kind=input`，追加版本和 SHA-256 |
| blueprint revision | EXISTS | 绑定 input hash，批准绑定精确版本/hash |
| chapter/content revision | EXISTS | 稳定 chapter id、family、input/blueprint hash |
| approval | EXISTS | 蓝图/artifact 单一目标，实际操作者与当前授权快照 |
| artifact/version/hash | EXISTS | 不覆盖旧版，生成幂等，下载时校验 hash |
| family | EXISTS | technical-solution/feasibility/presentation 已有字段；本轮只执行 technical-solution |
| stale/current | PARTIAL | 候选 Word 由 `candidate_current()` 校验；前端输出列表已显示 current/stale，但本次未跑 Browser E2E |
| 审计 | EXISTS | 创建、修改、排队、下载、预览、核验和批准均记录服务端 actor |

## 运行时现状

- `127.0.0.1:55438` 有 PostgreSQL 监听，本任务未修改或连接正式数据库。
- `8100` Portal 与 `18410` Model Gateway 无监听；`docker compose ps` 无运行服务。
- `.env` 不存在；历史隔离环境明确设置模型调用禁用，未配置 RAGFlow 原生地址和授权映射。
- 冻结文档运行时存在：Python 3.12.12、python-docx 1.2.0、lxml 6.0.2、PyMuPDF 1.26.4。

## 前端状态

React 代码已包含上传、问题分类与核对、蓝图编辑/批准、章节编辑、排队、草稿下载、候选预览、逐页核验和成稿决策控件。由于本次未启动 Portal 与浏览器验收，结论为 `PARTIAL / FRONTEND_DEFERRED`，不是 Browser E2E PASS。