param()

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$uiPath = Join-Path $repoRoot "Web\video_agent"

Write-Host "Building Video Agent UI from $uiPath" -ForegroundColor Cyan
Push-Location $uiPath
try {
    npm install
    npm run build:archive
}
finally {
    Pop-Location
}
