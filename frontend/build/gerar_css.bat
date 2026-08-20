@echo off
rem ===========================================================================
rem  Gera frontend/css/tailwind.css a partir das classes usadas no painel.
rem
rem  So precisa rodar quando alguem MUDA as classes do HTML/JS. O CSS gerado
rem  fica versionado, entao quem so quer rodar o painel nao precisa de Node.
rem
rem  Requisito: Node.js instalado (node --version).
rem ===========================================================================
setlocal
cd /d "%~dp0"

where node >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Node.js nao encontrado no PATH.
  echo  O CSS ja versionado continua valendo: so rode isto se mudou as classes.
  echo  Instale em https://nodejs.org e rode de novo.
  echo.
  exit /b 1
)

if not exist "node_modules\tailwindcss" (
  echo Instalando o Tailwind ^(so na primeira vez^)...
  call npm install --no-audit --no-fund --loglevel=error
  if errorlevel 1 (
    echo  Falha no npm install. Sem rede ou proxy bloqueando o registry.
    exit /b 1
  )
)

echo Gerando ..\css\tailwind.css ...
call npx tailwindcss -c tailwind.config.js -i tailwind.entrada.css -o ..\css\tailwind.css --minify
if errorlevel 1 exit /b 1

rem O build e estatico: classe montada em runtime nao entra no CSS e o
rem elemento fica sem estilo, silenciosamente. O verificador pega isso.
where py >nul 2>&1
if not errorlevel 1 (
  echo.
  py verificar_classes.py
)

echo.
echo Pronto. Lembre de subir o ?v= dos assets nos .html se o CSS mudou.
endlocal
