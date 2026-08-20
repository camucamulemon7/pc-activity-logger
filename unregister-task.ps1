[CmdletBinding(SupportsShouldProcess, ConfirmImpact = "High")]
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

if ($PSCmdlet.ShouldProcess($TaskName, "Stop and unregister scheduled task")) {
    if ($task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $TaskName
        Write-Host "Stopped scheduled task: $TaskName"
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Unregistered scheduled task: $TaskName"
}
