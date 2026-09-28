[CmdletBinding()]
param(
    [switch]$UseExistingDependencies,
    [string[]]$ManagementCommand
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $root ".runtime"
$localEnv = Join-Path $runtimeDirectory "local.env"

function Set-EnvValue([string]$content, [string]$name, [string]$value) {
    $pattern = "(?m)^$([Regex]::Escape($name))=.*$"
    return [Regex]::Replace($content, $pattern, "$name=$value")
}

if (-not (Test-Path -LiteralPath $localEnv)) {
    New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $secretKey = [Convert]::ToHexString($bytes).ToLowerInvariant()
    $content = [IO.File]::ReadAllText((Join-Path $root ".env.example"))
    $content = Set-EnvValue $content "PORTAL_DEBUG" "1"
    $content = Set-EnvValue $content "PORTAL_SECRET_KEY" $secretKey
    $content = Set-EnvValue $content "PORTAL_HTTPS" "0"
    $content = Set-EnvValue $content "PORTAL_BEHIND_PROXY" "0"
    $content = Set-EnvValue $content "PORTAL_SQLITE_PATH" ".runtime/portal.sqlite3"
    $content = Set-EnvValue $content "PORTAL_DB_NAME" ""
    $content = Set-EnvValue $content "PORTAL_DB_USER" ""
    $content = Set-EnvValue $content "PORTAL_DB_PASSWORD" ""
    [IO.File]::WriteAllText($localEnv, $content, [Text.UTF8Encoding]::new($false))

    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $acl = [Security.AccessControl.FileSecurity]::new()
        $acl.SetOwner($identity.User)
        $acl.SetAccessRuleProtection($true, $false)
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $identity.User,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $localEnv -AclObject $acl
    } catch {
        Remove-Item -LiteralPath $localEnv -Force
        throw "无法限制 .runtime/local.env 的访问权限，已删除该文件。"
    }
}

$values = @{}
foreach ($line in [IO.File]::ReadAllLines($localEnv)) {
    if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        $values[$Matches[1]] = $Matches[2]
    }
}

if ($values.PORTAL_DEBUG -ne "1" -or $values.PORTAL_HTTPS -ne "0" -or $values.PORTAL_DB_NAME) {
    throw ".runtime/local.env 必须显式使用 PORTAL_DEBUG=1、PORTAL_HTTPS=0 和空 PORTAL_DB_NAME。"
}
if (($values.PORTAL_SECRET_KEY | Out-String).Trim().Length -lt 40) {
    throw ".runtime/local.env 的 PORTAL_SECRET_KEY 至少需要 40 个字符。"
}

$python = Join-Path $root '.venv/Scripts/python.exe'
$values['PYTHONIOENCODING'] = 'utf-8'
$savedEnvironment = @{}
$tenderConsumer = $null
$environmentNames = @($values.Keys) + @(Get-ChildItem Env: | Where-Object {
    $_.Name.StartsWith('PORTAL_') -or $_.Name -eq 'DJANGO_SETTINGS_MODULE'
} | ForEach-Object { $_.Name })
foreach ($name in ($environmentNames | Select-Object -Unique)) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}
Push-Location $root
try {
    if (-not $UseExistingDependencies) {
        & uv sync --frozen
        if ($LASTEXITCODE -ne 0) { throw "uv sync 失败。" }
    }
    if (-not (Test-Path -LiteralPath $python)) {
        throw '未找到 .venv/Scripts/python.exe，请先使用 uv sync --frozen 安装依赖。'
    }
    foreach ($name in $values.Keys) {
        [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
    }
    if ($ManagementCommand) {
        & $python backend/manage.py @ManagementCommand
        if ($LASTEXITCODE -ne 0) { throw '本地管理命令执行失败。' }
        return
    }
    Push-Location (Join-Path $root "frontend")
    try {
        if ($UseExistingDependencies) {
            if (-not (Test-Path node_modules/typescript/bin/tsc) -or
                -not (Test-Path node_modules/vite/bin/vite.js)) {
                throw '缺少前端依赖，请先安装锁定的 frontend/node_modules。'
            }
            & node node_modules/typescript/bin/tsc --noEmit
            if ($LASTEXITCODE -ne 0) { throw '前端类型检查失败。' }
            & node node_modules/vite/bin/vite.js build
        } else {
            & corepack pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { throw "pnpm install 失败。" }
            & corepack pnpm build
        }
        if ($LASTEXITCODE -ne 0) { throw "前端构建失败。" }
    } finally {
        Pop-Location
    }
    if ($values.PORTAL_PRODUCT_P1_ENABLED -eq "1" -and -not $UseExistingDependencies) {
        & (Join-Path $PSScriptRoot 'setup-product-intake.ps1')
    }
    & $python backend/manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw "本地数据库迁移失败。" }
    & $python backend/manage.py seed_portal
    if ($LASTEXITCODE -ne 0) { throw "本地基础角色和模块初始化失败。" }
    & $python backend/manage.py collectstatic --noinput
    if ($LASTEXITCODE -ne 0) { throw "本地后台静态资源收集失败。" }
    & $python backend/manage.py check
    if ($LASTEXITCODE -ne 0) { throw "Django 检查失败。" }

    if (Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue) {
        throw '8100 端口已有服务，请先停止原本地服务再启动。'
    }
    if ($values.PORTAL_TENDER_INGESTION_ENABLED -eq '1' -and
        ($values.PORTAL_TENDER_SCHEDULE_ENABLED -eq '1' -or $values.PORTAL_TENDER_MANUAL_REFRESH_ENABLED -eq '1')) {
        $tenderConsumer = Start-Process -FilePath $python -ArgumentList @('manage.py', 'run_tender_consumer') `
            -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $runtimeDirectory 'tender-consumer.log') `
            -RedirectStandardError (Join-Path $runtimeDirectory 'tender-consumer-error.log')
        $tenderConsumer.Id | Set-Content (Join-Path $runtimeDirectory 'tender-consumer.pid')
        Write-Host '公开商机后台更新已启动，运行状态可在全国商机看板查看。'
    }

    Write-Host "本地服务监听 http://127.0.0.1:8100；按 Ctrl+C 正常停止。"
    Push-Location (Join-Path $root "backend")
    try {
        & $python -m waitress --listen=127.0.0.1:8100 --threads=4 config.wsgi:application
        if ($LASTEXITCODE -ne 0) { throw '本地 Web 服务异常退出。' }
    } finally {
        Pop-Location
    }
} finally {
    if ($null -ne $tenderConsumer -and -not $tenderConsumer.HasExited) {
        # Windows virtualenv launchers can own a child Python process. Stop the
        # entire owned tree so a restart never leaves a second consumer behind.
        & taskkill.exe /PID $tenderConsumer.Id /T /F 2>$null | Out-Null
    }
    Pop-Location
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
}
