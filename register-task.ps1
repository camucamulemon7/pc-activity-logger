[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateNotNullOrEmpty()]
    [string]$TaskName = "PC Activity Logger",

    [string]$Config,

    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$projectDirectory = [System.IO.Path]::GetFullPath($PSScriptRoot)
$python = Join-Path $projectDirectory ".venv\Scripts\pythonw.exe"
if ([string]::IsNullOrWhiteSpace($Config)) {
    $configPath = Join-Path $projectDirectory "config.yaml"
} elseif ([System.IO.Path]::IsPathRooted($Config)) {
    $configPath = [System.IO.Path]::GetFullPath($Config)
} else {
    $configPath = [System.IO.Path]::GetFullPath((Join-Path $projectDirectory $Config))
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python virtual environment not found: $python. Run setup.ps1 first."
}
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Configuration file not found: $configPath"
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$quotedConfig = '"{0}"' -f $configPath.Replace('"', '\"')
$arguments = "-m pc_activity_logger.main --config $quotedConfig"

if (-not $PSCmdlet.ShouldProcess($TaskName, "Register or update scheduled task")) {
    Write-Host "User: $currentUser"
    Write-Host "Program: $python"
    Write-Host "Arguments: $arguments"
    Write-Host "Trigger: At logon"
    return
}

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument $arguments `
    -WorkingDirectory $projectDirectory
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Capture and analyze Windows activity through OpenWebUI" `
    -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName"
Write-Host "User: $currentUser"
Write-Host "Program: $python"
Write-Host "Config: $configPath"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started scheduled task: $TaskName"
} else {
    Write-Host "The task will start at the next logon. Use -StartNow to start it now."
}
