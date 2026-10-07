[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$BaseImage,
    [Parameter(Mandatory=$true)][string]$ImageTag
)
$ErrorActionPreference = 'Stop'
if ($BaseImage -notmatch '^langchain/langgraph-api:[A-Za-z0-9_.-]+@sha256:[0-9a-f]{64}$' -or
    $BaseImage -match '(?i)inmem|dev|rc|alpha|beta') {
    throw 'Use a licensed stable official langchain/langgraph-api image pinned by SHA256; developer images are not production.'
}
if ($ImageTag -notmatch '^[A-Za-z0-9][A-Za-z0-9._/:@-]+$') { throw 'Invalid image tag.' }
$repository = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
& docker build --file (Join-Path $PSScriptRoot 'Dockerfile') --build-arg "AGENT_BASE_IMAGE=$BaseImage" --tag $ImageTag $repository
if ($LASTEXITCODE -ne 0) { throw 'Agent image build failed; retain dependency conflicts as a release blocker.' }
