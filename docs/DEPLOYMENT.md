# 部署与运维

## 票据留存与重复隔离验收

`cleanup_tickets` 默认仅预览过期超过7天的票据；`--retention-days` 最少1天，显式 `--apply` 才删除。每批最多1000条，独立事务且删除前重查过期条件；不清理审计、账号或映射。当前批失败会回滚，之前已完成批次保留，重跑安全。已在隔离PostgreSQL实测，不表示已在生产运行或安装定时任务。运维账户应在确认目标环境/留存要求后安排，命令与证据见 `PHASE1_FOLLOWUP.md`。

验证脚本支持 `PORTAL_BRIDGE_VALIDATION_RUN` 的独立配置/证据命名空间，默认旧行为不变。新验收必须用新标识并创建新库；重复标识provision会拒绝覆盖，非法路径标识会拒绝。不得用该变量绕开登记库、回环地址和端口检查。

## 2026-09-21 最新恢复与桥接说明

已补齐包含0003新增运维表的恢复：`validation/ops_restore_rehearsal.py`，20表同一导出快照行数/指纹一致，说明及失败留痕见 `evidence/closure-backup/README.md`。恢复库和备份保持受限访问，不直接开放为日常实例；下方18表结果属于历史阶段。

新桥接仅在独立工作树与独立测试库实测，未改原8018、8100、18210。正式接入所需配置、固定路径、回退及未验收条件见 `PHASE1_CLOSURE.md` 与 `INTEGRATION_CONTRACT.md`。测试秘密已撤销，不能将测试env直接当作上线配置，也不能用开发runserver作为生产部署。

## 本次实际执行补记（2026-09-20）

主线程已执行最终Compose构建/迁移/collectstatic/健康检查（backend与db均healthy）、真实代理HTTP8项、生产check（仅W021）、PG80项测试和18表完整内容指纹恢复对比。证据见 ACCEPTANCE_REPORT。下文通用部署命令不意味着已部署正式域名/Nginx/TLS。

实际最终恢复由 `uv run --env-file .runtime/validation.env python validation/restore_rehearsal.py` 在手工验证PG执行，恢复到 `portal_phase1_restore_final`，18表行数及内容SHA256相同、源库前后未变。实际dump文件为 `backups/phase1-rehearsal-final/portal-phase1.dump`，同时备份 validation.env/postgres.env，并限制ACL。这个脚本目标已存在时拒绝重跑，不会覆盖/删除；新的演练须另行确认一个新的隔离库。下文手动示例使用 `portal_phase1.dump` 名称，执行时不要与实际文件混淆。

验收QA账号全部停用且密码失效；首次业务使用者需要交互式bootstrap_admin，不提供固定密码。恢复快照在验收账号关闭前生成，恢复后必须先处理测试账号，不能直接对业务人员开放。

`start-local.ps1`最后也已完整实跑：本机uv对Windows绝对env-file路径解析失败，经改用工作区相对正斜线路径后，冻结安装/构建/SQLite迁移/seed/collectstatic/Waitress全部执行完成，健康与Admin CSS均200。该临时SQLite实例已停止，8100恢复为原独立PostgreSQL验证实例；Compose验证容器已正常stop并保留卷。参见evidence/local-start-final.json及compose-stop.txt。

## 边界

- `backend` 默认仅发布到宿主机 `127.0.0.1:8100`；可用 `PORTAL_HTTP_PORT` 改变宿主端口，容器内仍为 `8100`。
- Compose 的 `db` 服务没有 `ports`，只能由 Compose 网络内的 backend 通过 `db:5432` 访问。
- Compose 不设置 `container_name`，数据库使用项目内命名卷 `phase1_pg_data`；它不会接管手工验证容器或其卷。
- Phase 1 使用库 `portal_phase1`，Django 测试库为 `test_portal_phase1`。两次隔离恢复库分别为已保留的 `portal_phase1_restore` 和最终新建的 `portal_phase1_restore_final`，都不得覆盖。
- Compose 不修改防火墙、不申请证书，也不把开发配置描述为互联网生产配置。
- Compose 的 Waitress 默认 `trusted-proxy=*` 仅适用于当前 backend 只发布到宿主回环、项目网络只有受信 backend/db、宿主机无不可信本地用户可直连该端口的前提。
- `/static/` 由 WhiteNoise 服务；Vite 的 `/assets/` 和前端路由由 Django frontend view 服务。

