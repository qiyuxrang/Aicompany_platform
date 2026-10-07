# Agent 平台任务级执行状态

批准日期：2026-09-30；最近恢复执行：2026-10-07。此文件是本任务唯一执行状态；`docs/SPEC.md` 保存稳定规则，三份方案保存获批设计与实施契约。

## 整稿批准与冻结登记

- 用户于 2026-09-30 本轮明确批准 SSD、详细设计、实施计划整稿及 A0–A5 开发；按依赖连续实施，不逐批确认。
- 批准前基线：`main / 7600718`；既有未跟踪内容仅 `deliverables/企业平台云服务器选型汇报_20260929.docx`、同名 PDF 和 `项目规划/`。2026-09-30 初始执行不提交、不迁移生产数据、不重启或部署；Git 上传授权已由 2026-10-07 最新指令变更，见 E31。
- 批准前 SHA256：SSD `9A2526A23AA230D5A6B45B021DA3C6DF43EA16C071DB65C9D2D82103918DF4A7`；详细设计 `656B4A2F7AF00AE561C2DE729F27BFA02A34C382906EC4A1752F60027CF2F899`；实施计划 `4B1A41C79DF6B8BA75A297E2491D3181EE23D2ADB2103DB50D81D0D04C519CD6`。三稿批准后的状态文字变更不改变获批业务边界；稳定规则同步唯一 `docs/SPEC.md`。
- 冻结范围：三稿中已确认业务规则、排除范围、A0–A5 任务和 AG-01–AG-16 验收条件。Q-FIN-01 已关闭：同部门财务员工不能修改他人填写的记录。
- 授权边界：批准范围内开发、定向验证、直接相关修复可自主继续；新增费用或明显成本变化、替换 Harness、扩大业务或资料外发、破坏性处理、旧失效 HR 资料恢复、生产迁移／部署及新分支操作仍须另行授权。用户于 2026-10-07 改为先提交并推送当前全部代码至指定 GitHub 仓库；先前完成后上传条件已被覆盖，凭据及私有运行资料排除、生产部署边界不变，见 E31。

## 唯一执行看板

### 本轮恢复执行核对（2026-09-30）

- 本次再次收到同范围整稿批准并开始连续实施；实查 HEAD 为 main / 7600718。工作区已有 18 个修改文件（HR 后端/页面与网关协议）以及 model_gateway/agent_protocol.py、规划目录和既有 DOCX/PDF 未跟踪文件，全部保留；不假定这些既有改动已经通过测试。
- 本轮读取时批准快照 SHA256：SSD BD59B561BCC2838F3C604726FEC52393198EAD36A0DD890CD94F6A16B6FA3E48；详细设计 8BE754D2B4B0FEF894044747747A4572C7527423FDC20C00333100A8A3A6FBA0；实施计划 4B1A41C79DF6B8BA75A297E2491D3181EE23D2ADB2103DB50D81D0D04C519CD6。随后审批状态文字修正不改变该快照冻结的业务边界。
- 稳定规则已同步 docs/SPEC.md 第 9 节；主代理负责网关、集成和验收，子任务写集拆为 A0 Runtime、A1 身份历史、A2 产品接缝、A3 前端、A4 财务、A5 HR，不争用生产服务或共享源文件。
- 未执行生产迁移、正式部署、Git 提交/推送/分支操作；未重启现有服务。真实模型与试点资料验证仍等待 AG-15/16 模拟安全出口证据。
- 后续用户授权（2026-09-30）：全量执行完成后自动提交并推送至 https://github.com/qiyuxrang/Aicompany_platform。该授权为完成后的条件授权，不提前推送部分实现、不包含无关既有交付物/凭据或生产部署；仍有未执行验收或阻塞时先如实记录，不冒充全量完成触发上传。

