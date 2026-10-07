[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$HelmPath,
    [Parameter(Mandatory=$true)][string]$ChartPackage,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string]$ValuesFile = (Join-Path $PSScriptRoot 'helm-values.example.yaml')
)
$ErrorActionPreference = 'Stop'
# A local reviewed official 0.3.3 archive; no cluster, repo update or deployment.
$metadata = & $HelmPath show chart $ChartPackage
if ($LASTEXITCODE -ne 0 -or ($metadata -join "`n") -notmatch '(?m)^version: 0\.3\.3\s*$') {
    throw 'Expected the reviewed official langgraph-cloud 0.3.3 chart archive.'
}
$manifest = & $HelmPath template portal-agent $ChartPackage --namespace portal-agent --values $ValuesFile
if ($LASTEXITCODE -ne 0) { throw 'Helm rendering failed.' }
[IO.File]::WriteAllLines($OutputPath, $manifest, [Text.UTF8Encoding]::new($false))
Write-Output 'Official chart rendered locally; no runtime or cloud acceptance implied.'
