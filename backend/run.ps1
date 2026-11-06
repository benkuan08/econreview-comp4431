param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv-backend\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv-backend and install backend/requirements.txt first. See backend/README.md.'
}
& $pythonPath -m uvicorn backend.app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
