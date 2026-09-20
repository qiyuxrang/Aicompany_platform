[CmdletBinding()]
param(
    [string]$AppEnvFile = ".env",
    [string]$DbEnvFile = ".runtime/postgres.env",
    [ValidateRange(1, 65535)]
    [int]$HttpPort = 8100,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appEnv = (Resolve-Path -LiteralPath (Join-Path $root $AppEnvFile)).Path
$dbEnv = (Resolve-Path -LiteralPath (Join-Path $root $DbEnvFile)).Path
$previousAppEnv = $env:APP_ENV_FILE
$previousDbEnv = $env:DB_ENV_FILE
$previousHttpPort = $env:PORTAL_HTTP_PORT

try {
    $env:APP_ENV_FILE = $appEnv
    $env:DB_ENV_FILE = $dbEnv
    $env:PORTAL_HTTP_PORT = $HttpPort
    Push-Location $root

    & docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "Compose 配置校验失败。" }

    $arguments = @("compose", "up", "--detach", "--wait", "--wait-timeout", "180")
    if (-not $NoBuild) { $arguments += "--build" }
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { throw "Compose 启动失败，请查看 docker compose logs。" }
} finally {
    Pop-Location
    $env:APP_ENV_FILE = $previousAppEnv
    $env:DB_ENV_FILE = $previousDbEnv
    $env:PORTAL_HTTP_PORT = $previousHttpPort
}
