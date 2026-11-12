param([Parameter(Mandatory = $true)][string]$ModelDir)

$ErrorActionPreference = 'Stop'
$modelPath = (Resolve-Path -LiteralPath $ModelDir).Path
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

& py -3.12 scripts/download_base_model.py --verify-dir $modelPath
if ($LASTEXITCODE -ne 0) { throw 'The downloaded model did not pass verification.' }

$pythonPath = Join-Path $projectRoot '.venv-backend\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    & py -3.12 -m venv .venv-backend
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python 3.12 environment.' }
}

& $pythonPath -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'Could not install the CUDA PyTorch packages.' }
& $pythonPath -m pip install -r backend/requirements.txt -r training/requirements-inference.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install the EconReview packages.' }
& $pythonPath scripts/download_adapter.py
if ($LASTEXITCODE -ne 0) { throw 'Could not verify or restore the trained adapter.' }

@'
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key

from backend.config import BASE_MODEL_ID, BASE_MODEL_REVISION, Settings

env_file = Path('.env')
existing = dotenv_values(env_file, interpolate=False)
values = {
    'BACKEND_MODEL_ID': BASE_MODEL_ID,
    'BACKEND_MODEL_REVISION': BASE_MODEL_REVISION,
    'BACKEND_MODEL_DIR': sys.argv[1],
    'BACKEND_ADAPTER': Settings.adapter,
    'BACKEND_MODEL_URL': Settings.model_url,
    'BACKEND_MODEL_TOKEN': existing.get('BACKEND_MODEL_TOKEN') or secrets.token_hex(32),
}
for key, value in values.items():
    set_key(env_file, key, value)
'@ | & $pythonPath - $modelPath
if ($LASTEXITCODE -ne 0) { throw 'Could not write the local model settings.' }

Write-Output 'Environment ready. Start the model service and the website in separate PowerShell windows.'
Write-Output '.\.venv-backend\Scripts\python.exe -m uvicorn backend.gpu_service:app --host 127.0.0.1 --port 8100'
Write-Output '.\backend\run.ps1'
