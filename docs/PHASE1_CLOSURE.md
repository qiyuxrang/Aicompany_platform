# 统一门户第一阶段交付闭环

日期：2026-09-21。**整体：部分完成，不视为全量验收通过。**

后续自主补齐：票据清理命令及10项回归、最终旧端提交的完整真实联调、27次并发回调与满槽拒绝、新一轮隔离资源生命周期均已验证。最新后端143项、旧端31项、前端45项通过；独立证据位于 `evidence/bridge-20260921-followup/`。复现命令及详细补验见 `PHASE1_FOLLOWUP.md`。以下初轮数据/证据作为历史保留；浏览器和正式环境边界不变。

本轮已完成获批旧端桥接、真实隔离联调、安全修复与复验、原生后台HTTP补验，以及包含新增运维表的数据库恢复。普通浏览器后台操作仍因没有可控Chrome/Edge连接而阻塞。没有将原旧项目上线/数据迁移作为暗含动作。

## 1. 分组验收

| 分组 | 已执行及结果 | 未执行/边界 |
|---|---|---|
| 门户与权限 | PostgreSQL后端133项、前端45项、TypeScript检查通过；真实Admin表单HTTP28项通过，涵盖创建、角色调整、密码重置、停用启用、审计过滤和只读拒绝 | 普通浏览器A01—A10未执行。HTTP脚本提交真实表单，不是普通浏览器点击验收 |
| 经营导航 | 既有导航仍保留原经营登录；本轮桥接开关关闭、门户只读URL清空均有实际回退验证，旧原生会话仍200 | 未改原8018网页与登录；隔离18318只用于旧端API，不宣称其根路径有可用经营网页 |
| 可信身份与只读数据 | 当前旧端源码工作树 + 两个全新PG库 + 实际双向HTTP，16组真实权限/数据检查和15组异常/恢复检查全部通过 | 数据是获准合成项目，不是公司财务数据；仅项目id/name/数量，不含金额；尚未发布到原日常系统 |
| 浏览器SSO（单列） | 无 | 未实现、未测试，明确不在本轮范围。可信服务器调用不会建立旧网页会话 |

本阶段验证平台底座，**不代表AI文档链路已经验证**。没有新增模型调用、文档生成、业务中心、部门组织树或部门级数据权限；旧端原有部门项目规则仅复用，不重建。

## 2. 撤权与账号边界

- 平台账号停用：下一次平台受保护请求拒绝；原平台会话失效。没有批量停用旧原生账号。
- 角色、模块、映射撤权：下一次请求、票据兑换及摘要返回前重新鉴权；撤销后恢复权限也不能复活旧票据。
- 旧端用户角色/部门、激活状态、首次改密标记及项目已有departments变化：下一次旧原生查询与桥接按同一权限函数重新计算，实测一致。
- 平台撤权不撤回已经返回的内容，也不关闭旧原生登录、既有旧会话或已知旧网址。旧账号停用属于旧系统独立生命周期管理。
- 在途请求以最后服务端权限校验为边界，不承诺撤权事务提交瞬间收回已发出的响应。
- 日常18210管理员 `bjrunner` 未重置密码、未停用；后续普通代码修改不能自动更改该账号或密码。

## 3. 实现与源代码保护

旧工作区：`C:\Users\BJRunner\Desktop\监控看板`，原HEAD `c0da7e2fb013c80dc0d6ad5ebc3c81306fdb2ed6`。存在既有未提交成果，不能仅用HEAD冒充当前运行源码。

隔离工作树：`C:\Users\BJRunner\Desktop\ledger-portal-bridge-isolated`，分支 `feature/portal-read-bridge`。

| 本地提交 | 内容 |
|---|---|
| `97a6bdb9fdba7c8398b886fd0418cf3dd775c82e` | 受控当前后端源码快照，45个既有文件差异；含当前原有迁移/原生认证测试，不是新增schema |
| `5ed1199d8f8ced7dbf974027e23d1b563f7458a2` | 仅获批四文件桥接改动：portal_bridge.py、ledger/urls.py、config/settings.py、test_portal_bridge.py |
| `beb933ab92775f727a99c01edd4a527a90414dcd` | 最终HEAD；在同一批准范围补充异常HTTP状态行/截断传输失败关闭及回归；相对源快照总差异仍只有四文件 |

四文件实际暂存blob检查已知运行秘密0匹配，再进行本地提交；没有推送/合并原工作区。原旧端HEAD、分支、索引及被登记源码哈希前后相同。门户已有未提交UI/运维成果完整保留，门户原索引未变、原frontend/dist/index.html哈希未变。

