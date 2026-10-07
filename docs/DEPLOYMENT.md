# 部署与运维

## 商机功能升级检查清单

适用于已有商机数据的环境，尤其是从迁移 `0028` 之前升级的数据库。迁移只增加分组字段，不会自动合并历史项目；原文未变化的重复采集也不能替代回填。

1. 确认目标仓库、数据库、环境文件及服务归属，停止该环境的 Web 服务和采集消费者，确认没有未收尾的活动采集批次；不要把其他环境的账号或数据库复制过来。
2. 按该环境既有备份流程备份数据库及私有配置，保留可恢复副本。备份和配置不得加入 Git。
3. 同步锁定依赖或构建更新后的部署镜像。此次公开 PDF 提取依赖 `pypdf`，已有依赖复用模式不会补装新依赖。
4. 使用目标环境自己的配置，依次执行 `migrate --noinput`、`group_tender_projects` 和 `check`。历史分组回填是旧库升级的必要步骤，不是一次新的联网采集；不会删除公告或重写采集时间。
5. 重新执行 `group_tender_projects` 应报告 `Updated 0 project grouping keys`。核对原始公告数和版本数未减少、同编号/采购单位/标包的项目展示合并、个人标记保留，再恢复服务与原有采集策略。

Windows 本地环境在仓库根目录执行以下命令；执行前必须完成停服和备份。管理命令模式不会启动消费者或 Web 服务，也不会修改保存的采集开关：

```powershell
uv sync --frozen
.\scripts\start-local.ps1 -UseExistingDependencies -DisableTenderUpdates -ManagementCommand @('migrate', '--noinput')
.\scripts\start-local.ps1 -UseExistingDependencies -DisableTenderUpdates -ManagementCommand group_tender_projects
.\scripts\start-local.ps1 -UseExistingDependencies -DisableTenderUpdates -ManagementCommand group_tender_projects
.\scripts\start-local.ps1 -UseExistingDependencies -DisableTenderUpdates -ManagementCommand check
```

检查通过后按 README 的本地启动流程重新构建并启动前端和 Web。生产环境须使用原部署相同的 Compose 项目与环境文件，在数据库就绪、业务写入停止且镜像已更新的情况下，通过 `docker compose run --rm --no-deps backend python manage.py <管理命令>` 执行同样步骤；不要使用本地启动脚本操作生产库。

任一步失败则保持停服并检查原因，不通过清库或覆盖旧版本绕过错误。分组维护命令不是整库事务；已完成部分可保留后重跑，需整体回退时使用升级前备份。

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

迁移由独立 `migrate` 一次性服务执行，Web 等待其成功；Web 副本重启不执行迁移。静态资源在镜像构建时生成。初始管理员必须通过交互命令创建，不存在默认账号或默认密码：

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

## 2026-10 云发布候选：完整服务与门禁

本节补充现有部署，不将上述历史演练视为当前 Agent/0034 全量生产验收。生产候选需要同时验收功能、隔离、恢复与容量；配置检查和镜像构建成功均不表示已批准上线。规划约束见 `项目规划/Agent平台/2026-09-30-agent-platform-detailed-design.md`。

### Portal 与领域 Worker

现有 Compose 保持 Agent 关闭时可独立运行。`db → migrate → backend → hr-worker` 是基础链；产品、招标、模型网关分别以 `product`、`tender`、`models` profile 启用。工程 profile 为 `engineering`，只运行既有工程任务：成本 CLI/受信 Python 由工程负责人交付，配置容器内绝对路径；平台不制造成本算法或工程助手。

工程启用时设置 `PORTAL_ENGINEERING_ENABLED=1`、`PORTAL_ENGINEERING_PYTHON`、`PORTAL_ENGINEERING_COST_CLI`，并将已准备的受信 Linux 运行时目录通过 `ENGINEERING_RUNTIME_DIR` 只读挂载到 `/opt/engineering`。空/不存在宿主目录不能被 Compose 自动创建冒充交付。不要把 Windows 虚拟环境复制到 Linux 容器。工程文件使用新增 `engineering_private_data`；产品、HR、招标仍各自使用私有卷。

