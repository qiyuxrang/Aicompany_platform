# 人事招聘模块接入主平台设计

日期：2026-09-24
状态：已确认
基线：`main@6ca196b`
独立来源：`C:\Users\BJRunner\Desktop\HR@e85612e`
适用范围：HR Recruitment V1 完整招聘链迁入；保留主平台现有转正工作流

## 1. 目标与已确认口径

将独立 HR Recruitment V1 的完整招聘能力迁入企业 AI 业务协同平台，同时保留主平台现有转正流程：

```text
企业 AI 平台
└── 人事部门
    ├── 招聘管理（迁入 HR Recruitment V1）
    │   ├── 招聘需求
    │   ├── JD 生成、编辑与确认
    │   ├── 简历批量上传
    │   ├── 简历解析
    │   ├── JD 匹配分析
    │   ├── 证据矩阵
    │   ├── 筛选、排序与详情
    │   └── CSV 导出
    ├── 历史 JD（主平台旧数据，只读）
    └── 转正管理（主平台现有实现）
```

已确认规则：

1. 完整招聘模块接入，不只接 JD。
2. 主平台现有 `HrJobTask / HrJobRevision` 保留为只读历史，不迁移到新模型，不再产生新的可编辑数据。
3. 独立 HR 的 fixture、测试用户、测试需求、测试 JD、测试简历、匹配结果和 SQLite 数据均不迁移。
4. 原始简历保存在主平台私有存储；发送给 `qwen-plus` 前必须自动脱敏。
5. 姓名、手机号、电话、邮箱、身份证号、地址、微信号、QQ 等明显个人识别信息不得发送给外部模型。
6. 招聘模块使用主平台身份、权限、审计、文件存储、模型网关和前端壳层，不保留独立登录、导航、模型服务或数据库。
7. 招聘不建设人才库，不做候选人生命周期、面试、Offer、录用、淘汰或入职。
8. 现有主平台转正状态机、历史、审计和页面保持不变。
9. RAGFlow、招投标接入和招投标筛选本轮不实施。

## 2. 迁移策略

### 2.1 保留并迁入的能力

从独立 HR 项目迁入主平台：

- `RecruitmentRequest`
- `JDVersion`
- `ResumeScreeningBatch`
- `ResumeArtifact`
- `CandidateProfile`（ResumeParseResult）
- `CandidateMatch`（ResumeMatchResult）
- 招聘需求完整性检查与 Missing Items
- JD Generation Skill
- Resume Parse Skill
- Candidate Match Skill
- 程序化评分与证据矩阵
- 筛选、排序、详情和 CSV 导出
- stale、幂等、单份失败隔离和仅失败项重试
- 独立 HR 116 项测试所表达的业务合同

### 2.2 替换的独立 Adapter

| 独立实现 | 主平台实现 |
| --- | --- |
| `FixedIdentityAdapter` | `request.user` + 主平台 HR 角色/模块权限 |
| `FixtureModelRuntime` | `portal.model_gateway.generate_for_use` |
| `LocalStorageAdapter` | 主平台 HR 私有 Artifact/Storage 实现 |
| `InMemoryAuditAdapter` | `portal.security.audit` / `AuditEvent` |

### 2.3 不迁入的内容

- 独立项目的 Django 工程壳、settings、manage.py；
- 独立登录、导航或用户体系；
- `csrf_exempt`；
- 固定测试 HR 用户；
- Fixture 模型作为运行时；
- 本地 storage `index.json`；
- 独立 migration 编号；
- 独立 HTML 模板；
- 独立 HR 数据库和测试数据。

## 3. 主平台业务模型

在 `backend/portal/` 中新增招聘模型，重新生成主平台 migration，不照搬独立项目 migration 编号。

### 3.1 RecruitmentRequest

字段：

- `id`
- `position_name`
- `headcount`
- `responsibilities`
- `required_requirements`
- `preferred_requirements`
- `education_requirement`
- `experience_requirement`
- `skill_requirements`
- `work_location`
- `notes`
- `input_version`
- `current_jd`
- `official_jd`
- `created_by`
- `updated_by`
- `created_at`
- `updated_at`

约束：招聘人数为空或大于 0；`input_version > 0`。

### 3.2 JDVersion

字段与状态：

- `request`
- `version`
- `input_version`
- `state = draft | confirmed`
- `source = skill | hr_edit`
- `body`
- `parent`
- `created_by`
- `confirmed_by`
- `confirmed_at`
- `created_at`

需求 `input_version` 变化后，旧 JD 自动 stale。Skill 只能生成 draft，确认只能由 HR 用户执行。

### 3.3 ResumeScreeningBatch

字段：

