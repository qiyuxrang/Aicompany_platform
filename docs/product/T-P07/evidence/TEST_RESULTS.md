# T-P07 Test Results

日期：2026-09-24

- 规范 SHA-256：`a26368d6ef7e9821a7d716784d803594a303980a683de289b2f021a5cd66df0b`
- 起始 HEAD：`58b11beb2011fe9cbdc0a7edbca4879ae4ff6307`
- 迁移漂移：`No changes detected`。
- T-P07 定向测试：1/1 PASS。
- 产品后端完整回归：109/109 PASS，167.181 秒。
- 覆盖：版本父链、字段级 diff、发起人/编辑人/AI task/批准人角色、原因、文件/工件 hash、来源引用、current/stale、越权读取、只读边界、上传人/作者未核实边界。
- 初次沙箱运行产生 98 个临时目录拒绝错误；在获准非沙箱环境重跑后消失，不计业务失败。另修正既有 PairDraft 测试仅关闭 FileResponse 文件资源，避免测试信号关闭 PostgreSQL 事务连接；2/2 定向与 109/109 全量均通过。
- 原始日志：`backend-product-regression.txt`，SHA-256 `4fddfbd4896a0aae43c462311497a08633b235fec3ddab91ba5855d1cd1e7b64`。
- 未执行：Browser E2E、业务人员签认、部署验收。