## 镜像与锁文件

- Python 基础镜像、uv 镜像和 PostgreSQL 均固定到版本或 digest；Node 固定为 `24.14.0-bookworm-slim`。
- Python 安装使用 `uv sync --frozen --no-dev`，前端安装使用 `pnpm install --frozen-lockfile`。
- 当前 PostgreSQL 是本机已有并验证可用的 `17.5` digest。2026-09-20 查询 `17.10-alpine` manifest 时 Docker Hub 超时，因此这里不声称 `17.5` 是最新补丁。
- 上线前必须重新核对 PostgreSQL 17 的受支持补丁版本和 digest：先备份、完成隔离恢复演练，再同时更新 `compose.yaml` 中的版本与 digest。不要只移动 tag。

## 环境文件

生产应用环境文件应从 `.env.example` 创建，至少满足：

- `PORTAL_DEBUG=0`
- `PORTAL_HTTPS=1`
- `PORTAL_BEHIND_PROXY=1`
- `PORTAL_SECRET_KEY` 为独立随机值且不少于 40 字符
- `PORTAL_DB_NAME=portal_phase1`
- `PORTAL_DB_HOST=db`、`PORTAL_DB_PORT=5432`（Compose 内部地址）
- `PORTAL_ALLOWED_HOSTS` 包含正式域名
- `PORTAL_CSRF_TRUSTED_ORIGINS` 使用对应的 `https://` 来源

应用环境文件中的数据库名、用户和密码必须与 PostgreSQL 初始化环境文件一致。现有 `.runtime/validation.env` 与 `.runtime/postgres.env` 不应被脚本覆盖，也不应直接当作公网生产凭据。

手工验证链路与 Compose 是两套隔离运行：

- 手工链路由宿主机 uv 进程读取 `.runtime/validation.env`，连接现有手工容器 `enterprise-portal-phase1-db` 的 `127.0.0.1:55438`。
- Compose backend 强制使用 `PORTAL_DB_HOST=db`、`PORTAL_DB_PORT=5432`，连接 Compose 自己的 `db` 服务及项目卷；即使复用 validation.env 中的账号字段，也不会连接手工容器。
- `scripts/start.ps1` 和 `scripts/stop.ps1` 只管理 Compose 项目，不能用于接管手工验证容器。

`.env`、`.runtime/*.env` 和数据库备份都包含秘密：保持在忽略目录，限制为运行账号可读，并把加密备份放到独立受控位置。Windows 可在创建文件后执行：

```powershell
icacls .env /inheritance:r /grant:r "${env:USERNAME}:(F)"
icacls .runtime\postgres.env /inheritance:r /grant:r "${env:USERNAME}:(F)"
```

备份账号如需读取，应单独授予只读 ACL；不要把秘密打印到终端、日志或工单。

## 本地开发

本地开发明确使用 SQLite、`PORTAL_DEBUG=1` 和 HTTP，仅绑定 `127.0.0.1`：

```powershell
.\scripts\start-local.ps1
```

首次运行会创建受 ACL 保护的 `.runtime/local.env` 和独立随机 `PORTAL_SECRET_KEY`，不会读取或覆盖 `.runtime/validation.env`、`.runtime/postgres.env`。脚本按锁文件同步依赖、构建前端、迁移 SQLite、幂等执行 `seed_portal`、收集后台静态资源，再以前台 Waitress 启动；按 `Ctrl+C` 正常停止。此模式不得用于互联网生产。

## Compose 启停

先由负责 PostgreSQL 的操作者确认环境文件，再启动。生产式容器验证可改用回环端口 `18100`，避免占用手工 HTTP 验证的 `8100`：

```powershell
.\scripts\start.ps1 -AppEnvFile .runtime/validation.env -DbEnvFile .runtime/postgres.env -HttpPort 18100
```

