import json
import time
import sys
import socket
import concurrent.futures
import os
import subprocess
import requests
import threading
from datetime import datetime, timedelta
import paho.mqtt.client as mqtt
import config

class ParsersMixin:
    def _check_device_state_change(self, device_key, new_is_on, label, category, extra_info=""):
        """Verifica le variazioni di stato on/off di ciascun dispositivo ed invia la notifica push alla categoria corretta"""
        with self.lock:
            old_is_on = self.device_states_history.get(device_key)
            self.device_states_history[device_key] = new_is_on

            if old_is_on is not None and old_is_on != new_is_on:
                st_txt = "ACCESA 🟢" if new_is_on else "SPENTA 🔴"
                msg = f"🔔 *NOTIFICA DISPOSITIVO DOMOTICO*\n\nIl dispositivo *{label}* è stato impostato su **{st_txt}**."
                
                # Aggiunge extra_info (es. Modalità o Programma) al log SQLite
                log_detail = f"Dispositivo impostato su {'ACCESO 🟢' if new_is_on else 'SPENTO 🔴'}{extra_info}"
                
                # Registra SEMPRE l'evento nel log SQLite per mantenerne piena traccia
                if self.event_logger is not None:
                    self.event_logger.log_event(category, label, log_detail)

                # Gestione intelligente notifiche push per telecamere interne (Camera Soggiorno, Camera Corridoio)
                is_camera = (
                    category in ["camera", "camera_soggiorno", "camera_corridoio", "tapo_camera"] or
                    device_key in ["camera_soggiorno", "camera_corridoio", "tapo_camera"]
                )

                if is_camera:
                    now = time.time()
                    armed, alarm_label = self.is_any_alarm_armed()

                    # 1. Se almeno uno degli allarmi (Casa o Garage) è attualmente INSERITO:
                    #    L'accensione o lo stato delle telecamere fa parte della sicurezza attiva: nessuna notifica Telegram
                    if armed:
                        print(f"[CAMERA NOTIF SUPPRESSED] {label} ({st_txt}): soppressa notifica Telegram perché allarme {alarm_label} è INSERITO.")
                        return

                    # 2. Se siamo entro 180 secondi dal disinserimento di una qualsiasi zona allarme:
                    #    Sopprime tutte le notifiche di spegnimento e rimbalzi on/off al rientro a casa
                    time_since_disarm = now - getattr(self, "last_alarm_disarm_time", 0.0)
                    if time_since_disarm < 180.0:
                        print(f"[CAMERA NOTIF SUPPRESSED] {label} ({st_txt}): soppressa notifica durante finestra disinserimento allarme ({time_since_disarm:.1f}s fa).")
                        return

                    # 3. Se un allarme è stato appena inserito negli ultimi 60 secondi:
                    time_since_arm = now - getattr(self, "last_alarm_arm_time", 0.0)
                    if time_since_arm < 60.0:
                        print(f"[CAMERA NOTIF SUPPRESSED] {label} ({st_txt}): soppressa notifica durante finestra inserimento allarme ({time_since_arm:.1f}s fa).")
                        return

                    # 4. Caso spegnimento a riposo (allarme disinserito):
                    #    Se era in corso un timer per la notifica di accensione di questa telecamera, viene annullato
                    if not new_is_on:
                        if hasattr(self, "pending_camera_notifications") and device_key in self.pending_camera_notifications:
                            try:
                                self.pending_camera_notifications[device_key].cancel()
                            except Exception:
                                pass
                            self.pending_camera_notifications.pop(device_key, None)

                    # 5. Caso accensione a riposo: le telecamere potrebbero accendersi 2-3 secondi PRIMA
                    #    che la centralina allarme chiuda l'ingresso dello Shelly all'uscita di casa.
                    #    Ritardiamo la notifica Telegram di 10 secondi: se nel frattempo l'allarme viene inserito, la notifica viene annullata.
                    if new_is_on:
                        if not hasattr(self, "pending_camera_notifications"):
                            self.pending_camera_notifications = {}

                        if device_key in self.pending_camera_notifications:
                            try:
                                self.pending_camera_notifications[device_key].cancel()
                            except Exception:
                                pass

                        def _send_delayed_camera_notif(dev_k=device_key, cat=category, notif_msg=msg, lbl=label, txt=st_txt):
                            with self.lock:
                                self.pending_camera_notifications.pop(dev_k, None)
                                is_armed_now, arm_lbl = self.is_any_alarm_armed()
                                if is_armed_now or (time.time() - getattr(self, "last_alarm_arm_time", 0.0) < 60.0):
                                    print(f"[CAMERA NOTIF SUPPRESSED] Notifica ritardata annullata per {lbl}: allarme inserito.")
                                    return
                                print(f"[NOTIFICA DISPOSITIVO {cat}] {notif_msg}")
                                if self.notification_callback:
                                    self.notification_callback(cat, notif_msg)

                        timer = threading.Timer(10.0, _send_delayed_camera_notif)
                        timer.daemon = True
                        self.pending_camera_notifications[device_key] = timer
                        timer.start()
                        print(f"[CAMERA NOTIF PENDING] Notifica per {label} ({st_txt}) posticipata di 10s per verificare eventuale inserimento allarme.")
                        return

                print(f"[NOTIFICA DISPOSITIVO {category}] {msg} {extra_info}")
                if self.notification_callback:
                    self.notification_callback(category, msg)


    def _update_io_input(self, channel_id, new_state):
        """Aggiorna lo stato degli ingressi IO e invia notifiche in caso di cambio stato (Input 0: Allarme Garage, Input 1: Allarme Casa)"""
        with self.lock:
            new_state = bool(new_state)
            old_state = self.io_inputs.get(channel_id)
            self.io_inputs[channel_id] = new_state

            # Se lo stato è cambiato (escludendo il caricamento iniziale)
            if old_state is not None and old_state != new_state:
                if new_state:
                    self.last_alarm_arm_time = time.time()
                    # Annulla eventuali notifiche pendenti di accensione telecamere
                    if hasattr(self, "pending_camera_notifications"):
                        for t in list(self.pending_camera_notifications.values()):
                            try:
                                t.cancel()
                            except Exception:
                                pass
                        self.pending_camera_notifications.clear()
                    print(f"[ALARM GUARD] Input Allarme {channel_id} INSERITO: soppresse notifiche telecamere pendenti.")
                else:
                    self.last_alarm_disarm_time = time.time()
                    # Annulla eventuali notifiche pendenti di telecamere
                    if hasattr(self, "pending_camera_notifications"):
                        for t in list(self.pending_camera_notifications.values()):
                            try:
                                t.cancel()
                            except Exception:
                                pass
                        self.pending_camera_notifications.clear()
                    print(f"[ALARM GUARD] Input Allarme {channel_id} DISINSERITO: avviata finestra di grazia silenziosa telecamere (180s).")

                # Input 0: Allarme Garage
                if channel_id == 0:
                    st_desc = "INSERITO (ATTIVO) 🚨" if new_state else "DISINSERITO 🔓"
                    if self.event_logger is not None:
                        self.event_logger.log_event("alarm_garage", "Allarme Garage", f"Allarme Garage è stato {st_desc}")
                    msg = (
                        "🚗 *NOTIFICA ALLARME GARAGE*\n\n"
                        f"🚨 L'allarme del Garage è stato **{st_desc}**!"
                    )
                    print(f"[NOTIFICA ALLARME GARAGE] {msg}")
                    if self.notification_callback:
                        self.notification_callback("alarm_garage", msg)

                # Input 1: Allarme Casa
                elif channel_id == 1:
                    st_desc = "INSERITO (ATTIVO) 🚨" if new_state else "DISINSERITO 🔓"
                    if self.event_logger is not None:
                        self.event_logger.log_event("alarm_house", "Allarme Casa", f"Allarme Casa è stato {st_desc}")
                    msg = (
                        "🛡️ *NOTIFICA ALLARME CASA*\n\n"
                        f"🚨 L'allarme di Casa è stato **{st_desc}**!"
                    )
                    print(f"[NOTIFICA ALLARME CASA] {msg}")
                    if self.notification_callback:
                        self.notification_callback("alarm_house", msg)

                # In Modalità Vacanza: se l'allarme viene disinserito, forziamo le telecamere a rimanere ACCESE
                if not new_state and self.is_vacation_mode():
                    print(f"[VACATION GUARD] Input Allarme {channel_id} disinserito in Modalità Vacanza: forzo la riaccensione delle telecamere!")
                    threading.Thread(target=self._force_vacation_cameras_on, daemon=True).start()

    def _update_io_output(self, channel_id, new_state):
        """Aggiorna lo stato degli output IO (Output 0: Camera Corridoio) ed intercetta lo spegnimento in Modalità Vacanza"""
        with self.lock:
            new_state = bool(new_state)
            old_state = self.io_outputs.get(channel_id)
            self.io_outputs[channel_id] = new_state

            if channel_id == 0:
                self._check_device_state_change("camera_corridoio", new_state, "Camera Corridoio", "camera_corridoio")

                # Se l'Output 0 (Camera Corridoio) viene spento mentre la Modalità Vacanza è ATTIVA
                if not new_state and self.is_vacation_mode():
                    print("[VACATION GUARD] Spegnimento Output 0 di AllarmeIO (Camera Corridoio) intercettato in Modalità Vacanza! Riaccensione automatica...")
                    threading.Thread(target=self._force_vacation_cameras_on, daemon=True).start()

    def _force_vacation_cameras_on(self):
        """Assicura che le telecamere (TapoHome e Camera Corridoio su AllarmeIO Output 0) rimangano accese durante la Modalità Vacanza inviando i comandi su tutti i canali MQTT"""
        try:
            time.sleep(0.5)  # Attesa per sovrascrivere eventuali comandi HTTP Action inviati dagli Shelly
            
            # 1. Telecamera Casa / Soggiorno (TapoHome)
            self.send_device_command("TapoHome", "on")
            
            # 2. Telecamera Corridoio (AllarmeIO Output 0)
            self.send_device_command("CameraCorridoio", "on")

            # Invio ridondante dei comandi MQTT per massima tempestività e compatibilità
            if self.client:
                # TapoHome
                self.client.publish("shellies/TapoHome.shelly/relay/0/command", "on")
                self.client.publish("shellies/TapoHomePlug/relay/0/command", "on")
                self.client.publish("TapoHomePlug/rpc", json.dumps({"id": 1, "src": "TapoHomePlug/rpc", "method": "Switch.Set", "params": {"id": 0, "on": True}}))
                
                # Camera Corridoio (AllarmeIO Output 0)
                self.client.publish("AllarmeIO.shelly/rpc", json.dumps({"id": 1, "src": "AllarmeIO.shelly/rpc", "method": "Switch.Set", "params": {"id": 0, "on": True}}))
                self.client.publish("shellies/AllarmeIO.shelly/relay/0/command", "on")

            if hasattr(self, "event_logger") and self.event_logger:
                self.event_logger.log_event("camera", "Telecamere Casa & Corridoio", "Modalità Vacanza: Telecamere (TapoHome e Corridoio AllarmeIO Out 0) forzate ad ACCESE 🌴")
            print("[VACATION GUARD] Comandi di riaccensione telecamere (TapoHome + Camera Corridoio AllarmeIO Output 0) inviati con successo su tutti i canali MQTT.")
        except Exception as e:
            print(f"[!] Errore nel forzare l'accensione telecamere in vacanza: {e}")


    def _on_gate_opened_timer(self):
        """Callback eseguita dopo N secondi dall'impulso di apertura del cancello (garage/stato/2)"""
        with self.lock:
            self.gate_timer = None
            if self.event_logger is not None:
                self.event_logger.log_event("gate", "Cancello Garage", "Il cancello del garage risulta ora APERTO 🚗")
            msg = (
                "🚗 *NOTIFICA CANCELLO GARAGE*\n\n"
                "È stato rilevato il comando di apertura del cancello.\n"
                "🚗 Il cancello del garage risulta ora **APERTO**."
            )
            print(f"[NOTIFICA CANCELLO] {msg}")
            if self.notification_callback:
                self.notification_callback("gate", msg)


    def _on_shutter_stabilized(self, new_state, voltage_val):
        """Callback eseguito solo quando la tensione del voltmetro si è stabilizzata per N secondi"""
        with self.lock:
            self.shutter_timer = None
            self.pending_shutter_state = None

            if self.stable_shutter_state != new_state:
                old_state = self.stable_shutter_state
                self.stable_shutter_state = new_state

                # Se è la prima lettura all'avvio (old_state è None), NON inviare notifiche e NON registrare falsi eventi nel DB
                if old_state is None:
                    print(f"[SHUTTER INIT] Stato iniziale serranda all'avvio: {new_state} ({voltage_val:.2f}V). Nessuna notifica generata.")
                    return

                if self.event_logger is not None:
                    self.event_logger.log_event("shutter", "Serranda Garage", f"Serranda garage risulta ora {new_state} ({voltage_val:.2f}V)")

                if new_state == "APERTA":
                    msg = (
                        "🚨 *ATTENZIONE: SERRANDA GARAGE APERTA!*\n\n"
                        f"La tensione di `AllarmeIO.shelly` si è stabilizzata a *{voltage_val:.2f} V* (> 5.3V).\n"
                        "🚪 La serranda del garage risulta ora **APERTA**."
                    )
                    print(f"[NOTIFICA STABILIZZATA] {msg}")
                    if self.notification_callback:
                        self.notification_callback("shutter", msg)

                    # --- CONTROLLO ALLARME INTRUSIONE IN MODALITÀ VACANZA ---
                    is_vacation = self.is_vacation_mode()
                    # Input 0: Allarme Garage (True = INSERITO/ATTIVO)
                    is_garage_alarm_active = bool(self.io_inputs.get(0, False))

                    if is_vacation and is_garage_alarm_active:
                        crit_msg = (
                            "🚨🚨 *ALLARME CRITICO: INTRUSIONE GARAGE IN MODALITÀ VACANZA!* 🚨🚨\n\n"
                            "⚠️ Rilevata apertura della **SERRANDA GARAGE** mentre l'allarme garage è **INSERITO (ATTIVO)**!\n"
                            "📞 Avvio chiamata vocale automatica di emergenza su Telegram..."
                        )
                        print(f"[ALLARME CRITICO VACANZA] {crit_msg}")
                        if hasattr(self, "event_logger") and self.event_logger:
                            self.event_logger.log_event("alarm_garage", "Allarme Garage", "INTRUSIONE: Serranda aperta in vacanza con allarme inserito!")
                        
                        if self.notification_callback:
                            self.notification_callback("alarm_garage", crit_msg)

                        if getattr(config, "VACATION_ALARM_CALL_ENABLED", True):
                            target_user = getattr(config, "VACATION_ALARM_CALL_TARGET", getattr(config, "VACATION_REPORT_CHAT_ID", "283084390"))
                            call_txt = "ATTENZIONE! ALLARME INTRUSIONE GARAGE! La serranda del garage e stata APERTA in modalita vacanza con allarme inserito!"
                            self.trigger_telegram_voice_call(target_user, call_txt)

                            wa_phone = getattr(config, "WHATSAPP_PHONE", "").strip()
                            wa_key = getattr(config, "WHATSAPP_API_KEY", "").strip()
                            if wa_phone and wa_key:
                                self.trigger_whatsapp_voice_alert(wa_phone, wa_key, call_txt)

                elif new_state == "CHIUSA":
                    msg = (
                        "🟢 *SERRANDA GARAGE CHIUSA*\n\n"
                        f"La tensione di `AllarmeIO.shelly` si è stabilizzata a *{voltage_val:.2f} V* (<= 5.3V).\n"
                        "🚪 La serranda del garage è nuovamente **CHIUSA**."
                    )
                    print(f"[NOTIFICA STABILIZZATA] {msg}")
                    if self.notification_callback:
                        self.notification_callback("shutter", msg)

    def _process_voltmeter_reading(self, val):
        """Filtra le oscillazioni temporanee della serranda durante l'apertura/chiusura"""
        self.shutter_voltage = val
        candidate = "APERTA" if val > 5.3 else "CHIUSA"

        # Se la lettura indica un cambio di stato rispetto allo stato stabilizzato attuale
        if candidate != self.stable_shutter_state:
            if candidate != self.pending_shutter_state:
                # Annulla eventuale timer pendente se il valore sta ancora oscillando
                if self.shutter_timer:
                    self.shutter_timer.cancel()

                self.pending_shutter_state = candidate
                # Avvia il timer di stabilizzazione (es. 3.0 secondi)
                self.shutter_timer = threading.Timer(
                    self.shutter_debounce_delay,
                    self._on_shutter_stabilized,
                    args=[candidate, val]
                )
                self.shutter_timer.daemon = True
                self.shutter_timer.start()
        else:
            # Se la tensione è tornata al livello dello stato stabile corrente, cancella il timer
            if self.pending_shutter_state and self.shutter_timer:
                self.shutter_timer.cancel()
                self.shutter_timer = None
                self.pending_shutter_state = None



    def request_active_status(self):
        """Interroga attivamente tutti i dispositivi inviando comandi RPC, Announce e STATUS tramite MQTT"""
        if not self.client or not self.connected:
            return

        print("[...] Invio interrogazioni RPC per aggiornare lo stato di tutti i dispositivi...")

        # Dispositivi Gen1 (Broadcast Announce)
        try:
            self.client.publish("shellies/command", "announce")
        except Exception:
            pass

        # Dispositivi Casa, Garage & Allarme (Shelly Gen2 RPC)
        devices = ["Ricircolo.shelly", "TapoHome.shelly", "LuceCucina.shelly", "LedAndrea.shelly", "Allarme.shelly", "PowerMeterBox.shelly"]
        for dev_id in devices:
            topic_rpc = f"{dev_id}/rpc"
            msg_payload = json.dumps({"id": 1, "src": topic_rpc, "method": "Switch.GetStatus", "params": {"id": 0}})
            try:
                self.client.publish(topic_rpc, msg_payload)
            except Exception as e:
                print(f"[!] Errore invio RPC su {topic_rpc}: {e}")

        # Modulo IO Allarme (AllarmeIO.shelly)
        try:
            msg_io = json.dumps({"id": 1, "src": "AllarmeIO.shelly/rpc", "method": "Shelly.GetStatus"})
            self.client.publish("AllarmeIO.shelly/rpc", msg_io)
        except Exception as e:
            print(f"[!] Errore invio RPC AllarmeIO: {e}")

        # Comandi Garage
        try:
            self.client.publish("garage/comando/1", "STATUS")
            self.client.publish("garage/comando/2", "STATUS")
        except Exception as e:
            print(f"[!] Errore invio comando garage: {e}")

        # Request update per Termostato BTicino / Netatmo e Clima Daikin
        try:
            self.client.publish("Termostato.bticino/request_update", "STATUS")
            self.client.publish("Canalizzato.daikin/request_update", "STATUS")
        except Exception:
            pass


    def _find_shelly_device(self, search_key):

        """Cerca un dispositivo in self.shelly_devices in modo flessibile e case-insensitive"""
        search_clean = search_key.lower().replace(".shelly", "").replace("shellies/", "")
        for registered_id, data in self.shelly_devices.items():
            reg_clean = registered_id.lower().replace(".shelly", "").replace("shellies/", "")
            if search_clean == reg_clean or search_clean in reg_clean or reg_clean in search_clean:
                return registered_id, data
        return None, None

    def _extract_output_and_power(self, data):
        output = None
        power = None
        voltage = None
        current = None
        energy_kwh = None
        timer_duration = None
        timer_started_at = None
        timer_remaining = None

        if not isinstance(data, dict):
            return None, None, None, None, None, None, None, None

        # Estrazione timer da data, result o params
        src_dict = data.get("result") if "result" in data and isinstance(data["result"], dict) else (data.get("params") if "params" in data and isinstance(data["params"], dict) else data)
        sw_dict = src_dict.get("switch:0") if isinstance(src_dict, dict) and "switch:0" in src_dict and isinstance(src_dict["switch:0"], dict) else src_dict

        if isinstance(sw_dict, dict):
            timer_duration = sw_dict.get("timer_duration")
            timer_started_at = sw_dict.get("timer_started_at")
            timer_remaining = sw_dict.get("timer_remaining")
            
            # Estrazione metriche extra (Gen 2: voltage, current, energy, power)
            if "voltage" in sw_dict:
                voltage = sw_dict.get("voltage")
            if "current" in sw_dict:
                current = sw_dict.get("current")
            if "aenergy" in sw_dict and isinstance(sw_dict["aenergy"], dict):
                e_tot = sw_dict["aenergy"].get("total")
                if e_tot is not None:
                    energy_kwh = float(e_tot) / 1000.0
            if "apower" in sw_dict or "power" in sw_dict:
                p_raw = sw_dict.get("apower") if sw_dict.get("apower") is not None else sw_dict.get("power")
                try:
                    power = float(p_raw)
                except (ValueError, TypeError):
                    pass
            if "output" in sw_dict and isinstance(sw_dict["output"], bool):
                output = sw_dict["output"]

        # 1. 'output' al livello principale del JSON
        if output is None and "output" in data and isinstance(data["output"], bool):
            output = data["output"]

        # 2. 'output' all'interno di 'result' (Risposta RPC al comando GetStatus)
        elif output is None and "result" in data and isinstance(data["result"], dict):
            res = data["result"]
            if "output" in res and isinstance(res["output"], bool):
                output = res["output"]
            elif "switch:0" in res and isinstance(res["switch:0"], dict):
                output = res["switch:0"].get("output")

        # 3. 'output' all'interno di 'params' (Notifiche di cambio stato NotifyStatus)
        elif output is None and "params" in data and isinstance(data["params"], dict):
            params = data["params"]
            if "switch:0" in params and isinstance(params["switch:0"], dict):
                output = params["switch:0"].get("output")
            elif "output" in params and isinstance(params["output"], bool):
                output = params["output"]

        # Backup su 'state' se 'output' non è presente nel JSON
        if output is None and "state" in data:
            st = data["state"]
            if isinstance(st, bool):
                output = st
            elif isinstance(st, str):
                output = st.lower() in ["on", "true", "1"]

        # Estrazione potenza se non già estratta
        if power is None and isinstance(src_dict, dict):
            p_val = src_dict.get("apower") if src_dict.get("apower") is not None else src_dict.get("power")
            if p_val is not None:
                try:
                    power = float(p_val)
                except (ValueError, TypeError):
                    pass
        if power is None:
            p_val = data.get("apower") if data.get("apower") is not None else data.get("power")
            if p_val is not None:
                try:
                    power = float(p_val)
                except (ValueError, TypeError):
                    pass

        return output, power, timer_duration, timer_started_at, timer_remaining, voltage, current, energy_kwh

    def _parse_shelly_message(self, topic, payload, json_data, is_retained=False):
        """
        Parser per i dispositivi Shelly basato sul valore booleano 'output' e parametri timer
        """
        topic_lower = topic.lower()
        parts = topic.split('/')

        # Ignora i messaggi di connettività LWT o comandi di servizio per evitare falsi stati di accensione relè
        if topic_lower.endswith("/online") or "/online/" in topic_lower or topic_lower.endswith("/announce") or topic_lower.endswith("/command"):
            return None

        # --- 0. TERMOSTATO BTICINO / NETATMO E CLIMA DAIKIN ---
        if topic_lower.startswith("termostato.bticino"):
            if isinstance(json_data, dict):
                self.termostato_data = json_data
                hum = json_data.get("humidity")
                if hum is not None:
                    try:
                        self._check_humidity_alert("Termostato BTicino", float(hum))
                    except ValueError:
                        pass
                mode = json_data.get("mode")
                if mode:
                    b_on = (mode.lower() != "off")
                    mod_str = "Riscaldamento" if mode.lower() == "heat" else "Raffrescamento" if mode.lower() == "cool" else mode
                    self._check_device_state_change("bticino", b_on, "Termostato BTicino", "bticino", extra_info=f" (Modalità: {mod_str})")

                # Registra la telemetria climatica su SQLite per l'analisi grafica
                if hasattr(self, "event_logger") and self.event_logger:
                    curr_t = json_data.get("current_temp")
                    hum_v = json_data.get("humidity")
                    target_t = json_data.get("target_temp")
                    mode_v = json_data.get("mode")
                    try:
                        self.event_logger.log_climate_telemetry("Termostato BTicino", curr_t, hum_v, target_t, mode_v)
                    except Exception as e_log:
                        print(f"[!] Errore salvataggio telemetria clima BTicino: {e_log}")
            return None

        if "daikin" in topic_lower:
            if isinstance(json_data, dict):
                self.daikin_data = json_data
                hum = json_data.get("room_humidity") or json_data.get("humidity")
                if hum is not None:
                    try:
                        self._check_humidity_alert("Climatizzatore Daikin", float(hum))
                    except ValueError:
                        pass
                p_val = json_data.get("power") if json_data.get("power") is not None else json_data.get("on_off")
                if p_val is not None:
                    d_on = p_val if isinstance(p_val, bool) else (str(p_val).lower() in ["on", "true", "1"])
                    
                    mode_raw = json_data.get("mode", "")
                    mod_str = ""
                    if mode_raw:
                        mod_tr = "Riscaldamento" if "heat" in mode_raw.lower() else "Raffrescamento" if "cool" in mode_raw.lower() else "Deumidificatore" if "dry" in mode_raw.lower() else "Ventilazione" if "fan" in mode_raw.lower() else mode_raw
                        mod_str = f" (Modalità: {mod_tr})"
                        
                    self._check_device_state_change("daikin", d_on, "Climatizzatore Daikin", "daikin", extra_info=mod_str)
            return None

        if "bosch" in topic_lower or "lavastoviglie" in topic_lower:
            if topic_lower.endswith("/event"):
                if isinstance(json_data, dict) and json_data.get("event") == "ProgramFinished":
                    msg = "🍽️ *LAVASTOVIGLIE TERMINATA!*\n\nIl programma di lavaggio è appena terminato."
                    if hasattr(self, "event_logger") and self.event_logger is not None:
                        self.event_logger.log_event("bosch", "Lavastoviglie Bosch", "Lavaggio Terminato ✅")
                    if self.notification_callback:
                        self.notification_callback("bosch", msg)
            else:
                if isinstance(json_data, dict):
                    self.bosch_data = json_data
                    p_state = json_data.get("power")
                    if p_state:
                        b_on = (p_state.lower() == "on")
                        
                        prog_raw = json_data.get("program", "")
                        prog_str = ""
                        if prog_raw:
                            prog_clean = prog_raw.replace('BSH.Common.Root.ActiveProgram.', '')
                            if "Eco50" in prog_clean: prog_clean = "Eco 50°C"
                            elif "Auto2" in prog_clean: prog_clean = "Auto 45-65°C"
                            elif "Intensiv70" in prog_clean: prog_clean = "Intensivo 70°C"
                            elif "Quick65" in prog_clean: prog_clean = "Rapido 65°C"
                            elif "Quick45" in prog_clean: prog_clean = "Rapido 45°C"
                            prog_str = f" (Programma: {prog_clean})"
                            
                        # Tracciamo lo stato internamente, ma NON chiamiamo _check_device_state_change 
                        # per evitare notifiche di Accensione/Spegnimento
                        with self.lock:
                            self.device_states_history["bosch"] = b_on
            return None



        # Determinazione dell'ID dispositivo corretto (gestisce sia shellies/id che id/status)
        if parts[0].lower() == "shellies" and len(parts) > 1:
            s_id = parts[1]
        else:
            s_id = parts[0]

        # Gestione payload status per dispositivi EM (es. PowerMeterHome.shelly/status)
        if isinstance(json_data, dict) and "emeters" in json_data and ("status" in topic_lower or "emeter" in topic_lower):
            em_device_id = s_id
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            with self.lock:
                if em_device_id not in self.shelly_devices:
                    self.shelly_devices[em_device_id] = {"shelly_id": em_device_id, "channels": {}, "last_updated": now_str}

                for idx, em in enumerate(json_data["emeters"]):
                    ch_str = str(idx)
                    if ch_str not in self.shelly_devices[em_device_id]["channels"]:
                        self.shelly_devices[em_device_id]["channels"][ch_str] = {"is_on": None, "power": None}
                    ch_entry = self.shelly_devices[em_device_id]["channels"][ch_str]
                    if "power" in em and em["power"] is not None:
                        ch_entry["power"] = float(em["power"])
                        ch_entry["is_on"] = float(em["power"]) > 0
                    if "voltage" in em and em["voltage"] is not None:
                        ch_entry["voltage"] = float(em["voltage"])
                    if "current" in em and em["current"] is not None:
                        ch_entry["current"] = float(em["current"])
                    if "total" in em and em["total"] is not None:
                        ch_entry["energy_kwh"] = float(em["total"]) / 1000.0

                self.shelly_devices[em_device_id]["last_updated"] = now_str

            # Log telemetria per il canale 0 principale
            if json_data["emeters"] and hasattr(self, "event_logger") and self.event_logger:
                em0 = json_data["emeters"][0]
                pw0 = em0.get("power", 0.0)
                v0 = em0.get("voltage")
                a0 = em0.get("current")
                e0 = (float(em0.get("total", 0.0)) / 1000.0) if "total" in em0 else None
                try:
                    self.event_logger.log_power_telemetry(em_device_id, float(pw0), voltage=v0, current=a0, energy_kwh=e0)
                    print(f"[EM] Telemetria {em_device_id} da /status: {float(pw0):.1f}W, {v0 or '?'}V, {a0 or '?'}A")
                except Exception as e_log:
                    print(f"[!] Errore log telemetria {em_device_id}: {e_log}")

                # Outage Prevention: verifica sovraccarico contatore & avviso Alexa
                try:
                    self._check_power_outage_alert(em_device_id, float(pw0), voltage=v0, current=a0)
                except Exception as e_outage:
                    print(f"[!] Errore controllo outage prevention {em_device_id}: {e_outage}")

            return None

        # Il blocco emeter qui sotto gestisce tutti i device EM genericamente
        # Supporta sia shellies/<id>/emeter/<ch>/<field> sia <id>/emeter/<ch>/<field>
        if "emeter" in topic_lower and len(parts) >= 3:
            if parts[0].lower() == "shellies" and len(parts) >= 4:
                em_device_id = parts[1]   # es. "PowerMeterHome.shelly"
            else:
                em_device_id = parts[0]   # es. "PowerMeterHome.shelly"
            em_field = parts[-1].lower()
            try:
                em_value = float(payload.strip())
            except (ValueError, TypeError):
                em_value = None

            if em_value is not None and hasattr(self, "event_logger") and self.event_logger:
                # Accumuliamo i valori in un dizionario temporaneo per device
                if not hasattr(self, "_em_accum"):
                    self._em_accum = {}
                if em_device_id not in self._em_accum:
                    self._em_accum[em_device_id] = {}

                if em_field == "power":
                    self._em_accum[em_device_id]["power"] = em_value
                elif em_field == "voltage":
                    self._em_accum[em_device_id]["voltage"] = em_value
                elif em_field in ("current", "a"):
                    self._em_accum[em_device_id]["current"] = em_value
                elif em_field in ("total", "energy"):
                    self._em_accum[em_device_id]["energy_kwh"] = em_value / 1000.0  # Wh → kWh

                acc = self._em_accum[em_device_id]
                if "power" in acc:
                    try:
                        self.event_logger.log_power_telemetry(
                            em_device_id,
                            acc["power"],
                            voltage=acc.get("voltage"),
                            current=acc.get("current"),
                            energy_kwh=acc.get("energy_kwh")
                        )
                        print(f"[EM] Telemetria {em_device_id}: {acc['power']:.1f}W, {acc.get('voltage', '?')}V, {acc.get('current', '?')}A")
                    except Exception as e_em:
                        print(f"[!] Errore salvataggio telemetria EM {em_device_id}: {e_em}")

                    try:
                        self._check_power_outage_alert(em_device_id, acc["power"], voltage=acc.get("voltage"), current=acc.get("current"))
                    except Exception as e_outage:
                        print(f"[!] Errore controllo outage prevention {em_device_id}: {e_outage}")



                # Aggiorna anche shelly_devices per mostrare potenza in /stato
                # NOTA: siamo già dentro self.lock (acquisito da _on_message) — NO with self.lock
                if em_device_id not in self.shelly_devices:
                    self.shelly_devices[em_device_id] = {"shelly_id": em_device_id, "channels": {}, "last_updated": ""}
                if "0" not in self.shelly_devices[em_device_id]["channels"]:
                    self.shelly_devices[em_device_id]["channels"]["0"] = {"is_on": None, "power": None}
                ch0 = self.shelly_devices[em_device_id]["channels"]["0"]
                if em_field == "power":
                    ch0["power"] = em_value
                    ch0["is_on"] = em_value > 0
                elif em_field == "voltage":
                    ch0["voltage"] = em_value
                elif em_field in ("current", "a"):
                    ch0["current"] = em_value

            return None  # Gestito: non proseguire il parsing standard


        # --- 1. SENSORI / INGRESSI ALLARME IO (AllarmeIO.shelly) ---
        is_dict = isinstance(json_data, dict)


        if "allarmeio" in s_id.lower() or "shellyplusuni" in s_id.lower():
            # Estrazione del dizionario sorgente (supporta sia json_data diretto che json_data["result"])
            res_dict = json_data.get("result") if is_dict and isinstance(json_data.get("result"), dict) else (json_data if is_dict else {})

            # 1. Parsing Input 0 (Allarme Garage)
            if "input:0" in res_dict and isinstance(res_dict["input:0"], dict):
                st0 = res_dict["input:0"].get("state")
                if st0 is not None:
                    self._update_io_input(0, st0)
            elif "input:0" in topic_lower or "input/0" in topic_lower:
                st0 = json_data.get("state") if is_dict else (payload.strip().lower() in ["on", "true", "1"])
                if st0 is not None:
                    self._update_io_input(0, st0)

            # 2. Parsing Input 1 (Allarme Casa)
            if "input:1" in res_dict and isinstance(res_dict["input:1"], dict):
                st1 = res_dict["input:1"].get("state")
                if st1 is not None:
                    self._update_io_input(1, st1)
            elif "input:1" in topic_lower or "input/1" in topic_lower:
                st1 = json_data.get("state") if is_dict else (payload.strip().lower() in ["on", "true", "1"])
                if st1 is not None:
                    self._update_io_input(1, st1)

            # 3. Parsing Switch 0 (Output Relè Camera Corridoio)
            if "switch:0" in res_dict and isinstance(res_dict["switch:0"], dict):
                sw0 = res_dict["switch:0"].get("output") if res_dict["switch:0"].get("output") is not None else res_dict["switch:0"].get("state")
                if sw0 is not None:
                    self._update_io_output(0, bool(sw0))
            elif "switch:0" in topic_lower or "relay/0" in topic_lower:
                sw0 = json_data.get("output") if (is_dict and "output" in json_data) else (json_data.get("state") if is_dict else (payload.strip().lower() in ["on", "true", "1"]))
                if sw0 is not None:
                    self._update_io_output(0, bool(sw0))

            # Voltmeter 100: Feedback Serranda Garage
            if "voltmeter:100" in res_dict and isinstance(res_dict["voltmeter:100"], dict):
                v = res_dict["voltmeter:100"].get("voltage")
                if v is not None:
                    try:
                        self._process_voltmeter_reading(float(v))
                    except ValueError:
                        pass
            elif "voltmeter:100" in topic_lower or "voltmeter/100" in topic_lower:
                v = json_data.get("voltage") if is_dict and "voltage" in json_data else None
                if is_dict and "voltmeter:100" in json_data and isinstance(json_data["voltmeter:100"], dict):
                    v = json_data["voltmeter:100"].get("voltage")
                if v is not None:
                    try:
                        self._process_voltmeter_reading(float(v))
                    except ValueError:
                        pass





        # --- 2. COMANDI E STATO GARAGE (garage/stato/1 e garage/stato/2 o garage/comando/2) ---
        if topic_lower.startswith("garage/"):
            ch_id = parts[-1]
            st_val = payload.strip().upper()
            
            # Non sovrascrivere lo stato con la stringa di comando "STATUS"
            if st_val != "STATUS":
                self.garage_states[ch_id] = st_val

            # Se l'impulso è per il cancello (canale 2) ed è un'attivazione d'apertura
            if ch_id == "2" and st_val in ["ON", "ACTIVE", "ATTIVO", "1", "TRUE", "OPEN", "OPENED", "TRIG", "TOGGLE", "PUSH"]:
                uptime = time.time() - getattr(self, "start_time", 0.0)
                is_boot_grace = uptime < 25.0
                if is_retained:
                    print(f"[GATE] Ricevuto stato cancello RETAINED '{st_val}'. Notifica apertura soppressa.")
                elif is_boot_grace:
                    print(f"[GATE INIT] Ricevuto stato cancello all'avvio: '{st_val}' (uptime: {uptime:.1f}s < 25s). Notifica apertura soppressa.")
                else:
                    if self.gate_timer:
                        self.gate_timer.cancel()
                    delay = getattr(config, "GATE_OPEN_NOTIFY_DELAY", self.gate_notify_delay)
                    self.gate_timer = threading.Timer(
                        delay,  # Ritardo impostato in .env (GATE_OPEN_NOTIFY_DELAY)
                        self._on_gate_opened_timer
                    )
                    self.gate_timer.daemon = True
                    self.gate_timer.start()

            return None





        # --- 3. ESTRAZIONE STATO RELÈ TRAMITE IL VALORE BOOLEANO 'output' ---
        output = None
        apower = None
        voltage = None
        current = None
        energy_kwh = None
        t_dur = None
        t_start = None
        t_rem = None

        if is_dict:
            output, apower, t_dur, t_start, t_rem, voltage, current, energy_kwh = self._extract_output_and_power(json_data)

        # Ripiego per payload in testo semplice (es. "on"/"off", "true"/"false")
        # ECCEZIONE: Shelly EM Gen1 pubblica relay/0=off anche se non ha un relè reale —
        # ignorare il relay per questi device per non sovrascrivere is_on calcolato da emeter/power
        is_em_device = "powermeterhome" in s_id.lower()
        is_relay_topic = "relay" in topic_lower
        if output is None and not (is_em_device and is_relay_topic) and payload.strip().lower() in ["on", "off", "true", "false", "1", "0"]:
            output = payload.strip().lower() in ["on", "true", "1"]


        if output is not None or apower is not None or t_dur is not None or t_rem is not None or voltage is not None:
            return {
                "shelly_id": s_id,
                "channel": "0",
                "is_on": bool(output) if output is not None else None,
                "power": apower,
                "voltage": voltage,
                "current": current,
                "energy_kwh": energy_kwh,
                "timer_duration": t_dur,
                "timer_started_at": t_start,
                "timer_remaining": t_rem
            }

        return None