- `jd_version`
- `resume_count`
- `status = pending | queued | running | completed | partial_failed | failed`
- `fence`
- `lease_until`
- `attempt_count`
- `error_code`
- `created_by`
- `created_at`
- `updated_at`

状态只表示系统处理过程，不是 HR 决策。

### 3.4 ResumeArtifact

字段：

- `batch`
- `display_name`
- `file_id`
- `filename`
- `media_type`
- `sha256`
- `size`
- `version`
- `source_relation`
- `redacted_text`
- `redacted_sha256`
- `current_profile`
- `processing_status = pending | parsed | failed`
- `processing_error`
- `uploaded_by`
- `created_at`
- `updated_at`

业务对象不得保存物理文件绝对路径。

### 3.5 CandidateProfile

字段：

- `resume`
- `profile_version`
- `source_sha256`
- `source_version`
- `redacted_sha256`
- `fields`
- `current_match`
- `created_by`
- `created_at`

结构化字段包括：姓名占位、教育、工作经历、项目、技能、证书、行业经验、管理经验、语言和其他可验证信息。每个字段必须包含 `status=extracted|unknown` 和可定位的脱敏文本 `source_ref`。

### 3.6 CandidateMatch

字段：

- `jd_version`
- `candidate_profile`
- `match_version`
- `requirement_matrix`
- `score`
- `rule_version`
- `jd_input_version`
- `profile_sha256`
- `created_by`
- `created_at`

旧 JD 或简历变化后匹配结果 stale。不得保存录用、淘汰、推荐面试等字段。

## 4. 旧 JD 与转正数据

### 4.1 旧 JD

现有 `HrJobTask / HrJobRevision`：

- 数据和版本历史保留；
- 新增只读历史 API 或复用现有 GET；
- 前端只显示历史列表与正文；
- 不再展示创建、生成、修改或确认按钮；
- 原写端点不再作为新招聘页面入口；
- 不删除旧表和历史数据。

### 4.2 转正工作流

保留：

- `ProbationCase`
- `ProbationRevision`
- `ProbationTransition`
- 现有确定性状态机
- 主管/HR 双身份规则
- 历史与审计
- 前端 `ProbationWorkspace`

招聘接入不得修改转正数据结构或状态机。

## 5. 身份、对象权限与审计

### 5.1 HR 权限

具备主平台 `hr` 角色且人事模块启用的用户可以创建和操作招聘对象。检查继续复用主平台 `_is_hr` 语义：

- 用户活跃；
- 已完成首次改密；
- 角色包含 `hr`；
- `hr` 模块已启用且获得授权。

### 5.2 对象权限

招聘需求、JD、批次、简历、解析结果、匹配结果和导出均按 `created_by / uploaded_by` 归属当前 HR 用户。其他 HR 用户或普通用户直接修改 UUID 时返回 404，不泄露对象是否存在。

平台管理员不自动获得简历正文或匹配证据权限。

### 5.3 审计

所有写操作与业务事务同事务记录 `AuditEvent`。动作统一使用：

- `hr_recruitment_request_create/update`
- `hr_jd_generate/edit/confirm`
- `hr_screening_batch_create/run/retry`
- `hr_resume_upload/parse`
- `hr_match_generate`
- `hr_screening_export`

审计日志不保存简历正文、脱敏全文、模型提示词或模型完整输出。

## 6. 私有文件存储

### 6.1 支持格式

第一版支持：

- UTF-8 TXT
- DOCX
- PDF

旧 `.doc` 暂不支持，返回稳定错误 `unsupported_file`。

### 6.2 上传边界

- 文件名清洗并拒绝路径分隔符；
- MIME 与文件签名同时校验；
- 空文件拒绝；
- 单文件大小设明确上限；
- 单批文件数量设明确上限；
- 原子写入；
- SHA-256 校验；
- 下载前重新校验对象权限与文件 hash；
- 不返回服务器绝对路径。

### 6.3 文件 ID

主平台新增 HR 私有存储 helper，以随机 file ID 定位，保存相对路径和登记 hash。招聘业务模型只持有 file ID/hash/version。不得直接复用只允许 CSV/TXT 的产品上传解析器。

## 7. 文本提取与脱敏

### 7.1 文本提取

- TXT：严格 UTF-8；
- DOCX：读取 OOXML 段落与表格文本，拒绝宏和损坏包；
- PDF：提取文本层；无文本或扫描件返回 `text_unavailable`，本期不增加 OCR。

### 7.2 脱敏字段

外发前识别并替换：

- 中文姓名；
- 手机号；
- 固定电话；
- 邮箱；
- 身份证号；
- 住址；
- 微信号、QQ；
- 其他明显联系方式。

