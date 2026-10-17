# -*- coding: utf-8 -*-
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import json
import time
import os
import sys
import threading
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE_DIR, "homeconnect_tokens.json")

BASE_TOPIC = "Lavastoviglie.bosch"

class BoschHomeConnectBridge:
    def __init__(self):
        self.api_base = "https://api.home-connect.com"
        self.load_tokens()
        self.ha_id = None
        self.state = {
            "power": "Off",
            "door": "Closed",
            "operation_state": "Inactive",
            "program": None,
            "remaining_time": 0,
            "progress": 0
        }

    def load_tokens(self):
        if not os.path.exists(TOKEN_FILE):
            print(f"[!] File {TOKEN_FILE} non trovato. Esegui prima test_homeconnect.py!")
            sys.exit(1)
        with open(TOKEN_FILE, "r") as f:
            data = json.load(f)
            self.client_id = data["client_id"]
            self.client_secret = data["client_secret"]
            self.access_token = data["access_token"]
            self.refresh_token = data["refresh_token"]

    def save_tokens(self):
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token
        }
        with open(TOKEN_FILE, "w") as f:
            json.dump(data, f, indent=4)

    def do_refresh_token(self):
        url = f"{self.api_base}/security/oauth/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token
        }
        try:
            resp = requests.post(url, data=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                self.access_token = data["access_token"]
                if "refresh_token" in data:
                    self.refresh_token = data["refresh_token"]
                self.save_tokens()
                print("[Bosch] Token aggiornato con successo.")
                return True
            else:
                print(f"[!] Errore refresh token: HTTP {resp.status_code} - {resp.text}")
                return False
        except Exception as e:
            print(f"[!] Errore connessione refresh token: {e}")
            return False

    def api_get(self, endpoint):
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/vnd.bsh.api.v1+json, application/vnd.bsh.sdk.v1+json"}
        url = f"{self.api_base}{endpoint}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 401:
                if self.do_refresh_token():
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("data", {})
            else:
                print(f"[!] Errore HTTP {resp.status_code} in api_get({endpoint}): {resp.text}")
                return None
        except Exception as e:
            print(f"[!] Errore api_get {endpoint}: {e}")
            return None

    def discover_appliance(self):
        data = self.api_get("/api/homeappliances")
        print(f"[DEBUG] Risposta API homeappliances: {json.dumps(data, indent=2)}")
        if data and "homeappliances" in data:
            for ha in data["homeappliances"]:
                if ha.get("type") == "Dishwasher":
                    self.ha_id = ha.get("haId")
                    print(f"[Bosch] Trovata Lavastoviglie: {ha.get('name')} (ID: {self.ha_id})")
                    return True
        print("[!] Nessuna lavastoviglie trovata sull'account Bosch.")
        return False

    def get_initial_state(self):
        if not self.ha_id: return
        # Recupera status
        status_data = self.api_get(f"/api/homeappliances/{self.ha_id}/status")
        if status_data and "status" in status_data:
            self.parse_items(status_data["status"])
            
        # Recupera impostazioni/stato operativo
        settings_data = self.api_get(f"/api/homeappliances/{self.ha_id}/settings")
        if settings_data and "settings" in settings_data:
            self.parse_items(settings_data["settings"])

    def parse_items(self, items):
        changed = False
        for item in items:
            key = item.get("key")
            value = item.get("value")
            
            if key == "BSH.Common.Status.DoorState":
                new_state = value.split(".")[-1]
                if self.state["door"] != new_state:
                    self.state["door"] = new_state
                    changed = True
            elif key == "BSH.Common.Status.OperationState":
                new_state = value.split(".")[-1]
                if self.state["operation_state"] != new_state:
                    self.state["operation_state"] = new_state
                    changed = True
            elif key == "BSH.Common.Setting.PowerState":
                new_state = value.split(".")[-1]
                if self.state["power"] != new_state:
                    self.state["power"] = new_state
                    changed = True
            elif key == "BSH.Common.Root.ActiveProgram":
                prog = value.split(".")[-1] if value else None
                if self.state["program"] != prog:
                    self.state["program"] = prog
                    changed = True
            elif key == "BSH.Common.Option.RemainingProgramTime":
                if self.state["remaining_time"] != value:
                    self.state["remaining_time"] = value
                    changed = True
            elif key == "BSH.Common.Option.ProgramProgress":
                if self.state["progress"] != value:
                    self.state["progress"] = value
                    changed = True

        return changed

    def start_sse_listener(self, mqtt_client):
        while True:
            headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "text/event-stream"}
            url = f"{self.api_base}/api/homeappliances/{self.ha_id}/events"
            try:
                print("[Bosch] Connessione al flusso eventi (SSE)...")
                response = requests.get(url, headers=headers, stream=True, timeout=600)
                
                if response.status_code == 401:
                    print("[Bosch] Token scaduto durante SSE, aggiorno...")
                    self.do_refresh_token()
                    continue
                    
                if response.status_code != 200:
                    print(f"[!] SSE Error {response.status_code}. Riprovo tra 10s...")
                    time.sleep(10)
                    continue

                event_type = None
                for line in response.iter_lines():
                    if line:
                        decoded_line = line.decode('utf-8').strip()
                        if decoded_line.startswith("event:"):
                            event_type = decoded_line.split(":", 1)[1].strip()
                        elif decoded_line.startswith("data:") and event_type in ["STATUS", "EVENT", "NOTIFY"]:
                            data_str = decoded_line.split(":", 1)[1].strip()
                            if data_str:
                                try:
                                    payload = json.loads(data_str)
                                    items = payload.get("items", [])
                                    if self.parse_items(items):
                                        publish_mqtt(mqtt_client, self.state)
                                        # Controlla completamento per mandare un evento push specifico
                                        if event_type == "EVENT":
                                            for item in items:
                                                if item.get("key") == "BSH.Common.Event.ProgramFinished":
                                                    mqtt_client.publish(f"{BASE_TOPIC}/event", '{"event": "ProgramFinished"}')
                                except json.JSONDecodeError:
                                    pass
            except requests.exceptions.Timeout:
                print("[Bosch] SSE Timeout (normale riavvio connessione).")
            except Exception as e:
                print(f"[!] Errore ciclo SSE: {e}")
                time.sleep(10)

def publish_mqtt(client, state):
    client.publish(f"{BASE_TOPIC}/status", json.dumps(state), retain=True)
    print(f"[MQTT] Stato aggiornato: {state}")

def on_connect(client, userdata, flags, rc, props):
    if rc == 0:
        print("[MQTT] Connesso al broker locale.")
        client.subscribe(f"{BASE_TOPIC}/command")

def on_message(client, userdata, msg):
    # Predisposto per gestire comandi futuri (es. on/off, start program)
    pass

if __name__ == "__main__":
    print("Avvio Bridge Bosch Home Connect...")
    bridge = BoschHomeConnectBridge()
    if not bridge.discover_appliance():
        sys.exit(1)
        
    bridge.get_initial_state()
    
    client = mqtt.Client(CallbackAPIVersion.VERSION2, f"BoschBridge_{os.getpid()}")
    client.on_connect = on_connect
    client.on_message = on_message
    
    # Connessione con riprova
    for attempt in range(1, 30):
        try:
            client.connect("localhost", 1883, 60)
            break
        except Exception as e:
            print(f"[!] Tentativo {attempt}/30: Broker MQTT non pronto. Riprovo in 5s...")
            time.sleep(5)
            
    client.loop_start()
    
    # Pubblica subito lo stato iniziale
    publish_mqtt(client, bridge.state)
    
    # Blocca sul flusso SSE
    bridge.start_sse_listener(client)
