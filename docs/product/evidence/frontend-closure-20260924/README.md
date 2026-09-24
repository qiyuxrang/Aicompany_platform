# P1 前端业务链 Core Evidence

日期：2026-09-24

- 唯一规范权威：`docs/SPEC.md`，SHA-256 `a26368d6ef7e9821a7d716784d803594a303980a683de289b2f021a5cd66df0b`。
- 基线 HEAD：`8470a1a05e9d68ab926c9a19ce482bc236acf2bf`。
- 范围：补齐 T-P01～T-P06 及 T-P07 版本链在现有 React 工作台中的 Core 业务操作，不执行 Browser E2E、不调用真实模型/RAGFlow、不做业务签认。
- 结论：`FRONTEND_CORE_IMPLEMENTED / BROWSER_NOT_RUN`。本证据不构成任一 Task、P1、Release 或 Production Ready 的 PASS。

## 实现边界

1. 三类成果状态、批准、stale/current、allowed actions 与 blockers 均直接使用后端返回值；前端不重算权限或业务状态。
2. 技术方案、可研结构化内容分别审核；PPT 仅从后端允许的已批准内容动作生成。
3. 技术方案/可研章节携带 family；编辑时显式选择目标成果。
4. outputs/history 响应绑定 task version；刷新失败会清空旧 current 与下载链接。
5. 新 lineage 成果在章节、输入、蓝图、标题、批准授权变化后变为 stale；普通下载返回 409，只有显式 `history=1` 可下载，文件名和响应头标记已过期。
6. lineage 不完整的旧记录 fail-closed：不作为 current、不允许派生 PPT，普通下载返回 409，仅可显式 `history=1` 追溯。

## 证据文件

- `frontend-tests.txt`：135/135 PASS，SHA-256 `925c14ee0c6784cab882b3236c63f47177de2384e0698dd44dcaab99e7c9352f`。
- `frontend-build.txt`：TypeScript + Vite build PASS，SHA-256 `e107abe07a63b2c1adeb9e5f96b8e7b754890fcfe6ce84030c77feb96c470c76`。
- `backend-product-regression.txt`：110 tests，109 PASS / 1 SKIP，SHA-256 `ac3c7bbd51777cc4948ab6e3b3918e860ab7c35c0aa3aa2d69e69c70df10d569`。
- `backend-product-regression-initial-failed.txt`：加固后首次全量回归的失败记录，110 tests 中 1 ERROR / 1 SKIP，SHA-256 `7813eeaf09da7f9f721af665e2b34ea4c8f6212189da0d910d4bfe22dbdc35d7`。
- `AT_RESULTS.md`：业务操作链合同级验收与未验证边界。
- `INITIAL_FAILURES.md`：本轮开发期失败及修正说明。
