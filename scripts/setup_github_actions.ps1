$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

$GithubRepo = "linwuyen/YT_MEDIA"
$Workflow = "publish.yml"
$WindowsTaskName = "YT_MEDIA_AutoPublisher"
$Runtime = Join-Path $env:LOCALAPPDATA "YT_MEDIA"
$ClientSecret = Join-Path $Runtime "client_secret.json"
$DriveToken = Join-Path $Runtime "drive_token.json"
$YouTubeToken = Join-Path $Runtime "youtube_token.json"
$YouTubeManageToken = Join-Path $Runtime "youtube_manage_token.json"
$LocalState = Join-Path $Runtime "state.json"
$LocalLock = Join-Path $Runtime "agent.lock"

function Find-Gh {
    $cmd = Get-Command gh.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command gh -ErrorAction SilentlyContinue }
    if ($cmd) { return $cmd.Source }
    $candidate = Join-Path $env:ProgramFiles "GitHub CLI\gh.exe"
    if (Test-Path $candidate) { return $candidate }
    return $null
}

function Ensure-Gh {
    $gh = Find-Gh
    if ($gh) { return $gh }
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { throw "GitHub CLI is required and winget is unavailable." }
    Write-Host "Installing GitHub CLI..." -ForegroundColor Cyan
    & $winget.Source install -e --id GitHub.cli --accept-source-agreements --accept-package-agreements | Out-Host
    $gh = Find-Gh
    if (-not $gh) { throw "GitHub CLI was installed but gh.exe was not found. Reopen PowerShell and rerun SETUP_GITHUB_ACTIONS.cmd." }
    return $gh
}

function Find-Gcloud {
    $cmd = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command gcloud.exe -ErrorAction SilentlyContinue }
    if (-not $cmd) { $cmd = Get-Command gcloud -ErrorAction SilentlyContinue }
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd",
        "$env:ProgramFiles\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Test-CommandOk([string]$Exe, [string[]]$Arguments) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments *> $null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $old
    }
}

function Invoke-Checked([string]$Exe, [string[]]$Arguments, [string]$Label) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
    if ($code -ne 0) { throw "$Label failed with exit code $code" }
}

function Enable-YouTubeAnalyticsApiBestEffort {
    Write-Host "Checking YouTube Analytics API..." -ForegroundColor Cyan
    try {
        $client = Get-Content -Raw -Encoding UTF8 $ClientSecret | ConvertFrom-Json
        $projectId = $null
        if ($client.installed -and $client.installed.project_id) { $projectId = [string]$client.installed.project_id }
        if (-not $projectId -and $client.web -and $client.web.project_id) { $projectId = [string]$client.web.project_id }
        if (-not $projectId) {
            Write-Warning "Could not read OAuth project_id from client_secret.json; skipping API enable check."
            return
        }

        $gcloud = Find-Gcloud
        if (-not $gcloud) {
            Write-Warning "gcloud is not installed. Analytics OAuth will still be configured; if the API is disabled, publishing continues and Analytics learning stays paused."
            return
        }
        if (-not (Test-CommandOk $gcloud @("auth","list","--filter=status:ACTIVE","--format=value(account)"))) {
            Write-Warning "gcloud has no usable active login. Skipping YouTube Analytics API enable; this does not block publishing."
            return
        }

        $old = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $gcloud services enable youtubeanalytics.googleapis.com --project $projectId --quiet *> $null
            $code = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $old
        }
        if ($code -eq 0) {
            Write-Host "YouTube Analytics API is enabled for OAuth project $projectId." -ForegroundColor Green
        } else {
            Write-Warning "Could not enable YouTube Analytics API automatically for $projectId. Uploading will continue; Analytics learning remains fail-open until the API is enabled."
        }
    } catch {
        Write-Warning "YouTube Analytics API bootstrap check failed: $($_.Exception.Message). Publishing is not blocked."
    }
}

