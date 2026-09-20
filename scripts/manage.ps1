[CmdletBinding()]
param(
    [string]$AppEnvFile = ".env",
    [string]$DbEnvFile = ".runtime/postgres.env",
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$ManagementArguments
)

$ErrorActionPreference = "Stop"
if (-not $ManagementArguments) {
    throw "请提供 Django 管理命令，例如 bootstrap_admin admin。"
}

$root = Split-Path -Parent $PSScriptRoot
$env:APP_ENV_FILE = (Resolve-Path -LiteralPath (Join-Path $root $AppEnvFile)).Path
$env:DB_ENV_FILE = (Resolve-Path -LiteralPath (Join-Path $root $DbEnvFile)).Path

Push-Location $root
try {
    & docker compose exec backend python manage.py @ManagementArguments
    if ($LASTEXITCODE -ne 0) { throw "Django 管理命令执行失败。" }
} finally {
    Pop-Location
}
