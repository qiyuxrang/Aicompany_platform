# 经营接入契约与撤权边界

2026-09-21。已获批准，在旧端隔离工作树和全新PostgreSQL测试库完成真实双向HTTP身份兑换及项目范围对照。数据为合成隔离项目，不是公司生产财务数据；原8018实例未部署桥接。浏览器 SSO 单独为“未实现、不在本轮范围”。

已批准范围：INTEGRATION_APPROVAL.md 的4文件；仅授权项目id/name与project_count，不接财务金额；updated_at为本次查询快照生成时间，不代表业务记录最后修改时间。最终实测证据见 PHASE1_CLOSURE.md。

## 撤权先决边界

| 访问路径 | 平台停用/撤权影响 | 生效时点与例外 |
|---|---|---|
| 门户受保护页面、API、Django Admin | 停用阻断全部；移除角色/模块仅阻断对应权限 | 数据库事务提交后的下一次鉴权请求；没有权限缓存宽限期 |
| 门户经营启动接口 | 失去经营模块授权即禁止获得导航响应 | 下一次请求；不能回收先前已知的旧网址 |
| 平台可信票据兑换与只读摘要 | 重查账号、会话版本、授权版本、模块和映射 | 兑换时及摘要返回前校验；撤销后再恢复也不能复活旧票据 |
| 已打开的旧经营页面、原生旧账号/旧会话、直接旧网址 | **不受平台撤权自动控制** | 仍按旧系统原生权限/会话策略；不得默认禁用旧账号 |
| 已返回到浏览器的内容/下载 | 无法远程收回已交付内容 | 本期不做终端内容擦除；保护接口设置 no-store |

在途请求的边界是其最后一次服务端权限校验，不承诺撤权事务提交瞬间撤回已发送响应。重置密码、改密、停用/重新启用通过 session_version 使先前平台会话失效；角色和模块/映射变更提升 grant_version，使已签发票据永久失效。生产操作仅走受审计 Admin/受控管理命令，不支持绕过应用的 SQL/update 批量改权（它会绕过应用信号和审计）。

原生旧账号仅能由旧系统负责人按其流程停用。未来若要求旧原生登录同步撤销，属于额外生命周期管理需求，需明确授权和单独设计，不能用当前导航方案宣称完成。

## 平台接口

| 接口 | 身份与结果 |
|---|---|
| GET /api/csrf/ | 返回 masked CSRF；浏览器只保存在内存 |
| POST /api/login/ | username/password + CSRF；会话 Cookie，不发浏览器 JWT |
| GET /api/me/、POST /api/logout/、POST /api/password/ | 本人会话；不安全操作 CSRF；改密 old_password/new_password |
| GET /api/modules/、GET /api/modules/{code}/ | 仅自身授权范围；篡改 user_id/role 无法扩权 |
| POST /api/modules/{code}/launch/ | CSRF + 后端授权 + 固定可信地址 HEAD；2xx/401/403/405可判在线，3xx一律拒绝 |
| GET /api/business/summary/ | 平台经营权限，内部请求固定旧服务端地址；未配置返回503 integration_not_configured |
| POST /api/integration/redeem/ | 仅服务端 Bearer 凭据，不依赖浏览器 Cookie；ticket/audience/purpose必填 |

启动只返回已保存的可信 URL，不把任何令牌、身份、密码放到 URL。管理员输入的 URL 必须匹配环境中精确 origin，禁止用户信息、查询参数、片段、控制字符、反斜线；生产只准 HTTPS。不跟随重定向、不使用机器代理环境变量。只读地址同样由环境固定，用户不能指定目标。域名解析/出口网络仍需部署侧限制，禁止信任来源指向不受控内网服务。

## 可信身份协议（隔离旧端已实现）