| 批次 | 任务级状态 | 待实际证据 |
| --- | --- | --- |
| A0 基线与适配 | 定向模拟及 Portal PG 通过／原生 PG Runtime 许可阻塞 | 完整协议8/8、网关62/62；预发布原生HTTP异步/隔离/周期落盘强杀恢复通过但不证明立即持久化。主代理最终Portal PG 19模块145run/144通过/1PPT跳过、exit0，严格并发及未知预留恢复通过；官方原生PG/Redis Runtime实际exit3/BLOCKED_LICENSE，不混同两者 |
| A1 身份与归档 | 实现及后端/Portal PG 回归通过／综合出口未完成 | 正常配置完整后端Ran1083、1064通过/19跳过、exit0；后续行锁接缝修复正常配置定向16/16，最新源码Portal PG 144通过/1跳过。身份、项目、历史、要求、附件、撤权与摘要已接线；正式Runtime恢复仍待验 |
| A2 产品自主执行 | 原 Worker 接入与质量守卫已实现／正式交付未验 | 精确 owner 蓝图确认后自动三件套；旧要求/迟到/撤权拒绝、重复填充和无依据结论负例复验。获准真实多模型及当前试点 ≥50000/≥70000 正式成果尚未执行 |
| A3 页面与技能 | 前端整套与隔离真实页面通过／正式运行闭环待验 | 主代理 41 文件 469/469、tsc/build 通过；真实 API 页面明确 runtime_unconfigured 未执行，财务刷新授权接缝定向复验。原生共享技能与存储有模拟证据，但不能以页面通过替代 AG-15/16 |
| A4 财务与管理 | 权限/Portal PG/合成自发布闭环通过／全项未验 | 本人草稿→精确本人确认→发布版本2→Work完成→原页面历史查看；GM精确摘要19/19、财务刷新/完成/身份24/24，最新普通员工限制修改及财务PG回归通过。未操作真实财务、不新增财务账本，正式Runtime全链路仍待验 |
| A5 HR 与质量 | 长期归档及根 Worker 守卫定向通过／正式质量未验 | active 长期可读，显式 legacy_expired 保持失效、清理不恢复；HR 所有当前批次终态与 typed 来源接线，物理调用计量。Word/PPT 结构/质量负例21/21，不冒充真实正文、Office 渲染或人工内容核对 |

“实施中”表示整稿授权后的任务级推进，不表示各批技术出口已通过。各批实际开始、失败、阻塞、证据位置与结论只在此文件更新。

| 验收范围 | 当前结果 | 门禁 |
| --- | --- | --- |
| AG-01 自然语言／项目 | API/归档定向通过；真实模型待验 | test_agent_api、test_agent_history；实际页面普通问答在无 Runtime 时正确归档为未执行，不能声称已完成问答执行 |
| AG-02 动态行为 | 原生模拟有证据；真实动态行为待验 | native-evidence.json、test_agent_harness、领域工具；不是固定路由执行，也不以模拟行为代替真实业务 |
| AG-03 多模型并行 | 模拟并行通过；实际不同模型未执行 | 原生两个异步子任务有重叠且主任务同期工作；AG-15/16 门禁未齐，不外发真实资料 |
| AG-04 产品单用户 | 精确蓝图及自动派工定向通过；正式三件套未验 | test_agent_product、product_worker 旧接口回归；未使用第二审核人/成稿二次审批，也未用短原型代替正式交付 |
| AG-05 持续指导 | 要求版本/来源撤权定向通过；正式运行待验 | test_agent_execution、test_agent_read_sources、test_agent_hr_guards；更正先 received 再 applied，同根累计不重置 |
| AG-06 页面断开 | 前端补读及原生 HTTP 模拟通过；正式部署待验 | AgentWorkspace.test、native-evidence/restart-evidence；关闭页面不发取消，不新增重复 run |
| AG-07 故障幂等 | 启动未知/通知查重通过；持久化出口未齐 | test_agent_runtime、abrupt-restart-evidence；周期落盘后强杀恢复不是即时同步持久化，也不代替原生 PG 演练 |
| AG-08 取消撤权 | 跨接缝定向通过；正式恢复待验 | test_agent_termination、test_agent_source_permissions、test_agent_read_sources、HR/product guards；迟到不提交、旧来源新版本不能洗白 |
| AG-09 技能隔离 | 本人隔离/原生工具模拟通过；正式 Runtime 待验 | test_agent_isolation、native-evidence；共享技能只读、固定版本、不扩大业务授权 |
| AG-10 工作归档 | 财务/HR/来源/旧 GM 工作定向通过 | test_agent_finance_completion、test_agent_hr_completion、test_agent_history、test_agent_scope_review；成果不按问答/附件/retry 重复计算，正式交付归档未验 |
| AG-11 财务编辑与自发布 | 业务权限定向与隔离合成闭环通过 | 本人不可改他人，save/import/delete/伪造身份及旧 can_publish 均有负例；本人版本2实际发布/查看，未操作真实财务 |
| AG-12 GM 只读 | typed 原件及摘要一致性定向通过 | test_agent_management 与 scope_review 共19/19；不读私有会话/记忆/native 后端，不编辑/代发布、不提权，正式跨部门成果待验 |
| AG-13 用量真实 | 物理调用/未知占位/去重定向通过 | test_agent_model、test_agent_runtime、HR guards；unknown 保持未知，不补零；未生成真实获准模型 token 证据 |
| AG-14 质量原流程 | 负例/结构验证通过；正式质量未执行 | 文档结构21/21、前端469/469；真实技术方案≥50000、可研≥70000有效非空白正文及 PPT/Office 渲染、来源/版本/人工核对均未验 |
| AG-15 全工具／存储 | 部分原生模拟与 Portal PG 证据；全出口未通过 | 自动工具/Store/checkpoint/内部后端越界模拟及即时撤权有证据；原生 PG Runtime 许可阻塞，真实模型和资料门禁保持关闭 |
| AG-16 累计终止 | 同根/派工/领域累计及严格 PG 并发通过；全出口未通过 | 主代理真实Portal PostgreSQL并发恰好2次成功admission、未知预留恢复保持累积、无OperationalError吞没，19模块最终144通过/1跳过；仍不替代原生许可与持久化恢复出口，不以业务授权/网关限频/单文档上限冒充Harness根保护 |
| 生产迁移与部署 | 未授权、未执行 | 许可、出网、费用、备份恢复、维护窗口及发布另行批准 |

