[CmdletBinding()]
param(
    [string]$AppEnvFile = ".env",
    [string]$DbEnvFile = ".runtime/postgres.env"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:APP_ENV_FILE = (Resolve-Path -LiteralPath (Join-Path $root $AppEnvFile)).Path
$env:DB_ENV_FILE = (Resolve-Path -LiteralPath (Join-Path $root $DbEnvFile)).Path

Push-Location $root
try {
    & docker compose stop --timeout 30
    if ($LASTEXITCODE -ne 0) { throw "Compose 停止失败。" }
} finally {
    Pop-Location
}
