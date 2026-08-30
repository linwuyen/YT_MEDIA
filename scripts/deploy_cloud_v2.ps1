$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

$Runtime = Join-Path $env:LOCALAPPDATA "YT_MEDIA"
$ClientSecret = Join-Path $Runtime "client_secret.json"
$DriveToken = Join-Path $Runtime "drive_token.json"
$YouTubeToken = Join-Path $Runtime "youtube_token.json"
$LocalState = Join-Path $Runtime "state.json"
$LocalLock = Join-Path $Runtime "agent.lock"

$Region = "asia-east1"
$SchedulerRegion = "asia-east1"
$JobName = "yt-media-autopublisher"
$SchedulerName = "yt-media-hourly"
$WindowsTaskName = "YT_MEDIA_AutoPublisher"
$RuntimeSaName = "yt-media-runner"
$SchedulerSaName = "yt-media-scheduler"
$DeploySaName = "yt-media-github-deployer"
$GithubRepo = "linwuyen/YT_MEDIA"
$PoolName = "github-actions"
$ProviderName = "yt-media"

function Find-Exe([string[]]$Names, [string[]]$Candidates) {
    foreach ($name in $Names) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Find-Gcloud {
    return Find-Exe @("gcloud.cmd", "gcloud") @(
        (Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"),
        (Join-Path $env:ProgramFiles "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd")
    )
}

function Find-Gh {
    return Find-Exe @("gh.exe", "gh") @(
        (Join-Path $env:ProgramFiles "GitHub CLI\gh.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "GitHub CLI\gh.exe")
    )
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

function Invoke-Capture([string]$Exe, [string[]]$Arguments) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = @(& $Exe @Arguments 2>$null)
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
    if ($code -ne 0) { return @() }
    return $output
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

function Ensure-Gcloud {
    $gcloud = Find-Gcloud
    if ($gcloud) { return $gcloud }
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { throw "Google Cloud CLI is missing and winget is unavailable." }
    Write-Host "Installing Google Cloud CLI..." -ForegroundColor Cyan
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $winget.Source install -e --id Google.CloudSDK --accept-source-agreements --accept-package-agreements | Out-Host
    } finally {
        $ErrorActionPreference = $old
    }
    $gcloud = Find-Gcloud
    if (-not $gcloud) { throw "Google Cloud CLI was installed but gcloud was not found. Reopen PowerShell and rerun DEPLOY_CLOUD.cmd." }
    return $gcloud
}

function Ensure-Gh {
    $gh = Find-Gh
    if ($gh) { return $gh }
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { return $null }
    Write-Host "Installing GitHub CLI for automatic future deployments..." -ForegroundColor Cyan
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $winget.Source install -e --id GitHub.cli --accept-source-agreements --accept-package-agreements | Out-Host
    } finally {
        $ErrorActionPreference = $old
    }
    return Find-Gh
}

function Ensure-ServiceAccount([string]$Gcloud, [string]$ProjectId, [string]$Name, [string]$DisplayName) {
    $email = "$Name@$ProjectId.iam.gserviceaccount.com"
    if (-not (Test-CommandOk $Gcloud @("iam","service-accounts","describe",$email,"--project",$ProjectId))) {
        Invoke-Checked $Gcloud @("iam","service-accounts","create",$Name,"--display-name",$DisplayName,"--project",$ProjectId) "Create service account $Name"
    }
    return $email
}

function Upsert-Secret([string]$Gcloud, [string]$ProjectId, [string]$Name, [string]$FilePath) {
    if (-not (Test-Path $FilePath)) { throw "Missing required credential file: $FilePath" }
    if (-not (Test-CommandOk $Gcloud @("secrets","describe",$Name,"--project",$ProjectId))) {
        Invoke-Checked $Gcloud @("secrets","create",$Name,"--replication-policy","automatic","--project",$ProjectId) "Create secret $Name"
    }
    Invoke-Checked $Gcloud @("secrets","versions","add",$Name,"--data-file",$FilePath,"--project",$ProjectId) "Upload secret $Name"
}

function Test-BillingEnabled([string]$Gcloud, [string]$ProjectId) {
    $lines = Invoke-Capture $Gcloud @("billing","projects","describe",$ProjectId,"--format=value(billingEnabled)")
    $value = (($lines | Select-Object -First 1) -as [string]).Trim().ToLowerInvariant()
    return ($value -eq "true")
}

function Ensure-Billing([string]$Gcloud, [string]$ProjectId) {
    if (Test-BillingEnabled $Gcloud $ProjectId) { return }

    Write-Host "Google Cloud Billing is not enabled for $ProjectId." -ForegroundColor Yellow
    $accounts = @(Invoke-Capture $Gcloud @("billing","accounts","list","--filter=open=true","--format=value(name)")) | Where-Object { $_ }
    if ($accounts.Count -eq 1) {
        $account = ($accounts[0] -as [string]).Trim()
        Write-Host "One active billing account was found: $account" -ForegroundColor Cyan
        $answer = Read-Host "Link this project to that billing account now? [y/N]"
        if ($answer -match '^[Yy]$') {
            Invoke-Checked $Gcloud @("billing","projects","link",$ProjectId,"--billing-account",($account -replace '^billingAccounts/','')) "Link billing account"
            if (Test-BillingEnabled $Gcloud $ProjectId) { return }
        }
    }

    try { Start-Process "https://console.cloud.google.com/billing?project=$ProjectId" } catch {}
    throw "Billing must be enabled for $ProjectId before Cloud Run migration can continue. A Billing page was opened; link/create billing, then rerun DEPLOY_CLOUD.cmd."
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

function Wait-LocalAgentIdle([string]$LockPath, [int]$TimeoutMinutes = 30) {
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ((Test-LocalAgentRunning) -or (Test-LocalLockHeld $LockPath)) {
        if ((Get-Date) -ge $deadline) {
            throw "Local YT_MEDIA agent is still active after $TimeoutMinutes minutes. Cloud cutover was not started."
        }
        Write-Host "Waiting for the current Windows YT_MEDIA run to finish..." -ForegroundColor Yellow
        Start-Sleep -Seconds 10
    }
}

function Seed-CloudState([string]$Gcloud, [string]$BucketName, [string]$LocalStatePath) {
    $uri = "gs://$BucketName/state.json"
    if (Test-CommandOk $Gcloud @("storage","ls",$uri)) {
        if (-not (Test-Path $LocalStatePath)) {
            Write-Host "Cloud state already exists and there is no local state to migrate." -ForegroundColor Cyan
            return
        }
        $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("yt-media-cloud-state-" + [guid]::NewGuid().ToString("N") + ".json")
        try {
            Invoke-Checked $Gcloud @("storage","cp",$uri,$temp) "Download existing cloud state"
            $localHash = (Get-FileHash -LiteralPath $LocalStatePath -Algorithm SHA256).Hash
            $cloudHash = (Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash
            if ($localHash -ne $cloudHash) {
                throw "Both local and cloud state.json exist but differ. Refusing to overwrite either state because that could duplicate YouTube uploads."
            }
            Write-Host "Cloud state already matches the local state." -ForegroundColor Green
            return
        } finally {
            Remove-Item $temp -Force -ErrorAction SilentlyContinue
        }
    }

    if (Test-Path $LocalStatePath) {
        Write-Host "Migrating local idempotency state to Cloud Storage..." -ForegroundColor Cyan
        Invoke-Checked $Gcloud @("storage","cp",$LocalStatePath,$uri) "Seed Cloud Storage state"
    } else {
        $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("yt-media-empty-state-" + [guid]::NewGuid().ToString("N") + ".json")
        try {
            '{"files":{}}' | Set-Content -LiteralPath $temp -Encoding UTF8
            Invoke-Checked $Gcloud @("storage","cp",$temp,$uri) "Seed empty Cloud Storage state"
        } finally {
            Remove-Item $temp -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "=== YT_MEDIA cloud migration ===" -ForegroundColor Cyan
Write-Host "This keeps OAuth credentials out of GitHub and moves scheduled execution to Google Cloud."

foreach ($required in @($ClientSecret,$DriveToken,$YouTubeToken)) {
    if (-not (Test-Path $required)) { throw "Missing $required. Run INSTALL.cmd and complete OAuth first." }
}

$Gcloud = Ensure-Gcloud
$accounts = Invoke-Capture $Gcloud @("auth","list","--format=value(account)")
$activeAccount = $accounts | Select-Object -First 1
if (-not $activeAccount) {
    Write-Host "Opening Google Cloud sign-in..." -ForegroundColor Cyan
    Invoke-Checked $Gcloud @("auth","login") "gcloud auth login"
}

$currentProjectLines = Invoke-Capture $Gcloud @("config","get-value","project")
$currentProject = (($currentProjectLines | Select-Object -First 1) -as [string]).Trim()
if ($currentProject -eq "(unset)") { $currentProject = "" }
if ($currentProject) {
    $answer = Read-Host "Google Cloud project ID [$currentProject]"
    $ProjectId = if ($answer) { $answer.Trim() } else { $currentProject }
} else {
    Write-Host "Available projects:" -ForegroundColor Cyan
    Invoke-Checked $Gcloud @("projects","list","--format=table(projectId,name)") "List projects"
    $ProjectId = (Read-Host "Google Cloud project ID").Trim()
}
if (-not $ProjectId) { throw "Google Cloud project ID is required." }

Invoke-Checked $Gcloud @("config","set","project",$ProjectId) "Set gcloud project"
Ensure-Billing $Gcloud $ProjectId

$projectNumberLines = Invoke-Capture $Gcloud @("projects","describe",$ProjectId,"--format=value(projectNumber)")
$ProjectNumber = (($projectNumberLines | Select-Object -First 1) -as [string]).Trim()
if (-not $ProjectNumber) { throw "Could not resolve project number for $ProjectId" }

$BucketName = "$ProjectId-yt-media-state"
$ComputeSa = "$ProjectNumber-compute@developer.gserviceaccount.com"
$RestoreWindowsTaskOnFailure = $false
$CutoverComplete = $false

try {
    Write-Host "Enabling required Google Cloud APIs..." -ForegroundColor Cyan
    Invoke-Checked $Gcloud @("services","enable","run.googleapis.com","cloudscheduler.googleapis.com","secretmanager.googleapis.com","storage.googleapis.com","cloudbuild.googleapis.com","artifactregistry.googleapis.com","iamcredentials.googleapis.com","sts.googleapis.com","drive.googleapis.com","youtube.googleapis.com","--project",$ProjectId) "Enable APIs"

    $RuntimeSa = Ensure-ServiceAccount $Gcloud $ProjectId $RuntimeSaName "YT_MEDIA Cloud Run runtime"
    $SchedulerSa = Ensure-ServiceAccount $Gcloud $ProjectId $SchedulerSaName "YT_MEDIA Cloud Scheduler invoker"
    $DeploySa = Ensure-ServiceAccount $Gcloud $ProjectId $DeploySaName "YT_MEDIA GitHub deployer"

    Write-Host "Creating Cloud Storage state bucket..." -ForegroundColor Cyan
    if (-not (Test-CommandOk $Gcloud @("storage","buckets","describe","gs://$BucketName","--project",$ProjectId))) {
        Invoke-Checked $Gcloud @("storage","buckets","create","gs://$BucketName","--project",$ProjectId,"--location",$Region,"--uniform-bucket-level-access") "Create state bucket"
    }

    # Freeze the Windows producer before copying state. This closes the only
    # dangerous migration window: a local upload must never race the first
    # cloud run after state.json has been copied.
    $windowsTask = Get-ScheduledTask -TaskName $WindowsTaskName -ErrorAction SilentlyContinue
    if ($windowsTask) {
        if ($windowsTask.State -ne "Disabled") {
            Disable-ScheduledTask -TaskName $WindowsTaskName | Out-Null
            $RestoreWindowsTaskOnFailure = $true
            Write-Host "Temporarily disabled Windows scheduled task $WindowsTaskName." -ForegroundColor Cyan
        } else {
            Write-Host "Windows scheduled task is already disabled." -ForegroundColor Cyan
        }
    }
    Wait-LocalAgentIdle $LocalLock
    Seed-CloudState $Gcloud $BucketName $LocalState

    # If an older partial migration created a scheduler, pause it before any
    # deploy/verification work. It is activated only after verification PASS.
    if (Test-CommandOk $Gcloud @("scheduler","jobs","describe",$SchedulerName,"--project",$ProjectId,"--location",$SchedulerRegion)) {
        Invoke-Checked $Gcloud @("scheduler","jobs","pause",$SchedulerName,"--project",$ProjectId,"--location",$SchedulerRegion) "Pause existing scheduler"
    }

    Write-Host "Uploading OAuth material to Secret Manager..." -ForegroundColor Cyan
    Upsert-Secret $Gcloud $ProjectId "yt-media-client-secret" $ClientSecret
    Upsert-Secret $Gcloud $ProjectId "yt-media-drive-token" $DriveToken
    Upsert-Secret $Gcloud $ProjectId "yt-media-youtube-token" $YouTubeToken

    Write-Host "Granting runtime permissions..." -ForegroundColor Cyan
    Invoke-Checked $Gcloud @("projects","add-iam-policy-binding",$ProjectId,"--member","serviceAccount:$RuntimeSa","--role","roles/secretmanager.secretAccessor","--quiet") "Grant Secret Manager access"
    Invoke-Checked $Gcloud @("storage","buckets","add-iam-policy-binding","gs://$BucketName","--member","serviceAccount:$RuntimeSa","--role","roles/storage.objectAdmin") "Grant state bucket access"
    Invoke-Checked $Gcloud @("projects","add-iam-policy-binding",$ProjectId,"--member","serviceAccount:$ComputeSa","--role","roles/run.builder","--quiet") "Grant Cloud Run builder role"
    foreach ($role in @("roles/run.sourceDeveloper","roles/serviceusage.serviceUsageConsumer")) {
        Invoke-Checked $Gcloud @("projects","add-iam-policy-binding",$ProjectId,"--member","serviceAccount:$DeploySa","--role",$role,"--quiet") "Grant deployer $role"
    }
    Invoke-Checked $Gcloud @("iam","service-accounts","add-iam-policy-binding",$RuntimeSa,"--member","serviceAccount:$DeploySa","--role","roles/iam.serviceAccountUser","--project",$ProjectId,"--quiet") "Allow deployer to use runtime service account"

    $EnvVars = "YT_MEDIA_RUNTIME_DIR=/tmp/YT_MEDIA,YT_MEDIA_STATE_GCS_URI=gs://$BucketName/state.json,YT_MEDIA_CLIENT_SECRET_PATH=/secrets/client_secret.json,YT_MEDIA_DRIVE_TOKEN_PATH=/secrets/drive_token.json,YT_MEDIA_YOUTUBE_TOKEN_PATH=/secrets/youtube_token.json"
    $SecretMounts = "/secrets/client_secret.json=yt-media-client-secret:latest,/secrets/drive_token.json=yt-media-drive-token:latest,/secrets/youtube_token.json=yt-media-youtube-token:latest"

    Write-Host "Deploying Cloud Run Job from this checkout..." -ForegroundColor Cyan
    Invoke-Checked $Gcloud @("run","jobs","deploy",$JobName,"--source",".","--project",$ProjectId,"--region",$Region,"--service-account",$RuntimeSa,"--cpu","2","--memory","8Gi","--task-timeout","3h","--max-retries","0","--tasks","1","--set-env-vars",$EnvVars,"--set-secrets",$SecretMounts,"--quiet") "Deploy Cloud Run Job"

    Invoke-Checked $Gcloud @("run","jobs","add-iam-policy-binding",$JobName,"--project",$ProjectId,"--region",$Region,"--member","serviceAccount:$SchedulerSa","--role","roles/run.invoker","--quiet") "Grant scheduler invoker"

    Write-Host "Configuring keyless GitHub -> Google Cloud deployment..." -ForegroundColor Cyan
    if (-not (Test-CommandOk $Gcloud @("iam","workload-identity-pools","describe",$PoolName,"--project",$ProjectId,"--location","global"))) {
        Invoke-Checked $Gcloud @("iam","workload-identity-pools","create",$PoolName,"--project",$ProjectId,"--location","global","--display-name","GitHub Actions") "Create WIF pool"
    }
    if (-not (Test-CommandOk $Gcloud @("iam","workload-identity-pools","providers","describe",$ProviderName,"--project",$ProjectId,"--location","global","--workload-identity-pool",$PoolName))) {
        Invoke-Checked $Gcloud @("iam","workload-identity-pools","providers","create-oidc",$ProviderName,"--project",$ProjectId,"--location","global","--workload-identity-pool",$PoolName,"--display-name","YT_MEDIA GitHub","--issuer-uri","https://token.actions.githubusercontent.com","--attribute-mapping","google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref","--attribute-condition","assertion.repository=='$GithubRepo'") "Create WIF provider"
    }
    $WifMember = "principalSet://iam.googleapis.com/projects/$ProjectNumber/locations/global/workloadIdentityPools/$PoolName/attribute.repository/$GithubRepo"
    Invoke-Checked $Gcloud @("iam","service-accounts","add-iam-policy-binding",$DeploySa,"--project",$ProjectId,"--member",$WifMember,"--role","roles/iam.workloadIdentityUser","--quiet") "Bind GitHub WIF"
    $ProviderResource = "projects/$ProjectNumber/locations/global/workloadIdentityPools/$PoolName/providers/$ProviderName"

    $Gh = Ensure-Gh
    if ($Gh) {
        if (-not (Test-CommandOk $Gh @("auth","status"))) {
            Write-Host "Opening GitHub sign-in so repository variables can be configured..." -ForegroundColor Cyan
            Invoke-Checked $Gh @("auth","login","--web","--git-protocol","https") "GitHub CLI login"
        }
        foreach ($pair in @(
            @("GCP_PROJECT_ID",$ProjectId),
            @("GCP_REGION",$Region),
            @("GCP_STATE_BUCKET",$BucketName),
            @("GCP_RUNTIME_SA",$RuntimeSa),
            @("GCP_DEPLOY_SA",$DeploySa),
            @("GCP_WIF_PROVIDER",$ProviderResource)
        )) {
            Invoke-Checked $Gh @("variable","set",$pair[0],"--body",$pair[1],"--repo",$GithubRepo) "Set GitHub variable $($pair[0])"
        }
    } else {
        Write-Warning "GitHub CLI is unavailable. Cloud runtime is deployed, but automatic GitHub redeploy variables were not configured."
    }

    # Verification happens before Scheduler activation. The copied state.json is
    # already authoritative, so this execution can reconcile old uploads and/or
    # take the first new batch without creating duplicates.
    Write-Host "Running one cloud execution as final verification..." -ForegroundColor Cyan
    Invoke-Checked $Gcloud @("run","jobs","execute",$JobName,"--project",$ProjectId,"--region",$Region,"--wait") "Cloud verification run"

    Write-Host "Activating hourly Cloud Scheduler trigger..." -ForegroundColor Cyan
    if (Test-CommandOk $Gcloud @("scheduler","jobs","describe",$SchedulerName,"--project",$ProjectId,"--location",$SchedulerRegion)) {
        Invoke-Checked $Gcloud @("scheduler","jobs","delete",$SchedulerName,"--project",$ProjectId,"--location",$SchedulerRegion,"--quiet") "Replace scheduler"
    }
    $RunUri = "https://run.googleapis.com/v2/projects/$ProjectId/locations/$Region/jobs/$JobName`:run"
    Invoke-Checked $Gcloud @("scheduler","jobs","create","http",$SchedulerName,"--project",$ProjectId,"--location",$SchedulerRegion,"--schedule","0 * * * *","--time-zone","Asia/Taipei","--uri",$RunUri,"--http-method","POST","--oauth-service-account-email",$SchedulerSa,"--message-body","{}") "Create Cloud Scheduler job"

    if (Get-ScheduledTask -TaskName $WindowsTaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $WindowsTaskName -Confirm:$false
        Write-Host "Removed Windows scheduled task $WindowsTaskName." -ForegroundColor Green
    }
    $RestoreWindowsTaskOnFailure = $false
    $CutoverComplete = $true
} catch {
    if ($RestoreWindowsTaskOnFailure) {
        try {
            Enable-ScheduledTask -TaskName $WindowsTaskName | Out-Null
            Write-Warning "Cloud cutover failed; the previously enabled Windows scheduler was restored."
        } catch {
            Write-Warning "Cloud cutover failed and the Windows scheduler could not be restored automatically."
        }
    }
    throw
}

if (-not $CutoverComplete) { throw "Cloud cutover did not complete." }

Write-Host ""
Write-Host "Cloud migration complete." -ForegroundColor Green
Write-Host "Runtime: Cloud Run Job $JobName ($Region)"
Write-Host "Schedule: Cloud Scheduler every hour (Asia/Taipei)"
Write-Host "State: gs://$BucketName/state.json"
Write-Host "Secrets: Google Secret Manager"
Write-Host "Future main-branch code changes: GitHub Actions -> Cloud Run"
Write-Host "Windows scheduled task: removed"
Write-Host "Your PC no longer needs to stay on."
