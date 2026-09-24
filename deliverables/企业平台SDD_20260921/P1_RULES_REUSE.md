# P1 旧项目规则最小复用合同

## 范围

本次只复用旧文档项目中与当前产品 P1 一致的通用有据写作和审查约束。规则固化在 `backend/portal/product_assets/p1_rules.json`，加载器运行时只读取该本地文件，不读取旧项目工作树、不自行调用模型/网络/WorkBuddy。主线已接入现有worker和审批授权上下文；本规则增量不修改冻结模板或数据库字段。

当前冻结资产 `backend/portal/product_assets/bj_docs/manifest.json` 保持原样。业务审核人、审批层级、正式模板及发布政策继续由现有平台门禁和待确认项决定，本规则不补造政策。

## 稳定接口

- `portal.product_rules.stage_rules(route) -> str`：`route` 是规则阶段键，只接受 `blueprint`、`write`、`review`，不是可配置的模型网关 route。建议调用方按 `payload.action` 映射：`blueprint -> blueprint`、`chapter -> write`、`independent_review -> review`。
- `portal.product_rules.rules_hash() -> str`：校验资产后返回固定 JSON 的原始字节 SHA-256，当前为 `7ed4d2eabea00402e63bfd26e85f5551e79e449e0ad4f859b150816d1edfec55`。
- `portal.product_rules.ProductRulesError`：规则阶段无效时抛出，错误文本为 `product_rules_stage_invalid`。
- `portal.product_rules.ProductRulesUnavailable`：本地资产缺失、字节哈希变化、JSON 结构或内容校验失败时抛出，错误文本为 `product_rules_unavailable`；它是 `ProductRulesError` 子类。

主线已按 `payload.action` 映射三阶段 system prompt，并把 `rules_hash` 纳入模型调用记录、审批授权哈希和生成指纹；规则变化时拒绝旧输出，资产缺失时安全失败且不外呼。任何 `ProductRulesError` 都必须在模型外呼前终止；本轮不新增 migration。

## 来源基线

旧项目只读根：`C:\Git_projects\bj_device_company`。下列哈希由 2026-09-22 本次读取的文件原始字节计算；运行时不回读这些路径。

| 来源 | SHA-256 | 复用内容 |
| --- | --- | --- |
| `skills/bj-docs/references/intake.md` | `af32f0d8281c8ed0f99c1e8671bd0d1b1f463203425ba9aedcf3549a81c4ca22` | 来源、事实、需求、缺项和冲突分离；不补造值、单位或授权 |
| `skills/bj-docs/references/technical-solution.md` | `2056900ab63c9fc0cef54280f4e5bdcf1edd2bff7992ec571ec5a3b9f4192c3a` | 约束到响应、接口、失败处理、责任和验收证据的有据展开 |
| `skills/bj-docs/references/review.md` | `0096f3849f052043fa93a958e508c910b02039e7e2cb600250a6d139859b9b0c` | 精确版本审查、材料变化回蓝图、受影响范围复核和旧回执失效 |
| `skills/bj-docs/references/plan.md` | `3af4d63a5db7ff9a5fd076e6482e866688a7fb0f70577791308e5951f4887446` | 蓝图覆盖、来源绑定、待确认保留及不静默扩张范围 |

## 阶段约束

- `blueprint`：只使用当前任务及获授权来源；明确目的、受众、章节范围、来源、条件、缺项和冲突；模板仅证明格式来源；审批政策仍走现有平台门禁。
- `write`：只使用已批准蓝图、事实快照和本章来源；不补造数字、单位、参数、标准或结论；证据不足保留待确认；跨章数字、术语和接口方向一致。
- `review`：绑定输入、蓝图、章节和规则哈希；检查事实来源、需求覆盖、数字单位、范围接口、安全和跨章一致性；审查只提问题，不批准材料性变化；无法确定影响范围时扩大复核。

## 明确排除

- 不复用旧项目技术方案 50,000 字、可研 70,000 字及逐章预算。
- 不复用每份 Word 至少 20 张图片及相关数量门槛。
- 不复用 WorkBuddy 原生 AgentTool、实例要求、`bootstrap.cjs` 命令或其他专有工具依赖。
- 不把旧模板章节、结构检查、Office 渲染或规则文本本身当作工程事实、内容通过或正式业务批准。

## 变更与失败关闭

加载器固定资产路径、schema、三阶段、四个来源、三项排除和资产 SHA-256。文件缺失或任意字节变化均失败关闭；有意更新规则时必须在同一获批变更中更新 JSON、固定哈希、测试和本合同。定向测试覆盖阶段路由、规则及来源哈希、缺失资产和变更资产拒绝。
