@echo off
<<<<<<< HEAD
setlocal EnableDelayedExpansion
title Captacao e Resgate - Launcher
color 0B
cd /d "%~dp0"

rem ===========================================================================
rem  CAPTACAO E RESGATE - inicializador autossuficiente
rem
rem  Exigencia unica: Python 3.10+ instalado. O resto (ambiente virtual,
rem  dependencias, arquivo .env, portas) este script resolve sozinho.
rem
rem  CONVIVENCIA COM O CVM MONITOR PRO
rem  Os dois projetos rodam juntos na mesma maquina. As portas padrao nao se
rem  cruzam (aqui 8000/5500, la 8080) e, se alguma estiver ocupada, o script
rem  procura a proxima livre em vez de subir por cima de um servidor alheio.
rem  Cada projeto tem o seu proprio ambiente virtual, dentro da sua pasta.
rem ===========================================================================

set "API_PORT=8000"
set "WEB_PORT=5500"

echo ========================================================
echo       CAPTACAO E RESGATE - INICIANDO O SISTEMA
echo ========================================================
echo.

rem --- 1) Encontrar um Python utilizavel -------------------------------------
rem O "python" do PATH pode ser o atalho da Microsoft Store, que nao executa
rem nada e so abre a loja. Por isso testamos rodando de fato, e caimos no
rem lancador "py -3" quando o primeiro nao serve.
set "PY="
call :testar_python "python" && set "PY=python"
if not defined PY call :testar_python "py -3" && set "PY=py -3"

if not defined PY (
    color 0C
    echo [ERRO] Nao encontrei um Python 3.10 ou superior neste computador.
    echo.
    echo Instale pelo site oficial ^(python.org/downloads^) e marque a opcao
    echo "Add python.exe to PATH" durante a instalacao. Depois rode este
    echo arquivo de novo.
    echo.
    pause
    exit /b 1
=======
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
>>>>>>> bf068a7c2f2bfc1dc7325a58c22b1a5b66a748e1
)
for /f "delims=" %%v in ('%PY% -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%v"
echo [INFO] Python %PYVER% encontrado.

<<<<<<< HEAD
rem --- 2) Ambiente virtual ---------------------------------------------------
cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Primeira execucao: criando o ambiente virtual isolado...
    echo        ^(pode levar um minuto; acontece so uma vez^)
    %PY% -m venv .venv
    if errorlevel 1 (
        color 0C
        echo [ERRO] Falhei ao criar o ambiente virtual em backend\.venv
        pause
        exit /b 1
    )
)
set "VENV_PY=%~dp0backend\.venv\Scripts\python.exe"

rem --- 3) Dependencias -------------------------------------------------------
rem Instalar a cada clique custaria uns 10 segundos por nada. Guardamos uma
rem copia do requirements.txt dentro do venv: enquanto os dois forem iguais,
rem o ambiente ja esta correto e o pip nem e chamado.
set "PRECISA_INSTALAR=1"
if exist ".venv\requirements.lock" (
    fc /b "requirements.txt" ".venv\requirements.lock" >nul 2>&1 && set "PRECISA_INSTALAR=0"
)

if "!PRECISA_INSTALAR!"=="1" (
    echo [INFO] Instalando as dependencias do backend...
    "%VENV_PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
    "%VENV_PY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
    if errorlevel 1 (
        color 0C
        echo [ERRO] Falhei ao instalar as dependencias.
        echo        Verifique a conexao com a internet e tente de novo.
        pause
        exit /b 1
    )
    copy /y "requirements.txt" ".venv\requirements.lock" >nul
    echo [INFO] Dependencias instaladas.
) else (
    echo [INFO] Dependencias ja instaladas.
)

if not exist ".env" (
    echo [INFO] Criando backend\.env a partir do exemplo...
    copy /y ".env.example" ".env" >nul
)

rem --- 4) Portas -------------------------------------------------------------
rem Se a porta padrao estiver ocupada (outra copia do projeto, ou qualquer
rem outro servico), andamos para a proxima livre. Subir em cima de um servidor
rem que ja esta la daria um erro obscuro no meio do log do uvicorn.
call :porta_livre %API_PORT% API_PORT
call :porta_livre %WEB_PORT% WEB_PORT

