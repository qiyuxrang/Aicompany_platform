[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $python = '.runtime/product-parser-python/Scripts/python.exe'
    if (-not (Test-Path $python)) {
        & uv venv --python 3.12 .runtime/product-parser-python
        if ($LASTEXITCODE -ne 0) { throw '无法创建独立资料解析环境。' }
    }
    & uv pip sync --python $python backend/portal/source_parsers/requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '资料解析依赖安装失败。' }
    & $python -c "import openpyxl, pypdfium2, xlrd; from rapidocr_onnxruntime import RapidOCR; RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1); print('本地资料解析与中文 OCR 已就绪。')"
    if ($LASTEXITCODE -ne 0) { throw '本地 OCR 自检失败。' }
    $documentPython = '.runtime/product-documents-python/Scripts/python.exe'
    if (-not (Test-Path $documentPython)) {
        & uv venv --python 3.12 .runtime/product-documents-python
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create isolated document runtime.' }
    }
    & uv pip sync --python $documentPython backend/portal/product_assets/document-runtime.txt
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install document runtime dependencies.' }
    Push-Location 'backend/portal/product_assets/mermaid-runtime'
    try {
        & corepack pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw 'Unable to install locked Mermaid runtime dependencies.' }
    } finally { Pop-Location }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js is required for Mermaid rendering.' }
    $browser = @(
        "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $browser) { throw 'Microsoft Edge or Google Chrome is required for Mermaid rendering.' }
} finally { Pop-Location }