该命令会创建或使用 Compose 自己的 `db` 服务和项目卷，不会复用、启动、停止或恢复手工 `enterprise-portal-phase1-db`。validation.env 中的 `127.0.0.1:55438` 会被 Compose backend 的 `db:5432` 覆盖。

Waitress 的 Compose 启动参数为：

```text
--trusted-proxy="${WAITRESS_TRUSTED_PROXY:-*}"
--trusted-proxy-count=1
--trusted-proxy-headers="x-forwarded-proto x-forwarded-for"
--clear-untrusted-proxy-headers
```

`WAITRESS_TRUSTED_PROXY` 可在应用环境文件中设置为反向代理与 backend 建连时的精确 peer IP。Docker Desktop/宿主桥接 peer 可能变化，当前默认 `*` 是在回环发布和专用 Compose 网络边界内避免绑定易变容器 IP 的折中，不是公网安全默认值。只要 backend 改为非回环发布、加入不可信容器、允许不可信本地进程直连，或代理不能强制覆盖转发头，就必须停止使用 `*` 并设置精确可信 peer；Waitress 3.0.2 不接受 CIDR。

正式环境默认读取 `.env` 与 `.runtime/postgres.env`：

```powershell
.\scripts\start.ps1
.\scripts\stop.ps1
```

停止脚本使用 `docker compose stop`，不会删除容器、命名卷或数据库。禁止用 `docker compose down -v` 做日常停止。

只检查 Compose 配置且不输出解析后的秘密：

```powershell
$env:APP_ENV_FILE = (Resolve-Path .runtime/validation.env)
$env:DB_ENV_FILE = (Resolve-Path .runtime/postgres.env)
$env:PORTAL_HTTP_PORT = 18100
docker compose config --quiet
```

## 初始管理员与后端 CLI

容器构建已经执行冻结依赖同步；本机单独同步可运行：

```powershell
uv sync --frozen
Push-Location frontend
corepack pnpm install --frozen-lockfile
Pop-Location
```

迁移由 backend 容器启动命令幂等执行。初始管理员必须通过交互命令创建，不存在默认账号或默认密码：

```powershell
.\scripts\manage.ps1 -AppEnvFile .runtime/validation.env bootstrap_admin admin
```

常用检查：

```powershell
.\scripts\manage.ps1 -AppEnvFile .runtime/validation.env seed_portal
.\scripts\manage.ps1 -AppEnvFile .runtime/validation.env check --deploy
.\scripts\manage.ps1 -AppEnvFile .runtime/validation.env check_production_readiness --json
.\scripts\manage.ps1 -AppEnvFile .runtime/validation.env test
```

`check_production_readiness` 是只读检查：不联网、不写业务数据，也不输出密钥。它把平台配置错误记为
`blocked`，把真实模型、正式基础设施等尚需现场完成的事项记为 `external_gate`。构建平台侧生产候选包时可以使用
`--allow-external-gates`，但该参数只改变命令退出码，不会把外部门槛改写成通过，也不能用于正式发布签字。

Django 测试会创建并删除 `test_portal_phase1`；运行前确认没有同名人工数据库。
`check --deploy` 当前会保留 `security.W021`：站点已启用 HSTS，但没有自动加入浏览器 preload 列表；只有在域名及所有子域长期满足 preload 要求并经负责人确认后才应修改该策略。

## HTTPS 反向代理

默认 Compose 配置是生产 HTTPS 模式，必须放在 HTTPS 反向代理后。复制并替换 `deploy/nginx.conf.example` 中的固定正式域名、证书路径和实际回环端口，然后由现有 Nginx 加载。模板的默认站点拒绝未知 HTTP Host 和 TLS 握手，正式 HTTP 站点只跳转到固定域名，不使用请求的 `$host`。

正式 HTTPS 站点必须覆盖而不是追加客户端转发头：`X-Forwarded-Proto` 固定为 `https`，`X-Forwarded-For` 固定为 Nginx 看到的单个 `$remote_addr`。禁止使用 `$proxy_add_x_forwarded_for`，否则客户端可注入地址链。Waitress 只信任这两个头、只接受一跳，并把受控 `X-Forwarded-For` 归一为 WSGI `REMOTE_ADDR`；Django 登录限流继续读取该 `REMOTE_ADDR`，不读取或盲信 `X-Real-IP`。

