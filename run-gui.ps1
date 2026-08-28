$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Error "Virtual environment not found. Run setup.ps1 first."
    exit 1
}

& $python -m pc_activity_logger.gui @args
exit $LASTEXITCODE
