# 管理员运维工作台：范围、数据与接口

本文记录运维界面的历史增量；后续2026-09-21获批经营桥接及恢复补验结果已更新到 `PHASE1_CLOSURE.md`。本文旧的“不实施桥接/恢复待验”不代表最新状态，普通浏览器补验仍未完成。

本轮以新附件为准：仅升级门户运维及前端体验，不实施旧经营桥接、SSO、AI或其他业务功能，不改旧项目，不提交代码。现有未提交的接入审批文档保持原样。

## 信息结构与实施计划

保留员工工作台；管理员默认进入 `/ops`，可切换员工视图。一级菜单严格六项：运维总览、人员与权限、业务与使用分析、模块与接入管理、问题中心、系统维护。管理写操作复用Django Admin，新React只提供账号/授权/模块概览及准确深链接；新增运维动作仅固定目标检查、问题状态/备注。

1. 复核原实现、运行实例、权限与数据来源；冻结最小接口合同。
2. 后端增加受控探测与问题记录，前端实现六页、共享状态组件、原生SVG趋势和详情侧栏。
3. 用独立验收库/账号做真实HTTP、浏览器、权限与统计验证；原有测试回归。
4. 独立审查实际diff，修复复验；在本文补充证据与限制。不把测试夹具当生产经营数据。

## 数据来源及语义

| 展示 | 数据来源 | 口径/刷新/缺失 |
|---|---|---|
| 启用账号 | User.is_active | 当前快照，不是在线人数 |
| 期间登录人数/次数 | AuditEvent(action=login,result=success,actor非空) | 本地日期窗口内去重用户数/成功事件数；活跃明确称“登录活跃” |
| 模块启动 | AuditEvent(action=module_launch,result=success) | 明确启动成功事件，不是业务使用、产出或绩效 |
| 趋势、模块排行 | 上述审计按天/模块聚合 | 1/7/30天，Asia/Shanghai自然日，截止查询时刻；只统计现有留存事件 |
| 应用响应耗时/错误率 | 单应用进程内有界内存采样 | 最近15分钟有效API请求；排除运维/健康/CSRF/静态轮询；非主机/容器全量，重启清空，未采到显示未采集 |
| 数据库健康 | 当前连接SELECT 1 | 当前检查时间，仅连通性，不是完整业务验收 |
| 模块健康/问题 | 管理员显式POST固定模块探测 | 每模块60秒冷却、3秒网络超时、禁重定向；GET页面刷新不探测外部服务 |
| 账号明细/管理动作 | 用户角色、last_login、既有审计 | 不展示密码、会话或绩效 |
| 备份恢复 | 固定位置的既有脱敏演练报告（若结构有效） | 仅历史记录，不因文件存在判当前备份可恢复；缺失/无效明确未接入 |
| 版本/环境 | 已知应用版本、Python/Django/数据库引擎 | 不返回env全集、路径、密码、连接串；部署历史无可靠来源则未接入 |
| CPU/内存/磁盘/业务财务 | 本轮不新增整机采集或旧业务接口 | 未采集/未接入；业务入口仍走本人原有授权 |

平台管理与运维暂都映射现有platform_admin；概念分开，未重建权限模型。所有 `/api/ops/*` 与 `/ops/*` 后端验证管理员和首次改密状态；管理员不自动获得业务内容。员工接口/登录/票据既有语义不变。

## 最小存储与保留

新增两表：ModuleCheck（每模块一条最近检查/冷却状态）与OperationalIssue（每模块可用性问题一个聚合键，首末发生时间/次数/最近证据/处理状态/最多20条脱敏备注）。检查配置变更后旧结果须标过期，不能当当前状态。人工关闭与检查恢复分别保存，人工操作不改探测健康结果。

内存响应采样仅存路由模板、方法、状态分组、次数与耗时聚合，时间窗15分钟且键数有硬上限；不存用户ID、query、请求正文、Cookie或票据。采集异常/锁竞争不得阻断业务。审计加必要查询索引，不改历史记录和审计不可编辑边界。运维问题默认保留30天，清理命令默认预览、显式apply才清理已恢复/人工关闭且久未发生的记录；未解决问题和既有账号权限审计不自动清除。

## 前后端接口合同

统一JSON；401未登录，403非管理员/首次改密限制，400非法筛选，404无对象，429探测冷却（Retry-After），503真实服务错误。POST复用现有CSRF会话认证，拒绝用户自报角色、任意URL或未知操作参数。金额/人事数据不在这些响应内。

### 通用类型

