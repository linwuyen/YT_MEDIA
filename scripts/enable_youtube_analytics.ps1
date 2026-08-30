$ErrorActionPreference = "Stop"

$Runtime = Join-Path $env:LOCALAPPDATA "YT_MEDIA"
$ClientSecret = Join-Path $Runtime "client_secret.json"

function Find-Gcloud {
    $cmd = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command gcloud -ErrorAction SilentlyContinue }
    if ($cmd) { return $cmd.Source }
    $candidate = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (Test-Path $candidate) { return $candidate }
    return $null
}

if (-not (Test-Path $ClientSecret)) {
    Write-Warning "client_secret.json not found; YouTube Analytics API enablement skipped. Data API fallback remains available."
    exit 0
}

try {
    $client = Get-Content -Raw -Encoding UTF8 $ClientSecret | ConvertFrom-Json
    $projectId = $null
    if ($client.installed -and $client.installed.project_id) { $projectId = [string]$client.installed.project_id }
    if (-not $projectId -and $client.web -and $client.web.project_id) { $projectId = [string]$client.web.project_id }
    if (-not $projectId) {
        Write-Warning "OAuth client JSON has no project_id; Analytics API enablement skipped."
        exit 0
    }

    $gcloud = Find-Gcloud
    if (-not $gcloud) {
        Write-Warning "gcloud not found; Analytics API enablement skipped. Publisher will use YouTube Data API fallback until the API is enabled."
        exit 0
    }

    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $account = & $gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>$null | Select-Object -First 1
        if (-not $account) {
            Write-Warning "gcloud has no active account; Analytics API enablement skipped."
            exit 0
        }
        Write-Host "Enabling YouTube Analytics API for project $projectId..." -ForegroundColor Cyan
        & $gcloud services enable youtubeanalytics.googleapis.com --project $projectId | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }

    if ($code -eq 0) {
        Write-Host "YouTube Analytics API is enabled." -ForegroundColor Green
    } else {
        Write-Warning "Could not enable YouTube Analytics API automatically. Data API fallback remains active; uploads will continue."
    }
} catch {
    Write-Warning "Analytics API enablement was skipped: $($_.Exception.Message)"
    Write-Warning "Data API fallback remains active; uploads will continue."
}

exit 0