新版本发布先停止新 Agent 派发及相关长任务领取，确认在途任务排空或按守卫收尾，记录维护窗口和恢复点。只执行一次 `docker compose run --rm migrate`，成功后用 `docker compose up --no-deps -d backend` 及已启用 Worker 更新应用（数据库需预先健康）；不得同时运行多个发布进程。首次 `compose up` 使用一次性服务依赖；升级现有已完成服务时必须显式运行该步骤，不能以旧容器的成功退出码代替新迁移。Web 启动不再迁移，镜像静态文件一致。回退首先关闭 Agent 新入口，保留原业务与历史；不自动降迁移、删表或删除已关联任务。

`deploy/nginx.conf.example` 保留默认64KiB，产品来源/Agent附件21MiB、HR批次/工程任务41MiB、JD/台账CSV导入3MiB；仅精确管理路由 `/admin/portal/gatewaymodel/import/` 使用128KiB请求上限，为允许的64KiB配置JSON文件保留multipart/CSRF余量，其它管理路由不放宽。应用仍执行每文件/数量限制。部署时替换域名、证书和可信代理地址，验证完整multipart上限、超限413、CSRF及撤权下载。长解析上传拥有有限180秒代理超时；其他请求仍为60秒。不能只在直连Web端测试上传。

### 正式三件套：Office 渲染是独立部署阻塞

**当前单 Linux Compose 不能承诺正式三件套已可运行。** 冻结 PACK 的 `scripts/office_render.py` 使用 `win32com.client` 与 Microsoft Word/PowerPoint COM，`document-runtime.txt` 仅在 Windows 安装 pywin32；Debian Docker 镜像安装的是 Chromium、字体和 OOXML 文档库，没有 Microsoft Office。Mermaid 真实绘图及 DOCX/PPTX 结构生成成功不等于 Word 目录/分页和 PowerPoint 逐页渲染成功。不能通过设置一个启用开关、复用本机 Word 成功报告或安装 LibreOffice 宣称当前冻结模板与渲染脚本已获支持。

`PORTAL_PRODUCT_FORMAL_RELEASE_ENABLED=1` 与 `PORTAL_PRODUCT_OFFICE_RENDER_ENABLED=1` 现在分别触发 readiness Office 依赖检查，不能依赖是否开启模型调用。正式发布要求 Office 开关启用（`product_formal_office_enabled`）；任一开关启用都检查受信文档解释器（`product_document_runtime`）和本地平台（`product_office_platform`），当前 COM-only renderer 在 Linux 或未知平台明确报告配置阻塞。Windows 文件/Python 路径存在也仅是配置条件，真实 Word/PPT 渲染、授权、执行路由与目标运行方式的支持性仍是 `product_office_acceptance` 独立门禁。未完成这些条件时关闭正式能力，保留已授权的原业务与诚实标注的结构生成结果，不增加蓝图以外的成稿人工审批。仅启用模型调用、两项正式/Office 开关均关闭时，Linux 结构生成不因缺少 Office 被阻塞。

与现有架构相容的候选路径是将**已有领域 `run_product_worker`** 放到专属受信 Windows 文档执行环境，Linux 继续运行 Portal、业务 PostgreSQL、网关与官方 Native Runtime。该路径只是部署方案，尚未云端验收；不增加通用 Harness/Runtime，也不修改冻结 PACK：

1. Windows 节点使用相同发布版本、锁定 Python/Node 依赖、经过核验的 Mermaid bundle/manifest，以及获准安装和授权的 Word、PowerPoint 与公司字体。不能复制 Linux venv 到 Windows。执行者使用专属工作身份和受控桌面/用户配置，Office 工作串行化；只创建、关闭自己的 Office 实例，不能清理用户已有会话。
2. Worker 连接**同一 Portal PostgreSQL**和相同任务/账号/根守卫，不克隆一个业务库或补另一套排队系统。云端私有 DNS、可达性、数据库身份与 TLS 证书校验必须实际验证；Compose 内部名 `db` 不能直接当作跨主机地址。凭据通过受控部署配置提供，不写进 CLI 或验收输出。模型出口仍走获准网关、权限与计量守卫。
3. Portal、Native Runtime 与该 Worker 的产品私有根指向**同一持久文件内容**。跨系统可评估受控共享文件系统/SMB 等方案，但要实测 Windows ACL、Linux UID/GID、相对路径、文件锁、原子替换和故障恢复；本机 Compose named volume 不自动成为跨主机共享卷，现有路径存储也不冒称支持 S3。仅共享必要产品目录，不暴露宿主根、用户桌面或其它员工材料。
4. 必须补齐部署中的执行能力路由和 Worker 身份/健康证据，防止同一正式任务被 Linux `product-worker` 领取并错误调用 COM。Linux 上不应仅因 Windows Worker 存在就将本地 Office 配置判为可用；Web/API 需要与领域执行者的能力配置对应，现有单 Compose 并没有提供这个跨主机能力证明。部署迁移时先停写/排空相关领取，再调整 Worker；保留输入、蓝图、章节与模板哈希绑定及撤权/取消/fence 检查。
5. 在目标运行方式中实际生成规定长度的两份 Word 与 PPT，刷新并核验 Word TOC/页数/全黑文本，使用冻结 PACK 的 PowerPoint renderer 对全部页面导出并核对。现有 PPT structural quality pass、`office_render=not_run` 不能替代这一步；需要自动编排真实 PPT 渲染、保存与该版本绑定的文件/页图摘要，并检验异常弹窗、超时、进程中断、撤权、多任务与恢复。验收证据记录真实 Office/操作系统/字体/脚本版本和输入/输出哈希，不成为用户下载前的第二次人工审批。

