@echo off
title MQTT Telegram Bot - z:\www\domotica\bot
cd /d "%~dp0"

echo ===================================================
echo   AVVIO SISTEMA MONITOR MQTT TELEGRAM BOT
echo ===================================================
echo.

REM Verifica e installazione dipendenze se necessario
echo [*] Verifica dipendenze Python...
python -m pip install -r requirements.txt --quiet

echo.
echo [*] Avvio del servizio in corso...
python main.py
pause
