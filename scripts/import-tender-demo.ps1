[CmdletBinding()]
param(
    [string]$InputFile,
    [switch]$CheckOnly,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot 'start-local.ps1'
$localEnv = Join-Path $root '.runtime/local.env'

if (-not (Test-Path -LiteralPath $localEnv -PathType Leaf)) {
    throw '尚未初始化本地环境。请先按 README 的本地启动步骤运行 scripts/start-local.ps1 -DisableTenderUpdates，完成依赖与本地配置后停止服务，再执行本脚本。不会导入或创建账号密码。'
}
if ([string]::IsNullOrWhiteSpace($InputFile)) {
    $InputFile = Join-Path $root 'data/demo/tender-public.json.gz'
}
if (-not (Test-Path -LiteralPath $InputFile -PathType Leaf)) {
    throw "未找到公开商机演示包：$InputFile"
}
$inputPath = (Resolve-Path -LiteralPath $InputFile).ProviderPath

# Validation is read-only and runs before migrations or data import. Reuse the
# same private local environment handling as the standard launcher.
& $launcher -UseExistingDependencies -DisableTenderUpdates -ManagementCommand @(
    'import_tender_demo', '--input', $inputPath, '--check'
)
if ($CheckOnly) {
    Write-Host '公开商机演示包验证完成，未迁移或写入数据库，未启动服务。'
    return
}

& $launcher -UseExistingDependencies -DisableTenderUpdates -ManagementCommand @('migrate', '--noinput')
& $launcher -UseExistingDependencies -DisableTenderUpdates -ManagementCommand @(
    'import_tender_demo', '--input', $inputPath
)
if ($NoStart) {
    Write-Host '公开商机演示包导入完成。使用 scripts/start-local.ps1 -UseExistingDependencies -DisableTenderUpdates 启动演示。'
    return
}

Write-Host '公开商机演示包导入完成，正在启动本地演示；本次进程不启用联网采集。'
& $launcher -UseExistingDependencies -DisableTenderUpdates
