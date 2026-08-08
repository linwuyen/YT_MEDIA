$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $env:LOCALAPPDATA "YT_MEDIA"
$LockPath = Join-Path $Runtime "agent.lock"
$LogDir = Join-Path $Runtime "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Lock = $null
try {
    $Lock = [System.IO.File]::Open($LockPath, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    exit 0
}

try {
    Set-Location $Repo
    if (Get-Command git -ErrorAction SilentlyContinue) {
        git pull --ff-only origin main 2>&1 | Out-File -FilePath (Join-Path $LogDir "git-pull.log") -Append -Encoding utf8
    }
    & ".\.venv\Scripts\python.exe" -m yt_media.agent run 2>&1 | Tee-Object -FilePath (Join-Path $LogDir "scheduled-run.log") -Append
    exit $LASTEXITCODE
} finally {
    if ($Lock) { $Lock.Dispose() }
    Remove-Item $LockPath -Force -ErrorAction SilentlyContinue
}