## 实际验证记录（追加证据，不换算总完成率）

- E01 主代理：工具协议实际 loopback provider 往返 8/8，通过。命令 .venv/Scripts/python.exe -m unittest discover -s tests -p test_agent_gateway_protocol.py -v；日志 .runtime/agent-platform-evidence/gateway-protocol-tests.log。含多 ID、空文本 tool_calls、SSE 重组、未知 usage、越权工具、旧文本/图片协议拒绝。
- E02 主代理：既有网关 34/34，通过。隔离 Django 测试数据库；日志 .runtime/agent-platform-evidence/legacy-gateway-baseline.log。首次因并行迁移分叉停止，已串联 0030→0031→0032→0033 后复验通过；生产库未迁移。
- E03 主代理：Runtime 启动薄接缝和工作摘要 14/14，通过。命令 qa/agent_platform/.venv/Scripts/python.exe backend/manage.py test portal.tests.test_agent_execution portal.tests.test_work_summary --noinput；日志 .runtime/agent-platform-evidence/execution-summary-tests.log。仅模拟客户端，不代表实际原生服务通过。
- E04 子任务：正式质量负例 7 项实跑，4通过、2失败、1跳过；失败为重复填充被接受及缺投资依据仍保留经济数字；PPT因该测试 Python 缺 python-pptx 跳过。原始记录 qa/agent_platform/formal_quality_evidence.json；修复和复验另追加，不将本轮标 AG-14 通过。
- E05 主代理：HR/财务预生产业务链、财务 Agent 和 HR 长期归档相关 14/14，通过；日志 .runtime/agent-platform-evidence/business-integration-tests.log。90 天 active 资料仍可读，显式 legacy_expired 继续拒绝且清理不恢复；隔离测试，生产未迁移。
- E06 主代理：GM 类型白名单原文/下载 16/16，通过；日志 .runtime/agent-platform-evidence/management-reference-tests.log。真实路由已接 portal.agent_management；含非GM/管理员/撤权/旧版本/私有对象/文件完整性/未发布财务负例。
- E07 主代理：前端整套 40 文件、459/459，通过，类型检查与生产 build 通过；日志 .runtime/agent-platform-evidence/frontend-full-regression.log 和 frontend-final-build.log。新增更正先 defer_dispatch、再精确要求更新；包含旧 HR/产品/台账页面回归。此前小套一项参数断言失败与修正有日志，不当成业务后台验收。
- E08 主代理：财务权限/导入/项目/Agent 定向 24/24，通过；日志 .runtime/agent-platform-evidence/finance-permission-retest.log。首次 qa 隔离 Python 缺已有 xlrd 造成 10 个导入错误，finance-permission-tests.log 保留；复用现有完整 .venv 复验，不改授权规避错误。
- E09 主代理：正式质量负例复验 6通过/1PPT缺包跳过；日志 .runtime/agent-platform-evidence/formal-quality-retest.log。另用已有 product-documents Python 3.12/python-pptx 1.0.2 实测损坏 PPT 被拒绝，通过（ppt-negative-test.log）；不是正式 PPT/Office 渲染验收，未将原 2失败 JSON 改写为通过。
- E10 子任务：原生 Runtime 同部署真实 HTTP 模拟异步、两子任务重叠、主独立工作、通知去重、工具/Store/内部状态隔离、优雅重启记录 qa/agent_platform/native-evidence.json、restart-evidence.json；强杀检查点丢失仍是失败，见 a0-verification.md。来源即时撤权与 HR Worker 物理计量正在补齐，真实模型/资料未执行。
- E11 主代理：身份/启动/取消/迁移复验 23通过、1错误，日志 identity-execution-migration-tests.log。错误为迁移演练在0033中途使用当前已新增0034字段的 live ORM，已派原演练任务改用 historical apps 并扩展最新链；不标迁移完成。
- 用户取消状态图后已停止本任务独占的127.0.0.1:20730只读服务；不再制作进度图，不影响原业务服务。当前批次完整验收仍 0/6；仅按真实出口改变，不制造百分比或 ETA。

