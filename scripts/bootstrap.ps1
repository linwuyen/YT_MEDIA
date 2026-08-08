$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$Runtime = Join-Path $env:LOCALAPPDATA "YT_MEDIA"
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

Write-Host "=== YT_MEDIA 一次安裝 ===" -ForegroundColor Cyan

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "找不到 Python，嘗試使用 winget 安裝 Python 3.12..."
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    }
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "仍找不到 Python Launcher (py)。請安裝 Python 3.11+ 後重跑。"
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "找不到 FFmpeg，嘗試使用 winget 安裝..."
    try {
        winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    } catch {
        Write-Warning "FFmpeg 自動安裝失敗；Agent 仍可直接使用原始 MP4。"
    }
}

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -e .

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
        Write-Host "已自動找到 OAuth client_secret：$($Found.FullName)" -ForegroundColor Green
    }
}
if (-not (Test-Path $ClientTarget)) {
    Write-Host ""
    Write-Host "缺少 Google Desktop OAuth JSON。" -ForegroundColor Yellow
    Write-Host "請把它改名為 client_secret.json 放到："
    Write-Host $ClientTarget
    throw "client_secret.json 尚未準備完成。"
}

Write-Host ""
Write-Host "開始一次性 Google 授權..." -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m yt_media.agent authorize
if ($LASTEXITCODE -ne 0) { throw "Google 授權或 YouTube 頻道驗證失敗。" }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\install_schedule.ps1"
Write-Host ""
Write-Host "安裝完成。從現在開始只要把影片丟進 Google Drive。" -ForegroundColor Green
Write-Host "Agent 會每小時檢查並自動排程。"
