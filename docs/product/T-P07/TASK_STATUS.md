# T-P07 任务状态

| 字段 | 值 |
| --- | --- |
| 唯一规范权威 | `docs/SPEC.md` |
| 独立 TASK_SDD | 不存在；按 SPEC 的 PRD-011 / AT-PRD-011 目标、边界和验收原则执行 |
| 状态 | `NOT_VERIFIED`（`CORE_PASS / FRONTEND_DEFERRED`） |
| 核心结论 | 版本父链、原因、主体、差异、hash、来源引用、AI attempt、批准人与 artifact current/stale 已形成同权限只读时间线；旧版追加保留 |
| 外部上传边界 | 记录实际上传人和所收文件 hash；内容作者固定为 `unverified`，不冒充完整编辑史 |
| 修改/删除边界 | 普通业务 API 仅 GET；PATCH/DELETE 返回 405；后台审计模型继续只读管理 |
| Browser E2E | `NOT_RUN`（`FRONTEND_DEFERRED`），因此整个 Task 不记 PASS |
| Checkpoint | 待本次本地 checkpoint 后回填 |

## 执行记录

1. 复用既有 revision / approval / artifact / audit，不建设在线编辑器。
2. 新增不可覆盖的 `parent_sha256`、`change_reason`、上传人和作者核验状态字段。
3. 新增任务历史只读 API：`GET /api/product/tasks/{task_id}/history/`。
4. 差异读取复用正文对象权限；无权用户返回 404，来源撤权后的关联记录不泄露正文内容。
5. 产品后端回归 109/109 通过；迁移漂移检查为 `No changes detected`。
6. 真实模型与真实 RAGFlow 不属于本任务调用范围，未调用；Browser 与业务签认留待后续轮次。

## 证据

- `evidence/TEST_RESULTS.md`
- `evidence/AT_RESULTS.md`
- `evidence/backend-product-regression.txt`