替换占位符稳定为：`[姓名]`、`[手机号]`、`[电话]`、`[邮箱]`、`[身份证]`、`[地址]`、`[联系方式]`。

脱敏结果必须保存 `redacted_sha256`，但模型调用日志不保存脱敏全文。原始简历只在 HR 授权详情页按需读取。

### 7.3 证据引用

模型证据必须逐字存在于脱敏文本中，否则程序降级为 `UNKNOWN`。保存：

- `locator`
- `quote`
- `redacted_sha256`
- `resume_artifact_id`
- `source_sha256`

## 8. 模型网关接入

新增并启用三个 `ModelRoute`：

```text
hr_jd_draft
hr_resume_parse
hr_match_summary
```

第一版全部绑定 `qwen-plus`。

实现 `GatewayModelRuntime`，内部调用：

```python
generate_for_use(request.user, purpose_code, messages)
```

### 8.1 JD Generation

- 缺项时不调用模型；
- 输入为结构化 RecruitmentRequest；
- 输出只保存 draft；
- 不得自动确认。

### 8.2 Resume Parse

- 只发送脱敏文本；
- 输出固定字段结构；
- 无法确认时返回 `unknown`；
- quote 必须逐字存在于脱敏文本；
- 不得猜测敏感信息。

### 8.3 Candidate Match

- 输入为 confirmed 且未 stale 的 JD、CandidateProfile 和脱敏文本；
- verdict 只允许 `MATCH / PARTIAL / UNKNOWN / NOT_MATCH`；
- 无有效证据时降级为 `UNKNOWN`；
- 模型不得提供最终分、录用或淘汰建议。

模型调用保留当前网关的权限复验、超时、限流、输出大小和 Token 日志。

## 9. 程序评分与业务红线

保留独立 HR 的现有确定性规则：

- `MATCH = 1`
- `PARTIAL = 0.5`
- `NOT_MATCH = 0`
- `UNKNOWN` 不计入已判断分母
- hard 权重 `1.0`
- bonus 权重 `0.5`
- 硬条件 `NOT_MATCH` 进入 `hard_gap`
- 加分项不得抵消 `hard_gap`
- 保存 `rule_version`

页面统一称“辅助匹配结果”，不使用“录用评分”“淘汰结果”等表达。

## 10. 批量 Worker

筛选不得在 HTTP 请求中同步完成。主平台新增最小 HR Screening Worker：

```text
pending → queued → running → completed / partial_failed / failed
```

必须具备：

- 数据库持久任务；
- lease；
- fence；
- attempt 上限；
- 幂等领取；
- 单份简历失败隔离；
- retry 只处理 failed artifact；
- Worker 重启后恢复；
- 迟到 Worker 写入拒绝；
- 模型调用和文件状态落库。

不建设通用平台任务中心，只实现招聘筛选当前需要的 Worker。

## 11. API 合同

路径固定为：

```text
GET/POST /api/hr/recruitment/requests/
GET/PATCH /api/hr/recruitment/requests/{id}/
POST      /api/hr/recruitment/requests/{id}/generate-jd/
GET/POST  /api/hr/recruitment/requests/{id}/jd-versions/
POST      /api/hr/recruitment/requests/{id}/jd-versions/{jd_id}/confirm/
GET       /api/hr/recruitment/confirmed-jds/
GET/POST  /api/hr/recruitment/batches/
POST      /api/hr/recruitment/batches/{id}/resumes/
POST      /api/hr/recruitment/batches/{id}/run/
POST      /api/hr/recruitment/batches/{id}/retry/
GET       /api/hr/recruitment/batches/{id}/progress/
GET       /api/hr/recruitment/batches/{id}/summary/
GET       /api/hr/recruitment/batches/{id}/export/
GET       /api/hr/recruitment/resumes/{id}/
GET       /api/hr/recruitment/resumes/{id}/download/
GET       /api/hr/jobs-history/
GET       /api/hr/jobs-history/{id}/
```

所有端点使用 DRF Session Authentication、CSRF、HR 权限、对象归属、未知字段拒绝和稳定错误码。

## 12. 前端信息架构

人事导航调整为：

```text
工作概览
招聘需求与 JD
简历筛选
筛选结果
历史 JD
转正工作流
```

### 12.1 招聘需求与 JD

- 结构化需求表单；
- 实时缺项；
- 缺项时生成按钮禁用且后端独立拒绝；
- AI 生成 JD draft；
- HR 编辑新版本；
- HR 确认；
- 版本和 stale 展示。

### 12.2 简历筛选

- 选择当前有效 confirmed JD；
- 批量上传；
- 展示文件类型、大小和处理状态；
- 启动筛选；
- 查看批次进度；
- 重试失败项。