## 本轮持续整合（2026-09-30）

- E12 主代理：迁移、完整 HR guard/Worker/API/长期归档相关30/30，exit0；main-migration-hr-regression.log。0034已纳入历史ORM中间态及最新leaf恢复；原0033 live ORM失败保留，生产未迁移。
- E13 主代理：GM员工/实际用量面板已接入口，前端41文件466/466、exit0，类型检查/build通过；main-frontend-management-regression.log、main-frontend-management-build.log。预览不读取，普通员工不调用账号管理。
- E14 主代理：首次Agent综合71项断言通过但exit1，Windows销毁测试库WinError32，原main-integrated-agent-regression.log保留。子任务修复线程ORM连接释放后71/71 exit0；全部新接缝合并后主代理仍需再跑最终回归。
- E15 子任务：当前文档Python3.12/Django5.2.17/docx1.2.0/pptx1.0.2结构/质量负例21/21无跳过；历史09:33字体/中文失败缺完整traceback、当前不可复现，不能声称根因已修复；正式长篇三件套/Office人工内容验收未执行。
- E16 主代理：独占.runtime/main-acceptance-venv以uv sync --frozen --group dev --group agent-runtime安装101锁定依赖exit0；现有服务Python不动。当前锁API0.17.0.dev3/inmem0.37.0.dev3是预发布，稳定版强杀失败保留，不声明生产可用。
- 正在整合：HR/GM/存量产品/财务已读来源登记和即时复验、HR终态工作归档、本人精确财务发布归档、同根新工作后旧GM原件可读、用量按原会话部门，以及根终止同步工作状态。末尾综合验收不得用子代理自报替代。
- 财务完成回传首次13项中3项因使用不存在的Work.finished_at字段失败；main-finance-completion-tests.log保留。已复用终态事件实际完成时间，不新增重复字段/迁移，复验进行中。
- 原生PG/Redis许可预检实际exit3/BLOCKED_LICENSE；native-pg-preflight-evidence.json，独占容器/内部网络已清理，生产未连接。当前未发现三个Runtime许可key环境变量；另作获准的独占Portal PostgreSQL锁/迁移验证，不冒充原生Runtime持久化。
- AG-15/16全出口未通过，真实模型/当前试点资料及正式质量保持关闭；不新增付费服务、替换Harness或扩大资料外发/生产部署。
- 用户再次确认最终上传qiyuxrang/Aicompany_platform；全量实际验收完成后提交并推送当前获准代码。当前无新分支、未提交/推送；不把局部通过冒充全量完成。

## 持续执行与独立复验（2026-10-07）

