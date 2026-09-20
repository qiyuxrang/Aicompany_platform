# 独立审查、修复与复验

2026-09-20。主线程负责架构、实现整合与关键HTTP/部署/恢复验证；独立子智能体 Newton（Sol xhigh）只读审查需求和真实代码并独立运行定向复现，没有编写被审查实现。测试子智能体 Tesla（Sol xhigh）负责 backend/portal/tests；部署子智能体 Huygens负责部署范围。确实进行了独立智能体审查，但“审查者认为通过”不代替运行证据。

## 发现与闭环

| 严重度/问题 | 位置与影响 | 修复 | 实际复验 |
|---|---|---|---|
| 高：登录CSRF装饰顺序 | backend/portal/views.py；DRF csrf_exempt属性导致错误位置的csrf_protect失效 | csrf_protect置于DRF处理内侧；不安全登录真实CSRF校验 | 两项修复测试、最终后端80项、真实HTTP缺CSRF403 |
| 高：撤权再恢复可复活旧票据 | models/signals/integration/security；只检查当前授权不能表达历史撤销 | grant_version随角色/模块/映射变更单调推进，签发绑定、兑换及返回前比较 | 撤角色/模块/映射后恢复仍拒旧票据，新票据有效；PG并发1成功4拒绝 |
| 高：陈旧User保存回退安全字段 | backend/portal/models.py；仅epoch单调还可能恢复旧密码/账号/首次改密状态 | 加载安全字段快照；全量保存只写显式改变的安全字段；锁行保持DB当前值；版本不接受陈旧值写回 | 独立复现旧密码未恢复、新密码有效、首次改密/停用保留；新增3项测试后PG80全过、SQLite79过1跳 |
| 中：同宿主Cookie冲突 | config/settings.py；不同端口共享Cookie命名空间 | enterprise_portal_session/enterprise_portal_csrf专用名 | HTTP47中各角色Cookie隔离验证 |
| 高：代理头丢失/限流共桶 | compose.yaml；Waitress丢XFP导致health301；真实IP未归一导致同代理用户共桶 | 限可信网络和回环，Waitress只处理受控单跳XFP/XFF；Nginx覆盖XFF | compose-final健康成功；proxy-acceptance8项包括两来源IP桶分离 |
| 中：不受信Host重定向 | deploy/nginx.conf.example；请求Host不应决定跳转域 | 固定域名跳转，未知HTTP/TLS默认拒绝 | 模板静态检查、应用Host真实400；Nginx加载未执行 |
| 中：未授权用户也请求经营摘要 | frontend/src/App.tsx | 由真实模块权限决定是否渲染/调用摘要 | 前端新增测试共29过；产品/管理员浏览器页面无摘要 |
| 中：信任入口3xx | backend/portal/integration.py；可用性探测不应认可外部重定向 | 禁跟随且拒绝全部3xx | 后端拒绝3xx测试 |
| 中：审计不可关联失败/遗漏拒绝 | backend/portal/views.py、integration.py | 失败登录写带秘密HMAC桶标识，不记录明文凭据/IP；经营拒绝写审计 | 后端审计与敏感信息排除测试 |
| 中：同步回调线程耗尽 | backend/portal/integration.py；4条摘要阻塞可能不给兑换回调留线程 | 每进程最多2条上游调用，非阻塞满槽503，finally释放；标准Waitress4线程 | 两项槽满/失败释放合成测试；真实旧端回调负载尚未验证 |
| 中：Admin注销仅跳转 | backend/portal/admin.py | POST-only真实logout及审计，保留CSRF | 后端用例通过；内嵌浏览器Origin:null被拒，未降低安全保障 |
| 构建：宿主node_modules污染Linux镜像 | .dockerignore | 构建上下文排除宿主依赖及dist | 固定锁文件容器重建成功 |
| 启动：Windows环境文件路径解析 | scripts/start-local.ps1；实际完整运行发现本机uv调用丢失绝对路径反斜线 | 切换为已明确工作目录下的相对正斜线env-file路径 | 完整启动复跑，SQLite4模块5角色0用户、health及Admin CSS均200 |

最初失败证据 `backend-postgres.txt`、`compose-up.txt` 保留历史，不混入最终通过计数。PG测试初始连接错误属于测试fixture在TestCase原子事务中关闭连接的问题，测试隔离方式已修复；没有因此改生产数据库事务语义。最终以 `backend-postgres-final.txt`、`backend-sqlite-final.txt`、`compose-final.txt` 和 `proxy-acceptance.json` 为准。

## 独立最终定向复审

审查者初次复验明确指出“epoch单调不等于secret-field不可回退”，主线程没有忽略，而是再次修改实现和补回归。最终独立复现确认：陈旧全量save不恢复旧密码、不清除must_change_password、不重新启用账号，也不会无故推进版本；该P1已关闭。其余指定修复未发现新严重问题。独立复审未宣称替代最终全套测试；主线程核对真实日志并重新构建容器及执行HTTP47项。

## 剩余风险与不能宣称的能力

- 真实经营身份和数据未联调、浏览器SSO未实现，是明确交付缺口，整体仍为部分完成。
- Nginx只是配置样例，TLS/域名及正式网络未部署；默认trusted-proxy通配仅允许回环和专用可信Compose网络，接入不可信容器/允许外部直连会破坏信任前提。
- 管理员能合法授予业务角色，本期未做双人审批。审计是应用层不可改删，不对数据库所有者/主机管理员提供防篡改保证。
- 不承诺SQL/bulk update绕过模型事件时自动版本推进，运营只能使用受审计应用路径；数据库运维权限应独立管理。
- 本机固定PG17.5未完成最新安全补丁核对；上线前由运维完成版本升级与回归。
- 服务回调容量、移动真机、普通浏览器原生Admin写操作、完整旧项目测试仍需补验。已明确标注而非模拟通过。

当前已发现的P1实现问题均有修复与回归证据；这不是无漏洞承诺或正式生产安全认证。
