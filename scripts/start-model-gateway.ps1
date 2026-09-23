[CmdletBinding()]
param([int]$Port = 18410)

$ErrorActionPreference = "Stop"
if ($Port -lt 1024 -or $Port -gt 65535) { throw "网关端口必须在1024至65535之间。" }
$root = Split-Path -Parent $PSScriptRoot
$environmentFile = Join-Path $root ".runtime/model-gateway.env"
if (-not (Test-Path -LiteralPath $environmentFile)) {
    throw "请先按 docs/MODEL_GATEWAY.md 配置 .runtime/model-gateway.env，不要把密钥写入源码。"
}
$tokenLine = Get-Content -LiteralPath $environmentFile | Where-Object { $_ -match '^MODEL_GATEWAY_SERVICE_TOKEN=' } | Select-Object -First 1
if (-not $tokenLine -or $tokenLine.Substring("MODEL_GATEWAY_SERVICE_TOKEN=".Length).Length -lt 40) {
    throw "请配置至少40字符的独立网关服务令牌。"
}
Push-Location $root
try {
    & uv run --frozen --env-file $environmentFile python -m uvicorn model_gateway.app:app --host 127.0.0.1 --port $Port --workers 1 --limit-concurrency 16 --timeout-keep-alive 5 --no-access-log
    if ($LASTEXITCODE -ne 0) { throw "模型网关启动失败。" }
} finally {
    Pop-Location
}
