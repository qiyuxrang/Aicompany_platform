# 产品事业部一期接入收口冒烟 Evidence

日期：2026-09-24
基线：`b3780aa`
分支：`feature/product-department-phase1`

## 范围

- 一期仅包含产品事业部“项目成果”模块：技术方案、可行性研究报告、汇报 PPT。
- 未接入、未展示尚在独立开发中的招投标模块；前端源码检索未发现 `招投标`、`Tender` 或 `tender` 入口。
- 保持既有 API、权限、状态机、Worker 与文档生成链不变；本次未修改业务源码。
- 使用既有本机演示服务 `http://127.0.0.1:18320/`、隔离 SQLite 与代表性成果副本。
- 未执行全量回归、故障恢复、真实模型/RAGFlow、正式业务签认或生产部署验收。

## 轻量技术检查

| 检查 | 结果 |
| --- | --- |
| Django `check` | `PASS`，0 issue |
| 前端 TypeScript | `PASS` |
| 前端 Vite build | `PASS`，41 modules transformed |
| 演示服务健康检查 | `PASS`，`/health/` 返回 HTTP 200 |

## Chromium 双角色冒烟

使用真实 headless Chromium，凭据仅通过进程环境变量注入，未写入 Evidence 或 Git。

### 经办人

1. 登录成功。
2. 进入 `/centers/product/documents`，一级标题为“项目成果流水线”。
3. 读取到代表性任务。
4. 页面显示技术方案、可行性研究报告、汇报 PPT 三类当前成果。
5. 三类成果均显示“下载当前草稿”入口。
6. 技术方案 Word 与汇报 PPT 均由浏览器真实下载并落盘。

### 审核人

1. 登录成功。
2. 进入同一产品事业部项目成果页面。
3. 可读取代表性任务、三类成果和“最终审核/等待审核”状态。
4. 本轮仅核对读取与审核状态可见性，未执行批准、退回或状态变更。

## 下载及 Office 打开验证

| 文件 | 大小 | SHA-256 | 验证 |
| --- | ---: | --- | --- |
| 技术方案 DOCX | 31,035 bytes | `e27923dc3f522837144dfeff6bae1b0e69f6af67b5711cd29c906df076524e1d` | ZIP/OOXML 签名有效；Microsoft Word COM 只读打开成功，正文非空 |
| 汇报 PPTX | 37,938 bytes | `40c14cebc6473b6b9d6fea1bdff20666bb323883eb7b1e042be3666d3891fd16` | ZIP/OOXML 签名有效；Microsoft PowerPoint COM 只读打开成功，页数大于 0 |

下载文件与一次性浏览器脚本保存在被 Git 忽略的 `.runtime/`，不作为仓库交付物。

## 结论

- **产品事业部一期接入冒烟完成。**现有平台已提供项目成果入口，经办人与审核人均可访问代表性任务，三类成果可见，至少一个 Word 和一个 PPT 已完成浏览器真实落盘及 Microsoft Office 打开验证。
- 完整 Browser E2E 仍为 `NOT_VERIFIED`：未覆盖新建任务、上传、输入问题处理、蓝图保存/批准、双报告内容批准、新生成三件套、退回重做、stale、撤权及故障恢复。
- 本结果不得外推为 T-R01、P1、Release 或 Production Ready `PASS`。
