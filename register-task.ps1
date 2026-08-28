[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateNotNullOrEmpty()]
    [string]$TaskName = "PC Activity Logger",

    [string]$Config,

    [string]$Executable,

    [switch]$Gui,

    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$projectDirectory = [System.IO.Path]::GetFullPath($PSScriptRoot)
if ([string]::IsNullOrWhiteSpace($Config)) {
    $configPath = Join-Path $projectDirectory "config.yaml"
} elseif ([System.IO.Path]::IsPathRooted($Config)) {
    $configPath = [System.IO.Path]::GetFullPath($Config)
} else {
    $configPath = [System.IO.Path]::GetFullPath((Join-Path $projectDirectory $Config))
}

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Configuration file not found: $configPath"
}

if (-not [string]::IsNullOrWhiteSpace($Executable)) {
    $program = [System.IO.Path]::GetFullPath($Executable)
    $programArguments = '--background --config "{0}"' -f $configPath.Replace('"', '\"')
} else {
    $program = Join-Path $projectDirectory ".venv\Scripts\pythonw.exe"
    $module = if ($Gui) { "pc_activity_logger.gui" } else { "pc_activity_logger.main" }
    $modeArguments = if ($Gui) { " --background" } else { "" }
    $programArguments = '-m {0}{1} --config "{2}"' -f $module, $modeArguments, $configPath.Replace('"', '\"')
}
if (-not (Test-Path -LiteralPath $program -PathType Leaf)) {
    throw "Executable not found: $program. Run setup.ps1 first."
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if (-not $PSCmdlet.ShouldProcess($TaskName, "Register or update scheduled task")) {
    Write-Host "User: $currentUser"
    Write-Host "Program: $program"
    Write-Host "Arguments: $programArguments"
    Write-Host "Trigger: At logon"
    return
}

$action = New-ScheduledTaskAction `
    -Execute $program `
    -Argument $programArguments `
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
Write-Host "Program: $program"
Write-Host "Config: $configPath"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started scheduled task: $TaskName"
} else {
    Write-Host "The task will start at the next logon. Use -StartNow to start it now."
}
