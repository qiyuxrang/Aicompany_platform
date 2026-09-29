[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw '请先在项目根目录运行 uv sync --frozen。'
}
$node = (Get-Command node -ErrorAction Stop).Source
$frontend = Join-Path $root 'frontend'
$frontendTools = @{
    test = Join-Path $frontend 'node_modules/vitest/vitest.mjs'
    typecheck = Join-Path $frontend 'node_modules/typescript/bin/tsc'
    build = Join-Path $frontend 'node_modules/vite/bin/vite.js'
}
foreach ($tool in $frontendTools.Values) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        throw '前端依赖未安装完整，请先在 frontend 运行 pnpm install --frozen-lockfile。'
    }
}
$runName = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$output = Join-Path $root ".runtime/preproduction-checks/$runName"
New-Item -ItemType Directory -Path $output | Out-Null

function Invoke-Check([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory) {
    $log = Join-Path $output "$Name.log"
    Write-Host "[$Name] 开始"
    Push-Location $WorkingDirectory
    try {
        & $Executable @Arguments *> $log
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Get-Content -LiteralPath $log -Tail 12
    if ($code -ne 0) {
        throw "$Name 失败（退出码 $code）；日志：$log"
    }
    Write-Host "[$Name] 通过"
}

$originalEnvironment = @{}
foreach ($entry in Get-ChildItem Env:) { $originalEnvironment[$entry.Name] = $entry.Value }
$allowedEnvironment = @('SystemRoot', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE',
    'LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA', 'ALLUSERSPROFILE',
    'HOMEDRIVE', 'HOMEPATH', 'OS', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'COREPACK_HOME', 'PNPM_HOME')
try {
    foreach ($name in $originalEnvironment.Keys) {
        if ($name -notin $allowedEnvironment) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    }
    $env:COREPACK_ENABLE_NETWORK = '0'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PORTAL_FRONTEND_ENV_DIR = Join-Path $output 'empty-env'
    New-Item -ItemType Directory -Path $env:PORTAL_FRONTEND_ENV_DIR | Out-Null
    Invoke-Check 'backend' $python @('qa/run_prd_tests.py', '--fast-passwords', 'portal.tests') $root
    Invoke-Check 'schema' $python @('qa/run_prd_tests.py', '--check-schema') $root
    Invoke-Check 'gateway' $python @('-m', 'unittest', 'discover', '-s', 'model_gateway/tests') $root
    Invoke-Check 'frontend' $node @($frontendTools.test, 'run') $frontend
    Invoke-Check 'typecheck' $node @($frontendTools.typecheck, '--noEmit') $frontend
    Invoke-Check 'build' $node @($frontendTools.build, 'build', '--outDir', (Join-Path $output 'frontend-dist')) $frontend
} finally {
    foreach ($entry in Get-ChildItem Env:) {
        if (-not $originalEnvironment.ContainsKey($entry.Name)) {
            [Environment]::SetEnvironmentVariable($entry.Name, $null, 'Process')
        }
    }
    foreach ($name in $originalEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], 'Process')
    }
}
Write-Host "部署前代码检查通过。日志与隔离构建：$output"
Write-Host '此结果不包含真实模型质量、人工签收或生产部署验收。'
