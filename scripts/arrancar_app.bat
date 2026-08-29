@echo off
title facelabs - app (API + tablero en el 1000)
cd /d "%~dp0..\web"

if not exist "..\.venv\Scripts\python.exe" (
    echo No encuentro .venv\Scripts\python.exe en %~dp0..
    echo Crea el entorno virtual primero, ver README.
    pause
    exit /b 1
)

if not exist "node_modules" (
    echo No encuentro web\node_modules.
    echo Corre primero:  cd web ^&^& npm install
    pause
    exit /b 1
)

REM Un solo comando levanta LAS DOS COSAS: web\scripts\dev.mjs arranca uvicorn
REM (API en el 8077), espera a que /health responda, y recien entonces lanza
REM Vite (tablero en el 1000). Por eso no hace falta un .bat por proceso como
REM en chaturlist.
REM
REM Si el API ya esta arriba, dev.mjs lo detecta y NO levanta un segundo: se
REM engancha al que hay. Y si el 8077 lo tiene otro servicio que no es facid,
REM avisa y no arranca, en vez de competir por la misma base.

REM Vite tiene strictPort:false, asi que si el 1000 esta ocupado se mueve al
REM 1001 y el tablero abriria en otra direccion sin decir nada aqui. Mejor
REM avisarlo ahora que buscarlo despues.
netstat -ano -p TCP | findstr /R /C:":1000 .*LISTENING" >nul
if %errorlevel% equ 0 (
    echo.
    echo AVISO: ya hay algo escuchando en el puerto 1000.
    echo Probablemente el tablero ya esta arriba: abre http://localhost:1000
    echo Si arrancas otro, Vite se movera al 1001 y la direccion cambiara.
    echo.
    choice /c SN /m "Arrancar de todos modos"
    if errorlevel 2 exit /b 0
)

echo.
echo Arrancando la app completa:
echo   tablero : http://localhost:1000
echo   API     : http://127.0.0.1:8077/health
echo.
echo Ctrl+C detiene las dos cosas.
echo.

REM npm run dev = node scripts/dev.mjs (ver web\package.json).
call npm run dev

echo.
echo La app se detuvo.
pause
