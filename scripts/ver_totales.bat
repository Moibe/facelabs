@echo off
title facelabs - total de caras del corpus
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo No encuentro .venv\Scripts\python.exe en %cd%.
    echo Crea el entorno virtual primero, ver README.
    pause
    exit /b 1
)

REM Solo CONSULTA: no carga el modelo ni toca una sola foto, sale todo de
REM out\index.sqlite. Por eso responde al instante aunque el corpus tenga
REM decenas de miles de fotos, y da igual si hay una indexacion corriendo.
REM
REM Para AVANZAR el conteo (la corrida larga) es indexar_corpus.bat.

.venv\Scripts\python.exe -m facid cobertura %*

echo.
pause