桥接只接受POST、独立机器秘密和严格三字段票据，回调精确兑换端点。旧User必须存在、启用且不待首次改密；使用原 `BusinessAccessPermission` 的GET项目权限及 `accessible_project_ids`。返回白名单项目、固定source、与列表长度一致的project_count；不写旧业务表、不创建浏览器会话。

门户修复：固定获准桥接路径后才发秘密/票据；兑换拒绝额外身份字段；拒绝额外财务字段、错误来源和计数不一致；满槽不生成无效票据，失败释放槽；分块读取防持续正文慢滴长期占用。旧端对正文慢滴施加截止时间并关闭连接，实际测试槽位可恢复。

## 4. 实际测试与证据

所有以下路径相对于本仓库；不同类型的数量不合并为一个总通过数。

| 测试/检查 | 实际结果 | 证据 |
|---|---|---|
| 门户全套PostgreSQL测试 | 133通过 | `docs/evidence/closure-integration/portal-unit-tests.txt` |
| 旧桥接及既有原生认证 | 31通过 | `docs/evidence/closure-integration/legacy-unit-tests.txt` |
| 前端组件/API测试 | 45通过；类型检查通过 | 同目录 `frontend-tests.txt`、`frontend-typecheck.txt` |
| 原生旧端真实HTTP与数据对照 | 16组通过 | 同目录 `http-acceptance.json`、`http-tests.txt` |
| 双向异常及回退复验 | 15组通过 | 同目录 `fault-acceptance.json`、`fault-tests.txt` |
| 原生Admin真实HTTP表单 | 28项通过 | 同目录 `admin-http-acceptance.json` |
| 普通浏览器补验 | 阻塞，未记为通过 | 同目录 `browser-availability.json`；`docs/ADMIN_BROWSER_ACCEPTANCE.md` |
| 当前20表备份恢复 | 表集合/行数/内容指纹一致；0003迁移一致 | `docs/evidence/closure-backup/restore-rehearsal.json` |
| 源码与实例保护 | 原源码/索引/构建未变；8100/18210/8018健康200 | `docs/evidence/closure-integration/final-boundary-check.json` |
| 日常管理员状态 | 只读检查启用且密码可用，未改账号 | 同目录 `persistent-account-check.json` |
| 秘密扫描 | 已知运行秘密在交付文本/运行日志0匹配 | `docs/evidence/delivery-secret-check.json`，不冒充未知密钥全量检测 |

真实数据对照包含manager全量4项目、sales及engineering各2项目；每个项目id/name及数量与旧原生 `/api/projects/` 比较。覆盖无映射、停用/不存在映射用户、首次改密拒绝、平台管理员不默认业务授权、4类撤权及撤权恢复、平台停用不影响旧原生会话、旧端权限变化和空集合。旧业务表在测试前后指纹一致。

票据验证含错误凭据、额外user_id、错误audience/purpose、无效/过期/重放、GET拒绝；5个并发兑换仅1个200，其他4个403。上述是实际端点调用，单元测试另外标识。

故障测试包含真实停旧服务、停门户回调、关闭桥接、清空门户只读URL；以及明确标识的故障端点注入无效JSON、超大响应、302、超时和持续正文慢滴，双向均失败关闭并恢复。故障端点不是业务数据源，不计为原生数据对照。持续慢滴重复调用验证槽恢复，不是生产负载测试。

初轮证据版本边界：真实HTTP及故障联调在桥接提交5ed1199执行，清理后beb933a补修只跑后端回归。**该版本覆盖缺口现已补齐**：后续轮建立全新库和新随机凭据，对最终beb933a重新完成16组真实HTTP、15组故障、28项Admin HTTP和27次并发检查；没有复活初轮已撤销账号/秘密。新旧轮次证据分别保存。

## 5. 运行与复现

日常门户仍为 `http://127.0.0.1:18210/`，原8100/8018未重启或重配。桥接验收用18310/18318，完成后已停；不要为查看成果擅自重启旧实例或把日常账号加入fixture。

已有源码定向测试命令（在门户根目录执行；需保留受限测试env与登记测试库）：

```powershell
uv run python validation/bridge_environment.py run portal test portal.tests --noinput
uv run python validation/bridge_environment.py run ledger test ledger.tests.test_portal_bridge ledger.tests.test_auth_permissions --noinput
pnpm --dir frontend test
pnpm --dir frontend typecheck
```

