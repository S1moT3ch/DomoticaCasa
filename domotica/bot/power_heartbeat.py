#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Client Heartbeat per Rilevamento Assenza Corrente Elettronica (Blackout)
Invia un segnale di vita (HTTP o WebSocket) ogni N secondi al server remoto Express.
"""
import time
import requests
import json
import os
import sys

# Carica variabili d'ambiente dal file .env
try:
    from dotenv import load_dotenv
    base_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(base_dir, ".env"))
except ImportError:
    pass

# URL del tuo Server Remoto Vercel
REMOTE_SERVER_URL = os.getenv("REMOTE_SERVER_URL", "https://powermonitor-one.vercel.app/api/heartbeat")
HEARTBEAT_SECRET = os.getenv("HEARTBEAT_SECRET", "SecretHeartbeatKey2026")
PING_INTERVAL_SECONDS = int(os.getenv("PING_INTERVAL_SECONDS", "60"))

def get_latest_power():
    """Recupera la lettura di consumo più recente per Alexa Skill e monitoraggio remoto"""
    try:
        import sqlite3
        base_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(base_dir, "logs", "domotica_events.db")
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path, timeout=5.0)
            c = conn.cursor()
            c.execute("SELECT power_w, voltage, current, timestamp FROM power_telemetry WHERE device_name = 'PowerMeterHome.shelly' ORDER BY id DESC LIMIT 1")
            row = c.fetchone()
            conn.close()
            if row:
                return {
                    "power_w": float(row[0]),
                    "voltage": float(row[1]) if row[1] is not None else 225.0,
                    "current": float(row[2]) if row[2] is not None else round(float(row[0])/225.0, 2),
                    "power_timestamp": row[3]
                }
    except Exception:
        pass
    return {}

def send_heartbeat(session=None):
    if session is None:
        session = requests.Session()

    headers = {
        "x-heartbeat-secret": HEARTBEAT_SECRET,
        "Content-Type": "application/json"
    }
    payload = {
        "secret": HEARTBEAT_SECRET,
        "timestamp": time.time()
    }
    # Arricchisce il payload con il consumo in tempo reale per Alexa
    latest_power = get_latest_power()
    if latest_power:
        payload.update(latest_power)

    for attempt in range(1, 4):
        try:
            resp = session.post(REMOTE_SERVER_URL, json=payload, headers=headers, timeout=8)
            if resp.status_code == 200:
                p_info = f" (Potenza: {latest_power.get('power_w', 0):.1f} W)" if latest_power else ""
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [+] Heartbeat inviato con successo al server remoto{p_info}.")
                try:
                    res_data = resp.json()
                    recovered = res_data.get("recovered_events", [])
                    if recovered:
                        for ev in recovered:
                            ev_desc = ev.get("description", ev.get("type", "unknown"))
                            dur = ev.get("duration_seconds", 0)
                            started = ev.get("started_at_iso", "N/D")
                            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [RECOVERY] Evento registrato da Vercel: {ev_desc} (Inizio: {started}, Durata: {dur}s)")
                except Exception:
                    pass
                return True

            elif resp.status_code == 429 or "quota_exceeded" in resp.text:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [!] Upstash Redis quota mensile superata (500k comandi). In attesa del reset mensile.")
                return "quota_exceeded"
            else:
                print(f"[!] Risposta server remoto ({resp.status_code}): {resp.text} (tentativo {attempt}/3)")
        except Exception as e:
            if attempt < 3:
                time.sleep(2)
            else:
                print(f"[!] Errore invio heartbeat verso {REMOTE_SERVER_URL}: {e}")
    return False

def main():
    print("=================================================================")
    print("      HEARTBEAT SENDER - RILEVAMENTO BLACKOUT CASALINGO")
    print("=================================================================")
    print(f"Server Remoto Target: {REMOTE_SERVER_URL}")
    print(f"Intervallo Ping: {PING_INTERVAL_SECONDS} secondi\n")

    session = requests.Session()
    while True:
        res = send_heartbeat(session)
        if res == "quota_exceeded":
            time.sleep(300)
        else:
            time.sleep(PING_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
