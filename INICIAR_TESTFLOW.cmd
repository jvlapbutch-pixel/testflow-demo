@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Preparando ambiente virtual Python para este Windows...
  py -m venv .venv
  if errorlevel 1 (
    python -m venv .venv
    if errorlevel 1 (
      echo Python nao encontrado. Instale Python 3.11 ou superior.
      pause
      exit /b 1
    )
  )
)
echo Instalando ou verificando dependencias...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Falha na instalacao das dependencias.
  pause
  exit /b 1
)
echo TestFlow disponivel em http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