真实联调的本轮顺序为：`bridge_preflight.py`登记源快照 → `bridge_environment.py provision`创建全新库与随机秘密 → 分别 `run ledger migrate --noinput` / `run portal migrate --noinput` → `run ledger seed` / `run portal seed` → `bridge_system_acceptance.py --keep-running` → `bridge_admin_http_acceptance.py` → 停止登记的两组测试进程 → `bridge_cleanup.py`。

前置快照/provision/seed均拒绝覆盖现有资源。**不要原样重复本轮建库命令**：当前fixture凭据已作废。现在可设置全新的 `PORTAL_BRIDGE_VALIDATION_RUN`，在独立运行目录和新数据库重新provision，不再需要搬走或覆盖旧配置。见 `PHASE1_FOLLOWUP.md`。普通浏览器补验也需要独立临时账号；测试脚本不是生产账号运维脚本。

生产部署仍沿用 `DEPLOYMENT.md` 的固定依赖、独立数据库与受控启动方式。原旧端未应用本桥接，正式启用属于受控发布：先复核当前源码漂移，按四文件功能差异应用，不能强行cherry-pick整个当前源码快照覆盖原未提交成果。

双方配置：门户 `PORTAL_BUSINESS_SUMMARY_URL=https://受控旧端/api/portal-bridge/summary/`、`PORTAL_INTEGRATION_SECRET`、精确 `PORTAL_TRUSTED_MODULE_ORIGINS`；旧端 `LEDGER_PORTAL_BRIDGE_ENABLED=1`、`LEDGER_PORTAL_REDEEM_URL=https://受控门户/api/integration/redeem/`、`LEDGER_PORTAL_BRIDGE_SECRET`，回调超时不超过2秒。两边独立配送同一新生成的服务秘密；不得使用本轮已撤销值。门户进程不配置旧数据库凭据。

上线前须配置HTTPS、出口允许清单、服务秘密配送/轮换、反向代理头部/总请求时限及多线程回调容量。旧端测试用Django runserver仅用于隔离验收，不是生产方案。当前socket空闲超时不能覆盖任意响应头慢滴/DNS阻塞；不能据此宣称网络硬实时总期限或高可用已验收。已新增默认保留过期票据7天的 `cleanup_tickets`，默认仅预览、明确 `--apply` 才执行，实际隔离验证通过；未安装生产定时任务，不删除审计。

## 6. 回退、恢复和测试清理

- 桥接回退：旧端 `LEDGER_PORTAL_BRIDGE_ENABLED=0` 后桥接404；门户清空只读URL后503明确未配置，导航保留原登录。两种回退均实测通过，旧原生会话可继续使用。
- 本期无新增旧端schema，不执行数据库反向迁移，不默认禁用任何旧原生业务账号。
- 最新恢复命令：`uv run --env-file .runtime/ops-validation.env python validation/ops_restore_rehearsal.py`。脚本创建全新恢复库，不覆盖源库；完整操作与两次失败保留记录见 `docs/evidence/closure-backup/README.md`。
- 最终恢复库 `portal_ops_restore_20260921_040512`；备份包含账户等敏感信息，仅在受限忽略目录保存，不随Git交付。恢复实例开放前须再次核查账号、秘密、目标网络，不能把快照直接发布为日常库。
- 本轮 `portal_bridge_e2e_20260921_120250` 与 `ledger_bridge_e2e_20260921_120250` 保留未drop；8个门户合成用户和5个旧端合成用户全部停用、密码不可用，测试会话清理、票据失效；桥接秘密清空且开关关闭，18310/18318/18319已无监听。证据 `cleanup-portal.json`、`cleanup-ledger.json`、`final-boundary-check.json`。

## 7. 剩余阻塞与下一步

1. **普通浏览器后台补验**：需要可控普通Chrome/Edge或人工按A01—A10实际操作并留证；只发现Codex内嵌浏览器。不关闭CSRF、不信任Origin:null来凑通过。
2. **正式环境受控发布**：本轮没有公司数据和正式网络/TLS验收；需要旧系统负责人确认发布时点、生产账号映射、只读项目数据范围及运维责任后另行部署。不能将隔离合成数据验证说成生产财务对账。
3. **运维补强**：票据清理实现与本机8线程/2摘要槽的27次并发冒烟已完成；生产前仍需头部/总请求限制、生产负载容量和清理排程。不得把本机冒烟当作生产压测；不扩展通用队列/Agent框架。

浏览器SSO和AI文档链路不是待补测即自动具备的功能，而是明确未实施的独立后续范围。
