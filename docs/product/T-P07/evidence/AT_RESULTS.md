# T-P07 AT-PRD-011 Results

日期：2026-09-24

| 场景 | 实际结果 | 结论 |
| --- | --- | --- |
| AI 生成、人工修订、外部上传后查看版本链 | 时间线区分 `initiator` / `editor` / `ai_task` / `approver`，保存原因、前后 hash、父 hash、字段级差异和来源引用 | `PASS`（隔离核心） |
| 外部上传作者边界 | 保存上传人、文件名、大小与 SHA-256；内容作者返回 `unverified` | `PASS`（隔离核心） |
| 旧版保留 | input v1/v2/v3 追加保留，父 hash 连续，旧 payload 未覆盖 | `PASS`（隔离核心） |
| 越权读取敏感 diff | 非 owner / reviewer 返回 404；reviewer 复用正文对象授权可读 | `PASS`（隔离核心） |
| 正常角色修改/删除日志 | history 端点 PATCH / DELETE 均返回 405；无业务写接口 | `PASS`（隔离核心） |
| 浏览器版本页、差异页与历史页 | 尚未接入 | `NOT_RUN / FRONTEND_DEFERRED` |
| 司法级防篡改 | SPEC 明确不宣称；API 仅声明应用级只读 | `NOT_APPLICABLE` |

结论：T-P07 后端核心满足 AT-PRD-011 的可自动验证范围，状态为 `CORE_PASS / FRONTEND_DEFERRED`；不得写成整个 Task 或 P1 `PASS`。

真实依赖：本 AT 未调用真实模型或 RAGFlow，调用次数 0；二者状态不由本 AT 改写。