# Builds everything that is missing, then starts the web app on http://localhost:8000
param([switch]$Rebuild, [int]$Port = 8000)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment and installing packages..."
    python -m venv .venv
    & $py -m pip install --quiet --disable-pip-version-check -r requirements.txt
}
if ($Rebuild -or -not (Test-Path "data\lakehouse.duckdb")) {
    Write-Host "Building the lakehouse (generate, bronze, silver, gold, quality, ai)..."
    & $py -W ignore -m hip.pipelines.run all
    if ($LASTEXITCODE -ne 0) { throw "Pipeline failed" }
}
Write-Host "Starting the app on http://localhost:$Port  (Ctrl+C to stop)"
& $py -m uvicorn hip.api.app:app --port $Port