- 批准及业务冻结仍为2026-09-30，本日是恢复实施和复验日期，不重开业务决定。重新核对全局AGENTS.md、三稿、主规范、Git、当前锁和环境；main/7600718及已有未提交/未跟踪内容保留。唯一任务级记录仍是本文件，无状态图或重复进度文档。
- E17 主代理第一次完整后端回归Found1078/Ran1077，3失败、1错误、18跳过，exit1；main-full-backend-20261007.log保留。失败来自旧15天自动到期测试，错误来自周期重复正文计数夹具。修正为显式legacy_expired及active长期归档正例，长度边界采用明确标注的非周期合成测试文本，并保留重复填充拒绝负例；不回滚长期归档、不放宽正式质量。
- E18 独立只读审查新增GM原文reference.digest精确匹配负例，首次确实失败；主代理在所有业务版本原文共享入口核对reference.digest与已发布revision.checksum，且继续验证原始records/state内容hash。首次96项中旧正例摘要夹具1失败日志保留，修正正例为真实checksum后main-management-integrity-final-20261007.log实测19/19、exit0。见qa/agent_platform/scope-review-evidence-20261007.md及test_agent_scope_review.py，不以子代理自报替代主复验。
- E19 主代理frontend41文件469/469、exit0，tsc/vite build exit0；main-frontend-final-20261007.log和main-frontend-final-build-20261007.log。消息接收不再误报执行，runtime_unconfigured/unavailable/blocked/unknown/submitted/pending各有真实提示；收到不等于applied/完成。本人财务business_revision沿用现有精确版本原文API，不新增台账。
- E20 主代理网关全部62/62、Agent完整工具协议8/8、现有文档环境结构/质量负例21/21无跳过，全部exit0；main-gateway-regression-20261007.log、main-agent-protocol-20261007.log、main-word-structure-20261007.log。makemigrations portal --check --dry-run无变更、exit0；main-migration-drift-20261007.log。结构验证不等于正式50k/70k有效正文、事实来源、PPT/Word渲染及人工内容核对。
- E21 原生SDK/API预发布模拟HTTP异步、两子并行、默认工具/存储、周期落盘后强杀恢复实测exit0；main-native-crash-retest-20261007.log及qa/agent_platform/native/restart/abrupt-restart-evidence.json。强杀前等待12秒而native定时刷新10秒，不能声称零丢失或即时同步持久化。原稳定版检查点为空失败证据和本日之前快照保留。官方原生PG/Redis Runtime实际exit3/BLOCKED_LICENSE；main-native-pg-preflight-20261007.log及native-pg-preflight-evidence.json。预检包装命令exit0仅表示记录写出，绝不是Runtime PASS。
- E22 独立worker正常config.settings + PostgreSQL16.15实测0030/0034迁移及7模块55/55、exit0，严格根并发恰好2次成功、未知占位恢复累积不重置，自己的runner/PG/volume/internal network均核验清理。主代理实际看到了完整测量JSON，随后误用旧脚本--help意外启动第二次验证，Linux wheel准备失败且覆盖默认文件；现默认portal-pg-evidence-20261007.json为FAIL/0 tests，不标成PASS。已将原先真实测量快照从主代理已读内存另存portal-pg-worker-observed-20261007.json；脚本增加argparse、独占--evidence且拒绝覆盖后主代理另做独立复验。仅Portal业务PG，不替代原生许可/检查点出口。
- E23 隔离SQLite正常0001→0034、真实前端/API、合成财务账号无can_publish，实测草稿1→本人核对精确hash→发布版本2→Work完成→现有台账页面打开历史版本2。精确原文API实用/api/login会话返回200，id/revision/records/checksum完全匹配数据库；main-finance-exact-view-authenticated-20261007.log。先前遗漏PORTAL_DEBUG导致配置拒绝及仅force_login导致401的验证错误日志保留，不冒充通过。API JSON直接新标签导航被浏览器客户端阻止，未绕过；改在既有业务页面使用其正常历史版本查看，页面看到已发布2及真实合成记录。
- E23页面证据：ui-finance-waiting-20261007.png、ui-finance-exact-preview-20261007.png、ui-finance-completed-20261007.png、ui-finance-published-original-20261007.png及ui-runtime-unconfigured-20261007.png均在.runtime/agent-platform-evidence。刷新财务助手曾404：后端finance别名须复用business模块，且必须财务部门/finance grant并排除GM；修复公共frontend入口，原模块/试用限制不绕过，main-finance-refresh-tests-20261007.log实际24/24、exit0。只重启自己的18812隔离QA服务以加载修复，结束核对PID1220后停止、监听0；未重启承载用户任务的服务。
- E24 主代理第二次完整后端Found1082/Ran1081，1062通过、1失败、18跳过，770.377秒、exit1；main-full-backend-final-20261007.log完整保留。唯一失败为SQLite并发测试吞掉OperationalError后所有admission均未成功；不能把0次成功当隔离通过。测试改为仅有真实select_for_update行锁时运行、不吞数据库异常、严格要求2次成功；SQLite跳过此生产行锁证据，必须由实际PostgreSQL独立复验补齐。该轮全量收集早于新增两项finance刷新用例，后续24项定向复验覆盖它们，不冒充同一轮全量已通过。
- E25 主代理第三轮正常config.settings完整后端Found1084/Ran1083，1064通过、19跳过，810.903秒、exit0；main-full-backend-current-20261007.log。保持正常密码哈希器、实际全部迁移和原URL，不使用空URLConf、跳过迁移或MD5替代完整回归。另一次重启复验因漏tests/ PYTHONPATH出现收集错误，核对本任务独占进程父子关系后停止并保留main-full-backend-rowlockscope-20261007.log；不是通过证据。19跳过包括真实PG并发、文档环境PPT/lxml、Windows符号链接及未配置工程CLI，文档21项独立结构验证不能转写为全量19项通过。
- E26 主代理独立Portal PG扩展all两次在质量验证器导入时失败/0tests，main-portal-pg-all-20261007.json与main-portal-pg-all-retest-20261007.json保留；脚本补白名单QA文件复制及容器内容checksum，并用check=False保留真实测试失败计数。随后main-portal-pg-current-20261007.json实际144run、122通过、5失败、16错误、1跳过、exit1，严格根保护两项及原并发恰好两次admission真实通过，但不标整套PASS，独占资源cleanup=true。失败分为隔离环境未复制model_gateway/受信规则与模板、未设HTTP QA导致301，以及PG才暴露的FOR UPDATE DISTINCT查询和直接测试调用缺原生产事务边界；正继续各自修复及重验。
- E27 实际PG受限员工修改入口不能锁带DISTINCT的查询；主代理在共享ordinary_users移除冗余DISTINCT，M2M排除保持NOT EXISTS及原权限过滤，新增普通双角色员工唯一列表/合法修改/不提权正例。所有生产product_worker._guard调用逐一确认已有atomic；仅其直接调用测试夹具加实际事务，不放宽生产锁或拒绝结果。主代理正常SQLite配置test_agent_identity与test_agent_product16/16、exit0；main-pg-portability-targeted-20261007.log。这3文件改动晚于E25全套收集，E25仍是其当时快照的通过，当前代码另外做定向和最终PG复验，不冒充同次全套已涵盖新正例。
- E28 独立只读上传审查报告qa/agent_platform/upload-readiness-review-20261007.md，源码/锁/空凭据样例未发现高置信度真实密钥；所有.runtime/.env/原始QA运行JSON及无关DOCX/PDF默认仅本机保留，未来必须显式白名单暂存，禁止git add -A。另发现HEAD及本地origin/main已有未改动招标demo压缩样本含个人信息样式字段，属于既有风险而非本轮新增；未联网断言当前远端状态、未删除/改写历史/重新外发，不将其新纳入本轮上传。若需要处理既有公开样本须另定受控脱敏/移除范围，不能未经授权做破坏性清理。
- E29 隔离PG环境最小补齐14个明确白名单支持文件、正常HTTP QA/PYTHONPATH，受信模板manifest和322份已复制内容SHA均核对；不复制真实资料、private/.runtime/.env，不新增依赖/仓库挂载/宿主端口，内部网络只有独占PG和runner。独立worker修复后all实测145run/144通过/1PPT缺包跳过、154.401秒、exit0，verify_portal_pg-all-worker-20261007-92b8a02bb5a64166998870b88d7d5dc4.json；主代理随后用新UUID全新DB再次all实测同样145run/144通过/1跳过、154.694秒、exit0，main-portal-pg-verified-20261007.json及.runtime/agent-platform-evidence/main-portal-pg-verified-20261007.log。0030→0034正常config.settings实际PG迁移、两条严格根保护及三份最新修复源/用例都通过；源码全部SHA再比当前文件一致。Django临时测试库、独占容器/卷/网络清理核验通过，当前本任务PG容器0。旧55项观察PASS、0tests FAIL、144项FAIL均保留、不覆盖。
- E30 当前余下依赖项真实受阻：原生PG Runtime许可仍BLOCKED_LICENSE，不是Portal PG失败。本机Process/User/Machine以及已有.runtime/local.env均没有三种Runtime许可变量；检查只输出存在性，不读取/打印任何凭据值到报告。AG-15/16完整原生持久化出口、实际不同模型并行、当前试点正式50k/70k有效正文、Word/PPT渲染及人工内容核对未执行；默认Agent及真实资料/模型门禁不自动打开。截至本条复验时Git仍main/7600718、暂存空、无新提交/分支/推送；之后用户改为先上传，见 E31；生产未迁移/部署，原有用户业务服务未重启。需本机提供已有获准Runtime开发密钥或有效enterprise/offline许可及必要验证网络，不能以假key/绕过许可/替换Harness/新购服务暗中解决。

