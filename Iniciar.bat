@echo off
REM Sobe a API (porta 8000) e o front (porta 5500) em janelas separadas.
REM Requer Python no PATH e requirements ja instalados (veja README).

echo Iniciando Captacao e Resgate...

cd /d "%~dp0backend"
if not exist ".venv" (
    echo Criando ambiente virtual...
    python -m venv .venv
    call .venv\Scripts\activate
    pip install -r requirements.txt
    if not exist ".env" copy .env.example .env
) else (
    call .venv\Scripts\activate
)

start "API - Captacao" cmd /k "cd /d %~dp0backend && call .venv\Scripts\activate && uvicorn app.main:app --reload --port 8000"

timeout /t 2 >nul

start "Front - Captacao" cmd /k "cd /d %~dp0frontend && python -m http.server 5500"

timeout /t 2 >nul
start http://localhost:5500

echo.
echo API:   http://localhost:8000/docs
echo Front: http://localhost:5500
echo.