Windows 不自动消除支持性问题。Microsoft 明确说明[不支持无人值守、非交互组件的 Office server-side Automation](https://support.microsoft.com/en-us/visio/considerations-for-server-side-automation-of-office)，并指出交互桌面、并发、挂起与许可限制。把当前 COM 脚本放到 Windows SYSTEM 服务或非交互计划任务，或者只设置超时/串行化，不能据此承诺受支持的生产服务。若终态要求完全无人值守云部署，必须先确定真正受支持且获准的私有渲染服务/授权、数据边界及冻结模板一致性方案；未取得该能力前，正式三件套维持明确阻塞，不自动采购或外发资料，也不伪造 LibreOffice 兼容结论。

Word 生成链在冻结 PACK 输出之后、真实 Office 更新之前执行 `generated-ooxml-layout-v1` 规范化：仅为内容版本书签明确对应的正文标题设置 keepNext/keepLines，将纯 TOC 外层结束标记移入显式1pt、零段距段，并收紧其后无内容的分节段，关闭继承的绑定和网格吸附。原始模板、manifest、章节内容哈希、全部文字/字段/书签/图片及分节保持；其它 ZIP part（含图片、关系、样式）逐字节保持。遇到缺失/重复书签、不平衡字段、带正文的分节段或不支持的 TOC 边界必须失败，不能删页或删内容“修复”。质量报告与工件记录生成前/规范化后 SHA、内容保留摘要和改动数量；Office 随后重新更新 TOC、渲染并绑定最终文件 SHA。该规范化证据始终标注 `visual_review=not_run`，实际 Word 目录、空白页与标题/图片同行必须重新查看全部目标页，不能沿用旧版页图或将结构测试当作视觉通过。

### Agent：官方 Helm/Kubernetes 生产主路径

使用官方 `langchain/langgraph-cloud` chart **0.3.3** 和本仓库 `deploy/agent/helm-values.example.yaml`，生产需要 Kubernetes、原生 PostgreSQL/Redis、内部TLS入口、私有共享卷和有效Runtime许可。官方生产路径为[Helm/Kubernetes](https://docs.langchain.com/langsmith/deploy-standalone-server)，Compose/inmem开发恢复证据不能替代生产PG恢复。

`deploy/agent/Dockerfile` 继承官方生产 Agent Server 的API/队列入口，加载现有DeepAgents图、自定义认证和HTTP收尾入口，使用主依赖冻结锁及官方 `/api/constraints.txt`；不安装 `agent-runtime` 开发依赖组。构建时用 `deploy/agent/build-image.ps1 -BaseImage <稳定官方tag@sha256> -ImageTag <候选仓库tag>`，base必须为已核对的官方稳定Python3.13生产镜像摘要。依赖冲突应使构建失败并阻塞发布，不能覆盖/关闭许可或偷偷换Harness。推送与发布另按当前任务授权执行；本节命令不自动执行服务。

镜像认证配置必须与 `langgraph.json` 保持一致，禁止覆盖为noop；图、auth或HTTP配置变更后重新核对镜像。许可证必须实际支持本项目自定义认证及正式部署，不以普通模型key、开发key存在或SDK开源许可代替。无许可时记录 `BLOCKED_LICENSE`；不填写假key、绕过校验或自动购买服务。

将example镜像仓库/tag替换为经验证的不可变发布，并执行 `helm template portal-agent langchain/langgraph-cloud --version 0.3.3 -f <本地受控values>` 做离线审阅。API和queue采用同一镜像和根身份，最小副本数为1；禁止scale-to-zero。副本数、jobs/worker与600秒排空窗口是候选部署参数，需根据压力与最长在途调用验证后固定；不新建第二套通用调度器。

Secret分开保存，均不提交Git，不把真实值写入命令行日志：

| Secret | 契约 |
| --- | --- |
| `portal-agent-license` | 官方chart的 `api_key` / `langgraph_cloud_license_key`（所需有效授权）；不要再通过extraEnv/envFrom注入同名Runtime许可变量 |
| `portal-agent-postgres` | `postgres_connection_url`；数据库与Portal业务库、其他Runtime独立，生产TLS连接与备份 |
| `portal-agent-redis` | `redis_connection_url`；独立Redis库编号、认证/TLS与可恢复配置 |
| `portal-agent-application` | Portal DB配置、与Portal相同的签名SECRET_KEY、Agent服务token、允许列表、获准模型preset及必要网关身份；不含模型供应商凭据或Runtime许可变量 |
| `portal-agent-tls` | 内部域名有效证书及私钥；Portal信任其CA，不能关闭证书验证 |

官方chart 0.3.3管理 `POSTGRES_URI` / `REDIS_URI` 与许可注入（独立Docker文档称数据库变量为 `DATABASE_URI`，必须以最终官方镜像与固定chart兼容性验收为准）。已核对官方API/queue模板的连接Secret键；正式部署仍需渲染固定chart并验证双方实际注入，不沿用其他版本假定。许可证必需出网仅在获准出口开放，默认禁发外部追踪（`LANGSMITH_TRACING=false`、`LANGCHAIN_TRACING_V2=false`）和内容日志；网络验证需证明附件/提示/工具正文未外发至遥测服务。

`internal-tls.yaml` 由**私有**Ingress controller处理，域名 `agent.portal.internal` 不对公网路由；Runtime原生端口仅ClusterIP。Portal配置 `PORTAL_AGENT_RUNTIME_URL=https://agent.portal.internal`，同地址加入允许列表，设置 `PORTAL_AGENT_DEPLOYMENT_MODE=helm_kubernetes`。只在本机开发允许loopback明文HTTP，不把 `http://agent-runtime:2024` 填成生产地址。Bearer服务token与签名binding由现有代码逐请求校验；TLS不会替代对象授权。`network-policy.yaml` 限制入口，只允许同namespace协调与指定私有Ingress namespace；需部署有效CNI并按实际namespace调整。出口按数据库/Redis/Portal网关/获准许可验证服务另设置明确规则，不能因DNS或证书问题改成全网开放。

Portal Web、产品/HR Worker、Runtime API/queue需看到同一产品/HR私有文件；example引用 `portal-product-private` / `portal-hr-private` PVC，PVC由集群存储负责人按现有数据导入与权限提供。多主机使用满足访问模式的共享文件系统/RWX，不能假定每节点local卷自动一致；不直接将现有路径API声称为S3支持。Runtime只挂需要的两类私有卷，不挂宿主根目录、平台凭据目录或员工私人目录。Portal候选镜像与Runtime统一非root UID/GID10001，chart使用fsGroup10001；既有卷可能属于旧镜像UID，升级前在备份与维护窗口内显式核对/迁移卷权限，不能直接重启后发现无权读写。官方镜像继承的API/队列入口及健康检查在该非root身份下必须真实测试，若需其受信目录权限只能在构建阶段定点调整，不改为生产root运行。

### 分层检查与可发布证据

`check_production_readiness` 在Agent/产品生成/知识服务/工程显式启用后追加相关身份、预设、受信本地运行时/目录检查；无功能启用不强迫安装外部工程能力。输出仍为 `configuration_only_no_network_calls`，`release_approved=false`。Agent许可、PG/Redis恢复、AG-15/16、真实产品质量和工程准确性均保持独立external_gate；目录存在或密钥配置绝不当作现场测试通过。

模型网关配置由 readiness 与真实请求构造器共用同一个纯校验函数：地址必须符合既有 HTTPS 或明确私有本地 HTTP 规则、准确匹配允许名单，且服务令牌符合既有长度/字符条件。允许名单不能让无协议/主机、嵌入凭据、查询/片段或非法令牌变成“配置通过”。这一步不创建网络客户端、不检查供应商连通性，也不证明真实模型质量；实际调用仍保留 `unconfigured`/503 错误契约。

正式启用前在相同发布镜像/拓扑独立证明：AG-15/16全部默认工具与后端正负例及根累计终止；两个不同获准模型真实并行且主运行继续工作；断线更正/取消/撤权、未知调用/副作用对账、强杀与跨副本恢复；本人精确财务发布及GM只读隔离；HR长期有效且旧失效/已删除资料不恢复；真实50k/70k三件套、PPT/Word渲染与内容核对。人工签收不成为产品生成/下载二次审批。只在获准试点与模型范围用真实资料，不扩大全库外发。

恢复点覆盖Portal DB、原生Runtime DB/checkpoint/store、全部私有文件卷以及删除/授权状态；先排空或停止写入形成一致快照，记录清单/摘要/恢复时间，再在隔离空白环境恢复并验权。旧Phase1仅数据库或单目录备份演练不证明当前所有存储的一致性。备份恢复不得重新开放已删除/撤权对象。容量测试事先明确用户数/任务组合/目标p95/p99/错误率/排队上限及RPO/RTO，覆盖渐增、突发、长时与故障，不以单元测试数量宣称“全量压力通过”。

### 当前候选的离线验证（2026-10-07）

Portal Web 候选维持4个执行线程，显式设置 `--connection-limit=512` 和 Linux `--asyncore-use-poll`，Compose为其配置4096个文件描述符预算。连接预算与执行并发不同；512不是吞吐或排队承诺，数据库及私有文件并发另行验收。原Waitress默认100连接在100会话准备阶段已耗尽，不能沿用默认值声称100会话可用。[Waitress参数说明](https://docs.pylonsproject.org/projects/waitress/en/latest/arguments.html)解释每连接最多使用多个文件描述符，故需同时限制连接并核实操作系统资源。Windows本机测试仅使用select，不能替代Linux poll/代理/云机器容量。

数据库镜像已固定 PostgreSQL 17.11 的官方多架构摘要 `sha256:ae69c452f483507a6b99fb654cf93aad7fe156ffd2c56247707eef4e36d3c12b`；此前17.5缺少后续安全修复。摘要实际读取Docker官方registry，17.11修复范围见[PostgreSQL发布说明](https://www.postgresql.org/docs/17/release-17-11.html)。本轮未启动或升级任何现有生产数据库；同主版本补丁仍须在实际发布镜像检查迁移、备份与扩展兼容性。

`deploy/validate_contract.py` 校验迁移/Web分离、工程共享卷、上传路由上限、图/auth/HTTP与镜像配置一致、私有Runtime与外部PG/Redis。readiness及部署合同定向测试16项通过；该轮不创建/访问业务数据库。

后续 Office 门禁复验中，readiness 的17项测试全部通过（原10项与新增7项）：分别覆盖仅 Office、仅正式发布、两项启用但 Linux、未知平台、Windows 路径不能代替实际验收，以及普通结构生成/关闭功能不被误阻塞。该复验使用 SimpleTestCase，跳过数据库创建，不执行 COM 或目标渲染；真实目标 Office 部署仍须通过上述独立门禁。

再后续网关配置与管理导入边界复验共33项通过（readiness18、部署合同10、原供应商/网关构造器5）：覆盖共享校验与真实请求构造器的有效/无效配置矩阵，以及64KiB合法JSON经真实multipart/CSRF编码后超64KiB但低于精确128KiB限额；缺失、过小、过宽管理例外及放宽全局限额均被负测拒绝。该轮跳过数据库创建且未发HTTP；仍未执行真实Nginx语法/代理测试。

使用Helm v3.19.0实际渲染并lint官方 `langgraph-cloud-0.3.3.tgz`（SHA256 `5b8c859f5d7dfa699a31d9ebc252be6b65f977df56a8bec85ac82968d44344d6`）；lint为1 chart/0失败。`deploy/agent/render.ps1` 是复验入口，`validate_rendered.py` 检查API/queue的实际Secret引用、分离队列、私有Service、排空窗口、非root身份与共享PVC。渲染只使用示例镜像占位，不向集群应用。图/认证仍须在真实获许可镜像启动后验证。

未执行的候选出口包括Nginx实际 `-t` /代理HTTP、大镜像构建、原生Runtime有效许可、非rootAPI/queue启动、生产持久化/排空和云环境验收；这些结果不能被上述离线PASS替代。真实发布必须替换镜像占位、提供Secret/PVC/TLS及所有现场证据。
