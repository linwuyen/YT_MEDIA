$ErrorActionPreference = "Stop"
$Runner = Join-Path $PSScriptRoot "run_agent.ps1"
$TaskName = "YT_MEDIA_AutoPublisher"
$PowerShell = (Get-Command powershell.exe).Source

$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
$Hourly = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(2)) -RepetitionInterval (New-TimeSpan -Hours 1)
$Logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($Hourly, $Logon) -Settings $Settings -Principal $Principal -Force | Out-Null

Write-Host "Scheduled task created: $TaskName" -ForegroundColor Green
Write-Host "The agent will run once per hour and also at Windows logon."