- Range：`{days,start,end,timezone}`，days仅1/7/30，默认7。
- Page：`{items,total,page,page_size,pages}`，page正整数、page_size固定20；未知筛选值返回400。
- AuditRow：`{id,created_at,actor,action,target,result,changes}`；actor是用户名或“系统”，changes仅字段名。输出只允许安全短文本，票据/凭据模式屏蔽。
- ModuleRow：`{id,code,name,description,url,status,enabled,admin_url,check}`；无效/含敏感参数的URL不返回原文，check为空意味着未检查。
- Check：`{state,checked_at,duration_ms,message,next_check_at,stale}`；state为reachable/unavailable/not_configured/disabled/error；message为固定安全说明，不输出原始异常。
- Issue：`{id,module_code,module_name,severity,title,status,health_state,first_seen,last_seen,occurrences,evidence,checked_at,notes,admin_url}`。status=open/investigating/closed/recovered；health_state=unresolved/recovered；closed只表示人工关闭。notes为`{at,actor,text}`数组；evidence为安全固定文本。

### 端点

| 请求 | 响应 |
|---|---|
| GET `/api/ops/overview/?days=7` | `{updated_at,range,health:{state,checked_at,database_ms,message},accounts:{enabled,total},usage:{login_users,login_count,module_launches},performance,issue_count,trend,modules,recent_issues,recent_audit,backup,version,my_modules}` |
| GET `/api/ops/users/?q=&status=all&role=&page=1` | Page加`roles:[{code,name}]`；items为`{id,username,display_name,is_active,must_change_password,roles:[{code,name}],last_login,admin_url,password_url}` |
| GET `/api/ops/users/<id>/` | 用户字段加`recent_audit:AuditRow[]` |
| GET `/api/ops/usage/?days=7&module=` | `{updated_at,range,summary:{enabled_accounts,login_users,login_count,module_launches},trend:[{date,login_users,login_count,module_launches}],ranking:[{code,name,launches}],definitions}`；module筛选只作用于启动数，登录指标始终全平台并明确标注 |
| GET `/api/ops/modules/` | `{updated_at,items:ModuleRow[]}` |
| POST `/api/ops/modules/<code>/check/`，body `{}` | `{module:ModuleRow,issue:Issue或null}`；不接受URL/自定义请求/业务重放 |
| GET `/api/ops/issues/?severity=all&status=all&module=&days=30&page=1` | Page，items为Issue；days针对last_seen |
| GET `/api/ops/issues/<id>/` | Issue |
| POST `/api/ops/issues/<id>/`，body `{status,note}` | Issue；status只能open/investigating/closed；note最多500字符且脱敏，拒绝不合法/空操作；人工关闭不能伪装恢复 |
| GET `/api/ops/maintenance/?days=7&page=1&action=` | `{updated_at,environment,performance,backup,version,deployment_history:{state,message},audit:Page}` |

performance为`{state,scope,window_seconds,collected_since,requests,errors,error_rate,average_ms,max_ms,routes}`，routes只给路由模板聚合，无用户和原URL。零样本state=not_collected，平均/最大值null；真实零错误率为0。backup为`{state,message,checked_at,record}`；record若有仅`{timestamp,table_count,all_tables_equal,source_unchanged,scope_matches}`。version为安全说明字符串。my_modules只返回本人实际授权的`{code,name,status,enabled}`。

## 验收矩阵（实施前定义，未执行不得勾选）

权限：匿名/普通用户API及直达页面拒绝、管理员业务数据仍拒绝、撤权下次请求失效、首次改密/停用/登录回归。统计：登录人数去重、次数、角色多对多不重复计数、日期边界、模块筛选口径、零与未采集区分。检查：固定目标、禁重定向、输入拒绝、冷却/并发、无GET探测、超时、不泄露原异常、配置变化结果过期。问题：聚合次数/首次最近、人工关闭≠恢复、备注脱敏/长度、CSRF、操作审计。维护：报告解析与缺失态、不泄露环境或敏感日志、采集失败隔离、有界内存/保留清理。前端：六页、筛选分页保留、键盘侧栏焦点、确认/防重复、空错态、真实桌面/窄屏截图。原经营只验证原导航不修改旧项目。

## 部署、清理与回退

未新增第三方依赖。迁移 `0003_modulecheck_operationalissue_audit_indexes` 新增两张运维表、问题状态索引和审计查询索引；不改变用户/角色/模块授权语义，不修改任何旧业务库。已有大表添加索引可能持锁，正式上线应由负责人安排维护窗口，不能把隔离小库迁移时间当生产耗时。

新增可选环境配置 `PORTAL_FRONTEND_DIST`，默认仍为 `frontend/dist`；用于本轮独立预览构建目录 `.runtime/ops-frontend-dist`。没有新增外部采集端点、秘密、消息服务或后台任务。部署仍沿用现有 HTTPS、访问限制和秘密管理说明。

