# 当前 UI 的独占浏览器验收

本套件使用新 UUID PostgreSQL cluster、独立空数据库、私有存储、动态 loopback HTTP 端口和独占 headless Edge。真实 Django settings/URLs、迁移、PBKDF2 密码哈希、浏览器登录、HTTP session 和 CSRF 均保持正常路径。不读取业务 env、用户资料或已有连接文件，不复用既有服务或浏览器，不调用真实模型、RAG、Agent Runtime。

使用 patched backend Python 启动主 runner，指定另一独立 Playwright Python。所需依赖分别是 backend 锁定依赖中的 Django/DRF/psycopg/Waitress，及 Playwright 1.63.0（已安装系统 Edge，无需下载 Chromium）。Windows Job Object 只管理本次创建的 browser worker 及其子进程，核验 active process count 为 0 后关闭；从不终止既有用户进程。

HTTP server 同样使用独占的随机命名 Job 和 stdin 启动门禁。Windows venv launcher 可能在主进程赋 Job 前已创建真实 Python child，因此 server/browser worker 在任何 Django/Playwright 初始化前先自行加入同一 owned Job 并核验。实际 ready PID 和父 launcher 必须出现在 Job 的真实 PID 列表；UUID、job name、私有路径、生产 settings/URL/hash 和 HTTP token identity 都必须匹配。TCP listener 的真实 owning PID 也必须属于该 Job 且只绑定 127.0.0.1。端口未知或不属于 owned Job 时，不发送 shutdown/HTTP token，也不能宣称端口关闭已核验。

```powershell
& .runtime/cloud-readiness-patched-venv/Scripts/python.exe qa/browser_acceptance/run.py `
  --postgres-bin .runtime/cloud-readiness-postgres/binaries/pgsql/bin `
  --server-python .runtime/cloud-readiness-patched-venv/Scripts/python.exe `
  --browser-python .runtime/cloud-readiness-browser-venv/Scripts/python.exe `
  --dist frontend/dist
```

覆盖产品、工程、人事、财务、总经理、平台管理员六类角色的 39 个入口，加 5 个管理员设计预览；每页验证 1280、1440、1920 三个桌面宽度，共 132 条入口/宽度断言，保存每个入口截图。每角色验证真实 UI 登录、授权 API、跨角色拒绝、无 CSRF 写入拒绝和带 CSRF 注销及会话失效。另验证首次登录强制改密、工程初次列表读取失败不能显示为空及恢复、HR 当前长期归档入口、总经理无写入控件、Agent 默认关闭的真实 503 提示、工程 D-05 未实现状态。

它是页面、权限、登录和桌面布局验收；不能替代所有业务写入/审批/发布流程验收，不能证明真实模型、Agent Runtime 长任务、Office 视觉审批、云 Linux 部署、生产容量或灾备已通过。默认关闭 Agent 的页面和明确未实现的定额入口仅验证状态准确，不计作这些功能实现通过。

知识库授权在此 fixture 中明确为空 `{}`，服务端启动和 HTTP identity 均核验这一前提。知识库资料和问答页面必须分别收到正式 `datasets/`、`status/` 的精确 `403 scope_revoked` 响应，并显示无权限提示；资料页不能误报空知识库，问答页必须禁用新会话与提问、清除回答与引用。仅这两个产品页面、对应 API 和 owned origin 的精确响应可归类为预期拒权；其他 403、缺少授权前提、不同错误文案或外域仍失败。此项只证明拒权准确，不能证明授权后的 RAG 业务可用。

每次保留 `.runtime/browser-acceptance/<UUID>/report.json`、`browser-result.json`、截图、server/browser 日志、UUID 身份和源文件/编译 dist 摘要；PG 自身证据留在 runner 报告引用的 `.runtime/portable-postgres/<UUID>/report.json`。凭据只通过环境或 stdin 传递，日志和 JSON 清除临时随机口令，不保存 cookie、session、请求 body 或 auth storage state。失败、超时、源漂移、遗漏角色/断言/截图、意外控制台/HTTP/浏览器错误、外部访问或 cleanup 未核验均为非零退出。原 cluster 和隔离存储作为证据保留，不删除业务目录。

已有套件仅供设计参考，不复用运行环境：`site_desktop_acceptance.py` 缺 finance/Agent，采用 SQLite/.venv；`prd_browser_acceptance.py` 固定端口且 HR 空记录文案已变化；`product_workbench_browser.py` 依赖既有固定 connection.json，旧创建表单的必填目标/创建项目按钮已变为资料上传并保存或生成。新套件按当前 `centers/config.ts` 和 `App.tsx` 路由权限构造计划。

纯安全检查不启动任何服务：

```powershell
& .runtime/cloud-readiness-patched-venv/Scripts/python.exe -m unittest qa.browser_acceptance.test_safety -v
```
