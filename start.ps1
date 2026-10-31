<#
.SYNOPSIS
  Sports Edge AI - local launcher (Windows PowerShell 5.1+).
.PARAMETER Install
  One-time setup: create .venv and install dependencies from PyPI (requires network).
.PARAMETER Port
  Local port of the interface (default 8501). The server listens on 127.0.0.1 only.
#>
param([switch]$Install, [int]$Port = 8501)
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if ($Install) {
    if (-not (Test-Path $py)) { python -m venv .venv }
    & $py -m pip install -e ".[app,dev]"
}
if (-not (Test-Path $py)) {
    Write-Host '[Sports Edge AI] Brak .venv. Uruchom: powershell -ExecutionPolicy Bypass -File start.ps1 -Install'
    exit 1
}
Write-Host '[Sports Edge AI] Sprawdzanie konfiguracji...'
& $py -m sports_edge.cli check
if ($LASTEXITCODE -ne 0) {
    Write-Host '[Sports Edge AI] Konfiguracja niepoprawna - popraw bledy wyzej.'
    exit 1
}
Write-Host "[Sports Edge AI] Interfejs: http://localhost:$Port"
& $py -m streamlit run src\sports_edge\app\main.py --server.address 127.0.0.1 --server.port $Port --browser.gatherUsageStats false
