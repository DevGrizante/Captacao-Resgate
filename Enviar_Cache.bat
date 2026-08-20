@echo off
setlocal EnableDelayedExpansion
title Enviar cache para o servidor
set "RAIZ=%~dp0"
cd /d "%~dp0"

rem ===========================================================================
rem  ENVIA O CACHE DA CVM PARA O SERVIDOR
rem
rem  Por que isto existe: ler os parquet prontos custa 366 MB de pico;
rem  RECONSTRUIR o cache a partir da CVM custa 2.078 MB - medido. Numa VM de
rem  1 GB o segundo caminho nao cabe, entao o servidor fica proibido de
rem  baixar qualquer coisa (TTLs travados em 10 anos pelo instalar.sh) e
rem  quem constroi o cache passa a ser esta maquina, que tem RAM de sobra.
rem
rem  USO
rem    Enviar_Cache.bat 129.153.x.x
rem    Enviar_Cache.bat 129.153.x.x /agendado
rem
rem  QUANDO RODAR
rem    Uma vez para colocar o servidor de pe, e depois quando quiser levar
rem    dado novo da CVM - semanalmente ja e bastante. A planilha diaria do
rem    e-mail NAO vem por aqui: ela tem caminho proprio, o
rem    Coletar_e_Enviar.bat, que fala com a API.
rem
rem  Requisito: o cliente OpenSSH do Windows (scp), que o Windows 10/11 ja
rem  traz. Confira com: where scp
rem ===========================================================================

rem Varre os argumentos em vez de assumir a posicao: assim /agendado pode vir
rem antes ou depois do endereco. Tomar o %1 as cegas fazia
rem `Enviar_Cache.bat /agendado` tentar conectar num servidor chamado
rem '/agendado'.
rem A raiz ja foi guardada la em cima, antes deste laco: `shift` desloca
rem tambem o %0, e depois dele %~dp0 nao aponta mais para este arquivo.
set "IP="
set "AGENDADO="
:proximo_arg
if "%~1"=="" goto fim_args
if /i "%~1"=="/agendado" (
  set "AGENDADO=1"
) else (
  if not defined IP set "IP=%~1"
)
shift
goto proximo_arg
:fim_args

if "!IP!"=="" (
  echo.
  echo  Uso: Enviar_Cache.bat ENDERECO_DO_SERVIDOR
  echo.
  echo  Exemplo:  Enviar_Cache.bat 129.153.10.20
  echo.
  if not defined AGENDADO pause
  exit /b 1
)

set "CHAVE=%USERPROFILE%\.ssh\oracle_painel"
if not exist "!CHAVE!" (
  echo.
  echo  [ERRO] Nao achei a chave privada em:
  echo         !CHAVE!
  echo.
  echo  E o arquivo SEM .pub no fim, gerado junto com a chave publica
  echo  que voce enviou para a Oracle.
  echo.
  if not defined AGENDADO pause
  exit /b 1
)

if not exist "!RAIZ!data\cache\*.parquet" (
  echo.
  echo  [ERRO] Nao ha cache para enviar em data\cache\.
  echo         Rode o painel local uma vez para construi-lo.
  echo.
  if not defined AGENDADO pause
  exit /b 1
)

echo.
echo  Enviando o cache para !IP! ...
echo.
for %%f in ("!RAIZ!data\cache\*") do echo    %%~nxf  -  %%~zf bytes
echo.

rem A pasta de destino precisa existir antes do scp; o -p do mkdir evita
rem erro quando ela ja esta la.
ssh -i "!CHAVE!" -o StrictHostKeyChecking=accept-new ubuntu@!IP! "mkdir -p ~/painel/data/cache"
if errorlevel 1 (
  echo  [ERRO] Nao consegui conectar em !IP!.
  if not defined AGENDADO pause
  exit /b 2
)

scp -i "!CHAVE!" -o StrictHostKeyChecking=accept-new "!RAIZ!data\cache\*" ubuntu@!IP!:~/painel/data/cache/
set "CODIGO=!errorlevel!"

if "!CODIGO!"=="0" (
  echo.
  echo  Cache enviado. Recarregando o painel no servidor...
  ssh -i "!CHAVE!" ubuntu@!IP! "sudo systemctl restart painel"
  echo.
  echo  [OK] Pronto. O servidor agora le o cache novo.
) else (
  echo.
  echo  [FALHOU] O scp devolveu !CODIGO!.
)
echo.

if not defined AGENDADO pause
exit /b !CODIGO!
