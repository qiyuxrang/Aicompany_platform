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
} finally { Pop-Location }
