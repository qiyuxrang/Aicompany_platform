# T-P08 AT Results

日期：2026-09-24  
规范：`docs/SPEC.md`，SHA-256 `a26368d6ef7e9821a7d716784d803594a303980a683de289b2f021a5cd66df0b`  
输入：仅代表性本地资料；未使用真实客户资料，未修改正式数据库。

| AT | 预期 | 实际 | 结论 |
| --- | --- | --- | --- |
| 来源与 hash | 代表性输入、系统来源和交付文件 hash 一致 | 2 个输入文件与 2 个 source 记录逐项一致；3 个交付文件重算 hash 一致 | `PASS` |
| 版本链 | input、blueprint、chapter、report、approval、artifact 可追溯 | v12 记录 21 个 revision、3 个批准、3 个 artifact；ID/hash/parent/source 保留 | `PASS` |
| 独立成果 | 两份 Word 与 PPT 独立生成、独立 artifact | technical-solution、feasibility、presentation 三个 family 独立，旧版本未覆盖 | `PASS` |
| 可研经济边界 | 无经核实成本/收益资料时不得编造结论 | DOCX 明确“不形成投资回报、成本节约或收益结论” | `PASS` |
| PPT 来源 | 只从已批准结构化内容派生 | PPTX 为 7 个可编辑 slide XML，包含两份 report version/hash/approval 引用 | `PASS` |
| current / stale | 当前成果可识别；源变化后旧成果 stale，普通下载拒绝 | v12 三件 current；产品回归覆盖章节/输入变更后的 stale、409 与显式历史下载 | `PASS`（隔离技术测试） |
| 权限 | 未授权用户不能读取或下载 | 产品回归覆盖 foreign user 404、reviewer 撤权和下载审计 | `PASS`（隔离技术测试） |
| Word 真实渲染 | 两份 Word 在目标应用真实渲染且逐页检查 | Microsoft Word 各 9 页；18/18 页技术检查无裁切、遮挡或乱码 | `PASS`（技术视觉） |
| PPT 真实渲染 | PowerPoint 真实渲染且逐页检查 | Microsoft PowerPoint 16.0，7/7 页技术检查通过 | `PASS`（技术视觉；非 PPT Master） |
| 正式批准 | 缺少正式授权时不得成为正式成果 | technical-solution、feasibility、presentation 三次均 `formal_release_blocked` | `PASS`（门禁） |
| 真实模型 / RAGFlow | 未授权时不得调用或伪报通过 | model_calls=0，ragflow_calls=0 | `NOT_VERIFIED` |
| 业务签认 | 必须由获授权人完成 | 未执行；代表性 reviewer 不构成业务签认 | `BLOCKED` |

结论：T-P08 自动化质量与技术渲染范围通过；正式模板、业务视觉、正式审核、真实模型/RAGFlow 与 Browser E2E 仍未验证，因此 Task 与 P1 均不记整体 `PASS`。