不要把 Web 端口改为 `0.0.0.0`，不要给 Compose `db` 增加宿主端口，也不要让 Waitress 直接承担 TLS。证书签发、DNS、公网入口和防火墙规则均由现有基础设施负责人处理。

现有 `127.0.0.1:8100` HTTP 仅用于受控的本地开发或手工验证，是独立路径，不代表生产 HTTPS、域名或证书已通过。`18100` 上的生产式容器验证也只能证明容器和代理头路径；没有真实反代与证书时不得宣称公网 HTTPS 验证完成。不得把 backend 发布到非回环地址，也不得把不可信容器接入其项目网络，否则 `trusted-proxy=*` 会允许伪造 scheme 和客户端 IP。

## 健康检查与日志

```powershell
docker compose ps
docker compose logs --tail 100 backend
curl.exe --fail --resolve portal.example.com:443:127.0.0.1 https://portal.example.com/health/
```

健康端点同时检查 Django 请求链和数据库连接。初次 Compose 启动的迁移和 `collectstatic` 成功，但 Waitress 3.0.2 清除了未信任的 `X-Forwarded-Proto`，健康请求被 Django 以 `301` 重定向，最终超时；原始记录保留在 `docs/evidence/compose-up.txt`。修复没有关闭或绕过 `SECURE_SSL_REDIRECT`，而是让 Waitress 在上述边界内处理受控代理头。

Compose 内部健康检查现在发送受控的 `X-Forwarded-Proto` 和单值 `X-Forwarded-For`。主代理重建后应复验三种行为：

```powershell
curl.exe -I -H "Host: 127.0.0.1" http://127.0.0.1:18100/health/
curl.exe --fail -H "Host: 127.0.0.1" -H "X-Forwarded-Proto: https" -H "X-Forwarded-For: 198.51.100.10" http://127.0.0.1:18100/health/
docker compose ps
```

第一条普通 HTTP 应为 `301`，第二条代理模拟应为 `200`，随后 backend 健康状态应为 `healthy`。这只验证 Waitress/Django 代理行为，不验证 TLS、域名或证书。还应由主代理从登录失败审计或受控测试确认不同的单值 `X-Forwarded-For` 被归一为不同 `REMOTE_ADDR`，避免所有客户端共享 Nginx 地址触发全局 30 次封锁。

## 备份与隔离恢复

备份由 PostgreSQL 负责人执行。根 `.gitignore` 必须保留 `backups/` 忽略规则（当前已配置；部署 write set 不修改根忽略文件）。最终演练目录固定为 `backups/phase1-rehearsal-final/`，最终证据固定为 `docs/evidence/postgres-restore-final.json`。创建受控目录后，在 Compose 容器内生成保留 owner 的 custom-format dump，再复制到宿主机，避免 PowerShell 二进制管道差异：

```powershell
$backupDir = New-Item -ItemType Directory -Force .\backups\phase1-rehearsal-final
icacls $backupDir.FullName /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)(F)"
docker compose exec -T db sh -ceu 'umask 077; pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-acl --file=/tmp/portal_phase1_final.dump'
docker compose cp db:/tmp/portal_phase1_final.dump .\backups\phase1-rehearsal-final\portal_phase1.dump
docker compose exec -T db rm -f /tmp/portal_phase1_final.dump
icacls .\backups\phase1-rehearsal-final\portal_phase1.dump /inheritance:r /grant:r "${env:USERNAME}:(F)"
```

恢复演练不得连接或覆盖 `portal_phase1`。首次演练库 `portal_phase1_restore` 必须原样保留；最终演练只允许新建 `portal_phase1_restore_final`。`createdb` 在目标已存在时必须失败，不使用 `--clean`、`dropdb` 或自动覆盖：

