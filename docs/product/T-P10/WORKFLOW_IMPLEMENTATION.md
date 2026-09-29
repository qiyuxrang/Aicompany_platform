# 产品事业部智能体工作流实施说明

状态：前五阶段已实施并通过隔离验收  
实施日期：2026-09-27  
交付格式基线：`docs/product/T-P09/DELIVERY_BASELINE.md`

## 1. 已实施范围

### 1.1 交付规范冻结

技术方案、可行性研究报告和商务科技型 PPT 已绑定机器可读基线。2026-09-29起仅使用正式篇幅：技术方案正文不少于50000字、可研报告正文不少于70000字；图文格式沿用现有规范。正文按章节分段生成，每段持久化，重试从未达标章节续写；少于下限不制作可交付文件。篇幅达标不等同于正式发布批准。

### 1.2 产品事业部页面

项目入口支持一份设备清单和多份背景材料。项目详情统一显示六个真实阶段：背景解析、设备分析、RAGFlow 检索、蓝图生成、人工审核、交付物生成。页面不估算服务端没有返回的进度，不把预览写成真实知识库命中。

### 1.3 LangGraph 编排

`backend/portal/product_workflow.py` 使用 LangGraph `StateGraph` 路由任务。Django 数据库仍是持久事实源，节点、任务、租约、动作和时间写入 `DocumentTask.checkpoint.workflow_graph`；租约失效后由既有 worker 重新领取并从数据库版本恢复。

工作流边界如下：

```text
dispatch
  ├─ blueprint → knowledge → blueprint → 等待人工审核
  ├─ knowledge → knowledge_complete → 等待生成蓝图
  ├─ generate_outputs → deliverables → 完成三件套
  └─ 既有兼容动作 → legacy_action
```

人工审核继续使用数据库审批记录，而不是把等待中的进程常驻内存。蓝图退回最多 3 次，每次意见绑定精确蓝图版本和 SHA256；批准后自动排队生成技术方案、可研报告和 PPT。

### 1.4 LangChain 节点

现有模型网关调用与严格 JSON 解析已包装为 LangChain `RunnableLambda` 管道。授权、调用次数、来源范围、规则哈希和业务校验仍由原有服务控制，避免让框架绕过平台门禁。

### 1.5 RAGFlow 适配层

当前默认模式为 `source_only_preview`：只读取本项目上传资料，不发起 RAGFlow 或互联网请求，知识来源数固定为 0，并在页面明确显示测试预览。

正式模式为 `ragflow_required`：用户先执行“检索 RAGFlow”，适配器通过 HTTPS 白名单、Token 环境变量和用户数据集授权调用真实检索接口。返回结果保存为新的不可变输入版本，快照包含 `provider`、`status`、`sources`、`scope_hash`、`query_hash` 和授权信息。蓝图节点会重新验证授权；缺配置、权限变化、网络失败或协议异常都会失败关闭。真实空检索记录为 `completed/no_hits`，不生成虚假来源。

系统不会在 RAGFlow 失败时自动改用联网搜索。未来若需要联网搜索，必须另建授权、域名白名单、快照与引用审计。

## 2. 启用方式

### 2.1 当前测试预览

```dotenv
PORTAL_PRODUCT_BLUEPRINT_KNOWLEDGE_MODE=source_only_preview
PORTAL_PRODUCT_KNOWLEDGE_ENABLED=0
PORTAL_PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED=0
```

### 2.2 未来正式接入 RAGFlow

```dotenv
PORTAL_PRODUCT_BLUEPRINT_KNOWLEDGE_MODE=ragflow_required
PORTAL_PRODUCT_KNOWLEDGE_ENABLED=1
PORTAL_PRODUCT_KNOWLEDGE_AI_CALLS_ALLOWED=1
PORTAL_PRODUCT_KNOWLEDGE_URL=https://受信任主机/api/v1/retrieval
PORTAL_PRODUCT_KNOWLEDGE_ALLOWED_URLS=https://受信任主机/api/v1/retrieval
PORTAL_PRODUCT_KNOWLEDGE_TOKEN_ENV=RAGFLOW_PRODUCT_TOKEN
RAGFLOW_PRODUCT_TOKEN=由部署环境注入的真实Token
PORTAL_PRODUCT_KNOWLEDGE_AUTHORIZATIONS={"用户ID":{"数据集ID":["文档ID"]}}
PORTAL_PRODUCT_KNOWLEDGE_AUTHORIZATION_REVISION=授权版本号
```

Token 不写入项目文件。正式配置必须同时具备 URL 白名单、环境变量 Token 和用户授权映射。

## 3. 验收结论

- 后端全量：`Ran 486 tests`，结果 `OK`，4 项按原有环境条件跳过，0 失败。
- 核心工作流：61 项通过；新增 LangGraph/RAGFlow 专项 6 项通过。
- 前端：23 个测试文件、227 项测试通过；TypeScript 检查和生产构建通过。
- 真实浏览器：15 项通过，覆盖桌面端、390 像素移动端、深色模式、资料上传、来源权限、人工蓝图、审核门禁和无伪造成果。
- 部署依赖：Python 3.13 环境可由 `uv sync --frozen` 成功创建，LangChain 1.4.2、LangGraph 1.2.12 已进入锁文件。
- Django：系统检查通过，迁移检查无漂移。
- 交付格式：冻结合同、16 项资源清单和 v10 三件套复验全部通过，外部模型、RAGFlow、网络调用均为 0。

机器汇总位于 `docs/product/T-P10/evidence/acceptance-summary.json`；真实浏览器结果和 8 张桌面端、移动端、深色模式截图位于 `docs/product/T-P10/evidence/browser/`。

当前唯一未执行的外部动作是真实 RAGFlow 调用，因为服务位于另一台当前不可连接的电脑。适配器、正式门禁、配置合同、界面入口和失败处理均已完成；接通后只需填写环境配置，不需要重构蓝图审核或文档生成链路。