- E31 2026-10-07 用户明确指令“先上传到github仓库，全部上船”：按“全部上传”执行当前全部源码、测试、迁移、依赖锁、配置样例、规划及非敏感项目交付物；覆盖先前“全量验收完成后上传”的时序条件，但不表示验收通过或上线获准。实查 gh 当前账户 qiyuxrang、目标公开仓库 qiyuxrang/Aicompany_platform、默认分支 main；git ls-remote 验证远端 main 与本地 HEAD 均为 7600718df4f37c189a3698a634f5d9784da09893。使用现有 main 普通提交/推送，不新建分支、不强推。
- E31 上传边界：所有现有代码及空凭据配置样例纳入显式暂存白名单，既有服务器选型 DOCX/PDF 已核对无凭据和个人信息样式，按本次全部上传授权纳入。真实 .env/.runtime、数据库、缓存、备份及原始 QA JSON 保留本机且不暂存；QA 源码、可复验入口和本文件真实结果摘要上传。公开文档本机绝对路径改为相对路径。既有未修改招标 demo 与远端基线相同，不新增其内容、不删除或重写历史；其既有个人信息样式风险仍记录于上传审查，不冒充已脱敏。实际上传结果另记 E32；当前许可阻塞、AG-15/16 未完成和正式质量未执行保持不变。

