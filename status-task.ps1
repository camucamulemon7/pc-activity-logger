[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$TaskName = "PC Activity Logger"
)

$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if (-not $task) {
    Write-Host "Scheduled task is not registered: $TaskName"
    exit 0
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName
[PSCustomObject]@{
    TaskName       = $task.TaskName
    State          = $task.State
    LastRunTime    = $info.LastRunTime
    LastTaskResult = $info.LastTaskResult
    NextRunTime    = $info.NextRunTime
} | Format-List
