# S0 平台基线（只读，未验收）

核验时间：2026-09-23 03:38 UTC；分支 `feature/phase1-portal`，HEAD `8f6db91`；`git status --short` 显示 140 条工作区记录（含先前未提交成果）；本轮未清理或提交。

| 类别 | 源码定位与已核对事实 | S1 要求 / 限制 |
| --- | --- | --- |
| 可复用 | `backend/portal/models.py:159` 的 `AuditEvent`；`backend/config/urls.py:28` 已接产品 API；`frontend/src/centers/ProductWorkspace.tsx` 有产品入口 | 审计字段/权限与页面布局可参考，新增商机仍需独立对象授权与审计用例 |
| 需补验 | `backend/portal/product_worker.py:55,388` 有领取与单次执行；`backend/portal/management/commands/run_product_worker.py` 受 `PRODUCT_P1_ENABLED` 限制；`backend/portal/product_retrieval.py:132,304` 有 P1 授权检索；`backend/portal/model_gateway.py:210` 有受控模型调用；`backend/portal/operations.py:374` 有运维探测 | 这些都是 P1 或平台特定能力；须验证并发领取、单站失败隔离、重试及权限，不能直接将 P1 worker 当定时采集器，不能假定运维探测就是来源健康告警 |
| 缺实现 | `backend/portal/product_models.py:9` 是 `DocumentTask` 而非 Notice；`frontend/src/centers/ProductWorkspace.tsx:6` 仅 solution；`backend/config/urls.py` 没有招投标 API | 需按获准批次实现来源/公告/快照与项目鉴权、变更、告警及产品入口；最小接入点为 `portal` API/模型和产品路由，不新建门户 |
| 外部阻塞 | `tender/SDD.md` 标为 V0.1 待审；`IMPLEMENTATION_AUTHORIZATION.md:7-19` 为原批 D-09，未列招投标 | A0 业务确认、新 D-09 文件范围；A1 每站许可；A2 企业资料；A3 完整文件/模型；A4 上线签认，均未据此材料确认 |

此表为代码静态定位，未运行该模块测试，不能推断新模块可用。P1 技术方案 Word 与本模块分别验收。
