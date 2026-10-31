@echo off
rem Sports Edge AI - local Windows launcher. No network access at start-up.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [Sports Edge AI] Brak srodowiska .venv. Utworz je jednorazowo:
  echo     python -m venv .venv
  echo     .venv\Scripts\python -m pip install -e ".[app,dev]"
  echo lub uruchom: powershell -ExecutionPolicy Bypass -File start.ps1 -Install
  pause
  exit /b 1
)
echo [Sports Edge AI] Sprawdzanie konfiguracji...
"%PY%" -m sports_edge.cli check
if errorlevel 1 (
  echo [Sports Edge AI] Konfiguracja niepoprawna - popraw bledy wyzej.
  pause
  exit /b 1
)
echo [Sports Edge AI] Interfejs: http://localhost:8501  (zamknij okno lub Ctrl+C, aby zakonczyc)
"%PY%" -m streamlit run src\sports_edge\app\main.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
endlocal