本轮隔离库为 `portal_ops_20260921_085811`（同一门户开发PG服务中的独立数据库，非旧业务库），监听 `127.0.0.1:18210`。必须从项目根目录使用正斜杠环境文件参数；旧 `8100` 与经营 `8018` 不应被重启或重配。

```powershell
uv run --env-file .runtime/ops-validation.env python backend/manage.py migrate --noinput
uv run --env-file .runtime/ops-validation.env python backend/manage.py check
$env:PYTHONPATH='backend'
uv run --env-file .runtime/ops-validation.env waitress-serve --listen=127.0.0.1:18210 --threads=4 config.wsgi:application
```

仅首次需要自行创建管理员时，另开终端交互输入密码：

```powershell
uv run --env-file .runtime/ops-validation.env python backend/manage.py bootstrap_admin portal_ops_owner
```

首次改密后重新登录；不存在默认密码。`ops_*` 是本轮临时隔离验收账号，不应作为正式账号交接。

迁移前备份仍按 DEPLOYMENT.md 的 `pg_dump`/隔离 `pg_restore` 流程，但显式选择目标环境和新的数据库名，禁止覆盖原库。新版本回退时先停止仅本轮实例，切回匹配的旧代码与旧前端；可以保留未使用的新增表和索引，不必删表。若确需退迁移，先导出运维记录并取得破坏性操作批准；本轮未执行删除表或覆盖恢复。当前页面历史恢复记录来自上一阶段，不能证明包含新增运维表的当前备份可恢复。

响应采样仅单进程内存、900秒窗口、5秒桶（边界精度约5秒）、最多182桶/128路由方法状态组合，重启清空、锁忙丢样；不是CPU/内存/磁盘监控或分布式完整请求统计。模块状态每模块只保留最新检查；问题按模块可用性聚合，保留最近20条备注；既有审计不在清理范围。

```powershell
uv run --env-file .runtime/ops-validation.env python backend/manage.py cleanup_ops
# 先阅读预览数量；明确接受删除旧已关闭/已恢复运维问题时再执行
uv run --env-file .runtime/ops-validation.env python backend/manage.py cleanup_ops --apply
```

清理不是自动任务。本轮只在测试数据库验证实际删除，预览库未执行清理。日志诊断只提供安全固定说明/路由模板，不提供原始日志下载或任意命令；备注不应填入业务原文或凭据，即使已有模式脱敏也不能保证识别任意未知秘密。

清理要求最后故障时间与对应关闭/恢复时间均早于30天；刚关闭的老问题不会被删。保留期按终态时间计算，追加备注不自动续期。若要长期保留应重新打开，审计仍不删除。

固定探测使用服务器已校验的模块地址；禁代理及重定向，网络在最多两个守护线程中执行，当前请求最多等待3秒，进程内同模块禁止重入；60秒冷却写入数据库。超时后的结果不会迟到覆盖数据库。两个底层DNS/网络调用若永久不返回，将只占满这两个探测槽，后续探测返回503而不无限建线程；需要管理员在维护窗口人工重启本门户实例恢复探测能力。当前保障适用于单进程4线程Waitress；未来多进程/多副本须重新设计共享in-flight租约，不宣称已有分布式探测能力。

## 2026-09-21 实际验收结果

| 检查 | 实际结果与证据 |
|---|---|
| 后端全量PostgreSQL | **119/119通过**，`evidence/ops/backend-tests-final.txt`。含原有认证、Admin、授权、票据并发回归及新增统计、探测、问题、清理、脱敏、采集测试。旧端票据测试仍是平台端协议测试，不是经营联调。 |
| 前端回归 | **43/43通过**，`frontend-tests-final.txt`；类型检查与生产构建通过，见同目录`frontend-typecheck-final.txt`、`frontend-build-final.txt`。无新增npm/Python依赖或锁文件变化。 |
| 真实HTTP与原生Admin表单 | 最终重启新实例后，`http-core.json` **54/54**、`http-recovery.json` **8/8**。真实独立PG：账号创建、多角色调整、管理权限撤销、首次改密、重置、停启、CSRF、审计、问题状态及导航。不是Mock，不等于浏览器表单验收。 |
| 受控故障与恢复 | 仅新验收库将business目标暂设本机闭端口18218，实际网络失败形成聚合问题；人工关闭不变成健康；恢复为8018后真实HEAD检查恢复，原导航仍返回8018。未改旧服务、不把注入故障称为生产事故。 |
| 安全与隔离 | 普通角色403，管理员业务查询403，撤权下次请求失效；运维专属`ops_forbidden`使前端卸载旧数据再刷新身份；CSRF错误不会误作撤权。JSON/YAML、命令行token、Bearer、Basic/Digest及URI凭据均有存储/响应脱敏回归。 |
| 指标增量开销 | `metrics-benchmark.json`：单路由记录p95约3.3µs；满181桶/128键合成最坏配置p95约2.23ms；锁忙丢样p95约1.1µs。仅独立进程基准，不请求生产、不代表全链路或正式压测。 |
| 迁移与清理 | Django check、makemigrations --check通过；新库应用0003成功。cleanup_ops预览为0，未在预览库执行--apply；测试数据库验证默认不删、只删过期终态且保留审计。 |

