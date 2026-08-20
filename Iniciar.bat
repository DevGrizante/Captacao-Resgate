@echo off
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
rem  UM PROCESSO SO (19/08/2026)
rem  Antes subiam dois: o uvicorn na 8000 e um `python -m http.server` na 5500
rem  para os arquivos do painel. O http.server fala HTTP/1.0 - fecha a conexao
rem  TCP a cada arquivo, nao manda ETag nem Cache-Control e nao comprime nada.
rem  Agora a propria API serve o painel: uma origem, uma porta, uma janela.
rem
rem  CONVIVENCIA COM O CVM MONITOR PRO
rem  Os dois projetos rodam juntos na mesma maquina. As portas padrao nao se
rem  cruzam (aqui 8000, la 8080) e, se a porta estiver ocupada, o script
rem  procura a proxima livre em vez de subir por cima de um servidor alheio.
rem  Cada projeto tem o seu proprio ambiente virtual, dentro da sua pasta.
rem ===========================================================================

set "API_PORT=8000"

echo ========================================================
echo       CAPTACAO E RESGATE - INICIANDO O SISTEMA
echo ========================================================
echo.

:: Verifica pastas do projeto
if not exist "%~dp0backend" (
    color 0C
    echo [ERRO] Pasta "backend" nao encontrada em %~dp0
    pause
    exit /b 1
)
if not exist "%~dp0frontend" (
    color 0C
    echo [ERRO] Pasta "frontend" nao encontrada em %~dp0
    pause
    exit /b 1
)

rem --- 1) Encontrar um Python utilizavel -------------------------------------
rem O "python" do PATH pode ser o atalho da Microsoft Store, que nao executa
rem nada e so abre a loja. Priorizamos versoes estaveis (3.10 a 3.12)
rem porque versoes muito recentes (3.13+) nao tem pacotes pre-compilados e tentam
rem baixar compiladores C/Rust, o que quebra em redes corporativas.
rem
rem Cada tentativa e um bloco `if` explicito. A forma compacta
rem `if not defined PY call :x && set ...` nao e confiavel em cmd: um `if` com
rem condicao falsa nao altera o errorlevel, e o `&&` acaba julgando o resultado
rem do comando ANTERIOR em vez do teste que acabou de rodar.
set "PY="
for %%c in ("py -3.12" "py -3.11" "py -3.10" "python" "py -3" "py -3.9") do (
    if not defined PY (
        call :testar_python %%c
        if not errorlevel 1 set "PY=%%~c"
    )
)

