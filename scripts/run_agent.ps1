$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $env:LOCALAPPDATA "YT_MEDIA"
$LockPath = Join-Path $Runtime "agent.lock"
$LogDir = Join-Path $Runtime "logs"
$RunnerLog = Join-Path $LogDir "runner.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-RunnerLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $RunnerLog -Value $line -Encoding UTF8
    Write-Host $Message
}

$Lock = $null
try {
    $Lock = [System.IO.File]::Open($LockPath, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    exit 0
}

try {
    Set-Location $Repo

    $GitCommand = Get-Command git -ErrorAction SilentlyContinue
    if ($GitCommand) {
        Write-RunnerLog "Updating repository from origin/main..."
        $GitProcess = Start-Process `
            -FilePath $GitCommand.Source `
            -ArgumentList @("pull", "--ff-only", "origin", "main") `
            -WorkingDirectory $Repo `
            -NoNewWindow `
            -Wait `
            -PassThru

        if ($GitProcess.ExitCode -ne 0) {
            Write-RunnerLog "WARNING: git pull failed with exit code $($GitProcess.ExitCode). Continuing with the current local version."
        }
    }

    $Python = Join-Path $Repo ".venv\Scripts\python.exe"
    if (-not (Test-Path $Python)) {
        throw "Python virtual environment was not found: $Python"
    }

    Write-RunnerLog "Starting YT_MEDIA agent..."
    $AgentProcess = Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "yt_media.agent", "run") `
        -WorkingDirectory $Repo `
        -NoNewWindow `
        -Wait `
        -PassThru

    Write-RunnerLog "YT_MEDIA agent finished with exit code $($AgentProcess.ExitCode)."
    exit $AgentProcess.ExitCode
} finally {
    if ($Lock) { $Lock.Dispose() }
    Remove-Item $LockPath -Force -ErrorAction SilentlyContinue
}
