# T-G01 任务证据目录（唯一）

本目录是 T-G01 的**唯一任务证据目录**。
裁决见 `../TASK_STATUS.md` §2 的 C-3（2026-09-23，状态 `RESOLVED`）。

## 边界

- 本目录保存 T-G01 的**任务级证据明细**：日志、截图、测试输出、清单、界面记录等。
- 项目级 evidence（`docs/evidence/`、`deliverables/企业平台SDD_V0.2_20260922/qa/`）只保存**跨任务证据或索引**，**不重复保存** T-G01 明细。
- 项目级证据索引只登记指向本目录的指针，不复制内容。

## 必需产出（按 `TASK_SDD.md` 第 12 节）

- [x] `CURRENT_ARCHITECTURE.md`
- [x] `GAP_MATRIX.md`
- [x] `INTERFACE_MAP.md`
- [x] `TEST_RESULTS.md`
- [x] `BLOCKERS.md`

## 可选子目录（按需创建）

- `logs/`
- `screenshots/`
- `test-output/`
- `manifests/`

## 记录规则

- 失败记录必须保留，不得只留成功结果。
- 摘要重构须明确标记，不得伪装成原始日志。
- 截图不能单独证明后端授权。
- mock / 夹具 / 模拟结果不得记为真实通过。
- 每份证据须可定位到：日期、规范版本、任务 ID、执行者、命令或浏览器步骤、结论。

## 当前状态

`PASS` —— T-G01 可信基线、差距映射与接口冻结已完成。真实模型、真实 RAGFlow、浏览器 E2E 与业务签认未被本结论覆盖，详见 `BLOCKERS.md`。
