# -*- coding: utf-8 -*-
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import json
import time
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from daikinController import (
    set_daikin_on_off, 
    set_daikin_temperature, 
    set_daikin_mode,
    set_daikin_fan_speed,
    get_daikin_status
)


# --- CONFIGURAZIONE ---
MQTT_BROKER = "localhost" 
DEVICE_ID = "4081c038-9743-49fb-8ffc-c4a1057b8329"
BASE_TOPIC = "Canalizzato.daikin"

call_counter = 0
# Memorizza il modo operativo in RAM per evitare la GET ridondante prima dei comandi
last_known_mode = "cooling" 

def log_api_call(function_name, details=""):
    global call_counter
    call_counter += 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [CALL #{call_counter}] API Cloud Daikin -> {function_name} {details}")

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"Connesso al broker MQTT. Sottoscrizione a {BASE_TOPIC}/#")
        client.subscribe(f"{BASE_TOPIC}/#")
    else:
        print(f"Errore connessione MQTT: {reason_code}")

def on_message(client, userdata, msg):
    global last_known_mode
    payload = msg.payload.decode("utf-8").strip()
    
    if not payload and msg.topic != f"{BASE_TOPIC}/request_update":
        return

    try:
        # 1. Richiesta aggiornamento esplicito dalla UI
        if msg.topic == f"{BASE_TOPIC}/request_update":
            publish_status(client)

        # 2. Accensione / Spegnimento Clima
        elif msg.topic == f"{BASE_TOPIC}/power":
            log_api_call("DaikinOnOff", f"Valore: {payload}")
            set_daikin_on_off(DEVICE_ID, payload)
            time.sleep(1.2) # Pausa minima necessaria all'allineamento cloud-macchina
            publish_status(client)

        # 3. Variazione Temperatura Target
        elif msg.topic == f"{BASE_TOPIC}/temp":
            temp = float(payload)
            log_api_call("DaikinTemperature", f"Temp: {temp}C, Modo locale: {last_known_mode}")
            set_daikin_temperature(DEVICE_ID, temp, mode=last_known_mode)
            time.sleep(1.2)
            publish_status(client)

        # 4. Cambio Modalita Operativa (Freddo, Caldo, Secco, Ventola)
        elif msg.topic == f"{BASE_TOPIC}/mode":
            log_api_call("DaikinMode", f"Nuovo modo: {payload}")
            last_known_mode = payload.lower()
            set_daikin_mode(DEVICE_ID, payload)
            time.sleep(1.2)
            publish_status(client)

        # 5. Cambio Velocita Ventola (Auto, Quiet, V1, V2, V3)
        elif msg.topic == f"{BASE_TOPIC}/fan":
            log_api_call("DaikinFanSpeed", f"Velocita: {payload}, Modo locale: {last_known_mode}")
            set_daikin_fan_speed(DEVICE_ID, payload, mode=last_known_mode)
            time.sleep(1.2)
            publish_status(client)

    except Exception as e:
        print(f"Errore gestione messaggio su {msg.topic}: {e}")

def publish_status(client):
    global last_known_mode
    log_api_call("DaikinStatus", "(Aggiornamento UI)")
    status = get_daikin_status(DEVICE_ID)
    if status:
        try:
            control = status['managementPoints'][1]
            mode = control['operationMode']['value']
            
            # Allinea la variabile locale con la verita del Cloud
            last_known_mode = mode.lower()
            
            # --- ESTRAZIONE VENTOLA CORRETTA ---
            fan_data = control.get('fanControl', {}).get('value', {}).get('operationModes', {}).get(mode, {})
            fan_speed_config = fan_data.get('fanSpeed', {})
            
            # Verifichiamo se il cloud riporta esplicitamente la modalita automatica attiva
            current_fan_mode = fan_speed_config.get('currentMode', {}).get('value', 'fixed')
            
            if str(current_fan_mode).lower() == 'auto':
                fan_speed_val = 'auto'
            else:
                # Se non e' auto, estrae in sicurezza il valore fisso o quiet memorizzato
                fan_speed_val = fan_speed_config.get('modes', {}).get('fixed', {}).get('value', '1')
            
            # Estrazione sicura umidità ambiente Daikin (se supportata dal sensore)
            sensory_data = control.get('sensoryData', {}).get('value', {})
            room_humidity = sensory_data.get('roomHumidity', {}).get('value') if isinstance(sensory_data.get('roomHumidity'), dict) else None

            # --- ESTRAZIONE SICURA TEMPERATURA TARGET (Gestisce anche la modalità 'dry' deumidificazione) ---
            target_temp_val = 24
            try:
                op_modes = control.get('temperatureControl', {}).get('value', {}).get('operationModes', {})
                mode_op = op_modes.get(mode) or op_modes.get('cooling') or op_modes.get('heating') or {}
                setpoints = mode_op.get('setpoints', {}).get('roomTemperature', {})
                if 'value' in setpoints:
                    target_temp_val = int(setpoints['value'])
            except Exception:
                pass

            # Generazione payload JSON pulito per MQTT
            data = {
                "power": control['onOffMode']['value'],
                "mode": mode.lower(),
                "target_temp": target_temp_val,
                "room_temp": sensory_data.get('roomTemperature', {}).get('value'),
                "room_humidity": room_humidity,
                "fan_speed": str(fan_speed_val).lower(), # Garantisce la stringa "auto" quando attiva
                "online": status['isCloudConnectionUp']['value']
            }
            
            # Pubblicazione con retain attivo per consentire la sincronizzazione immediata all'apertura web
            client.publish(f"{BASE_TOPIC}/status", json.dumps(data), retain=True)

            print(f"MQTT Status: {data['target_temp']}C, Modo: {data['mode']}, Ventola: {data['fan_speed']}")
            
        except Exception as e:
            print(f"Errore parsing dati Cloud: {e}")
    else:
        print("Errore: Cloud non raggiungibile (Rate Limit 429 attivo o errore token)")

if __name__ == "__main__":
    client = mqtt.Client(CallbackAPIVersion.VERSION2, f"DaikinBridge_{os.getpid()}")

    client.on_connect = on_connect
    client.on_message = on_message
    
    print("Bridge Daikin V2.6 in fase di avvio...")
    connected = False
    for attempt in range(1, 30):
        try:
            client.connect(MQTT_BROKER, 1883, 60)
            connected = True
            break
        except Exception as e:
            print(f"[!] Tentativo {attempt}/30: Broker MQTT non ancora pronto ({e}). Riprovo tra 5s...")
            time.sleep(5)

    if connected:
        client.loop_start()
        print("Bridge Daikin V2.6 Avviato e connesso al Broker MQTT.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            client.loop_stop()
            sys.exit(0)
    else:
        print("[!] Impossibile connettersi al broker MQTT dopo 30 tentativi.")
        sys.exit(1)