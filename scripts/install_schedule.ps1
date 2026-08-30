$ErrorActionPreference = "Stop"

# Compatibility entrypoint retained for INSTALL.cmd. Production scheduling is
# GitHub Actions; this script no longer creates a Windows Scheduled Task.
$Runtime = Join-Path $env:LOCALAPPDATA "YT_MEDIA"
$State = Join-Path $Runtime "state.json"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
if (-not (Test-Path $State)) {
    '{"files":{}}' | Set-Content -LiteralPath $State -Encoding UTF8
}

$Setup = Join-Path $PSScriptRoot "setup_github_actions.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Setup
exit $LASTEXITCODE