### 12.3 筛选结果

- 脱敏姓名或文件标识；
- 辅助匹配分；
- hard gap 数量；
- UNKNOWN 数量；
- 优势与缺口；
- 排序和 verdict 筛选；
- 证据矩阵；
- CSV 导出。

### 12.4 历史 JD 与转正

- 旧 JD 只读；
- 转正页面与行为不变；
- 管理预览不读取 HR 业务数据。

## 13. 数据迁移与兼容

- 不迁移独立 HR 数据；
- 不转换现有旧 JD 数据；
- 新招聘表从空数据开始；
- 旧 JD 保留只读；
- 转正表和迁移保持不变；
- 新 migration 只创建招聘表及必要索引/约束；
- migration 回退不得删除旧 JD 或转正数据。

## 14. 测试策略

### 14.1 迁入业务合同

将独立 HR 116 项测试表达的合同迁入主平台测试体系，包括：

- Adapter 语义；
- 招聘需求完整性；
- JD 草稿、编辑、确认和 stale；
- 简历上传、hash 和版本；
- 解析 source_ref/UNKNOWN；
- 匹配证据矩阵；
- 程序评分和 hard gap；
- 批次失败隔离、partial_failed、仅失败项 retry；
- 汇总、筛选、排序、详情和 CSV；
- stale 全链。

测试代码应适配主平台身份、存储、审计和模型网关，不机械复制 Fixture Adapter。

### 14.2 新增平台测试

- HR 权限和对象枚举保护；
- CSRF；
- 简历脱敏；
- 原文件/脱敏文本/模型输入边界；
- TXT/DOCX/PDF 提取；
- 损坏、空、超大、重复文件；
- 模型无授权、超时、无效 JSON、伪造 quote；
- Worker lease/fence/retry；
- PostgreSQL 并发；
- 审计失败事务回滚；
- 旧 JD 写操作不再出现在新 UI；
- 转正回归不受影响。

### 14.3 真实依赖验证

使用脱敏样例真实调用 `qwen-plus`：

- JD 生成；
- TXT/DOCX/PDF 简历解析；
- 候选匹配；
- 无证据降 UNKNOWN；
- PII 未进入模型请求；
- 调用日志只含元数据。

### 14.4 Browser E2E

```text
HR 登录
→ 创建招聘需求
→ 补齐缺项
→ AI 生成 JD
→ 编辑并确认
→ 上传多份简历
→ 启动筛选
→ 查看成功、UNKNOWN、hard gap 和失败项
→ 查看证据矩阵
→ 导出 CSV
→ 修改需求
→ 旧 JD 和匹配 stale
→ 新 JD 重新筛选
```

### 14.5 全量回归

HR 接入稳定后，再执行用户要求的平台全量测试：

- Django 全量测试；
- PostgreSQL 并发测试；
- 模型网关测试；
- 前端全部测试、typecheck、build；
- 产品三件套 Browser E2E；
- HR Browser E2E；
- 招投标若届时已接入则一并回归；
- Worker 故障恢复；
- 备份恢复；
- 安全和权限负向测试。

## 15. 实施顺序

1. 数据模型与 migration reconciliation；
2. 主平台身份/权限/审计 Adapter；
3. HR 私有文件存储与 TXT/DOCX/PDF 提取；
4. 简历脱敏器；
5. 三个 HR 模型路由和 Gateway Runtime；
6. 招聘 services / Skills / screening 迁入；
7. HR Screening Worker；
8. DRF API；
9. React 前端；
10. 旧 JD 只读入口；
11. 定向自动化、真实模型和 Browser E2E；
12. 平台全量测试。

## 16. 明确不做

- 独立 HR 服务或独立数据库；
- 人才库；
- 候选人生命周期；
- 自动录用、淘汰或面试建议；
- 面试安排；
- Offer；
- 入职；
- 招聘平台抓取；
- RAGFlow；
- 招投标接入；
- 修改转正状态机；
- 迁移独立 HR 测试数据；
- OCR 和旧 `.doc` 转换。

## 17. 成功标准

- HR 用户可以在主平台完成招聘需求、JD、简历上传、解析、匹配、筛选、证据查看和 CSV 导出；
- 原始简历私有保存，模型只接收脱敏正文；
- 真实 `qwen-plus` 完成 JD/解析/匹配，UNKNOWN 与证据规则不漂移；
- 旧 JD 只读，转正流程无回归；
- Worker 可恢复、可重试且单份失败隔离；
- 独立 HR 数据不迁移；
- HR 定向测试、真实模型验证和 Browser E2E 通过；
- 最终平台全量测试通过后才进入投入使用判断。
