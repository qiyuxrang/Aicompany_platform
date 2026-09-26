# 人事招聘模块接入主平台设计

日期：2026-09-24
状态：已确认
基线：`main@6ca196b`
独立来源：`C:\Users\BJRunner\Desktop\HR@e85612e`
适用范围：HR Recruitment V1 完整招聘链迁入；新增钉钉 360° 转正问卷评估与 AI 证据化汇总

## 1. 目标与已确认口径

将独立 HR Recruitment V1 的完整招聘能力迁入企业 AI 业务协同平台，同时保留主平台现有转正历史数据，并将后续转正办理升级为 360° 问卷评估：

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
    └── 转正工作流
        ├── HR 发起 360° 问卷
        ├── 钉钉推送上级/同级/下级
        ├── H5 问卷填写
        ├── 程序统计与 AI 证据化汇总
        └── HR 最终审核与 XLSX 汇总表
```

已确认规则：

1. 完整招聘模块接入，不只接 JD。
2. 主平台现有 `HrJobTask / HrJobRevision` 保留为只读历史，不迁移到新模型，不再产生新的可编辑数据。
3. 独立 HR 的 fixture、测试用户、测试需求、测试 JD、测试简历、匹配结果和 SQLite 数据均不迁移。
4. 原始简历保存在主平台私有存储；发送给 `qwen-plus` 前必须自动脱敏。
5. 姓名、手机号、电话、邮箱、身份证号、地址、微信号、QQ 等明显个人识别信息不得发送给外部模型。
6. 招聘模块使用主平台身份、权限、审计、文件存储、模型网关和前端壳层，不保留独立登录、导航、模型服务或数据库。
7. 招聘不建设人才库，不做候选人生命周期、面试、Offer、录用、淘汰或入职。
8. 现有转正数据和审计历史保留；原“主管审批→HR归档”流程升级为“360°问卷收集→程序统计→AI辅助汇总→HR最终审核”。
9. 评价关系固定为上级、同级、下级；评价人只提供问卷材料，不作出转正决定。
10. AI 可以对汇总问卷进行证据化答案归纳并辅助 HR 判断，但不能直接输出转正、不转正、淘汰或延长试用期结论。
11. 最终输出以网页结构化表格和 XLSX 汇总表为主，不以长篇 AI 报告为主。
12. 问卷对被评员工匿名；HR 可查看邀请/提交状态用于催办，AI 只看到匿名 response ID。
13. RAGFlow、招投标接入和招投标筛选本轮不实施。

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

### 4.2 转正 360° 问卷评估

保留现有 `ProbationCase / ProbationRevision / ProbationTransition` 的历史数据和审计能力，但新流程不再以单一主管审批作为评估主体。

核心流程：

```text
HR 发起评估并选择员工/问卷模板/评价人
→ 钉钉向上级、同级、下级发送待办或工作通知
→ 评价人在钉钉 H5 页面填写问卷
→ 平台统计完成率、分组均分、综合分和分歧
→ AI 汇总文本答案、优势、问题、分歧和证据不足
→ 生成网页表格与 XLSX 汇总表
→ HR 人工填写最终结论并归档
```

钉钉只承担身份确认、消息/待办、H5 入口和催办；主平台是问卷、回答、汇总、HR 结论和历史记录的数据权威。

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

## 10. 高并发批量筛选 Worker

筛选不得在 HTTP 请求中同步完成。采用“分阶段流水线 + 单份简历独立状态 + 有界并行 + 动态背压”，而不是无限并发。主平台新增最小 HR Screening Worker：

```text
pending → queued → running → completed / partial_failed / failed
```

必须具备：

- 数据库持久任务；
- lease、fence、attempt 上限和幂等领取；
- 单份简历失败隔离，retry 只处理 failed artifact；
- Worker 重启后恢复，迟到 Worker 写入拒绝；
- 模型调用和文件状态落库；
- 本地文件校验、提取、脱敏阶段并发为 `min(CPU核心数, 8)`；
- 模型解析/匹配阶段初始全局并发为 2，与当前模型网关容量一致；
- 每次数据库领取 10～20 份，避免长事务；
- 429/超时触发退避和动态降低并发；
- 缓存键为 `resume_sha256 + jd_version_sha256 + redaction_rule_version + match_rule_version`；
- 同一简历解析结果可复用于不同 JD，JD 变化只重跑匹配；
- 页面进度必须来自已落库的单份状态，不由前端估算。

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

## 12. 前端视觉基准与信息架构

### 12.1 像素级视觉基准

页面严格参考：`C:\Users\BJRunner\Downloads\ChatGPT 图像 2026年9月26日 11_19_02.png`，尺寸 1672×941，SHA-256 `549973f964b4eb08480643479a0dd85352ab67cab182901685ffcecd51931d83`。

转正流程参考：`C:\Users\BJRunner\AppData\Local\Temp\pi-clipboard-2daadd33-de84-443b-9c6a-72f800f3481c.png`，尺寸 1986×382，SHA-256 `20fd3f54b80b64248e95f84c924c2cf941c7c548d0279599c684f3b9be0ad89c`。流程语义为 HR 发起、钉钉推送、员工关系人 H5 填写、系统汇总、AI 辅助、HR 最终审核；视觉流程图本身不直接作为业务页面布局。

必须保持：

- 白色顶部全局导航、当前“人事部门”浅蓝选中态和蓝色底部指示线；
- 深蓝渐变左侧栏、白色线性图标和亮蓝渐变当前菜单；
- 浅灰蓝内容背景、白色卡片、细边框、弱阴影、12～16px 圆角；
- 蓝色主按钮，绿/橙/红/紫状态色；
- 待办卡片、岗位表格、转正统计卡、筛选批次进度表的尺寸关系和信息密度；
- 桌面端 1440px 以上优先严格复刻；窄屏仅允许导航折叠、卡片换行和表格横向滚动。

参考图中的示例公司名称和示例数据不作为真实业务内容；实施时使用平台当前品牌资产和后端真实数据，禁止静态伪造进度。

视觉验收采用 1672×941 固定视口截图进行叠加对照：全局/侧栏布局、卡片分区、表格密度、色彩、圆角和主要间距需保持一致；文字因真实数据长度允许自然变化，不能为了像素对齐截断关键业务信息。

### 12.2 导航

```text
工作台
招聘与 JD
简历筛选
筛选结果
转正工作流
```

旧 JD 只读历史作为“招聘与 JD”内的历史页签，不额外挤占左侧主导航。

### 12.3 工作台

工作台是分块的真实任务总览：

- 待办事项：JD 待确认、筛选完成、简历处理失败、问卷待 HR 审核、钉钉发送失败；
- 招聘中的岗位：招聘人数、JD 状态、简历总数、完成数、异常数、更新时间；
- 转正工作流：问卷待发送、填写中、待 HR 审核、钉钉失败；
- 最近筛选批次：关联 JD、总数、完成、失败、真实进度条、状态和入口。

### 12.4 招聘与 JD

同一招聘需求先生成和确认通用 JD，再由用户选择平台适配版本：通用版、BOSS直聘、智联招聘、前程无忧、猎聘、自定义。平台版只调整文案长度、结构和字段表达，不复制业务事实。

第一版支持生成、预览、复制和导出，不自动登录或发布到招聘网站。

### 12.5 招聘需求与 JD

- 结构化需求表单；
- 实时缺项；
- 缺项时生成按钮禁用且后端独立拒绝；
- AI 生成 JD draft；
- HR 编辑新版本；
- HR 确认；
- 版本和 stale 展示。

### 12.6 简历筛选

- 选择当前有效 confirmed JD；
- 批量上传；
- 展示文件类型、大小和处理状态；
- 启动筛选；
- 查看批次进度；
- 重试失败项。

### 12.7 筛选结果与历史批次

- 脱敏姓名或文件标识；
- 辅助匹配分；
- hard gap 数量；
- UNKNOWN 数量；
- 优势与缺口；
- 排序和 verdict 筛选；
- 证据矩阵；
- CSV 导出。

筛选结果同时是不可覆盖的历史记录。每个批次绑定招聘需求版本、JD 版本、简历 hash、解析版本、匹配规则版本、模型配置版本、发起人、处理时间和导出记录。旧 JD 变化后历史批次标记 stale，但仍可查看。

### 12.8 转正工作流

HR 发起 360° 问卷，选择问卷模板、截止时间及上级/同级/下级评价人；钉钉发送 H5 问卷；页面展示邀请、送达、打开、提交、过期、失败和催办状态；完成后显示结构化汇总表、AI 辅助分析和 HR 最终审核。

管理预览不读取招聘、简历、问卷或转正业务数据。

## 13. 360° 问卷、钉钉与 AI 汇总合同

### 13.1 数据模型

新增：

- `QuestionnaireTemplate`：名称、版本、适用岗位、问题 JSON、维度、关系适用范围、评分规则、权重、最低有效样本和启停状态；
- `ProbationEvaluation`：被评员工、部门、岗位、入职/试用期日期、模板版本、截止时间、状态、发起 HR、最终结论、审核人和审核时间；
- `EvaluationParticipant`：评价人、关系 `superior|peer|subordinate`、钉钉 userId 快照、匿名 response code、邀请状态、提交状态和时间；
- `QuestionnaireResponse`：participant、模板版本、回答、程序维度分、版本、提交时间；
- `EvaluationSummary`：完成统计、分组/综合维度分、分歧、优势、待提升、AI 分析、引用回答版本、规则版本和 stale；
- `DingTalkIdentityMapping`：平台 User ↔ 钉钉 userId，启用状态和更新时间；
- `DingTalkDispatch`：participant、业务幂等 ID、状态、attempt、next_retry_at、error_code、钉钉 task/message ID；
- `HrNotification`：平台内待办/通知，关联招聘、筛选批次或问卷评估。

评价人不能是被评员工本人；同一用户在同一次评估中只能属于一个关系组；至少一名上级；截止时间必须在未来。

### 13.2 问卷与匿名

- 上级、同级、下级可使用关系专属题目，也可共用基础维度；
- 问卷包含 1～5 量表、单选、多选和文本题；
- 必须提供“无法判断”；
- 每人仅能访问自己的邀请，转发链接不能代填；
- 默认每人提交一次；如 HR 允许截止前修改，则追加 response 版本，不覆盖历史；
- 被评员工不能看到单个评价人身份或原始回答；
- HR 可查看邀请与提交状态用于催办，汇总默认按关系组展示；
- AI 只接收匿名 `response_id` 和 relation，不接收评价人姓名/userId。

### 13.3 钉钉可靠发送

钉钉采用企业内部应用的工作通知/待办 + H5 问卷链接，不使用审批实例作为问卷主体。

数据库事务只创建评估、参与人和 `DingTalkDispatch(pending)`；后台 Worker 提交后发送，成功记录钉钉任务 ID。失败进入可重试状态，不回滚已创建评估，不重复创建待办。

配置通过环境变量或密钥中心提供 AppKey/AppSecret/CorpId、回调 Token/AES Key、应用首页和公网 HTTPS 回调地址，禁止进入 Git、前端和业务正文。

H5 使用钉钉免登身份换取用户身份，并与 participant 的钉钉 userId 精确比对。回调须验证签名、AES、时间戳/nonce、CorpId 和事件幂等；事件原文不写普通日志。

### 13.4 程序统计

程序负责：邀请/提交/未提交数、完成率、关系分组均分、维度均分、模板权重综合分、选项次数和组间差值。

权重绑定模板版本，默认示例为上级 40%、同级 30%、下级 30%，但实际取模板配置。缺少某关系组有效样本时不把其记为 0 分，结果标记信息不足并按模板规则决定是否允许完整汇总。

### 13.5 AI 辅助分析

新增模型路由：

```text
hr_probation_summary
```

AI 输入仅包含岗位/部门、程序统计、匿名关系、response_id、question_id 和经过个人信息过滤的回答。AI 输出固定结构：主要优势、待提升事项、多方一致、意见分歧、信息不足、风险提示和建议 HR 核实问题。

每个分析项必须引用真实存在的 `response_id + question_id`；无效引用的分析项丢弃。AI 不计算分数，不修改程序统计，不填 HR 最终结论，不输出建议转正、不转正、延长试用或淘汰。

问卷新增或修改后旧 Summary 标记 stale；重新汇总产生 v2/v3，不覆盖历史。未满足最低样本时只能输出“阶段性汇总”，不得显示完整评估。

### 13.6 表格与 XLSX

网页和 XLSX 至少包含：

1. 员工与评估基本信息；
2. 问卷完成情况（按关系分组）；
3. 分维度评分（上级/同级/下级/综合/有效样本）；
4. 评价分歧；
5. 优势汇总；
6. 待提升事项；
7. AI 辅助分析与证据状态；
8. HR 最终结论、依据、改进计划、采纳 AI 情况、审核人和时间。

XLSX 默认不含评价人真实身份、钉钉 userId、访问令牌、钉钉回调原文、模型提示词或完整模型响应。

## 14. 数据迁移与兼容

- 不迁移独立 HR 数据；
- 不转换现有旧 JD 数据；
- 新招聘表从空数据开始；
- 旧 JD 保留只读；
- 既有 `ProbationCase / Revision / Transition` 表和历史数据不删除；
- 新 migration 创建招聘表、360° 问卷、钉钉 Outbox、汇总与通知表及必要索引/约束；
- 新评估流程不复写旧转正历史；
- migration 回退不得删除旧 JD 或旧转正数据。

## 15. 测试策略

### 15.1 迁入业务合同

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

### 15.2 新增平台测试

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
- 360° 问卷参与人关系、重复人员和本人评价拦截；
- 钉钉发送 Outbox、重试、幂等、免登身份和问卷访问控制；
- 问卷单次提交/截止时间/版本；
- 程序统计、权重、完成率和分歧计算；
- AI response_id/question_id 证据引用校验；
- 匿名展示与 HR 催办身份边界；
- XLSX 导出不泄露评价人身份和钉钉凭据；
- 旧转正历史数据仍可读取。

### 15.3 真实依赖验证

使用脱敏样例真实调用 `qwen-plus`：

- JD 生成；
- TXT/DOCX/PDF 简历解析；
- 候选匹配；
- 无证据降 UNKNOWN；
- PII 未进入模型请求；
- 调用日志只含元数据。

### 15.4 Browser E2E

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
→ HR 发起 360° 转正问卷
→ 上级/同级/下级经钉钉 H5 提交
→ 查看完成率、分组评分和分歧
→ AI 生成证据化答案汇总
→ HR 最终审核并导出 XLSX
```

