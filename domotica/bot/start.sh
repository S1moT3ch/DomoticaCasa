#!/bin/sh
cd "$(dirname "$0")"

echo "==================================================="
echo "   AVVIO SISTEMA MONITOR MQTT TELEGRAM BOT"
echo "==================================================="

echo "[*] Installazione dipendenze..."
python3 -m pip install -r requirements.txt --quiet 2>/dev/null || python -m pip install -r requirements.txt --quiet

echo "[*] Avvio del servizio..."
python3 main.py 2>/dev/null || python main.py
