# 第一阶段实际验收报告

日期：2026-09-20。**整体：部分完成。** 本报告只对应本工作区 V0.1.0 实际产物，不把代码存在、合成测试或导航跳转当作真实经营身份/数据接入。正式互联网生产部署未验收；本阶段验证平台底座，不代表 AI 文档链路已经验证。

## 三组验收与 SSO

| 分组 | 结论 | 已完成 | 未完成/边界 |
|---|---|---|---|
| 门户与权限 | 本地/隔离验证通过 | 个人账号、改密/重置/停用、多角色模块授权、后台限制、审计、真实HTTP及PG测试 | 正式域名TLS、公司试用人员/管理SOP、普通外部浏览器后台写操作完整流程未验收 |
| 经营导航 | 真实导航通过 | 从授权工作台启动原8018网页，到达原生登录页；门户停机原入口和健康接口仍200 | 未登录旧账号，不等于旧业务资料权限或SSO验证 |
| 可信身份与只读数据 | **未完成，受批准阻塞** | 门户端映射、30秒一次票据、原子兑换、撤权epoch、摘要契约/错误防护；合成协议及PG并发测试通过 | 旧端适配未获批准、未实现；两类真实旧用户项目/金额范围对照、回调并发等未做 |
| 浏览器 SSO（单列） | **未实现、未验证** | 界面与文档明确保留旧登录 | 可信机器只读调用即使将来通过，也不等于浏览器SSO |

部门组织树与部门级数据授权不在本期范围，未补建、不列作已实现。管理员默认无业务资料权限；可显式调整角色授权且审计，不具有双人审批保证。

## 可复现命令和最终结果

命令均在 `C:\Users\BJRunner\Desktop\ai智能体平台` 执行。依赖、测试输入与运行库均与旧业务隔离。测试数据库允许创建/删除，不能把以下命令直接指向日常业务库。

| 检查/命令 | 实际结果 | 证据（相对docs/） |
|---|---|---|
| `uv sync --frozen`、`corepack pnpm install --frozen-lockfile` | 成功；镜像构建中也使用冻结锁文件 | evidence/deployment-validation.txt、evidence/compose-final.txt |
| `uv run --env-file .runtime/validation.env python backend/manage.py check` | 0问题 | evidence/backend-check-final.txt |
| 同上 `makemigrations --check --dry-run` | No changes detected | evidence/migrations-check.txt |
| `uv run --env-file .runtime/validation.env python backend/manage.py test portal.tests --verbosity 2 --noinput` | **80通过、0失败、0跳过**；65.563秒 | evidence/backend-postgres-final.txt |
| 独立SQLite测试配置运行同一 `portal.tests` | **79通过、0失败、1跳过**；62.012秒；仅PG并发测试跳过 | evidence/backend-sqlite-final.txt |
| `pnpm --dir frontend test` | **29通过、0失败**，2个文件 | evidence/frontend-tests.txt |
| `pnpm --dir frontend typecheck`、`pnpm --dir frontend build` | 类型检查、生产构建成功 | evidence/frontend-typecheck.txt、evidence/frontend-build.txt |
| `pwsh -NoProfile -File scripts/start-local.ps1` | 完整SQLite启动通过，4模块/5角色/0默认用户，health与Admin CSS均200 | evidence/local-start-final.txt、evidence/local-start-final.json |
| `uv run --env-file .runtime/validation.env python validation/http_acceptance.py` | **47项真实HTTP断言通过，0失败**；最后一轮已包含陈旧保存修复 | evidence/http-acceptance.json |
| `docker compose up --build --detach --wait --wait-timeout 180`，仅回环18100 | 最终构建、迁移、静态资源、backend/db健康成功 | evidence/compose-final.txt、evidence/compose-health.json |
| `docker compose exec -T backend python manage.py check --deploy` | 仅W021，未自动加入HSTS preload | evidence/production-check.txt |
| `uv run python validation/proxy_acceptance.py` | **8通过、0失败**，真实容器HTTP/代理头/分来源IP限流 | evidence/proxy-acceptance.json |
| `uv run --env-file .runtime/validation.env python validation/restore_rehearsal.py` | **18表行数及内容SHA256一致，源库前后未变化**；恢复到新库，不覆盖原库 | evidence/postgres-restore-final.json |
| `uv run python validation/legacy_regression.py` | 原经营项目定向**26通过、0失败**，独立数据目录/内存测试库 | evidence/legacy-regression.txt |
| `uv run python validation/check_delivery.py` | 当前已知随机运行凭据在交付文本及当前日志中0匹配 | evidence/delivery-secret-check.json |
| `uv run --env-file .runtime/validation.env python validation/close_qa.py` | 8个本期QA账号停用且密码失效，剩余启用0 | evidence/qa-closure.json |

SQLite复验以单独随机SECRET、DEBUG=1/HTTPS=0、空PORTAL_DB_NAME、隔离SQLite路径启动；未将SQLite跳过项目混成通过。前端fetch模拟与后端FakeUpstreamResponse均显式标为**合成契约测试**，没有声称调用真实旧系统。PG的5个并发兑换客户端仅1个200、其余4个403，验证数据库单次消费语义，不代表真实旧端端到端并发容量。

