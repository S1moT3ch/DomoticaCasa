import json
import time
import sys
import subprocess
import socket
import concurrent.futures
import threading
import requests
from datetime import datetime
import os
import config
import paho.mqtt.client as mqtt

# Garantisce che la directory del bot sia nel sys.path per l'importazione di event_logger
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from event_logger import EventLogger
except ImportError:
    EventLogger = None










from core.reports_mixin import ReportsMixin
from core.network_mixin import NetworkMixin
from core.alerts_mixin import AlertsMixin
from core.parsers_mixin import ParsersMixin
from core.zte_voip import ZTEVoIPClient
from core.phonebook import PhonebookManager

class MQTTDeviceTracker(ReportsMixin, NetworkMixin, AlertsMixin, ParsersMixin):
    def __init__(self):
        # Rubrica Telefonica Locale (vCard / Android / manuale)
        self.phonebook = PhonebookManager()

        # Client VoIP per modem ZTE Wind3 HUB
        router_ip = getattr(config, "ZTE_ROUTER_IP", "192.168.0.1")
        router_user = getattr(config, "ZTE_ROUTER_USER", "admin")
        router_pwd = getattr(config, "ZTE_ROUTER_PASSWORD", "")
        self.zte_client = ZTEVoIPClient(host=router_ip, username=router_user, password=router_pwd)


        # Mappa dei topic grezzi: { topic: {"payload": ..., "json_data": ..., "last_updated": ...} }
        self.raw_topics = {}
        # Mappa dei dispositivi Shelly elaborati: { shelly_id: {"name": ..., "channels": {channel_id: {"state": True/False, "power": ...}}, "last_updated": ...} }
        self.shelly_devices = {}
        # Mappa specifica per ingressi/allarmi (AllarmeIO.shelly)
        self.io_inputs = {}  # {0: state, 1: state}
        self.io_outputs = {} # {0: state}
        self.last_alarm_arm_time = 0.0
        self.last_alarm_disarm_time = 0.0
        self.pending_camera_notifications = {}  # {device_key: threading.Timer}

        # Mappa per voltmetro feedback serranda
        self.shutter_voltage = None
        self.stable_shutter_state = None       # "APERTA" o "CHIUSA"
        self.pending_shutter_state = None      # "APERTA" o "CHIUSA"
        self.shutter_timer = None
        self.shutter_debounce_delay = getattr(config, "SHUTTER_DEBOUNCE_DELAY", 3.0)

        # Mappa stato garage (serranda / cancello)
        self.garage_states = {}  # { "1": "PRONTO", "2": "PRONTO" }

        # Timestamp avvio per grace period notifiche al boot
        self.start_time = time.time()

        # Timer per notifica ritardata apertura cancello (garage/stato/2)
        self.gate_timer = None
        self.gate_notify_delay = getattr(config, "GATE_OPEN_NOTIFY_DELAY", 5.0)

        self.termostato_data = None
        self.daikin_data = None
        self.bosch_data = None
        self.humidity_alerts_sent = {}  # { "Termostato BTicino": bool, "Climatizzatore Daikin": bool }
        self.device_states_history = {}  # Storico stati on/off per notifiche personalizzate per dispositivo

        self.notification_callback = None
        self.network_watchdog_states = {}
        self.offline_alerts_sent = {}
        self.network_watchdog_running = False

        # Outage Prevention (Anti-distacco contatore) & Alexa
        self.outage_alert_active = False
        self.last_outage_alert_time = 0.0
        self.latest_home_power = 0.0
        self.latest_home_voltage = 230.0
        self.latest_home_current = 0.0

        if EventLogger is not None:
            try:
                self.event_logger = EventLogger()
            except Exception as e:
                print(f"[!] Impossibile creare istanza EventLogger: {e}")
                self.event_logger = None
        else:
            self.event_logger = None







        # Lock rientrante (RLock) per evitare deadlock

        self.lock = threading.RLock()
        self.client = None
        self.connected = False

    def set_notification_callback(self, callback):
        """Imposta la funzione di callback per l'invio di notifiche push su Telegram"""
        self.notification_callback = callback

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        # Compatibile sia con paho-mqtt 1.x (int rc) che paho-mqtt 2.x (ReasonCode object)
        is_success = (rc == 0) or (getattr(rc, "value", None) == 0) or (getattr(rc, "is_failure", True) is False) or (str(rc).lower() in ["0", "success"])

        if is_success:
            print(f"[+] Connesso con successo al Broker MQTT su {config.MQTT_BROKER}:{config.MQTT_PORT}")
            self.connected = True
            try:
                client.subscribe(config.MQTT_TOPIC)
                print(f"[+] Sottoscrizione attivata al topic: '{config.MQTT_TOPIC}'")
            except Exception as e:
                print(f"[!] Errore sottoscrizione topic '{config.MQTT_TOPIC}': {e}")

            # Richiede in background lo stato attivo di tutti i dispositivi (con doppio sync a 0.5s e 2.0s)
            def _async_sync():
                time.sleep(0.5)
                self.request_active_status()
                time.sleep(2.0)
                self.request_active_status()
            threading.Thread(target=_async_sync, daemon=True, name="StartupSyncThread").start()
        else:
            print(f"[!] Connessione al broker MQTT fallita con codice: {rc}")



    def _on_disconnect(self, client, userdata, rc, properties=None):
        self.connected = False
        print("[!] Disconnesso dal Broker MQTT. Il client tenterà di riconnettersi...")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        raw_payload = msg.payload.decode('utf-8', errors='ignore')
        now_str = datetime.now().strftime("%H:%M:%S (%d/%m)")

        # Parsing JSON: Garantisce che parsed_json sia esclusivamente un dict
        parsed_json = None
        try:
            val = json.loads(raw_payload)
            if isinstance(val, dict):
                parsed_json = val
        except Exception:
            parsed_json = None


        with self.lock:
            self.raw_topics[topic] = {
                "topic": topic,
                "payload": raw_payload,
                "json_data": parsed_json,
                "last_updated": now_str,
                "timestamp": time.time()
            }

            # Tenta parsing come dispositivo domotica / Shelly
            is_retained = getattr(msg, "retain", False)
            shelly_info = self._parse_shelly_message(topic, raw_payload, parsed_json, is_retained=is_retained)

            if shelly_info:
                s_id = shelly_info["shelly_id"]
                ch = shelly_info.get("channel", "0")

                if s_id not in self.shelly_devices:
                    self.shelly_devices[s_id] = {
                        "shelly_id": s_id,
                        "channels": {},
                        "last_updated": now_str
                    }

                if ch not in self.shelly_devices[s_id]["channels"]:
                    self.shelly_devices[s_id]["channels"][ch] = {"is_on": None, "power": None}

                if shelly_info.get("is_on") is not None:
                    self.shelly_devices[s_id]["channels"][ch]["is_on"] = shelly_info["is_on"]
                    
                    s_id_lower = s_id.lower()
                    if "lucecucina" in s_id_lower:
                        self._check_device_state_change("luce_cucina", shelly_info["is_on"], "Luce Cucina", "luce_cucina")
                    elif "ledandrea" in s_id_lower or "andrea" in s_id_lower:
                        self._check_device_state_change("led_andrea", shelly_info["is_on"], "Led Andrea", "led_andrea")
                    elif "ricircolo" in s_id_lower:
                        self._check_device_state_change("ricircolo", shelly_info["is_on"], "Ricircolo Acqua", "ricircolo")
                    elif "tapohome" in s_id_lower or "tapo" in s_id_lower:
                        self._check_device_state_change("camera_soggiorno", shelly_info["is_on"], "Camera Soggiorno", "camera_soggiorno")
                        # Se la telecamera viene spenta mentre è attiva la Modalità Vacanza, viene riaccesa automaticamente
                        if shelly_info["is_on"] is False and self.is_vacation_mode():
                            print(f"[VACATION GUARD] Spegnimento {s_id} intercettato in Modalità Vacanza! Riaccensione automatica...")
                            threading.Thread(target=self._force_vacation_cameras_on, daemon=True).start()
                    elif "allarmeio" in s_id_lower or "shellyplusuni" in s_id_lower:
                        if shelly_info["is_on"] is False and self.is_vacation_mode():
                            print(f"[VACATION GUARD] Spegnimento AllarmeIO Output 0 (Camera Corridoio) intercettato in Modalità Vacanza! Riaccensione automatica...")
                            threading.Thread(target=self._force_vacation_cameras_on, daemon=True).start()

                if shelly_info.get("power") is not None:
                    self.shelly_devices[s_id]["channels"][ch]["power"] = shelly_info["power"]
                    self.shelly_devices[s_id]["channels"][ch]["voltage"] = shelly_info.get("voltage")
                    self.shelly_devices[s_id]["channels"][ch]["current"] = shelly_info.get("current")
                    self.shelly_devices[s_id]["channels"][ch]["energy_kwh"] = shelly_info.get("energy_kwh")
                    
                    if hasattr(self, "event_logger") and self.event_logger:
                        try:
                            pw_float = float(shelly_info["power"])
                            v_float = float(shelly_info["voltage"]) if shelly_info.get("voltage") is not None else None
                            a_float = float(shelly_info["current"]) if shelly_info.get("current") is not None else None
                            e_float = float(shelly_info["energy_kwh"]) if shelly_info.get("energy_kwh") is not None else None
                            self.event_logger.log_power_telemetry(s_id, pw_float, v_float, a_float, e_float)
                            self._check_power_outage_alert(s_id, pw_float, voltage=v_float, current=a_float)
                        except (ValueError, TypeError) as e:
                            print(f"[!] Errore cast potenza per {s_id}: {e} (valore grezzo: {shelly_info['power']})")


                if shelly_info.get("timer_duration") is not None:
                    self.shelly_devices[s_id]["channels"][ch]["timer_duration"] = shelly_info["timer_duration"]
                if shelly_info.get("timer_started_at") is not None:
                    self.shelly_devices[s_id]["channels"][ch]["timer_started_at"] = shelly_info["timer_started_at"]
                if shelly_info.get("timer_remaining") is not None:
                    self.shelly_devices[s_id]["channels"][ch]["timer_remaining"] = shelly_info["timer_remaining"]

                self.shelly_devices[s_id]["last_updated"] = now_str

    def start(self):
        try:
            try:
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            except AttributeError:
                self.client = mqtt.Client()

            if config.MQTT_USERNAME and config.MQTT_PASSWORD:
                self.client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)

            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            print(f"[...] Avvio connessione MQTT verso {config.MQTT_BROKER}:{config.MQTT_PORT}...")
            self.client.connect_async(config.MQTT_BROKER, config.MQTT_PORT, 60)
            self.client.loop_start()
        except Exception as e:
            print(f"[!] Errore nell'avvio del client MQTT: {e}")

    def send_device_command(self, device_key, action):
        """Esegue il comando MQTT su un dispositivo specifico in modo sicuro"""
        with self.lock:
            if not self.client:
                return False, "Client MQTT non inizializzato."

            if not self.connected and hasattr(self.client, "is_connected") and self.client.is_connected():
                self.connected = True

            if not self.connected:
                return False, "Broker MQTT non connesso."


            try:
                # 1. Luce Cucina (Shelly)
                if device_key == "LuceCucina":
                    is_on = (action == "on")
                    topic = "LuceCucina.shelly/rpc"
                    payload = json.dumps({
                        "id": 1,
                        "src": "LuceCucina.shelly/rpc",
                        "method": "Switch.Set",
                        "params": {"id": 0, "on": is_on}
                    })
                    self.client.publish(topic, payload)
                    state_txt = "ACCESA 🟢" if is_on else "SPENTA 🔴"
                    return True, f"Luce Cucina impostata su **{state_txt}**"

                # 2. Led Andrea (Shelly Gen2 RPC)
                elif device_key in ["LedAndrea", "LedAndrea.shelly", "led_andrea"]:
                    is_on = (action == "on")
                    topic = "LedAndrea.shelly/rpc"
                    payload = json.dumps({
                        "id": 1,
                        "src": "LedAndrea.shelly/rpc",
                        "method": "Switch.Set",
                        "params": {"id": 0, "on": is_on}
                    })
                    self.client.publish(topic, payload)
                    cmd_str = "on" if is_on else "off"
                    self.client.publish("shellies/LedAndrea.shelly/relay/0/command", cmd_str)
                    state_txt = "ACCESO 🟢" if is_on else "SPENTO 🔴"
                    return True, f"Led Andrea impostato su **{state_txt}**"

                # 2. Ricircolo Acqua (Shelly)
                elif device_key == "Ricircolo":
                    is_on = (action == "on")
                    topic = "Ricircolo.shelly/rpc"
                    payload = json.dumps({
                        "id": 1,
                        "src": "Ricircolo.shelly/rpc",
                        "method": "Switch.Set",
                        "params": {"id": 0, "on": is_on}
                    })
                    self.client.publish(topic, payload)
                    state_txt = "ATTIVATO 🟢" if is_on else "DISATTIVATO 🔴"
                    return True, f"Ricircolo Acqua impostato su **{state_txt}**"

                # 3. Telecamera Casa (TapoHome / Shelly)
                elif device_key in ["TapoHome", "TapoHome.shelly", "tapo_camera"]:
                    is_on = (action == "on")
                    topic = "TapoHome.shelly/rpc"
                    payload = json.dumps({
                        "id": 1,
                        "src": "TapoHome.shelly/rpc",
                        "method": "Switch.Set",
                        "params": {"id": 0, "on": is_on}
                    })
                    self.client.publish(topic, payload)
                    cmd_str = "on" if is_on else "off"
                    self.client.publish("shellies/TapoHome.shelly/relay/0/command", cmd_str)
                    self.client.publish("shellies/TapoHomePlug/relay/0/command", cmd_str)
                    state_txt = "ACCESA 🟢" if is_on else "SPENTA 🔴"
                    return True, f"Camera Casa impostata su **{state_txt}**"

                # 4. Climatizzatore Daikin
                elif device_key == "Daikin":
                    if action == "dry":
                        self.client.publish("Canalizzato.daikin/mode", "dry")
                        self.client.publish("Canalizzato.daikin/power", "on")
                        return True, "Climatizzatore Daikin impostato su **DEUMIDIFICAZIONE (DRY) 💧🟢**"
                    elif action in ["on", "cool", "cooling"]:
                        self.client.publish("Canalizzato.daikin/mode", "cooling")
                        self.client.publish("Canalizzato.daikin/power", "on")
                        return True, "Climatizzatore Daikin impostato su **RAFFRESCAMENTO (COOL) ❄️🟢**"
                    elif action == "off":
                        self.client.publish("Canalizzato.daikin/power", "off")
                        return True, "Climatizzatore Daikin impostato su **SPENTO 🔴**"
                    else:
                        is_on = (action == "on")
                        self.client.publish("Canalizzato.daikin/power", "on" if is_on else "off")
                        state_txt = "ACCESO 🟢" if is_on else "SPENTO 🔴"
                        return True, f"Climatizzatore Daikin impostato su **{state_txt}**"



                # 5. Cancello Garage (Impulso Canale 2)
                elif device_key == "Cancello":
                    topic = "garage/comando/2"
                    payload = "ON"
                    self.client.publish(topic, payload)
                    return True, "Impulso apertura **Cancello Garage** inviato 🚗"

                # 6. Serranda Garage (Impulso Canale 1)
                elif device_key == "Serranda":
                    topic = "garage/comando/1"
                    payload = "ON"
                    self.client.publish(topic, payload)
                    return True, "Impulso comando **Serranda Garage** inviato 🚪"

                # 7. Camera Corridoio (AllarmeIO.shelly Output 0)
                elif device_key in ["CameraCorridoio", "AllarmeGarage", "AllarmeIO"]:
                    is_on = (action == "on")
                    topic = "AllarmeIO.shelly/rpc"
                    payload = json.dumps({
                        "id": 1,
                        "src": "AllarmeIO.shelly/rpc",
                        "method": "Switch.Set",
                        "params": {"id": 0, "on": is_on}
                    })
                    self.client.publish(topic, payload)
                    cmd_str = "on" if is_on else "off"
                    self.client.publish("shellies/AllarmeIO.shelly/relay/0/command", cmd_str)
                    state_txt = "ACCESA 🟢" if is_on else "SPENTA 🔴"
                    return True, f"Camera Corridoio (AllarmeIO Output 0) impostata su **{state_txt}**"

                else:
                    return False, f"Dispositivo sconosciuto: {device_key}"


            except Exception as e:
                return False, f"Errore durante l'invio del comando MQTT: {e}"

    def is_vacation_mode(self):
        v_file = getattr(config, "VACATION_MODE_FILE", os.path.join(getattr(config, "BASE_DIR", "."), "vacation_mode.json"))
        if not os.path.exists(v_file) and os.path.exists("z:/www/domotica/bot/vacation_mode.json"):
            v_file = "z:/www/domotica/bot/vacation_mode.json"
        if os.path.exists(v_file):
            try:
                with open(v_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("vacation_mode", False)
            except Exception:
                pass
        return False

    def set_vacation_mode(self, active: bool):
        v_file = getattr(config, "VACATION_MODE_FILE", os.path.join(getattr(config, "BASE_DIR", "."), "vacation_mode.json"))
        data = {
            "vacation_mode": bool(active),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open(v_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[!] Errore salvataggio {v_file}: {e}")
        
        if active:
            print("[VACATION MODE] Modalità Vacanza attivata: forzo l'accensione di sicurezza delle telecamere...")
            threading.Thread(target=self._force_vacation_cameras_on, daemon=True).start()

        return active





