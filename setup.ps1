$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot
python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

if (-not (Test-Path -LiteralPath "config.yaml")) {
    Copy-Item -LiteralPath "config.example.yaml" -Destination "config.yaml"
    Write-Host "Created config.yaml. Set the OpenWebUI URL, API key, and model before running."
}

Write-Host "Setup complete. Test with: .\run.ps1 -Once"