if "!API_PORT!"=="" (
    color 0C
    echo [ERRO] Nao achei nenhuma porta livre para a API.
    pause
    exit /b 1
)
if "!WEB_PORT!"=="" (
    color 0C
    echo [ERRO] Nao achei nenhuma porta livre para o frontend.
    pause
    exit /b 1
)
echo [INFO] API na porta !API_PORT! - frontend na porta !WEB_PORT!

rem O front precisa saber onde a API subiu, e a API precisa liberar o CORS
rem para onde o front subiu. As duas pontas saem daqui, do mesmo lugar.
> "%~dp0frontend\js\config.js" (
    echo // Gerado pelo Iniciar.bat com a porta em que a API subiu.
    echo // Editar a mao tambem funciona.
    echo window.API_BASE = "http://localhost:!API_PORT!";
)
set "CORS_ORIGINS=http://localhost:!WEB_PORT!,http://127.0.0.1:!WEB_PORT!"

rem --- 5) Subir os servidores ------------------------------------------------
rem As duas janelas herdam CORS_ORIGINS daqui - processo filho recebe o
rem ambiente do pai, o que evita ter que passar a variavel dentro da linha de
rem comando com aspas aninhadas.
rem Os caminhos do interpretador vao RELATIVOS ao /d de cada janela. Passar o
rem caminho absoluto exigiria aspas dentro do "cmd /k", que tem uma regra de
rem remocao de aspas propria e quebra quando a pasta do projeto tem espaco no
rem nome. Relativo nao tem espaco nunca.
echo [INFO] Iniciando a API...
start "API - Captacao porta !API_PORT!" /d "%~dp0backend" ^
  cmd /k .venv\Scripts\python.exe -m uvicorn app.main:app --port !API_PORT!

echo [INFO] Iniciando o frontend...
start "Front - Captacao porta !WEB_PORT!" /d "%~dp0frontend" ^
  cmd /k ..\backend\.venv\Scripts\python.exe -m http.server !WEB_PORT!

rem A API carrega dados da CVM no primeiro acesso; abrir o navegador antes de
rem ela responder mostraria a tela de erro de conexao sem motivo.
echo [INFO] Aguardando a API responder...
call :esperar_api !API_PORT!

echo.
echo ========================================================
echo [SUCESSO] Tudo pronto^^!
echo.
echo   Dashboard ........ http://localhost:!WEB_PORT!
echo   Painel de controle http://localhost:!WEB_PORT!/admin.html
echo   API / docs ....... http://localhost:!API_PORT!/docs
echo.
echo Mantenha as duas janelas pretas abertas enquanto usa o painel.
echo Para desligar, feche-as.
echo ========================================================
echo.

start "" "http://localhost:!WEB_PORT!"
ping -n 4 127.0.0.1 >nul
exit /b 0


rem ===========================================================================
rem  Sub-rotinas
rem ===========================================================================

rem Roda o interpretador candidato e confere a versao. Retorna 0 se serve.
:testar_python
%~1 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
exit /b %errorlevel%

rem :porta_livre <porta inicial> <nome da variavel de saida>
rem Anda para cima ate achar uma porta sem ninguem ouvindo. Devolve vazio se
rem as 20 seguintes tambem estiverem ocupadas - nesse caso a maquina tem outro
rem problema, e insistir so esconderia isso.
:porta_livre
set /a "_p=%~1"
set /a "_teto=%~1+20"
:_proxima_porta
netstat -an -p TCP | findstr /c:":!_p! " | findstr /i /c:"LISTENING" >nul 2>&1
if errorlevel 1 (
    set "%~2=!_p!"
    exit /b 0
)
echo [INFO] Porta !_p! ocupada, tentando a proxima...
set /a "_p+=1"
if !_p! gtr !_teto! (
    set "%~2="
    exit /b 1
)
goto :_proxima_porta

rem :esperar_api <porta> - sonda /health ate responder, com teto de ~40s.
:esperar_api
for /l %%i in (1,1,20) do (
    "%VENV_PY%" -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:%~1/health',timeout=2); sys.exit(0)" >nul 2>&1
    if not errorlevel 1 (
        echo [INFO] API respondendo.
        exit /b 0
    )
    ping -n 3 127.0.0.1 >nul
)
echo [AVISO] A API demorou a responder. A janela dela mostra o que aconteceu;
echo         o navegador vai abrir mesmo assim.
exit /b 0
=======
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
>>>>>>> bf068a7c2f2bfc1dc7325a58c22b1a5b66a748e1