1. 平台从当前会话重查用户经营权限、启用的模块及 BusinessMapping，**不采信浏览器传入的旧用户 ID**。
2. 平台生成高熵一次性票据，数据库只存 SHA-256 digest。绑定平台用户、旧映射 ID、旧用户标识、session_version、grant_version、audience=business、purpose=read_summary；默认30秒有效。
3. 平台 POST 配置的 `PORTAL_BUSINESS_SUMMARY_URL`，JSON为 ticket/audience/purpose，Authorization 为独立 `PORTAL_INTEGRATION_SECRET`。票据只存在机器请求体，不进入浏览器、重定向或日志。
4. 旧端须验证机器凭据，服务器调用门户 `/api/integration/redeem/`，提交同样的 JSON 和凭据；校验失败不降级为共享管理员。
5. 门户原子条件消费票据，只有一次请求能成功，返回 external_user_id/audience/purpose；过期、已用、撤权、映射变化返回403。
6. 旧端以 external_user_id 查找自己的**启用且非首次待改密用户**，复用 BusinessAccessPermission 的原生GET项目访问条件及 accessible_project_ids，只返回授权项目名称、标识与数量。旧端不得创建浏览器会话或越权使用管理员上下文，不涉及财务口径。
7. 门户检查返回结构/体积/HTTP状态；确认票据已被兑换，再重查最新平台授权。失败只报告错误，绝不展示模拟数据或共享缓存。

映射为平台用户一对一旧用户标识，唯一约束拒绝同一旧用户绑定多个平台账号；通过 Django Admin 明确维护。不能按显示名模糊匹配，不自动创建或停用旧账号，不保证两边用户名相同。

当前传输凭据为最小的独立共享服务秘密，不是 OIDC/OAuth，不宣称具备外部身份提供方功能。生产启用前须落实双方HTTPS、出口允许清单、秘密配送与轮换；不得记录 Authorization 或 ticket。门户只向获信任来源的精确路径 `/api/portal-bridge/summary/` 发送秘密和票据，旧端只回调固定 `/api/integration/redeem/`。平台每进程最多2个上游摘要调用，非阻塞拒绝其余请求（503），满槽不签发票据；需要多线程服务给回调预留容量。真实并发一次消费已验证，不代表吞吐压测或生产容量保证。

## 只读返回字段

顶层必须只有 `projects`, `summary`, `source`, `updated_at`：

| 字段 | 类型/约束 | 归属 |
|---|---|---|
| projects | 最多1000个 `{id: string或integer, name: string≤200}`；布尔ID不接受 | 旧端权限过滤后的项目，不能全表导出 |
| summary.project_count | 必填整数，不接受布尔，等于projects长度 | 完整授权集合数量，不截断后冒充总数 |
| source | 固定 `legacy-ledger:authorized-projects` | 原经营项目权限过滤查询 |
| updated_at | 带时区 ISO 日期时间 | 本次查询快照生成时间 |

`summary` 仅接受project_count，额外财务字段一律拒绝。空权限集合明确返回空列表和0。上游响应最大256KiB、socket超时3秒，正文分块读取检查截止时间；禁重定向，异常/超限/无效数据返回503，旧端403对应平台403。不把错误变成“没有项目”。只读票据不授权修改，旧业务事务与数据库均归旧系统。网络层还需配置反向代理总请求时限、头部读取限制和出口策略，不把socket空闲超时宣称为任意网络行为下的硬实时总时限。

## 浏览器 SSO 单列

**未实现、未验证。** 可信机器调用只是按映射用户获取只读数据，不会登录旧网页；导航目前必然保留原登录。未开发 OIDC、SAML 或浏览器登录票据，不可对外宣传为单点登录。

## 旧端批准范围与回退

已在经营项目隔离工作树新增 `backend/ledger/portal_bridge.py` 与测试，在 `backend/ledger/urls.py` 注册默认关闭接口，在 `backend/config/settings.py` 添加默认空/关闭可信配置。无新模型迁移、无原生登录/账号/会话改动、无主业务重构。门户运行进程没有旧数据库凭据；仅隔离验收脚本使用已获准测试库进行夹具准备和对照。

回退时旧端设置 `LEDGER_PORTAL_BRIDGE_ENABLED=0` 并重启受控新实例，桥接返回404；门户清空 `PORTAL_BUSINESS_SUMMARY_URL` 后只读返回503、导航保留原登录。上述两种回退均有实际HTTP测试，原生旧会话仍能访问项目。不得自动停用旧账号。原工作区未合并桥接，需要正式部署时按四文件差异在另行受控发布中应用，不覆盖既有未提交工作。