```powershell
docker compose cp .\backups\phase1-rehearsal-final\portal_phase1.dump db:/tmp/portal_phase1_final.dump
docker compose exec -T db sh -ceu 'createdb -U "$POSTGRES_USER" --owner="$POSTGRES_USER" portal_phase1_restore_final'
docker compose exec -T db sh -ceu 'pg_restore --exit-on-error --no-acl -U "$POSTGRES_USER" -d portal_phase1_restore_final /tmp/portal_phase1_final.dump'
docker compose exec -T db sh -ceu 'psql -U "$POSTGRES_USER" -d portal_phase1_restore_final -c "SELECT 1;"'
docker compose exec -T db rm -f /tmp/portal_phase1_final.dump
```

应用环境文件始终保持 `PORTAL_DB_NAME=portal_phase1`，因此恢复演练不会切换运行中的应用。`portal_phase1_restore` 与 `portal_phase1_restore_final` 都保留，除非 PostgreSQL 负责人另行明确决定；不要自动删除或覆盖任何数据库。

### Windows 手工 PG：密码、owner 与恢复对比

下列方法只启动一次性 PostgreSQL 客户端容器，通过 `host.docker.internal:55438` 连接手工验证 PG；不启动、停止或修改其容器配置。密码只保存在当前 PowerShell 进程环境中，不打印到终端。由 PostgreSQL 负责人逐步执行：

```powershell
function Read-EnvFile([string]$Path) {
    $result = @{}
    foreach ($line in [IO.File]::ReadAllLines((Resolve-Path $Path))) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $result[$Matches[1]] = $Matches[2] }
    }
    return $result
}

$settings = Read-EnvFile .runtime\validation.env
$dbUser = $settings.PORTAL_DB_USER
$env:PGPASSWORD = $settings.PORTAL_DB_PASSWORD
$backupDir = (New-Item -ItemType Directory -Force .\backups\phase1-rehearsal-final).FullName
icacls $backupDir /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)(F)"
$mount = "type=bind,source=$backupDir,target=/backup"
$image = 'postgres:17.5@sha256:aadf2c0696f5ef357aa7a68da995137f0cf17bad0bf6e1f17de06ae5c769b302'
$client = @('run', '--rm', '--env', 'PGPASSWORD', '--mount', $mount, $image)

try {
    & docker @client pg_dump --host=host.docker.internal --port=55438 --username=$dbUser --dbname=portal_phase1 --format=custom --no-acl --file=/backup/portal_phase1.dump
    if ($LASTEXITCODE) { throw 'pg_dump 失败。' }

    & docker @client createdb --host=host.docker.internal --port=55438 --username=$dbUser --owner=$dbUser portal_phase1_restore_final
    if ($LASTEXITCODE) { throw '隔离库创建失败；若已存在，请人工确认，禁止覆盖。' }

    & docker @client pg_restore --host=host.docker.internal --port=55438 --username=$dbUser --dbname=portal_phase1_restore_final --exit-on-error --no-acl /backup/portal_phase1.dump
    if ($LASTEXITCODE) { throw 'pg_restore 失败。' }

    $ownerSql = "SELECT n.nspname || '|' || c.relname || '|' || c.relkind || '|' || pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','S','v','m') ORDER BY 1;"
    $sourceOwners = & docker @client psql --host=host.docker.internal --port=55438 --username=$dbUser --dbname=portal_phase1 --tuples-only --no-align --command=$ownerSql
    if ($LASTEXITCODE) { throw '源库 owner 清单读取失败。' }
    $restoreOwners = & docker @client psql --host=host.docker.internal --port=55438 --username=$dbUser --dbname=portal_phase1_restore_final --tuples-only --no-align --command=$ownerSql
    if ($LASTEXITCODE) { throw '恢复库 owner 清单读取失败。' }

    $differences = Compare-Object $sourceOwners $restoreOwners
    if ($differences) { $differences; throw '源库与恢复库 owner 清单不一致。' }
    Write-Host '恢复完成，源库与隔离恢复库 owner 清单一致。'
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
```

owner 清单一致只证明对象及所有者恢复一致；仍应由业务负责人补充固定数据量、关键记录和登录只读验证，并把最终结果写入 `docs/evidence/postgres-restore-final.json`。整个过程不改变应用的 `PORTAL_DB_NAME=portal_phase1`，也不删除 `portal_phase1_restore` 或 `portal_phase1_restore_final`。