function Ensure-LocalOAuth([string]$Python) {
    Write-Host "Verifying local Google Drive + YouTube upload OAuth..." -ForegroundColor Cyan
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Python -m yt_media.agent doctor | Out-Host
        $doctorCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }

    if ($doctorCode -eq 0) {
        Write-Host "Local Drive + YouTube upload OAuth is valid." -ForegroundColor Green
        return
    }

    Write-Warning "Local OAuth token is missing, expired, revoked, or cannot refresh. Re-authorizing before setup."
    Write-Host "Two Google authorization browser steps may open: Drive first, then YouTube upload." -ForegroundColor Yellow
    Invoke-Checked $Python @("-m","yt_media.agent","authorize") "Google OAuth re-authorization"
    Invoke-Checked $Python @("-m","yt_media.agent","doctor") "Verify refreshed Google OAuth"

    foreach ($token in @($DriveToken, $YouTubeToken)) {
        if (-not (Test-Path $token)) { throw "OAuth completed but token file is missing: $token" }
    }
    Write-Host "Local Google OAuth repaired and verified." -ForegroundColor Green
}

function Ensure-MetadataOAuth([string]$Python) {
    Write-Host "Verifying YouTube metadata + Analytics OAuth..." -ForegroundColor Cyan
    if (Test-Path $YouTubeManageToken) {
        $old = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $Python -m yt_media.metadata_optimizer doctor | Out-Host
            $code = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $old
        }
        if ($code -eq 0) {
            Write-Host "YouTube metadata + Analytics OAuth has the required scopes." -ForegroundColor Green
            return
        }
    }

    Write-Host "One YouTube authorization is required for scheduled-video metadata edits and read-only Analytics." -ForegroundColor Yellow
    Write-Host "Select the Brand Account channel '象兒應援團' when Google asks which channel to authorize." -ForegroundColor Yellow
    Invoke-Checked $Python @("-m","yt_media.metadata_optimizer","authorize") "YouTube metadata + Analytics authorization"
    Invoke-Checked $Python @("-m","yt_media.metadata_optimizer","doctor") "Verify YouTube metadata + Analytics OAuth"
}

function Read-Utf8JsonWithoutBom([string]$Path) {
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    if ($text.Length -gt 0 -and [int][char]$text[0] -eq 0xFEFF) {
        $text = $text.Substring(1)
    }
    $null = $text | ConvertFrom-Json
    return $text
}

function Set-GhSecretFromFile([string]$Gh, [string]$Name, [string]$Path) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Gh
    $psi.Arguments = "secret set $Name --repo $GithubRepo"
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    if (-not $process.Start()) { throw "Could not start gh for secret $Name" }
    $process.StandardInput.Write((Read-Utf8JsonWithoutBom $Path))
    $process.StandardInput.Close()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "Set GitHub secret $Name failed: $stderr $stdout"
    }
}

function Test-LocalAgentRunning {
    try {
        $process = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match 'yt_media\.agent|run_agent\.ps1' } |
            Select-Object -First 1
        return ($null -ne $process)
    } catch {
        return $false
    }
}

function Test-LocalLockHeld([string]$Path) {
    if (-not (Test-Path $Path)) { return $false }
    $stream = $null
    try {
        $stream = [System.IO.File]::Open($Path, 'OpenOrCreate', 'ReadWrite', 'None')
        return $false
    } catch {
        return $true
    } finally {
        if ($stream) { $stream.Dispose() }
    }
}

function Wait-LocalAgentIdle {
    Write-Host "Waiting for the Windows uploader to be fully idle..." -ForegroundColor Cyan
    $deadline = (Get-Date).AddMinutes(15)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-LocalAgentRunning) -and -not (Test-LocalLockHeld $LocalLock)) {
            Write-Host "Windows uploader is idle." -ForegroundColor Green
            return
        }
        Start-Sleep -Seconds 5
    }
    throw "Timed out waiting for the Windows uploader to stop."
}

Write-Host "=== YT_MEDIA GitHub Actions growth-system setup ===" -ForegroundColor Cyan
Write-Host "No Google Cloud Billing is required for the production runtime."

