# -*- coding: utf-8 -*-
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import json
import time
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from netatmoController import NetatmoController


# --- CONFIGURAZIONE ---
env_file = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "bot", ".env"))
if os.path.exists(env_file):
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
    except ImportError:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if "=" in line_str and not line_str.startswith("#"):
                    k, v = line_str.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

CLIENT_ID = os.getenv("NETATMO_CLIENT_ID", "68cd33d12bbaa06205099cf0")
CLIENT_SECRET = os.getenv("NETATMO_CLIENT_SECRET", "q66X8MVs6XUSu5hEFmsjr3Wet1GcT9tuQt")
REFRESH_TOKEN = os.getenv("NETATMO_REFRESH_TOKEN", "67eeeb5f6a703cd05a0f513a|84f5faf97c2f614b269e0a4a1fc93c23")
HOME_ID = os.getenv("NETATMO_HOME_ID", "67eeee4f0bc3615f0a0e016a")
ROOM_ID = os.getenv("NETATMO_ROOM_ID", "3893347837")
BASE_TOPIC = "Termostato.bticino"

netatmo = NetatmoController(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)

def on_connect(client, userdata, flags, rc, props):
    if rc == 0:
        client.subscribe(f"{BASE_TOPIC}/#")
        print("Bridge BTicino pronto.")

def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8").strip()
    try:
        if msg.topic == f"{BASE_TOPIC}/request_update":
            publish_status(client)

        elif msg.topic == f"{BASE_TOPIC}/sync_schedule":
            schedule_json = json.loads(payload)
            print(f"Sincronizzazione pianificazione: {schedule_json.get('name')}")
            netatmo.sync_home_schedule(HOME_ID, schedule_json)
            time.sleep(2)
            publish_status(client)

        elif msg.topic == f"{BASE_TOPIC}/boost":
            mins = int(payload) if payload else 30
            netatmo.set_room_thermostat(HOME_ID, ROOM_ID, "manual", temp=30.0, duration=mins)
            time.sleep(1.5)
            publish_status(client)

        elif msg.topic == f"{BASE_TOPIC}/temp":
            netatmo.set_room_thermostat(HOME_ID, ROOM_ID, "manual", temp=float(payload))
            time.sleep(1.5)
            publish_status(client)

        elif msg.topic == f"{BASE_TOPIC}/mode":
            netatmo.set_room_thermostat(HOME_ID, ROOM_ID, payload)
            time.sleep(1.5)
            publish_status(client)

    except Exception as e: print(f"Errore Bridge: {e}")

def publish_status(client):
    status = netatmo.get_home_status(HOME_ID)
    if status:
        rooms = status.get("home", {}).get("rooms", [])
        modules = status.get("home", {}).get("modules", [])
        room = next((r for r in rooms if str(r["id"]) == str(ROOM_ID)), None)

        humidity = None
        if room and "humidity" in room:
            humidity = room.get("humidity")
        elif modules:
            for mod in modules:
                if "humidity" in mod:
                    humidity = mod.get("humidity")
                    break

        if room:
            data = {
                "current_temp": room.get("therm_measured_temperature"),
                "target_temp": room.get("therm_setpoint_temperature"),
                "humidity": humidity,
                "mode": room.get("therm_setpoint_mode"),
                "active_schedule": status.get("home", {}).get("therm_schedule_id")
            }
            client.publish(f"{BASE_TOPIC}/status", json.dumps(data), retain=True)
        else:
            # Gestione del caso in cui la stanza non c'è (es. dispositivo offline / Errore 6)
            errors = status.get("errors", [])
            if errors:
                print(f"[!] Impossibile trovare la stanza. Il Cloud segnala errori: {errors}")
                # Pubblichiamo comunque uno stato "offline" per far sapere al sistema che è irraggiungibile
                data = {
                    "current_temp": 0.0,
                    "target_temp": 0.0,
                    "humidity": 0,
                    "mode": "offline",
                    "active_schedule": None
                }
                client.publish(f"{BASE_TOPIC}/status", json.dumps(data), retain=True)


import threading

if __name__ == "__main__":
    client = mqtt.Client(CallbackAPIVersion.VERSION2, f"BTicinoBridge_{os.getpid()}")
    client.on_connect = on_connect
    client.on_message = on_message

    print("Bridge BTicino in fase di avvio...")
    connected = False
    for attempt in range(1, 30):
        try:
            client.connect("localhost", 1883, 60)
            connected = True
            break
        except Exception as e:
            print(f"[!] Tentativo {attempt}/30: Broker MQTT non ancora pronto ({e}). Riprovo tra 5s...")
            time.sleep(5)

    if connected:
        # Avvia il loop di polling autonomo sincronizzato sui multipli esatti di 15 minuti (es. 23:00, 23:15, 23:30, 23:45)
        def periodic_status_loop():
            while True:
                now = time.time()
                sleep_sec = 900 - (now % 900)
                time.sleep(sleep_sec)
                try:
                    publish_status(client)
                except Exception as e:
                    print(f"[!] Errore nel polling autonomo BTicino: {e}")

        t_poll = threading.Thread(target=periodic_status_loop, daemon=True)
        t_poll.start()

        # Invia subito il primo stato all'avvio in background
        threading.Thread(target=publish_status, args=(client,), daemon=True, name="InitialStatusThread").start()
        client.loop_forever()
    else:
        print("[!] Impossibile connettersi al broker MQTT dopo 30 tentativi.")
        sys.exit(1)