### 内嵌浏览器实测（不冒称Chrome/Edge或真机）

- 管理员真实登录默认进入运维页，六页均加载真实接口；人员搜索只返回匹配账号，详情显示真实授权/审计及Admin深链接；Escape关闭后焦点回到触发按钮。
- 使用分析1天/模块筛选、模块选项保全、图表鼠标及Enter进入当日详情已复验；没有把启动次数称为业务产出。
- 模块固定检查实际POST成功，未配置模块显示未配置、冷却按钮禁止重复；问题详情真实保存“处理中”备注，人工关闭仍显示未恢复。
- 审计列表真实翻至第2页，并按login筛选，URL保留筛选参数；账号新增深链接进入原生Admin表单。
- 已保存六页桌面截图及人员/问题/使用明细、审计筛选截图。`overview-narrow-final.png`为最终真实窄屏概览；`people-narrow.png`/`people-narrow-full.png`保留真实工具截图。390 CSS像素下人员页根宽度复验为375/390、不再出现此前664像素横向溢出，表格在内部滚动。
- **工具限制**：内嵌浏览器在Windows DPR1.25及多次视口切换后，部分截图出现截幅偏差，截图尺寸不能直接等同于CSS视口；部分后续点击发生CDP超时。未宣称完成所有设备/浏览器视觉验收。首次桌面截图记录当时运行状态，后续修复后的最终窄屏概览另存final文件。
- **未通过/未执行项**：原生Admin浏览器表单提交因该工具发送`Origin:null`而返回CSRF 403，未放宽CSRF；相同业务操作已用真实HTTP原生表单补验。最终“已打开页面再撤管理员角色”的浏览器复验因CDP点击超时未完成；其后端实际撤权与前端读/写fail-closed回归测试通过，不能代替该浏览器复验。临时撤销的隔离账号角色已恢复，随后统一关闭验收账号。

### 独立审查与修复记录

审查读取实际diff而非只采信实现者总结。已修复：运维403旧数据残留、常见秘密格式漏遮盖、网络探测缺少墙钟截止/并发边界、旧备份表集合误标当前范围、刚关闭历史问题提前清理；浏览器发现的SVG链接不响应、模块筛选选项丢失和`.sr-only`逃出滚动容器也已修复。保留早期失败日志`operations-tests.txt`/`backend-tests.txt`作过程记录，最终结果以`*-final.txt`为准。

### 真实数据与剩余边界

本轮预览统计来自隔离账号实际登录、Admin操作、模块启动、探测和问题记录，不是假造经营数据，也不是正式员工使用情况。账号/角色/模块/审计/数据库连通性/局部API耗时已接通；CPU、主机内存、磁盘、部署历史、跨模块依赖链、当前自动备份状态均未采集/未接入。备份页只读既有历史演练且当前范围不匹配；**新增两表的实际备份恢复本轮未执行**，迁移上线前仍需负责人按恢复流程补验。

运维工作台实现可运行，以上工具受阻项不算通过；原Phase 1整体仍为“部分完成”。可信身份和只读经营数据未完成真实旧端联调，浏览器SSO未实现且本轮不做。本阶段验证平台底座，**不代表AI文档链路已验证**。未修改旧项目、未新增业务模块、未提交/合并/推送；原有接入审批文档继续保留，不因本轮运维升级自动批准旧端变更。

收尾核验见`evidence/ops/final-runtime.json`：7个实际创建的隔离测试账号均停用且密码不可用，经营目标恢复8018并实际可达，8100/18210/8018均HTTP 200；原前端index与基线SHA256一致。Git HEAD仍为既有`8f6db91254e060118a9b51212df72297f373bf52`、暂存区为空。本期已知随机秘密扫描无匹配（`evidence/delivery-secret-check.json`），不宣称替代未知密钥检测。预览服务保留运行；创建自己的管理员后方可使用，临时测试凭据不作为交付密码。
