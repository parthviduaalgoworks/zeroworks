param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExecutable
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonPath = (Resolve-Path $PythonExecutable).Path
$exportScript = Join-Path $projectRoot "scheduled_tempo_export.py"
$taskName = "AITimesheetTempoFridayExport"

if (-not (Test-Path $exportScript)) {
    throw "Scheduled export script not found: $exportScript"
}
if (-not (Test-Path (Join-Path $projectRoot ".tempo_credentials.json"))) {
    throw "Save a Tempo API token and worker ID in the app before enabling the scheduled export."
}

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "`"$exportScript`"" `
    -WorkingDirectory $projectRoot
$fridayAtFive = (Get-Date).Date.AddHours(17)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At $fridayAtFive
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Downloads the current week's Tempo worklogs as CSV every Friday at 5 PM local time." `
    -Force | Out-Null

Write-Output "Enabled '$taskName' for Fridays at 5:00 PM local time."
Write-Output "Windows timezone: $((Get-TimeZone).DisplayName)"
Write-Output "The task runs when this Windows account is signed in; missed runs start when the PC is available."
