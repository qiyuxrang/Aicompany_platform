# 产品三件套单用户真实模型冒烟 Evidence

日期：2026-09-24  
分支：`feature/product-department-phase1`  
实现 checkpoint：`4f01631`

## 范围

- 单一产品用户创建任务、生成蓝图、确认蓝图并自动生成技术方案 Word、可行性研究报告 Word 和汇报 PPT。
- 模型：`qwen-plus`；通过平台 Django → FastAPI 模型网关 → 厂商兼容接口调用。
- 不使用第二审核账号，不执行报告内容二次审批，不配置应用内费用预算上限。
- 使用脱敏代表性输入；未连接 RAGFlow，未执行正式发布、生产部署或全量回归。

## 自动化验证

| 检查 | 结果 |
| --- | --- |
| Django check | `PASS`，0 issue |
| Migration drift | `PASS`，`No changes detected` |
| 产品链定向后端 | `PASS`，69 项 |
| 前端测试 | `PASS`，135 项 |
| TypeScript | `PASS` |
| Vite build | `PASS`，41 modules transformed |

## 真实 Chromium 单用户流程

使用产品用户登录 18320 本地隔离环境：

1. 创建不含 reviewer 的脱敏任务。
2. 请求真实模型生成蓝图。
3. 展开专业工作台，填写“确认说明”。
4. 点击“确认蓝图并生成三件套”。
5. Worker 自动生成两类独立正文、两份 Word 和 PPT。
6. 任务最终状态为 `COMPLETED`。
7. 三类 current artifact 均通过浏览器真实下载落盘。

## 保留的首次失败记录

首次蓝图调用返回 `invalid_model_output`。模型生成了不在当前任务授权范围内的 `skills/...` 来源 ID，服务端来源白名单校验正确拒绝，没有保存伪蓝图或生成成果。

修复：蓝图模型请求显式携带当前任务唯一允许的 source ID 列表，schema 中的 `source_ids` 同样使用该列表；服务端 `source_ids_belong()` 严格校验保持不变。重试后蓝图生成成功。

## 模型调用

成功链共记录 9 次真实业务调用：

- `product_blueprint`：1 次；
- `product_writing`：技术方案 3 章、可研 3 章，共 6 次；
- `product_review`：技术方案和可研各 1 次，共 2 次。

全部调用状态为 `success`，每次均记录耗时、输入 Token 和输出 Token；日志不保存提示词、模型输出或 API Key。

## 三件套下载与 Office 打开

| 成果 | 大小 | SHA-256 | Office 验证 |
| --- | ---: | --- | --- |
| 技术方案 DOCX | 44,908 bytes | `0b4fcfe2d1df954b0020f3c277e88e29512a5c6afd8b352b66635bcac4964417` | Microsoft Word 只读打开成功，5 页 |
| 可行性研究报告 DOCX | 45,879 bytes | `91618ff492cfbf6f876e998d50ed7b141b7e476eca314e6d65d8da018985473a` | Microsoft Word 只读打开成功，5 页 |
| 汇报 PPTX | 51,948 bytes | `4cf4b44809a5adda09b3284eb810fb18f383722aa2578ebee028a0d2b423c6af` | Microsoft PowerPoint 只读打开成功，13 页 |

两份 Word 均验证：

- 封面只有 1 个冻结 Logo 和对应文档主题；
- 不含“西安工业大学毕业设计（论文）”页眉；
- 文档主题存在；
- 文件为有效 OOXML，浏览器下载后 SHA-256 与落盘文件一致。

## 边界

- 本次是单用户真实模型和三件套技术冒烟，不是完整 Browser E2E。
- 未执行项目全量后端回归、T-G04 故障恢复、RAGFlow、正式模板业务视觉签认、业务成果签认或部署验收。
- 三件套仍为草稿，不代表正式发布或生产就绪。