Compose命令使用 `$env:APP_ENV_FILE='.runtime/validation.env'`、`$env:DB_ENV_FILE='.runtime/postgres.env'`、`$env:PORTAL_HTTP_PORT='18100'`；Compose强制DEBUG=0/HTTPS=1、连接自己的db服务/独立卷。代理测试使用文档保留地址作为**合成来源IP头**，不连接这些地址；实际目标只有本机18100。第一次重复运行被前一次IP限流窗口影响，测试脚本改用每轮独立测试来源后重跑8项通过；没有清空生产限流表或放宽限制。

## 账号、权限与管理矩阵

- 正确/错误密码、停用账号同一通用错误、登录频率限制、缺失/错误CSRF、超大/错误JSON、首次改密、旧密码校验、所有旧会话失效：后端80项及HTTP47项覆盖。
- 五角色、无角色、多角色并集、猜URL/参数/user_id不能扩权、原生superuser不替代平台角色、平台管理员默认无业务入口：后端与真实HTTP验证。
- 用户新增、初始改密、管理员重置、自身不可停用/移除管理员、拒绝越权字段、账号/角色/模块/映射变更审计、审计不可新增/改/删、禁止删除用户/基础角色/模块：后端Admin请求测试覆盖；原生UI已查看。
- 停用再启用不恢复旧会话；角色/模块/映射撤销再恢复不恢复旧票据；陈旧全量User.save不会恢复旧密码、清除首次改密或重新启用账号：新增回归测试及独立复现通过。
- 待接入不可启动、停用拒绝、离线/超时/格式错误安全失败、3xx不跳转、URL不接受任意浏览器目标：本期测试通过。旧原生账号撤权行为只能在获批真实集成后补验。

## 浏览器证据与真实边界

使用 Codex 内嵌浏览器真实访问本机服务，不使用静态截图代替服务端请求：

- `browser-login.png`：登录页。
- `browser-manager-workspace.png`：总经理仅经营入口、只读数据未配置提示。
- `browser-navigation.json`、`browser-native-login.png`：真实跳转8018原登录页，未输入旧账号凭据。
- `browser-admin-workspace.png`：平台管理员0个业务模块；`browser-admin.png`：Django Admin首页。
- `browser-admin-audit.json`、`browser-audit-readonly.png`：审计记录只有查看/关闭，没有保存删除操作。
- `browser-first-password.json`、`browser-first-password.png`：首次登录强制改密页；未通过UI提交改密，后端/API自动测试完成改密流程。
- 产品角色页面实测仅产品待接入入口，无经营摘要；门户退出按钮真实返回登录页。
- `browser-mobile-final.json` 记录页面可见DOM宽度375、scrollWidth375，无横向溢出；内嵌浏览器视口截图出现缩放/裁切（`browser-mobile-final.png`），**不把它当作移动真机视觉验收通过**，普通浏览器/真机补验。

浏览器曾暂时失联，重建工具标签后恢复。Django Admin原生“注销”表单在内嵌浏览器发出 `Origin: null` 被CSRF拒绝，**没有降低安全检查或声称该UI操作通过**；有效来源的API/后端Admin测试通过，门户自己的退出流程通过。普通Chrome/Edge真实Origin下的后台新增/改权/重置/注销全流程仍待部署操作者补验。

## 数据库、恢复与故障隔离

- 手工验证PG：固定PostgreSQL17.5 digest，本机回环55438，独立 `portal_phase1`；不是旧经营数据库。
- 迁移包含0001与0002 grant_version；最终恢复快照包含18表，数据及行数全等，源库未变。恢复库 `portal_phase1_restore_final` 与此前 `portal_phase1_restore` 均保留；演练不是对原库重置。
- 备份包含custom-format数据库dump及必要环境配置，位于忽略的受限 `backups/phase1-rehearsal-final/`。报告不含实际凭据。恢复快照在QA账号结束验证前生成，恢复后这些测试账号仍应执行停用步骤，不用作业务账号。
- 实际停止本期8100门户与Compose验证服务后，旧8018根页和 `/api/health/` 均200；见 `failure-isolation.json`。停止的是本期进程，未操作旧系统进程/库。
- 三个旧项目Git状态（非空302行）前后一致，见 `old-project-status-comparison.json`；这是状态对比，不伪称逐文件字节级快照。旧经营26项为定向回归，**不是全部旧测试套件**；工程/转正全套测试未执行。

## 尚未验证与交接

1. 真实旧端可信身份、项目和金额范围一致性、旧端用户停用、双方回调负载/网络故障；需先获批最小旧端改动及受控测试用户。
2. 浏览器SSO未实现，不以机器调用替代。
3. 正式DNS、TLS证书、Nginx配置加载、网络限制、备份异地加密与恢复业务登录；本机生产式配置通过不是互联网生产验收。
4. PostgreSQL17.5固定可复现但不是“已核实最新安全补丁”，上线前补丁审查和回归必须执行。
5. 本地启动脚本已完整执行：首次发现本机uv调用中的Windows反斜线环境文件路径解析失败，改用工作区相对正斜线路径后完整重跑通过。初次失败保留在local-start-initial.txt，最终以local-start-final记录为准。
6. 普通浏览器后台全流程、移动真机视觉和工程/转正全套回归未执行。

旧审批受阻没有停止门户开发、真实导航、部署、恢复和安全修复。处理记录见 REVIEW_REPORT；批准申请和下一阶段边界见 INTEGRATION_CONTRACT、HANDOFF。
