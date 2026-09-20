[CmdletBinding()]
param()

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

Push-Location $root
try {
    & uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw "uv sync 失败。" }
    Push-Location (Join-Path $root "frontend")
    try {
        & corepack pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw "pnpm install 失败。" }
        & corepack pnpm build
        if ($LASTEXITCODE -ne 0) { throw "前端构建失败。" }
    } finally {
        Pop-Location
    }
    & uv run --frozen --env-file .runtime/local.env python backend/manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw "本地数据库迁移失败。" }
    & uv run --frozen --env-file .runtime/local.env python backend/manage.py seed_portal
    if ($LASTEXITCODE -ne 0) { throw "本地基础角色和模块初始化失败。" }
    & uv run --frozen --env-file .runtime/local.env python backend/manage.py collectstatic --noinput
    if ($LASTEXITCODE -ne 0) { throw "本地后台静态资源收集失败。" }
    & uv run --frozen --env-file .runtime/local.env python backend/manage.py check
    if ($LASTEXITCODE -ne 0) { throw "Django 检查失败。" }

    Write-Host "本地服务监听 http://127.0.0.1:8100；按 Ctrl+C 正常停止。"
    Push-Location (Join-Path $root "backend")
    try {
        & uv run --project .. --frozen --env-file ../.runtime/local.env waitress-serve --listen=127.0.0.1:8100 --threads=4 config.wsgi:application
    } finally {
        Pop-Location
    }
} finally {
    Pop-Location
}
