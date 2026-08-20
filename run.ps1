param(
    [switch]$Once,
    [string]$Config = "$PSScriptRoot\config.yaml"
)

Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "Virtual environment not found. Run setup.ps1 first."
    exit 1
}

$arguments = @("-m", "pc_activity_logger.main", "--config", $Config)
if ($Once) {
    $arguments += "--once"
}

& $python @arguments
exit $LASTEXITCODE
