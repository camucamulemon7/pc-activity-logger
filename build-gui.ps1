$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Error "Virtual environment not found. Run setup.ps1 first."
    exit 1
}

& $python -m pip install -r requirements-gui.txt
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

try {
    $previousPythonUserBase = $env:PYTHONUSERBASE
    $env:PYTHONUSERBASE = Join-Path $PSScriptRoot "build\python-user-base"
    & $python -m PyInstaller --noconfirm --clean PCActivityLogger.spec
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    $env:PYTHONUSERBASE = $previousPythonUserBase
}

$executable = Join-Path $PSScriptRoot "dist\PCActivityLogger\PCActivityLogger.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    Write-Error "Build completed without the expected executable: $executable"
    exit 1
}

Write-Host "GUI build complete: $executable"
