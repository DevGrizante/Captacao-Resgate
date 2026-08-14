@echo off
title Captação e Resgate - Launcher
color 0B

echo ========================================================
echo       CAPTAÇÃO E RESGATE - INICIANDO O SISTEMA           
echo ========================================================
echo.

:: 1. Verifica se o Python esta instalado
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    color 0C
    echo [ERRO] O Python nao foi encontrado neste computador!
    echo.
    echo Por favor, instale o Python abrindo a Microsoft Store
    echo e pesquisando por "Python 3.11" ou superior.
    echo.
    pause
    exit /b
)

:: 2. Configurando o Backend
echo [INFO] Configurando o backend...
cd /d "%~dp0backend"

IF NOT EXIST ".venv\Scripts\activate.bat" (
    echo [INFO] Primeira execucao detectada. Criando ambiente virtual...
    python -m venv .venv
)

echo [INFO] Preparando as dependencias do backend...
call .venv\Scripts\activate.bat
pip install -r requirements.txt -q
if not exist ".env" copy .env.example .env

echo [INFO] Iniciando API (Backend)...
start "API - Captacao" cmd /k "cd /d "%~dp0backend" && call .venv\Scripts\activate.bat && uvicorn app.main:app --reload --port 8081"

:: Aguarda a API subir
timeout /t 3 >nul

:: 3. Configurando o Frontend
echo [INFO] Iniciando Frontend...
start "Front - Captacao" cmd /k "cd /d "%~dp0frontend" && python -m http.server 5500"

:: Aguarda o Front subir
timeout /t 2 >nul

:: 4. Finaliza e abre o navegador
echo.
echo ========================================================
echo [SUCESSO] Tudo pronto! 
echo.
echo O navegador abrira automaticamente.
echo Mantenha as janelas pretas abertas enquanto usa o painel.
echo ========================================================
echo.

start http://localhost:5500

pause