### 依赖、配置、迁移及回滚边界

- 主代理验收环境为独占.runtime/main-acceptance-venv，uv sync --frozen --group dev --group agent-runtime锁定101个依赖；DeepAgents0.7.19、langgraph-sdk0.4.5、LangGraph1.2.12、Django5.2.17、psycopg3.3.6。原生开发Runtime API0.17.0.dev3/inmem0.37.0.dev3为预发布，仅有受限模拟证据；官方PG Runtime镜像0.15.1-py3.13实际许可预检被阻塞，不混称同一生产栈已兼容。现有.venv不执行同步升级。
- Agent默认关闭：.env.example提供PORTAL_AGENT_ENABLED、Runtime URL/允许列表/服务token、graph、模型preset及有限Root policy；服务token/模型key不得进入源码、证据、日志或Git。真实模型/资料仍关闭，页面清楚展示未执行。运行许可必须由本机已获授权的LANGSMITH_API_KEY或有效enterprise/offline entitlement及其必需验证网络提供；当前Process/User/Machine未发现三种许可变量。不新购服务、不创建账户、不拿其他模型key代用、不绕过许可出口。
- 0030平台、0031 HR长期归档分类、0032财务作者身份、0033产品、0034HR根守卫只在隔离SQLite及独占Portal PostgreSQL演练。0031不恢复已失效/删除资料；回滚不能重启旧15天自动清理或抹去作者、精确发布版本及root计量。生产迁移/正式部署未获额外授权、未执行。
- 既有迁移演练验证历史ORM中间态与0034最新leaf，隔离备份/恢复与授权失效检查不是生产PG+对象存储一致恢复验收。正式上线需共同备份数据库/对象存储、保留凭据和批准窗口；失败时先停新Agent派发，保持旧业务/旧未关联任务可用，再按批准快照恢复，不擅自drop/downgrade生产。
- 本日为运行获准的隔离容器测试启动先前未运行的Docker Desktop；daemon同时自动恢复机器已有其他项目容器。未主动重启/停止/连接其业务资源，所有本任务测试使用新UUID、internal-only网络、合成凭据、无宿主端口/仓库挂载/真实资料；不停止当前已服务其他项目的daemon。
- 未解决且依赖受阻：原生PG Runtime许可/恢复出口、AG-15/16完整模拟门禁、获准实际多模型与当前试点真实正式成果质量、正式Word/PPT渲染/人工核对、生产发布。GitHub 上传按 E31 最新授权执行，不再依赖全量验收完成。继续可独立任务与直接修复；不以设计冻结、局部测试或合成UI完成标记全量完成。
