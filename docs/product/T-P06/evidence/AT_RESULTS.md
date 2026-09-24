# T-P06 AT Results

日期：2026-09-24

## 结论

`NOT_VERIFIED`（`CORE_PASS / FRONTEND_DEFERRED`）。Core 的代表性隔离链与目标环境 Office 渲染已完成；真实模型、真实 RAGFlow、正式人员签认、前端 Browser E2E 和发布门禁未通过，故不得记 Task/P1 PASS。

## 代表性执行链

- 隔离数据库：`.runtime/product-p1-three-20260924-v9.sqlite3`；未修改正式数据库。
- 隔离私有存储：`.runtime/product-p1-three-private-20260924-v9`。
- 输入文件 2 个，上传 hash、上传人和 `author_verification=unverified` 已记录。
- 当前 input v8、blueprint v1；技术方案 report v1 与可研 report v1 分别生成、分别批准。
- `three_drafts` attempt 与 `presentation` attempt 均 `done`，`model_calls=0`。
- PPT 的 source_versions 绑定 technical-solution / feasibility report id、version、sha256、approval_id、approved_by；block_refs 绑定来源。
- 三类 outputs 均为 current draft；正式 feasibility / presentation artifact 批准均被 `formal_release_blocked` 拒绝。
- 完整版本链：`representative-run-20260924-v9/version-chain.json`，SHA-256 `16763d9a5cfb10ca4a037026c6245f7b533a0013c52d6daf8314b3ca2ac93727`。

## 成果

| 成果 | 文件 | SHA-256 | 状态 |
| --- | --- | --- | --- |
| 可研 Word | `representative-run-20260924-v9/artifacts/园区安全接入与集中审计可行性研究报告-代表性草稿-v9.docx` | `af2022d25bf0f046b7214e777db5e27a75252e1fb5ed9e65bb2d3ae3c2f1c491` | 可打开草稿；正式批准 blocked |
| PPT | `representative-run-20260924-v9/artifacts/园区安全接入与集中审计汇报简版-代表性草稿-v9.pptx` | `3cda13f988bda526a1957beb0704afce219f3a8398dbce293315d4c575ae49b9` | 可编辑草稿；非 PPT Master；正式批准 blocked |
| 可研 PDF render | `representative-run-20260924-v9/word-render/可行性研究报告-v9.pdf` | `ac941d074264f4a8d9c9213b0aba102d4918275af63aae56a3f1f1ceb8aadad1` | Microsoft Word，9 页 |
| PPT PDF render | `representative-run-20260924-v9/ppt-render/汇报简版-v9.pdf` | `393552d4d50f5326e82ceadb83407aa2d5d47412780ae7cfb07adcaaded1da3c` | Microsoft PowerPoint 16.0，7 页 |

## 迭代记录

- v5：首个完整 Office 渲染；发现封面断行、页脚密集、待确认项重复。
- v6 / v7：验证脚本断言暴露 pending ref 命名与重复来源范围问题，未被记为 PASS。
- v8：视觉修正后完整通过，7 页 PPT。
- v9：在 v8 基础上将 PowerPoint 实际渲染证据持久化到 presentation artifact 的 `render_evidence`，再次完整通过；作为最终 T-P06 Evidence。

## 边界

- 代表性 reviewer 批准只证明版本/权限/门禁链工作，不代表正式业务签认。
- 技术视觉检查不替代人工视觉质量签认。
- 生成成功不替代 PPT Master 质量；本成果明确为 Draft Engine V1。
- 真实模型与真实 RAGFlow 均未调用，不以静态数据或 mock 冒充。