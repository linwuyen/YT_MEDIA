@echo off
chcp 65001 >nul
setlocal

set "GCLOUD_LOCAL=%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
set "GH_PROGRAMFILES=%ProgramFiles%\GitHub CLI\gh.exe"

where gcloud >nul 2>&1
if errorlevel 1 if not exist "%GCLOUD_LOCAL%" (
  where winget >nul 2>&1
  if errorlevel 1 (
    echo Google Cloud CLI is missing and winget is unavailable.
    echo Install Google Cloud CLI, then rerun DEPLOY_CLOUD.cmd.
    pause
    exit /b 1
  )
  echo Installing Google Cloud CLI...
  winget install -e --id Google.CloudSDK --accept-source-agreements --accept-package-agreements
  if errorlevel 1 (
    echo Google Cloud CLI installation failed.
    pause
    exit /b 1
  )
)

where gh >nul 2>&1
if errorlevel 1 if not exist "%GH_PROGRAMFILES%" (
  where winget >nul 2>&1
  if not errorlevel 1 (
    echo Installing GitHub CLI...
    winget install -e --id GitHub.cli --accept-source-agreements --accept-package-agreements
  )
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy_cloud.ps1"
set "EXITCODE=%ERRORLEVEL%"
pause
exit /b %EXITCODE%
