# T-P05 Acceptance Evidence

日期：2026-09-23

## 已实际执行

- 代表性资料上传：2 个文本来源，来源 ID、大小、解析内容和 SHA-256 已入 `version-chain.json`。
- 输入版本：保留事实、推断、冲突、缺项及审核历史。
- 蓝图：绑定当前 input hash，并由独立代表性审核账号对精确 revision/hash 批准。
- 分章：4 章，均绑定当前 input hash、blueprint hash 和授权 source IDs。
- Worker：真实 `render` attempt，状态 done，模型调用数 0。
- Artifact：v1，绑定 input/blueprint/review/template/generation hash；未批准。
- Word：Microsoft Word 实际打开并渲染 8 页；输出 PDF 与逐页 PNG；原草稿 hash 未变化。
- 文件可打开：ZIP 完整性无坏项；python-docx 解析得到 25 段、1 表、1 节。
- 正式门禁：批准请求返回 `409/formal_release_blocked`。

## 未通过或未验证

- RAGFlow：`BLOCKED_NOT_CONFIGURED`；真实探针状态 `WAITING_INPUT/retrieval_disabled`。
- 模型：`NOT_VERIFIED_NOT_CONFIGURED`；实际调用数 0，未用 mock 冒充。
- 内容检查：`passed=false`，保留 `unresolved_blueprint_items`；模型审查 `not_run`。
- Word 视觉验收：`NOT_VERIFIED`；页面 PNG 已生成，但未登记视觉 PASS。
- 正式业务签认：`BLOCKED`（D-02/D-03）。
- Browser E2E：`NOT_RUN / FRONTEND_DEFERRED`。

因此本 AT 结论为：技术方案草稿核心链已实际跑通，但正式成果与整个 P1 均不得记 PASS。