### 15.5 全量回归

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

## 16. 实施顺序

1. 数据模型与 migration reconciliation；
2. 主平台身份/权限/审计 Adapter；
3. HR 私有文件存储与 TXT/DOCX/PDF 提取；
4. 简历脱敏器；
5. 三个 HR 模型路由和 Gateway Runtime；
6. 招聘 services / Skills / screening 迁入；
7. HR Screening Worker；
8. DRF API；
9. 钉钉身份映射、Outbox、待办/通知和 H5 问卷；
10. 360° 问卷模型、程序统计和 AI 汇总；
11. React 前端严格复刻设计稿；
12. 旧 JD 只读入口；
13. XLSX 汇总导出；
14. 定向自动化、真实模型、钉钉沙箱和 Browser E2E；
15. 平台全量测试。

## 17. 明确不做

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
- 使用钉钉审批实例代替问卷；
- 让 AI 自动决定转正/不转正/延长试用期；
- 向被评员工展示单个评价人的身份；
- 迁移独立 HR 测试数据；
- OCR 和旧 `.doc` 转换。

## 18. 成功标准

- HR 用户可以在主平台完成招聘需求、JD、简历上传、解析、匹配、筛选、证据查看和 CSV 导出；
- 原始简历私有保存，模型只接收脱敏正文；
- 真实 `qwen-plus` 完成 JD/解析/匹配，UNKNOWN 与证据规则不漂移；
- 旧 JD 只读；
- HR 可发起钉钉 360° 问卷，上级/同级/下级完成匿名评价；
- 程序完成确定性统计，AI 完成有 response/question 证据的答案汇总，最终结论由 HR 人工填写；
- 网页表格与 XLSX 汇总可用且不泄露评价人身份；
- Worker 可恢复、可重试且单份失败隔离；
- 独立 HR 数据不迁移；
- HR 定向测试、真实模型验证和 Browser E2E 通过；
- 最终平台全量测试通过后才进入投入使用判断。
