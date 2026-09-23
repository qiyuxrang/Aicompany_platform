# T-G01 阻塞与解除条件

## 证据头

- 日期：2026-09-23
- 任务：T-G01
- 规范：`docs/SPEC.md` V1.0
- 结论：以下 blocker 仅阻塞其依赖路径；冻结资产验证、隔离测试、人工代表性草稿和非外发能力可继续。

| ID | 状态 | 阻塞路径 | 当前证据 | 解除条件 |
| --- | --- | --- | --- | --- |
| BG-01 | BLOCKED_EXTERNAL | 真实模型生成与独立审查 | Portal/Gateway 未运行；D-01 未给出获准模型、数据外发范围和限额 | 明确模型/路由/资料范围/限额，启动 Gateway，保留真实调用日志 |
| BG-02 | BLOCKED_EXTERNAL | 真实 RAGFlow 授权检索 | 无原生版本、HTTPS 端点、token 变量和 owner/reviewer dataset/document scope | 完成 D-01/D-08，执行授权正例、撤权负例、无命中和故障样例 |
| BG-03 | BLOCKED_EXTERNAL | 正式候选、正式成果与业务签认 | 冻结 manifest 明示业务确认阻塞；未指定正式母版批准引用、组织名和负责人 | 完成 D-02/D-03 并由实际产品负责人对精确 artifact hash 签认 |
| BG-04 | NOT_VERIFIED | Browser E2E | 当前 Portal 未运行，本次按前端延期规则未执行 | 启动获准隔离环境，用两个真实业务账号跑上传到下载/核验流程 |
| BG-05 | NOT_VERIFIED | 目标 Microsoft Word 可编辑保存 | 本机冻结 Office 脚本已存，T-G01 未对本轮最终 artifact 执行 | T-P05 对最终草稿生成 PDF/PNG，逐页视觉检查，并记录目标 Word 打开/保存结果 |