foreach ($required in @($ClientSecret, $LocalState)) {
    if (-not (Test-Path $required)) {
        throw "Missing required migration file: $required"
    }
}

$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Missing Python environment: $Python. Run INSTALL.cmd first." }

Ensure-LocalOAuth $Python
Enable-YouTubeAnalyticsApiBestEffort
Ensure-MetadataOAuth $Python

$Gh = Ensure-Gh
if (-not (Test-CommandOk $Gh @("auth","status"))) {
    Write-Host "Opening GitHub sign-in..." -ForegroundColor Cyan
    Invoke-Checked $Gh @("auth","login","--web","--git-protocol","https") "GitHub login"
}

$Task = Get-ScheduledTask -TaskName $WindowsTaskName -ErrorAction SilentlyContinue
$WasEnabled = $false
if ($Task) {
    $WasEnabled = ($Task.State -ne "Disabled")
    if ($WasEnabled) {
        Disable-ScheduledTask -TaskName $WindowsTaskName | Out-Null
        Write-Host "Temporarily disabled Windows scheduled task." -ForegroundColor Cyan
    }
}

try {
    Wait-LocalAgentIdle

    Write-Host "Seeding durable state into Google Drive..." -ForegroundColor Cyan
    Invoke-Checked $Python @("-m","yt_media.agent","seed-drive-state") "Seed Drive state"

    Write-Host "Uploading OAuth files to GitHub Actions Secrets..." -ForegroundColor Cyan
    Set-GhSecretFromFile $Gh "YT_MEDIA_CLIENT_SECRET_JSON" $ClientSecret
    Set-GhSecretFromFile $Gh "YT_MEDIA_DRIVE_TOKEN_JSON" $DriveToken
    Set-GhSecretFromFile $Gh "YT_MEDIA_YOUTUBE_TOKEN_JSON" $YouTubeToken
    Set-GhSecretFromFile $Gh "YT_MEDIA_YOUTUBE_MANAGE_TOKEN_JSON" $YouTubeManageToken

    Write-Host "Starting a GitHub Actions end-to-end verification run..." -ForegroundColor Cyan
    Invoke-Checked $Gh @("workflow","run",$Workflow,"--repo",$GithubRepo,"--ref","main") "Start GitHub Actions workflow"
    Start-Sleep -Seconds 5

    $runIdLines = & $Gh run list --repo $GithubRepo --workflow $Workflow --branch main --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId'
    if ($LASTEXITCODE -ne 0) { throw "Could not find the verification workflow run." }
    $RunId = (($runIdLines | Select-Object -First 1) -as [string]).Trim()
    if (-not $RunId) { throw "Verification workflow run ID is empty." }

    Write-Host "Waiting for GitHub Actions verification run $RunId..." -ForegroundColor Cyan
    Invoke-Checked $Gh @("run","watch",$RunId,"--repo",$GithubRepo,"--exit-status") "GitHub Actions verification"

    if (Get-ScheduledTask -TaskName $WindowsTaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $WindowsTaskName -Confirm:$false
        Write-Host "Removed Windows scheduled task $WindowsTaskName." -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "GitHub Actions growth-system setup complete." -ForegroundColor Green
    Write-Host "Runtime: GitHub-hosted ubuntu-latest"
    Write-Host "Schedule: hourly at minute 17"
    Write-Host "State: Google Drive root/.YT_MEDIA_STATE.json"
    Write-Host "Learning: staged metadata -> publish-time -> thumbnail experiments"
    Write-Host "Dashboard: Actions artifact yt-media-growth-dashboard"
    Write-Host "Your PC does not need to stay on."
} catch {
    if ($WasEnabled -and (Get-ScheduledTask -TaskName $WindowsTaskName -ErrorAction SilentlyContinue)) {
        try {
            Enable-ScheduledTask -TaskName $WindowsTaskName | Out-Null
            Write-Warning "Setup failed; Windows scheduled task was restored."
        } catch {
            Write-Warning "Setup failed and Windows scheduled task could not be restored automatically."
        }
    }
    throw
}
