@echo off
setlocal enabledelayedexpansion
title Captacao e Resgate - Launcher
color 0B
echo ========================================================
echo CAPTACAO E RESGATE - INICIANDO O SISTEMA
echo ========================================================
echo.

:: 1. Descobre um Python valido. Prioriza o launcher "py" (registrado
::    globalmente pelo instalador oficial), evitando pegar pythons
::    alternativos (MSYS2, Anaconda, etc.) que podem estar na frente no PATH.
set "PYEXE="
where py >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    py -3 --version >nul 2>&1
    IF !ERRORLEVEL! EQU 0 set "PYEXE=py -3"
)
IF NOT DEFINED PYEXE (
    where python >nul 2>&1
    IF %ERRORLEVEL% EQU 0 (
        python --version >nul 2>&1
        IF !ERRORLEVEL! EQU 0 set "PYEXE=python"
    )
)
IF NOT DEFINED PYEXE (
    color 0C
    echo [ERRO] Nenhum Python valido foi encontrado neste computador!
    echo.
    echo Instale o Python em https://www.python.org/downloads/
    echo e marque "Add python.exe to PATH" durante a instalacao.
    echo.
    pause
    exit /b 1
)
echo [INFO] Python detectado: !PYEXE!

:: 2. Verifica pastas do projeto
IF NOT EXIST "%~dp0backend" (
    color 0C
    echo [ERRO] Pasta "backend" nao encontrada em %~dp0
    pause
    exit /b 1
)
IF NOT EXIST "%~dp0frontend" (
    color 0C
    echo [ERRO] Pasta "frontend" nao encontrada em %~dp0
    pause
    exit /b 1
)

:: 3. Backend: cria o venv se preciso
cd /d "%~dp0backend"

IF NOT EXIST ".venv\Scripts\python.exe" (
    echo [INFO] Ambiente virtual ausente ou em formato incompativel. Recriando...
    IF EXIST ".venv" rmdir /s /q ".venv"
    !PYEXE! -m venv .venv
    IF !ERRORLEVEL! NEQ 0 (
        color 0C
        echo [ERRO] Falha ao criar o ambiente virtual.
        pause
        exit /b 1
    )
)

:: So aceita o layout padrao do Windows - nunca reaproveita um venv "bin\"
:: criado por um Python nao-oficial (ex: MSYS2)
set "VENV_PY=%~dp0backend\.venv\Scripts\python.exe"
IF NOT EXIST "!VENV_PY!" (
    color 0C
    echo [ERRO] O ambiente virtual foi criado, mas nao no formato esperado do Windows.
    echo Isso indica que "!PYEXE!" nao e um Python oficial do Windows.
    echo Desinstale/ignore Pythons alternativos ^(MSYS2, WSL, etc.^) do PATH e
    echo instale o Python oficial em https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Detecta o python.exe de dentro do venv, sem depender de activate.bat
set "VENV_PY=%~dp0backend\.venv\Scripts\python.exe"
IF NOT EXIST "!VENV_PY!" set "VENV_PY=%~dp0backend\.venv\bin\python.exe"
IF NOT EXIST "!VENV_PY!" (
    color 0C
    echo [ERRO] O ambiente virtual foi criado, mas o python.exe nao apareceu dentro dele.
    echo Isso indica um Python nao-padrao ^(ex: MSYS2^). Instale o Python oficial
    echo em https://www.python.org/downloads/ e rode este script novamente.
    pause
    exit /b 1
)

echo [INFO] Instalando dependencias do backend ^(pode demorar na 1a vez^)...
"!VENV_PY!" -m pip install --upgrade pip -q
"!VENV_PY!" -m pip install -r requirements.txt
IF !ERRORLEVEL! NEQ 0 (
    color 0C
    echo [ERRO] Falha ao instalar dependencias. Veja o erro do pip acima.
    pause
    exit /b 1
)

IF NOT EXIST ".env" (
    IF EXIST ".env.example" copy ".env.example" ".env" >nul
)

echo [INFO] Iniciando API ^(Backend^)...
start "API - Captacao" cmd /k ""!VENV_PY!" -m uvicorn app.main:app --reload --port 8000 --app-dir "%~dp0backend""

timeout /t 3 >nul

:: 4. Frontend
echo [INFO] Iniciando Frontend...
start "Front - Captacao" cmd /k "cd /d "%~dp0frontend" && !PYEXE! -m http.server 5500"

timeout /t 2 >nul

echo.
echo ========================================================
echo [SUCESSO] Tudo pronto!
echo O navegador abrira automaticamente.
echo Mantenha as janelas pretas abertas enquanto usa o painel.
echo ========================================================
echo.
start http://localhost:5500
pause
