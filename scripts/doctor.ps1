$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
& ".\.venv\Scripts\python.exe" -m yt_media.agent doctor
exit $LASTEXITCODE
