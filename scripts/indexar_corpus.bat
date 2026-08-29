@echo off
title facelabs - indexar corpus (chequeo de caras)
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo No encuentro .venv\Scripts\python.exe en %cd%.
    echo Crea el entorno virtual primero, ver README.
    pause
    exit /b 1
)

REM Esto NO necesita el tablero ni Vite: habla directo con facid y escribe en
REM la MISMA cache (out\index.sqlite) que usa Run. Lo que se indexe aqui ya
REM queda disponible para buscar desde el navegador, y al reves.
REM
REM Es la corrida larga: decenas de miles de fotos, horas en CPU. Se puede
REM cortar con Ctrl+C sin perder nada — la proxima vez retoma donde quedo,
REM porque la cache es por contenido (sha256), no por corrida.

REM Si el API esta arriba, puede haber una indexacion en curso lanzada desde
REM el tablero. Dos procesos indexando el mismo corpus no corrompen nada (la
REM cache es por sha256 y SQLite espera su turno), pero duplican el trabajo:
REM cada foto se extraeria dos veces. Mejor avisar.
netstat -ano -p TCP | findstr /R /C:":8077 .*LISTENING" >nul
if %errorlevel% equ 0 (
    echo.
    echo AVISO: el API de facelabs esta arriba en el puerto 8077.
    echo Si dejaste una indexacion corriendo desde el tablero, esto la
    echo duplicaria: las dos harian el mismo trabajo por separado.
    echo.
    echo Revisa http://127.0.0.1:8077/api/corpus/indexar/estado
    echo Si dice "en_curso":false, puedes seguir sin problema.
    echo.
    choice /c SN /m "Continuar de todos modos"
    if errorlevel 2 exit /b 0
)

REM Sin esto Python bufferea stdout cuando no va a una consola y las lineas de
REM avance no se ven hasta que se llena el buffer. Mismo motivo que en dev.mjs.
set PYTHONUNBUFFERED=1

echo.
echo Indexando el corpus. Ctrl+C para detener (no se pierde lo ya hecho).
echo.

REM --device cpu explicito: esta maquina no tiene GPU NVIDIA, y el default de
REM facid es cuda. Sin esto arrancaria pidiendo CUDA para caerse a CPU igual,
REM o fallar segun como este el entorno.
.venv\Scripts\python.exe -m facid indexar-corpus --device cpu %*

echo.
echo Termino. La ventana queda abierta para que puedas leer el resumen.
pause
