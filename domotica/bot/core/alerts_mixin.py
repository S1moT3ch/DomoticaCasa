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

class AlertsMixin:
    def _check_humidity_alert(self, sensor_name, val):
        """Verifica se l'umidità supera la soglia (default 60%) ed invia la notifica push con temperatura e tastiera in-line"""
        with self.lock:
            threshold = getattr(config, "HUMIDITY_ALERT_THRESHOLD", 60.0)
            reset_threshold = threshold - 2.0  # Hysteresis reset a 58%
            already_notified = self.humidity_alerts_sent.get(sensor_name, False)

            if val >= threshold:
                if not already_notified:
                    self.humidity_alerts_sent[sensor_name] = True
                    if self.event_logger is not None:
                        self.event_logger.log_event("humidity", sensor_name, f"Soglia umidità superata: {val:.1f}% (> {threshold:.0f}%)")

                    # Rilevamento temperatura stanza da Termostato BTicino o Daikin
                    room_temp_str = "--"
                    if self.termostato_data and isinstance(self.termostato_data, dict):
                        t_val = self.termostato_data.get("current_temp")
                        if t_val is not None:
                            room_temp_str = f"{t_val:.1f}" if isinstance(t_val, (int, float)) else str(t_val)
                    elif self.daikin_data and isinstance(self.daikin_data, dict):
                        d_val = self.daikin_data.get("room_temp")
                        if d_val is not None:
                            room_temp_str = f"{d_val:.1f}" if isinstance(d_val, (int, float)) else str(d_val)

                    msg = (
                        f"💧 *ALLERTA ELEVATA UMIDITÀ (> {threshold:.0f}%)*\n\n"
                        f"⚠️ Rilevato valore di umidità del *{val:.1f}%* da *{sensor_name}*.\n"
                        f"🌡️ *Temperatura Stanza*: *{room_temp_str}°C*\n\n"
                        f"❄️ Desideri attivare il *Climatizzatore Daikin* per deumidificare o rinfrescare l'ambiente?"
                    )
                    print(f"[HUMIDITY ALERT] {sensor_name}: {val:.1f}% (Temp: {room_temp_str}°C)")

                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "💧 Deumidifica (Dry)", "callback_data": "cmd_ask:Daikin:dry:Clima Daikin (Deumidifica)"},
                                {"text": "❄️ Accendi (Cool)", "callback_data": "cmd_ask:Daikin:on:Clima Daikin (Accendi)"}
                            ],
                            [
                                {"text": "❌ Ignora / Chiudi", "callback_data": "cmd_cancel"}
                            ]
                        ]
                    }

                    if self.notification_callback:
                        self.notification_callback("humidity", msg, reply_markup=reply_markup)

            elif val <= reset_threshold and already_notified:
                self.humidity_alerts_sent[sensor_name] = False
                print(f"[HUMIDITY ALERT RESET] {sensor_name}: umidità scesa a {val:.1f}%")


    def trigger_telegram_voice_call(self, target_user, text):
        """Effettua una chiamata vocale di emergenza via Telegram usando CallMeBot API"""
        def _call_worker():
            try:
                import urllib.parse
                clean_target = str(target_user).strip()
                if clean_target.isdigit():
                    print(f"[!] ATTENZIONE VOICE CALL: CallMeBot richiede un Username Telegram (es. @tuousername) o Numero (+39...), non un ID numerico ({clean_target}). Imposta VACATION_ALARM_CALL_TARGET nel file .env.")
                    return
                if not clean_target.startswith("@") and not clean_target.startswith("+"):
                    clean_target = f"@{clean_target}"
                
                api_key = getattr(config, "CALLMEBOT_API_KEY", "").strip()
                encoded_text = urllib.parse.quote(text)
                url = f"https://api.callmebot.com/start.php?user={clean_target}&text={encoded_text}&lang=it-IT"
                if api_key:
                    url += f"&apikey={api_key}"
                print(f"[VOICE CALL] Avvio chiamata vocale Telegram verso {clean_target}...")
                resp = requests.get(url, timeout=15)
                if resp.ok:
                    print(f"[VOICE CALL] Chiamata Telegram avviata con successo verso {clean_target}")
                else:
                    print(f"[!] Risposta CallMeBot Voice Call ({resp.status_code}): {resp.text}")
            except Exception as e:
                print(f"[!] Errore nell'avvio della chiamata vocale Telegram: {e}")

        t_call = threading.Thread(target=_call_worker, daemon=True, name="TelegramVoiceCallThread")
        t_call.start()

    def trigger_whatsapp_voice_alert(self, phone, apikey, text):
        """Invia un avviso vocale / messaggio di emergenza immediato su WhatsApp via CallMeBot"""
        def _wa_worker():
            try:
                import urllib.parse
                clean_phone = str(phone).strip()
                if not clean_phone.startswith("+"):
                    if clean_phone.startswith("3"):
                        clean_phone = f"+39{clean_phone}"
                    else:
                        clean_phone = f"+{clean_phone}"
                encoded_text = urllib.parse.quote(text)
                url = f"https://api.callmebot.com/whatsapp.php?phone={clean_phone}&text={encoded_text}&apikey={apikey}"
                print(f"[WHATSAPP EMERGENCY] Avvio notifica WhatsApp ad alta priorità verso {clean_phone}...")
                resp = requests.get(url, timeout=15)
                if resp.ok and ("Message sent" in resp.text or "ok" in resp.text.lower()):
                    print(f"[WHATSAPP EMERGENCY] Notifica WhatsApp di emergenza inviata con successo a {clean_phone}!")
                else:
                    print(f"[!] Risposta CallMeBot WhatsApp ({resp.status_code}): {resp.text[:200]}")
            except Exception as e:
                print(f"[!] Errore invio notifica WhatsApp: {e}")

        t_wa = threading.Thread(target=_wa_worker, daemon=True, name="WhatsAppCallThread")
        t_wa.start()






    def is_any_alarm_armed(self):
        """Restituisce (is_armed, alarm_type_name) verificando se Allarme Casa o Allarme Garage sono inseriti"""
        with self.lock:
            # 1. Allarme Casa (Input 1 di AllarmeIO.shelly)
            casa_armed = bool(self.io_inputs.get(1, False))

            # 2. Allarme Garage (Input 0 di AllarmeIO.shelly o relay Allarme.shelly)
            garage_armed = bool(self.io_inputs.get(0, False))

            allarme_shelly_data = self.shelly_devices.get("Allarme.shelly", {})
            if not garage_armed and allarme_shelly_data:
                ch0 = allarme_shelly_data.get("channels", {}).get("0", {})
                garage_armed = bool(ch0.get("is_on") or ch0.get("state"))

            if casa_armed and garage_armed:
                return True, "CASA & GARAGE"
            elif casa_armed:
                return True, "CASA"
            elif garage_armed:
                return True, "GARAGE"
            else:
                return False, ""

    def _check_power_outage_alert(self, device_name, power_w, voltage=None, current=None):
        """Verifica se la potenza assorbita dallo Shelly EM supera la soglia d'allarme contatore (.env)"""
        d_lower = str(device_name).lower()
        # Escludiamo il contatore del Garage/Box per l'allarme contatore Casa
        if "box" in d_lower or "garage" in d_lower:
            return

        if not getattr(config, "OUTAGE_PREVENTION_ENABLED", True):
            return

        threshold_kw = float(getattr(config, "POWER_ALERT_THRESHOLD_KW", 2.9))
        hysteresis_kw = float(getattr(config, "POWER_ALERT_HYSTERESIS_KW", 0.4))
        cooldown_sec = float(getattr(config, "POWER_ALERT_COOLDOWN_SECONDS", 180))
        target_device = getattr(config, "ALEXA_TARGET_DEVICE", "EchoPop_Cucina")

        power_kw = float(power_w) / 1000.0
        now = time.time()

        # Memorizziamo le ultime rilevazioni per query vocali
        self.latest_home_power = power_w
        if voltage is not None:
            self.latest_home_voltage = voltage
        if current is not None:
            self.latest_home_current = current

        with self.lock:
            # Superamento soglia di allarme
            if power_kw >= threshold_kw:
                last_alert = getattr(self, "last_outage_alert_time", 0.0)
                is_active = getattr(self, "outage_alert_active", False)

                if (not is_active) or ((now - last_alert) >= cooldown_sec):
                    self.outage_alert_active = True
                    self.last_outage_alert_time = now

                    speech_text = (
                        f"Attenzione! Rilevato assorbimento di {power_kw:.1f} kilowatt, "
                        f"superata la soglia di {threshold_kw:.1f} kilowatt: rischio distacco contatore!"
                    )

                    print(f"\n[OUTAGE ALERT] (!) SOVRACCARICO CONTATORE! ({power_kw:.2f} kW >= {threshold_kw:.2f} kW)")
                    print(f"               Invio annuncio vocale su '{target_device}'...")

                    # 1. Invio annuncio vocale ad Alexa su EchoPop_Cucina
                    self.trigger_alexa_speech(speech_text, target_device=target_device)

                    # 2. Log evento in SQLite
                    if hasattr(self, "event_logger") and self.event_logger:
                        log_msg = (
                            f"SOVRACCARICO: {power_kw:.2f} kW assorbiti (> {threshold_kw:.1f} kW). "
                            f"Dettagli: {power_w:.1f} W, {voltage or '?'} V, {current or '?'} A"
                        )
                        self.event_logger.log_event("outage_warning", "ShellyEM_Casa", log_msg)

                    # 3. Notifica Telegram prioritaria
                    v_str = f"{voltage:.1f} V" if voltage else "--"
                    a_str = f"{current:.2f} A" if current else "--"
                    msg_tg = (
                        f"⚡ *ALLARME SOVRACCARICO ELETTRICO!*\n\n"
                        f"⚠️ Rilevato assorbimento di *{power_kw:.2f} kW* (*{power_w:.0f} W*),\n"
                        f"superata la soglia di sicurezza di *{threshold_kw:.1f} kW*!\n\n"
                        f"🚨 *Rischio imminente di distacco del contatore!*\n"
                        f"⚡ Tensione: *{v_str}* | Corrente: *{a_str}*\n"
                        f"🔊 *Avviso vocale inviato su*: `{target_device}`\n\n"
                        f"💡 _Consiglio: spegni tempestivamente gli elettrodomestici energivori (forno, lavatrice, clima, phon)._"
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": "📊 Grafico Consumi Casa", "callback_data": "cmd_chart_power_casa"}],
                            [{"text": "❌ Chiudi Notifica", "callback_data": "cmd_cancel"}]
                        ]
                    }
                    if self.notification_callback:
                        self.notification_callback("outage_warning", msg_tg, reply_markup=reply_markup)

            # Rientro nella normalità sotto la soglia di reset (isteresi)
            elif power_kw <= (threshold_kw - hysteresis_kw):
                if getattr(self, "outage_alert_active", False):
                    self.outage_alert_active = False
                    print(f"[OUTAGE RECOVERY] [OK] Consumo rientrato a {power_kw:.2f} kW (< {threshold_kw - hysteresis_kw:.2f} kW). Sistema riarmato.")
                    if hasattr(self, "event_logger") and self.event_logger:
                        self.event_logger.log_event("outage_warning", "ShellyEM_Casa", f"Consumo rientrato sotto soglia di sicurezza: {power_kw:.2f} kW")


    def trigger_alexa_speech(self, text, target_device=None):
        """Invia un annuncio vocale Text-To-Speech all'altoparlante Alexa specificato (EchoPop_Cucina)"""
        if not target_device:
            target_device = getattr(config, "ALEXA_TARGET_DEVICE", "EchoPop_Cucina")

        def _alexa_worker():
            vm_token = getattr(config, "VOICEMONKEY_TOKEN", "").strip()

            # 1. Metodo Voice Monkey v3 (Annuncio TTS mirato sul dispositivo Echo)
            if vm_token:
                try:
                    headers = {
                        "Authorization": f"Bearer {vm_token}",
                        "Content-Type": "application/json"
                    }
                    dev_id = target_device
                    if dev_id in ("EchoPop_Cucina", "EchoPop Cucina", "echopop_cucina"):
                        dev_id = "echopop-cucina-w59fy"

                    payload_v3 = {
                        "token": vm_token,
                        "device": dev_id,
                        "speech": text,
                        "language": "it-IT"
                    }
                    url_v3 = "https://api-v3.voicemonkey.io/announce"
                    r = requests.post(url_v3, json=payload_v3, headers=headers, timeout=10)
                    if r.ok:
                        print(f"[ALEXA] [+] Annuncio vocale inviato con successo a {target_device} ({dev_id}) via Voice Monkey v3!")
                        return
                    else:
                        print(f"[ALEXA] [!] Risposta Voice Monkey v3 ({r.status_code}): {r.text}")
                except Exception as ex_vm:
                    print(f"[ALEXA] [!] Errore connessione Voice Monkey: {ex_vm}")

            # 2. Metodo Sinric Pro (se configurato SINRIC_POWER_DEVICE_ID)
            sinric_dev_id = getattr(config, "SINRIC_POWER_DEVICE_ID", "").strip()
            sinric_key = getattr(config, "SINRIC_APP_KEY", "").strip()

            if sinric_dev_id and sinric_key:
                try:
                    url_sinric = "https://api.sinric.pro/api/v1/devices/action"
                    headers = {
                        "x-sinric-api-key": sinric_key,
                        "Content-Type": "application/json"
                    }
                    sinric_payload = {
                        "deviceId": sinric_dev_id,
                        "action": "DoorbellPress",
                        "value": {}
                    }
                    r_sinric = requests.post(url_sinric, json=sinric_payload, headers=headers, timeout=10)
                    if r_sinric.ok:
                        print(f"[ALEXA] [+] Trigger SinricPro inviato al device {sinric_dev_id}")
                    else:
                        print(f"[ALEXA] [!] Risposta SinricPro API ({r_sinric.status_code}): {r_sinric.text}")
                except Exception as ex_sinric:
                    print(f"[ALEXA] [!] Errore trigger SinricPro: {ex_sinric}")

            if not vm_token and not sinric_dev_id:
                print(f"[ALEXA] [!] Notifica vocale generata per {target_device}: '{text}'")
                print(f"        (Configura VOICEMONKEY_TOKEN o SINRIC_POWER_DEVICE_ID in .env per la riproduzione fisica su Echo)")

        t_alexa = threading.Thread(target=_alexa_worker, daemon=True, name="AlexaVoiceAlertThread")
        t_alexa.start()