if not defined PY (
    color 0C
    echo [ERRO] Nao encontrei um Python 3.9 ou superior neste computador.
    echo.
    echo Instale pelo site oficial ^(python.org/downloads^) e marque a opcao
    echo "Add python.exe to PATH" durante a instalacao. Depois rode este
    echo arquivo de novo.
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('!PY! -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%v"
echo [INFO] Python !PYVER! encontrado.

rem --- 2) Ambiente virtual ---------------------------------------------------
cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Primeira execucao: criando o ambiente virtual isolado...
    echo        ^(pode levar um minuto; acontece so uma vez^)
    
    rem Contornar problemas de mapped drives (E:\ -> \\UNC...) com o pip do venv
    !PY! -m venv --without-pip .venv
    
    if not exist ".venv\Scripts\python.exe" (
        color 0C
        echo [ERRO] O Python falhou ao criar o python.exe dentro de .venv\Scripts\
        echo        Isso costuma acontecer em unidades de rede mapeadas.
        pause
        exit /b 1
    )
    
    echo [INFO] Instalando gerenciador de pacotes pip...
    .venv\Scripts\python.exe -m ensurepip --upgrade >nul 2>&1
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
    "!VENV_PY!" -m pip install --upgrade pip --quiet --disable-pip-version-check
    "!VENV_PY!" -m pip install -r requirements.txt --quiet --disable-pip-version-check
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
call :porta_livre !API_PORT! API_PORT

if "!API_PORT!"=="" (
    color 0C
    echo [ERRO] Nao achei nenhuma porta livre para a API.
    pause
    exit /b 1
)
echo [INFO] API e painel na porta !API_PORT!.

rem NOTA SOBRE 127.0.0.1 EM VEZ DE "localhost"
rem Medido nesta maquina: conectar em "localhost" custa 208 ms, contra 1,7 ms
rem em "127.0.0.1". O Windows resolve localhost para ::1 (IPv6) primeiro, os
rem servidores escutam so em IPv4, e cada conexao paga o timeout do fallback.
rem O front faz varias chamadas por tela, entao isso e a diferenca entre a
rem interface parecer instantanea e parecer travada. "localhost" continua
rem funcionando se o usuario digitar - so nao e mais o que geramos.

rem API_BASE vazio = mesma origem. O painel sai do mesmo servidor que a API,
rem entao o fetch e relativo: nao ha host para acertar, nao ha preflight de
rem CORS, e a porta pode mudar sem deixar o config.js apontando para o vazio.
rem
rem NENHUM PARENTESE NAS MENSAGENS ABAIXO. O bloco de escrita e delimitado por
rem parenteses, e o cmd o fecha no primeiro fecha-parentese que encontrar - de
rem dentro de um echo, de dentro de um rem, tanto faz. Escapar com ^^( tambem
rem nao resolve aqui. Travessao no lugar do parentese e o que funciona.
> "%~dp0frontend\js\config.js" (
    echo // Gerado pelo Iniciar.bat. Vazio = mesma origem do painel.
    echo // Preencha - por exemplo "http://127.0.0.1:8000" - apenas para
    echo // apontar este painel para uma API que roda em outra maquina.
    echo window.API_BASE = "";
)
rem Mesma origem dispensa CORS. A liberacao fica so para quem abrir o HTML
rem direto do disco (file://) ou de outro servidor durante um teste.
set "CORS_ORIGINS=http://127.0.0.1:!API_PORT!,http://localhost:!API_PORT!"

rem --- 5) Subir o servidor ----------------------------------------------------
rem A janela herda CORS_ORIGINS daqui - processo filho recebe o ambiente do
rem pai, o que evita passar a variavel dentro da linha de comando com aspas
rem aninhadas.
rem O caminho do interpretador vai RELATIVO ao /d da janela. Passar o caminho
rem absoluto exigiria aspas dentro do "cmd /k", que tem uma regra de remocao de
rem aspas propria e quebra quando a pasta do projeto tem espaco no nome.
rem Relativo nao tem espaco nunca.
rem
rem --host 127.0.0.1: escuta so no loopback IPv4. Sem isso o uvicorn abre em
rem   todas as interfaces - o que nao serve para nada aqui e ainda faz o
rem   Firewall do Windows perguntar se libera, a cada atualizacao do Python.
rem sem --reload: o vigia de arquivos re-varre o projeto o tempo todo e nao
rem   ajuda em nada quem so quer usar o painel.
echo [INFO] Iniciando a API e o painel...
start "Captacao e Resgate - porta !API_PORT!" /d "%~dp0backend" ^
  cmd /k .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port !API_PORT!

rem A API carrega dados da CVM no primeiro acesso; abrir o navegador antes de
rem ela responder mostraria a tela de erro de conexao sem motivo.
echo [INFO] Aguardando a API responder...
call :esperar_api !API_PORT!

rem Aquecimento: a primeira chamada de dados e a cara (le a planilha, cruza as
rem bases da CVM, classifica os fundos) e o resultado fica em memoria. Pagando
rem isso aqui, com o aviso na tela, o painel abre pronto em vez de mostrar
rem "Carregando..." por minutos sem explicacao.
echo [INFO] Carregando as bases (na primeira vez do dia isto demora)...
call :aquecer !API_PORT!

echo.
echo ========================================================
echo [SUCESSO] Tudo pronto^^!
echo.
echo   Dashboard ........ http://127.0.0.1:!API_PORT!/
echo   Tesourarias ...... http://127.0.0.1:!API_PORT!/tesourarias.html
echo   Papel bancario ... http://127.0.0.1:!API_PORT!/fundos.html
echo   Painel de controle http://127.0.0.1:!API_PORT!/admin.html
echo   API / docs ....... http://127.0.0.1:!API_PORT!/docs
echo.
echo Mantenha a janela preta aberta enquanto usa o painel.
echo Para desligar, feche-a.
echo ========================================================
echo.

start "" "http://127.0.0.1:!API_PORT!/"
ping -n 4 127.0.0.1 >nul
exit /b 0


rem ===========================================================================
rem  Sub-rotinas
rem ===========================================================================

rem Roda o interpretador candidato e confere a versao. Retorna 0 se serve.
rem :testar_python <comando> - o candidato serve?
rem
rem EXIGE QUE O INTERPRETADOR RESPONDA, e nao apenas que o errorlevel seja 0.
rem Motivo: `py -3.12` devolve 0 mesmo quando o 3.12 NAO esta instalado - o
rem launcher imprime "The system cannot find the path specified." e sai com
rem sucesso. Olhando so o errorlevel, o script elegia um interpretador que nao
rem existe, e a falha aparecia tres passos adiante, na criacao do venv, sem
rem relacao aparente com a causa. Interpretador ausente nao imprime nada.
rem
rem A PREFERENCIA POR 3.10-3.12 ESTA NA ORDEM DA LISTA, NAO AQUI. Versoes muito
rem novas as vezes ainda nao tem wheel para todos os pacotes e o pip cai para
rem compilar do fonte, o que quebra em rede corporativa - por isso elas sao as
rem ultimas da fila. Mas recusa-las de vez faria o script nao subir em maquina
rem que so tem a versao nova, que e um estrago maior que o risco que evita.
rem O codigo Python nao pode conter ">": dentro de `for /f ('...')` o cmd nao
rem desfaz o escape `^>` antes de entregar o comando, e o Python receberia
rem `version_info^>=(3,9)`, que e erro de sintaxe. Por isso a versao volta como
rem numero (313 para 3.13) e a comparacao acontece aqui no batch.
:testar_python
set "_resp="
for /f "delims=" %%r in ('%~1 -c "import sys;print(sys.version_info.major*100+sys.version_info.minor)" 2^>nul') do set "_resp=%%r"
if not defined _resp exit /b 1
if !_resp! GEQ 309 exit /b 0
exit /b 1

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

rem :aquecer <porta> - dispara a primeira carga de dados antes de abrir a tela.
rem
rem Sem teto curto, de proposito: com o cache da CVM vazio esta chamada baixa e
rem cruza as bases e passa de dois minutos. Cortar no meio nao economiza nada -
rem o trabalho continua no servidor e quem esperaria seria o usuario, ja com a
rem tela aberta e sem saber por que. Se falhar, seguimos: o painel dispara a
rem carga sozinho no primeiro acesso.
rem
rem O & da URL vai entre aspas duplas dentro do -c, entao o cmd nao o trata
rem como separador de comando.
:aquecer
rem O aquecimento faz LOGIN antes de pedir os dados: desde que o painel ganhou
rem senha, chamar /api/dashboard direto devolve 401. O scripts/aquecer.py cuida
rem disso e le a senha do proprio .env, entao ela nao aparece aqui nem no log.
"%VENV_PY%" "%~dp0backend\scripts\aquecer.py" %~1
if errorlevel 1 (
    echo [AVISO] Nao consegui pre-carregar as bases; o painel carrega ao abrir.
) else (
    echo [INFO] Bases carregadas.
)
exit /b 0
