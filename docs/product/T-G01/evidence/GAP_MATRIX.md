# T-G01 产品 P1 差距矩阵

## 证据头

- 日期：2026-09-23
- 任务：T-G01
- 规范：`docs/SPEC.md` V1.0
- 分类：仅使用 `EXISTING_REUSABLE` / `EXISTING_NEEDS_ADAPTER` / `PARTIAL` / `MISSING` / `BLOCKED_EXTERNAL` / `NOT_VERIFIED`

| 范围 | 结论 | 当前证据 | 后续起点 |
| --- | --- | --- | --- |
| React 产品入口 | PARTIAL | 业务控件已存，本次未做 Browser E2E | 按前端延期规则留至 Core Backend Closure 后验收 |
| Django/DRF Product API | EXISTING_REUSABLE | 任务、来源、蓝图、决策、章节、artifact 端点已存；隔离回归通过 | T-P01～T-P05 直接复用 |
| 服务端身份/对象权限 | EXISTING_REUSABLE | session 身份、模块权限、owner/reviewer 边界、撤权后拒绝 | 不建第二套权限 |
| Artifact 下载权限 | EXISTING_REUSABLE | 下载前复验对象权限、来源授权与文件 hash | T-P05 复用 |
| 事实/推断/冲突/缺项 | EXISTING_NEEDS_ADAPTER | `statements.category` 支持四类，解析 issue 可由审核人分类；尚无真实模型自动抽取验证 | T-P02 在不改变事实语义前提下适配 |
| Worker lease/fence/retry | EXISTING_REUSABLE | 过期 lease 回收、fence 拒绝迟到写入、尝试/模型调用上限已测 | T-P03/T-P04 复用 |
| 部分 Artifact 失败隔离 | PARTIAL | 单个技术方案生成与事务收口已测；三件套非本轮范围 | T-P05 只验技术方案，不扩展 T-P06 |
| Model Gateway 合同 | EXISTING_REUSABLE | 路由、超时、错误分类、用量日志、调用后撤权复验已存 | T-P03/T-P04 复用 |
| 真实模型调用 | BLOCKED_EXTERNAL | 无运行 Gateway；D-01 未指定获准模型、资料范围与限额 | 批准后跑真实章节生成/独立审查 |
| RAGFlow adapter 内部合同 | EXISTING_NEEDS_ADAPTER | `portal-retrieval-v1`、调用前后授权复验、来源 hash 校验已存 | 固定真实 RAGFlow 版本后仅补原生映射 |
| 真实 RAGFlow | BLOCKED_EXTERNAL | 未配置原生端点、token 变量或 owner/reviewer scope | D-01/D-08 解除后执行授权正负例 |
| 技术方案 Word 冻结资产 | EXISTING_REUSABLE | 母版、profile、prototypes、生成器和 Office 脚本均有 manifest SHA-256；真实 DOCX 生成测试通过 | T-P01 复用并产出本轮草稿 |
| 母版业务批准 | BLOCKED_EXTERNAL | manifest 明示 `formal_business_confirmation=blocked` | 草稿可生成；正式候选/发布不得 PASS |
| Word 真实渲染 | NOT_VERIFIED | 渲染运行时与脚本已存，但 T-G01 未生成本轮业务 artifact | 在 T-P01/T-P05 对最终草稿执行并逐页检查 |
| 成稿批准/发布 | BLOCKED_EXTERNAL | 代码门禁完整；`PRODUCT_FORMAL_RELEASE_ENABLED` 未获批 | 本轮交付草稿，不冒充正式成果 |

## T-P01 / T-P02 真实依赖

- T-P01 可在当前冻结资产和隔离文档运行时上继续；正式母版签认仍由 D-02 阻断。
- T-P02 可验证授权合同、错误语义和来源映射；真实 RAGFlow 由 D-01/D-08 阻断。
- 两者均不需要重建 Portal、Worker、权限或第二套文档工具。