$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$docx = Join-Path $root '企业平台总体规划_SDD评审版.docx'
$pdf = Join-Path $PSScriptRoot '企业平台总体规划_SDD评审版.pdf'
$word = $null
$document = $null
$owned = $false
try {
    $word = New-Object -ComObject Word.Application
    if ($word.Documents.Count -ne 0) { throw '检测到非空Word实例，不修改用户文档。' }
    $owned = $true
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $document = $word.Documents.Open($docx, $false, $true, $false)
    $document.Fields.Update() | Out-Null
    $document.Repaginate()
    $pages = $document.ComputeStatistics(2)
    $document.ExportAsFixedFormat($pdf, 17)
    [pscustomobject]@{Renderer='Microsoft Word'; Pages=$pages; PDF=$pdf; ReadOnly=$true} | ConvertTo-Json
} finally {
    if ($document) { $document.Close(0); [Runtime.InteropServices.Marshal]::ReleaseComObject($document) | Out-Null }
    if ($word -and $owned) { $word.Quit(0) }
    if ($word) { [Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null }
}
