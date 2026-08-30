@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_github_actions.ps1"
set "EXITCODE=%ERRORLEVEL%"
pause
exit /b %EXITCODE%
