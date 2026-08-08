$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$Runtime = Join-Path $env:LOCALAPPDATA "YT_MEDIA"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

Write-Host "=== YT_MEDIA bootstrap ===" -ForegroundColor Cyan

function Find-BootstrapPython {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return @{ Command = $py.Source; Args = @("-3") } }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @{ Command = $python.Source; Args = @() } }

    $localPythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $localPythonRoot) {
        $candidate = Get-ChildItem $localPythonRoot -Recurse -File -Filter "python.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch "\\Scripts\\" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) { return @{ Command = $candidate.FullName; Args = @() } }
    }

    return $null
}

$BootstrapPython = Find-BootstrapPython
if (-not $BootstrapPython) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Python was not found. Installing Python 3.12 with winget..."
        & $winget.Source install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        $BootstrapPython = Find-BootstrapPython
    }
}
if (-not $BootstrapPython) {
    throw "Python 3.11+ was not found. Install Python and run INSTALL.cmd again."
}

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "FFmpeg was not found. Trying winget installation..."
        try {
            & $winget.Source install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
        } catch {
            Write-Warning "FFmpeg installation failed. The agent can still upload the original MP4 file."
        }
    }
}

$VenvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $cmd = $BootstrapPython.Command
    $args = @($BootstrapPython.Args) + @("-m", "venv", ".venv")
    & $cmd @args
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Python virtual environment." }
}

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $VenvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "YT_MEDIA package installation failed." }

$ClientTarget = Join-Path $Runtime "client_secret.json"
if (-not (Test-Path $ClientTarget)) {
    $Candidates = @()
    $DownloadDirs = @(
        (Join-Path $env:USERPROFILE "Downloads"),
        "D:\Downloads"
    ) | Select-Object -Unique

    foreach ($Dir in $DownloadDirs) {
        if (Test-Path $Dir) {
            $Candidates += Get-ChildItem $Dir -Recurse -File -Filter "client_secret*.json" -ErrorAction SilentlyContinue
        }
    }

    $Found = $Candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($Found) {
        Copy-Item -LiteralPath $Found.FullName -Destination $ClientTarget -Force
        Write-Host "OAuth client_secret copied from: $($Found.FullName)" -ForegroundColor Green
    }
}

if (-not (Test-Path $ClientTarget)) {
    Write-Host ""
    Write-Host "Google Desktop OAuth JSON was not found." -ForegroundColor Yellow
    Write-Host "Rename it to client_secret.json and place it here:"
    Write-Host $ClientTarget
    throw "client_secret.json is missing."
}

Write-Host ""
Write-Host "Starting one-time Google authorization..." -ForegroundColor Cyan
& $VenvPython -m yt_media.agent authorize
if ($LASTEXITCODE -ne 0) {
    throw "Google authorization or YouTube channel verification failed."
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\install_schedule.ps1"
if ($LASTEXITCODE -ne 0) { throw "Task Scheduler installation failed." }

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host "From now on, put videos in the configured Google Drive folder."
Write-Host "The agent checks every hour and schedules uploads automatically."
