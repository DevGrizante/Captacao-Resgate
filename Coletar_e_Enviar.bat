@echo off
setlocal EnableDelayedExpansion
title Coletor - Captacao e Resgate
cd /d "%~dp0"

rem ===========================================================================
rem  COLETOR DA PLANILHA DIARIA
rem
rem  Le o e-mail da Quantum no Outlook, salva o anexo e publica no servidor.
rem  E a unica peca que ainda precisa rodar no Windows: o Outlook so existe
rem  aqui. Depois que este script roda, qualquer maquina le o dado do dia pela
rem  rede, sem instalar nada.
rem
rem  USO
rem    Coletar_e_Enviar.bat                 duplo clique, espera ENTER no fim
rem    Coletar_e_Enviar.bat /agendado       para o Agendador de Tarefas
rem    Coletar_e_Enviar.bat --destino https://painel.suaempresa.com
rem    Coletar_e_Enviar.bat --sem-outlook   publica o que ja esta na inbox
rem
rem  COMO AGENDAR (Agendador de Tarefas do Windows)
rem    Acao ....... Iniciar um programa
rem    Programa ... o caminho completo deste arquivo
rem    Argumentos . /agendado
rem    Gatilho .... diariamente 08:15, repetindo a cada 1h durante 3h
rem
rem  A repeticao cobre o e-mail que atrasa: se as 08:15 ainda nao chegou, as
rem  09:15 tenta de novo. Reenviar o mesmo arquivo nao custa nada - o servidor
rem  compara o hash e ignora repetido.
rem
rem  NAO marque 'Executar estando o usuario conectado ou nao'. O Outlook
rem  precisa da sessao do usuario aberta para responder ao COM; numa sessao
rem  desconectada a leitura falha silenciosamente e o script acaba publicando
rem  a planilha da vespera.
rem ===========================================================================

rem A raiz precisa ser guardada ANTES do laco de argumentos: `shift` desloca
rem tambem o %0, e depois dele %~dp0 deixa de ser a pasta deste arquivo.
set "RAIZ=%~dp0"

rem --- Separa /agendado dos argumentos que vao para o Python ----------------
set "AGENDADO="
set "ARGS="
:proximo_arg
if "%~1"=="" goto fim_args
if /i "%~1"=="/agendado" (
  set "AGENDADO=1"
) else (
  set "ARGS=!ARGS! %1"
)
shift
goto proximo_arg
:fim_args

set "VENV_PY=!RAIZ!backend\.venv\Scripts\python.exe"
if not exist "!VENV_PY!" (
  echo.
  echo  [ERRO] O ambiente Python ainda nao existe nesta maquina.
  echo.
  echo         Rode o Iniciar.bat UMA VEZ para monta-lo, e feche a janela
  echo         preta que ele abrir. Depois disto ele nao e mais necessario:
  echo         quem serve o painel e o servidor, e este coletor so precisa
  echo         do ambiente que aquele primeiro clique deixou pronto.
  echo.
  if not defined AGENDADO pause
  exit /b 1
)

set "PASTA_LOG=!RAIZ!data\logs"
if not exist "!PASTA_LOG!" mkdir "!PASTA_LOG!"

rem Um log por mes: da para investigar a semana passada sem abrir um arquivo
rem de dezenas de milhares de linhas nem lidar com 300 arquivinhos por ano.
rem A data vem do PowerShell porque %DATE% muda de formato conforme a regiao
rem do Windows, e um log chamado coletor_19/08.log nao existe.
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM"') do set "MES=%%d"
if not defined MES set "MES=sem-data"
set "LOG=!PASTA_LOG!\coletor_!MES!.log"

echo.
echo  Coletando a planilha do dia...
echo.

rem O log sai em UTF-8. Notepad e VS Code abrem certo; o Get-Content do
rem PowerShell 5.1 mostra acento errado, o que e problema do leitor e nao do
rem arquivo. Fixar a codificacao aqui evita depender da regiao do Windows.
set "PYTHONIOENCODING=utf-8"

rem Roda UMA vez, guarda a saida, e so entao mostra na tela e anexa ao log.
rem Rodar duas vezes (uma para a tela, outra para o arquivo) leria o Outlook
rem duas vezes e enviaria duas vezes.
set "SAIDA=%TEMP%\coletor_captacao_!RANDOM!.txt"
"!VENV_PY!" backend\scripts\coletar_vinculado.py !ARGS! > "!SAIDA!" 2>&1
set "CODIGO=!errorlevel!"

type "!SAIDA!"
>> "!LOG!" echo ---------- %DATE% %TIME% ----------
type "!SAIDA!" >> "!LOG!"
del "!SAIDA!" >nul 2>&1

echo.
if "!CODIGO!"=="0" (
  echo  [OK] Planilha do dia publicada.
) else (
  echo  [FALHOU] Codigo !CODIGO!. O detalhe esta em:
  echo           !LOG!
)
echo.

if not defined AGENDADO pause
exit /b !CODIGO!
