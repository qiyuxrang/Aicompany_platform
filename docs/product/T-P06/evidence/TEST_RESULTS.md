# T-P06 Test Results

日期：2026-09-24

- 规范 SHA-256：`a26368d6ef7e9821a7d716784d803594a303980a683de289b2f021a5cd66df0b`。
- 定向测试：`PairDraftTests` + `ThreeDraftFlowTests`，3/3 PASS；覆盖双内容批准、PPT 排队门禁、独立 report version、pending 去重与 stale 下载。
- stale artifact 回归复验：2/2 PASS；恢复“对象可追溯但批准被 409 门禁拒绝”的语义。
- 产品后端完整回归：126/126 PASS，145.041 秒；原始日志 `backend-product-regression.txt`，SHA-256 `34eced50c1068fe0fcd4d9cdc6e5dc38c3821d6fbf1608f3721fe8ec58fe3407`。
- 首次完整回归：125 项中 2 FAIL，原因是 artifact decision lookup 对 stale artifact 提前返回 404；修正后定向 2/2、全量 125/125。失败日志保留为 `backend-product-regression-initial-failed.txt`，SHA-256 `a13848fa1d2f5efa2221044f62ca4af2b5f1593b4a27207be487886143d3f34f`。
- 提交前审查新增“其他 family artifact 不得误伤技术方案 candidate”用例；修复按 family 取 latest 后 1/1、最终全量 126/126。变更前 125/125 日志保留为 `backend-product-regression-pre-family-fix-pass.txt`，SHA-256 `dc4d2a0f2a243c41aca09ec09aa1af5d86ae503af2d71564960a77519d649f90`。
- 代表性隔离 AT：v9 完成；未修改正式数据库，模型调用 0，RAGFlow 调用 0。
- 未执行：Browser E2E、真实模型、真实 RAGFlow、正式业务签认、部署验收。