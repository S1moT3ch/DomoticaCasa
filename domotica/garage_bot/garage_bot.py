#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Garage Telegram Bots Manager
----------------------------
Gestisce in modo parallelo due Bot Telegram dedicati per il garage:
1. AllarmeGarage: notifica "INSERITO" quando l'allarme viene inserito e "DISINSERITO" quando viene disinserito.
2. Serranda: notifica "APERTA" quando la serranda viene aperta e "CHIUSA" quando viene chiusa.

Entrambi i bot sono protetti da password e autenticazione a due fattori (2FA)
tramite il bot amministrativo ACSAHomeUtils.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import secrets
import threading
import requests
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

# --- CARICAMENTO CONFIGURAZIONE DA .ENV ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_file = os.path.join(BASE_DIR, ".env")
load_dotenv(env_file)

ALLARME_GARAGE_BOT_TOKEN = os.getenv("ALLARME_GARAGE_BOT_TOKEN", "").strip()
SERRANDA_BOT_TOKEN = os.getenv("SERRANDA_BOT_TOKEN", "").strip()
ACSA_UTILS_BOT_TOKEN = os.getenv("ACSA_UTILS_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

BOT_PASSWORD = os.getenv("BOT_PASSWORD", "DomoticaPass!2026").strip()
BOT_PASSWORD_SALT = os.getenv("BOT_PASSWORD_SALT", "a8f3b9e1c42d7051").strip()
BOT_PASSWORD_HASH = os.getenv("BOT_PASSWORD_HASH", "8b794ed6b455aca947f5268dc9b83896e63393e9956c9a9e5a603e0a97357a70").strip()

MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1").strip()
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "").strip() or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "").strip() or None

SHUTTER_DEBOUNCE_DELAY = float(os.getenv("SHUTTER_DEBOUNCE_DELAY", "4.0"))
VOLTMETER_THRESHOLD = float(os.getenv("VOLTMETER_THRESHOLD", "5.3"))

AUTHORIZED_USERS_FILE = os.path.join(BASE_DIR, "authorized_users.json")
PENDING_USERS_FILE = os.path.join(BASE_DIR, "pending_users.json")

# Verifica password a tempo costante (anti-timing attack)
def hash_password(password, salt=None):
    if not salt:
        salt = secrets.token_hex(16)
    salted = f"{salt}:{password}".encode("utf-8")
    return hashlib.sha256(salted).hexdigest(), salt

def verify_password(input_password):
    if not input_password:
        return False
    # 1. Verifica tramite hash salted (se configurato)
    if BOT_PASSWORD_HASH and BOT_PASSWORD_SALT:
        computed, _ = hash_password(input_password, BOT_PASSWORD_SALT)
        if hmac.compare_digest(computed, BOT_PASSWORD_HASH):
            return True
    # 2. Fallback a confronto diretto con BOT_PASSWORD
    if BOT_PASSWORD and hmac.compare_digest(input_password.strip(), BOT_PASSWORD):
        return True
    return False


# --- GESTORE UTENTI E AUTORIZZAZIONI ---
class GarageUserManager:
    def __init__(self, filepath=AUTHORIZED_USERS_FILE):
        self.filepath = filepath
        self.pending_filepath = PENDING_USERS_FILE
        self.lock = threading.RLock()
        self.authorized_users = {}
        self.pending_users = {}
        self._load()

    def _load(self):
        with self.lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        self.authorized_users = json.load(f)
                except Exception as e:
                    print(f"[!] Errore lettura {self.filepath}: {e}", flush=True)
                    self.authorized_users = {}

            if os.path.exists(self.pending_filepath):
                try:
                    with open(self.pending_filepath, "r", encoding="utf-8") as f:
                        self.pending_users = json.load(f)
                except Exception:
                    self.pending_users = {}

            # Assicura che l'amministratore principale sia pre-autorizzato su entrambi i bot
            if ADMIN_CHAT_ID and str(ADMIN_CHAT_ID) not in self.authorized_users:
                self.authorized_users[str(ADMIN_CHAT_ID)] = {
                    "name": "Cantore (Admin)",
                    "username": "S1moT3ch",
                    "chat_id": str(ADMIN_CHAT_ID),
                    "allarme_garage": True,
                    "serranda": True
                }
                self._save()

    def _save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.authorized_users, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[!] Errore salvataggio {self.filepath}: {e}", flush=True)

    def _save_pending(self):
        try:
            with open(self.pending_filepath, "w", encoding="utf-8") as f:
                json.dump(self.pending_users, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[!] Errore salvataggio {self.pending_filepath}: {e}", flush=True)

    def is_authorized(self, chat_id, bot_type="allarme_garage"):
        """bot_type: 'allarme_garage' oppure 'serranda'"""
        with self.lock:
            self._load()
            cid = str(chat_id)
            if cid == str(ADMIN_CHAT_ID):
                return True
            udata = self.authorized_users.get(cid)
            if not udata or not isinstance(udata, dict):
                return False
            return bool(udata.get(bot_type, False))

    def set_pending(self, chat_id, bot_type, user_info):
        with self.lock:
            cid = str(chat_id)
            if cid not in self.pending_users:
                self.pending_users[cid] = {}
            self.pending_users[cid][bot_type] = {
                "user_info": user_info,
                "timestamp": time.time(),
                "time_str": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            self._save_pending()

    def is_pending(self, chat_id, bot_type):
        with self.lock:
            cid = str(chat_id)
            return cid in self.pending_users and bot_type in self.pending_users[cid]

    def approve_user(self, chat_id, bot_type, user_info=None):
        with self.lock:
            self._load()
            cid = str(chat_id)
            p_info = {}
            if cid in self.pending_users:
                p_info = self.pending_users[cid].pop(bot_type, {}).get("user_info", {})
                if not self.pending_users[cid]:
                    self.pending_users.pop(cid, None)
                self._save_pending()

            target_info = user_info or p_info or {"name": f"User {cid}", "username": ""}
            if cid not in self.authorized_users:
                self.authorized_users[cid] = {
                    "name": target_info.get("name", f"User {cid}"),
                    "username": target_info.get("username", ""),
                    "chat_id": cid,
                    "allarme_garage": False,
                    "serranda": False
                }

            if bot_type in ["allarme_garage", "allarme"]:
                self.authorized_users[cid]["allarme_garage"] = True
            elif bot_type in ["serranda", "shutter"]:
                self.authorized_users[cid]["serranda"] = True
            elif bot_type in ["all", "both"]:
                self.authorized_users[cid]["allarme_garage"] = True
                self.authorized_users[cid]["serranda"] = True

            self._save()
            return self.authorized_users[cid]

    def reject_user(self, chat_id, bot_type):
        with self.lock:
            cid = str(chat_id)
            if cid in self.pending_users:
                self.pending_users[cid].pop(bot_type, None)
                if not self.pending_users[cid]:
                    self.pending_users.pop(cid, None)
                self._save_pending()
            if cid in self.authorized_users:
                if bot_type in ["allarme_garage", "allarme"]:
                    self.authorized_users[cid]["allarme_garage"] = False
                elif bot_type in ["serranda", "shutter"]:
                    self.authorized_users[cid]["serranda"] = False
                elif bot_type in ["all", "both"]:
                    self.authorized_users.pop(cid, None)
                self._save()

    def get_subscribers(self, bot_type):
        with self.lock:
            self._load()
            subs = []
            for cid, udata in self.authorized_users.items():
                if isinstance(udata, dict) and udata.get(bot_type, False):
                    subs.append(cid)
            if ADMIN_CHAT_ID and str(ADMIN_CHAT_ID) not in subs:
                subs.append(str(ADMIN_CHAT_ID))
            return list(set(subs))


# --- CLIENT TELEGRAM PER NOTIFICHE E ACSAHOMEUTILS ---
class ACSAHomeUtilsClient:
    """Client per interagire con il bot amministrativo ACSAHomeUtils (notifiche 2FA e alert)"""
    def __init__(self, token=ACSA_UTILS_BOT_TOKEN, admin_chat_id=ADMIN_CHAT_ID):
        self.token = token
        self.admin_chat_id = admin_chat_id
        self.api_url = f"https://api.telegram.org/bot{self.token}" if self.token else None

    def is_configured(self):
        return bool(self.token and not self.token.startswith("INSERISCI_QUI"))

    def send_message(self, text, reply_markup=None, parse_mode="HTML"):
        if not self.is_configured() or not self.admin_chat_id:
            print(f"[ACSAHomeUtils] Non configurato o admin_chat_id assente. Messaggio ignorato: {text[:50]}", flush=True)
            return False
        url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": self.admin_chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            resp = requests.post(url, json=payload, timeout=10)
            return resp.ok
        except Exception as e:
            print(f"[!] Errore invio messaggio a ACSAHomeUtils: {e}", flush=True)
            return False

    def send_approval_request(self, bot_type, target_chat_id, user_info):
        """Invia all'amministratore la richiesta 2FA con bottoni di approvazione/rifiuto"""
        bot_label = "AllarmeGarage 🚗" if bot_type == "allarme_garage" else "Serranda 🚪"
        name = user_info.get("name", "Sconosciuto")
        username = user_info.get("username", "")
        user_tag = f"@{username}" if username else "Nessuno"
        cid = str(target_chat_id)

        text = (
            "🔐 <b>NUOVA RICHIESTA 2FA - GARAGE</b>\n\n"
            f"🤖 <b>Bot:</b> {bot_label}\n"
            f"👤 <b>Utente:</b> {name}\n"
            f"🏷️ <b>Username:</b> {user_tag}\n"
            f"🆔 <b>Chat ID:</b> <code>{cid}</code>\n\n"
            "L'utente ha inserito la password corretta. Vuoi autorizzarlo a ricevere le notifiche di questo bot?"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "Approva ✅", "callback_data": f"garage_approve:{bot_type}:{cid}"},
                    {"text": "Rifiuta ❌", "callback_data": f"garage_reject:{bot_type}:{cid}"}
                ]
            ]
        }
        return self.send_message(text, reply_markup=reply_markup)

    def send_unauthorized_alert(self, bot_type, target_chat_id, user_info, attempted_text):
        """Avvisa l'amministratore di un tentativo di accesso con password errata o non registrato"""
        bot_label = "AllarmeGarage 🚗" if bot_type == "allarme_garage" else "Serranda 🚪"
        name = user_info.get("name", "Sconosciuto")
        username = user_info.get("username", "")
        user_tag = f"@{username}" if username else "Nessuno"
        cid = str(target_chat_id)

        text = (
            "⚠️ <b>TENTATIVO ACCESSO NON AUTORIZZATO</b>\n\n"
            f"🤖 <b>Bot:</b> {bot_label}\n"
            f"👤 <b>Utente:</b> {name} ({user_tag})\n"
            f"🆔 <b>Chat ID:</b> <code>{cid}</code>\n"
            f"💬 <b>Testo/Comando:</b> <code>{attempted_text}</code>\n\n"
            "ℹ️ <i>L'utente NON ha inserito la password corretta.</i>"
        )
        return self.send_message(text)


# --- CLASSE BASE BOT TELEGRAM DEDICATO ---
class DedicatedTelegramBot:
    def __init__(self, token, bot_type, bot_label, user_manager, acsa_client, get_status_callback=None):
        self.token = token
        self.bot_type = bot_type          # 'allarme_garage' oppure 'serranda'
        self.bot_label = bot_label        # 'AllarmeGarage' oppure 'Serranda'
        self.user_manager = user_manager
        self.acsa_client = acsa_client
        self.get_status_callback = get_status_callback
        self.api_url = f"https://api.telegram.org/bot{self.token}" if self.token else None
        self.last_update_id = 0
        self.running = False

    def is_configured(self):
        return bool(self.token and not self.token.startswith("INSERISCI_QUI"))

    def send_message(self, chat_id, text, parse_mode=None):
        if not self.is_configured():
            return False
        url = f"{self.api_url}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            resp = requests.post(url, json=payload, timeout=10)
            return resp.ok
        except Exception as e:
            print(f"[{self.bot_label}] Errore invio messaggio a {chat_id}: {e}", flush=True)
            return False

    def broadcast(self, text, parse_mode=None):
        """Invia il messaggio a tutti gli utenti autorizzati per questo bot"""
        subscribers = self.user_manager.get_subscribers(self.bot_type)
        print(f"[{self.bot_label}] Broadcast ('{text}') a {len(subscribers)} utenti: {subscribers}", flush=True)
        for cid in subscribers:
            self.send_message(cid, text, parse_mode=parse_mode)

    def handle_message(self, message):
        chat_id = str(message.get("chat", {}).get("id"))
        from_user = message.get("from", {})
        text = message.get("text", "").strip()
        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        username = from_user.get("username", "")
        full_name = f"{first_name} {last_name}".strip() or f"User {chat_id}"
        user_info = {"name": full_name, "username": username, "chat_id": chat_id}

        is_auth = self.user_manager.is_authorized(chat_id, self.bot_type)

        # 1. UTENTE GIÀ AUTORIZZATO
        if is_auth:
            cmd = text.lower().split()[0] if text else ""
            if cmd in ["/start", "/help", "/aiuto"]:
                st = self.get_status_callback() if self.get_status_callback else "N/D"
                help_msg = (
                    f"🤖 <b>Bot {self.bot_label}</b>\n\n"
                    f"✅ <b>Sei autorizzato a ricevere le notifiche.</b>\n"
                    f"📊 <b>Stato attuale:</b> <code>{st}</code>\n\n"
                    "📌 <b>Comandi disponibili:</b>\n"
                    "• /stato - Visualizza lo stato attuale in tempo reale"
                )
                self.send_message(chat_id, help_msg, parse_mode="HTML")

            elif cmd in ["/stato", "/status"]:
                st = self.get_status_callback() if self.get_status_callback else "N/D"
                # Risposta pura e pulita con lo stato esatto richiesto
                self.send_message(chat_id, str(st))

            # Comandi riservati all'amministratore
            elif str(chat_id) == str(ADMIN_CHAT_ID):
                parts = text.split()
                if cmd in ["/approve", "/accetta"] and len(parts) > 1:
                    target_cid = parts[1]
                    info = self.user_manager.approve_user(target_cid, self.bot_type)
                    self.send_message(chat_id, f"✅ Utente <code>{target_cid}</code> ({info.get('name')}) approvato per {self.bot_label}!", parse_mode="HTML")
                    self.send_message(target_cid, f"🎉 <b>REGISTRAZIONE APPROVATA!</b>\nOra sei autorizzato a ricevere le notifiche di <b>{self.bot_label}</b>.", parse_mode="HTML")

                elif cmd in ["/reject", "/rifiuta"] and len(parts) > 1:
                    target_cid = parts[1]
                    self.user_manager.reject_user(target_cid, self.bot_type)
                    self.send_message(chat_id, f"❌ Utente <code>{target_cid}</code> rimosso/rifiutato da {self.bot_label}.", parse_mode="HTML")
                    self.send_message(target_cid, f"❌ La tua autorizzazione per <b>{self.bot_label}</b> è stata revocata.", parse_mode="HTML")

                elif cmd in ["/utenti", "/users"]:
                    subs = self.user_manager.get_subscribers(self.bot_type)
                    lines = [f"👥 <b>Utenti autorizzati per {self.bot_label} ({len(subs)} totali):</b>"]
                    for s in subs:
                        u = self.user_manager.authorized_users.get(s, {})
                        lines.append(f"• <code>{s}</code> - {u.get('name', 'Sconosciuto')}")
                    self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

                else:
                    st = self.get_status_callback() if self.get_status_callback else "N/D"
                    self.send_message(chat_id, str(st))
            else:
                st = self.get_status_callback() if self.get_status_callback else "N/D"
                self.send_message(chat_id, str(st))
            return

        # 2. UTENTE IN ATTESA DI APPROVAZIONE 2FA
        if self.user_manager.is_pending(chat_id, self.bot_type):
            pending_msg = (
                "⏳ <b>RICHIESTA IN ATTESA DI APPROVAZIONE 2FA</b>\n\n"
                f"Hai inserito la password corretta per <b>{self.bot_label}</b>.\n"
                "La richiesta è attualmente in attesa di approvazione da parte dell'amministratore sul bot <b>ACSAHomeUtils</b>.\n"
                "Riceverai una notifica automatica non appena confermata."
            )
            self.send_message(chat_id, pending_msg, parse_mode="HTML")
            return

        # 3. UTENTE NON AUTORIZZATO
        # Verifica se l'utente ha digitato la password di registrazione
        if verify_password(text):
            self.user_manager.set_pending(chat_id, self.bot_type, user_info)
            print(f"[{self.bot_label}] Password corretta da Chat ID {chat_id} ({full_name}). Invio richiesta 2FA ad ACSAHomeUtils...", flush=True)

            # Invia richiesta di approvazione 2FA ad ACSAHomeUtils
            self.acsa_client.send_approval_request(self.bot_type, chat_id, user_info)

            success_pwd_msg = (
                "🔑 <b>PASSWORD CORRETTA!</b>\n\n"
                f"La tua richiesta di registrazione per <b>{self.bot_label}</b> è stata inoltrata all'amministratore per l'approvazione 2FA su <b>ACSAHomeUtils</b>.\n\n"
                "Riceverai una notifica qui non appena verrà confermata."
            )
            self.send_message(chat_id, success_pwd_msg, parse_mode="HTML")
        else:
            # Password errata o primo avvio: invia avviso all'amministratore e chiedi password all'utente
            self.acsa_client.send_unauthorized_alert(self.bot_type, chat_id, user_info, text)

            if text.lower() in ["/start", "/help", "/aiuto"]:
                prompt_pwd_msg = (
                    f"🔒 <b>ACCESSO RISERVATO - {self.bot_label.upper()}</b>\n\n"
                    "Benvenuto! Questo bot richiede autorizzazione specifica e 2FA.\n\n"
                    "🔑 Per richiedere l'accesso, invia la <b>password di registrazione</b>."
                )
                self.send_message(chat_id, prompt_pwd_msg, parse_mode="HTML")
            else:
                generic_msg = (
                    "❌ <b>ACCESSO NEGATO</b>\n\n"
                    "Non sei autorizzato ad utilizzare questo bot.\n"
                    "Invia la password di registrazione corretta per richiedere l'autorizzazione 2FA."
                )
                self.send_message(chat_id, generic_msg, parse_mode="HTML")

    def start_polling(self):
        if not self.is_configured():
            print(f"[!] Token per {self.bot_label} non configurato in .env. Polling non avviato.", flush=True)
            return

        print(f"[+] Avvio polling Telegram per {self.bot_label}...", flush=True)
        self.running = True

        while self.running:
            try:
                url = f"{self.api_url}/getUpdates"
                params = {"offset": self.last_update_id + 1, "timeout": 20}
                resp = requests.get(url, params=params, timeout=25)

                if resp.ok:
                    data = resp.json()
                    if data.get("ok"):
                        for update in data.get("result", []):
                            self.last_update_id = update["update_id"]
                            if "message" in update and "text" in update["message"]:
                                msg = update["message"]
                                threading.Thread(target=self.handle_message, args=(msg,), daemon=True).start()
                else:
                    time.sleep(4)
            except Exception as e:
                print(f"[!] Errore polling {self.bot_label}: {e}", flush=True)
                time.sleep(5)


# --- MONITOR MQTT PER INGRESSI E VOLTMETRO ALLARMEIO ---
class GarageMQTTMonitor:
    def __init__(self, user_manager, allarme_bot, serranda_bot):
        self.user_manager = user_manager
        self.allarme_bot = allarme_bot
        self.serranda_bot = serranda_bot

        self.start_time = time.time()
        self.alarm_state = None            # "INSERITO" o "DISINSERITO"
        self.shutter_voltage = None
        self.stable_shutter_state = None   # "APERTA" o "CHIUSA"
        self.pending_shutter_state = None
        self.shutter_timer = None
        self.debounce_delay = SHUTTER_DEBOUNCE_DELAY
        self.threshold = VOLTMETER_THRESHOLD

        self.lock = threading.RLock()
        self.client = None
        self.connected = False

    def get_alarm_state(self):
        return self.alarm_state or "DISINSERITO"

    def get_shutter_state(self):
        return self.stable_shutter_state or "CHIUSA"

    def _on_shutter_stabilized(self, new_state, voltage_val):
        with self.lock:
            self.shutter_timer = None
            self.pending_shutter_state = None

            if self.stable_shutter_state != new_state:
                old_state = self.stable_shutter_state
                self.stable_shutter_state = new_state

                # Al primo avvio iniziale (old_state None o grace period), memorizza lo stato senza notifiche
                uptime = time.time() - self.start_time
                if old_state is None or uptime < 10.0:
                    print(f"[SHUTTER INIT] Stato iniziale serranda stabilizzato: {new_state} ({voltage_val:.2f}V, uptime {uptime:.1f}s). Nessuna notifica.", flush=True)
                    return

                print(f"[SERRANDA NOTIF] Cambio stato: {old_state} -> {new_state} ({voltage_val:.2f}V)", flush=True)
                # Il messaggio deve essere "APERTA" quando la serranda viene aperta e "CHIUSA" quando viene chiusa
                self.serranda_bot.broadcast(new_state)

    def _process_voltmeter(self, val):
        with self.lock:
            self.shutter_voltage = val
            candidate = "APERTA" if val > self.threshold else "CHIUSA"

            # Se la lettura indica un cambio di stato rispetto allo stato stabilizzato attuale
            if candidate != self.stable_shutter_state:
                if candidate != self.pending_shutter_state:
                    if self.shutter_timer:
                        self.shutter_timer.cancel()
                    self.pending_shutter_state = candidate
                    self.shutter_timer = threading.Timer(
                        self.debounce_delay,
                        self._on_shutter_stabilized,
                        args=[candidate, val]
                    )
                    self.shutter_timer.daemon = True
                    self.shutter_timer.start()
            else:
                if self.pending_shutter_state and self.shutter_timer:
                    self.shutter_timer.cancel()
                    self.shutter_timer = None
                    self.pending_shutter_state = None

    def _process_alarm_input(self, state_bool):
        with self.lock:
            new_state = "INSERITO" if state_bool else "DISINSERITO"
            uptime = time.time() - self.start_time

            if self.alarm_state is None or uptime < 10.0:
                self.alarm_state = new_state
                print(f"[ALARM INIT] Stato iniziale allarme garage all'avvio: {new_state} (uptime {uptime:.1f}s). Nessuna notifica.", flush=True)
                return

            if self.alarm_state != new_state:
                old_state = self.alarm_state
                self.alarm_state = new_state
                print(f"[ALARM NOTIF] Cambio stato allarme garage: {old_state} -> {new_state}", flush=True)
                # Il messaggio deve essere "INSERITO" quando viene inserito e "DISINSERITO" quando viene disinserito
                self.allarme_bot.broadcast(new_state)

    def _parse_message(self, topic, payload):
        topic_lower = topic.lower()

        # Parsing JSON se applicabile
        json_data = None
        try:
            val = json.loads(payload)
            if isinstance(val, dict):
                json_data = val
        except Exception:
            json_data = None

        res_dict = json_data.get("result") if json_data and isinstance(json_data.get("result"), dict) else (json_data if json_data else {})

        # --- 1. STATO ALLARME GARAGE (Input 0 su AllarmeIO) ---
        if "allarmeio" in topic_lower or "shellyplusuni" in topic_lower or "input0" in topic_lower or "input:0" in topic_lower or "input/0" in topic_lower:
            # Controllo in result / dict diretto
            if "input:0" in res_dict and isinstance(res_dict["input:0"], dict):
                st0 = res_dict["input:0"].get("state")
                if st0 is not None:
                    self._process_alarm_input(bool(st0))
            elif "input:0" in topic_lower or "input/0" in topic_lower:
                st0 = json_data.get("state") if json_data else (payload.strip().lower() in ["on", "true", "1"])
                if st0 is not None:
                    self._process_alarm_input(bool(st0))

            # Notifiche RPC NotifyStatus / NotifyEvent
            params = json_data.get("params", {}) if json_data else {}
            if isinstance(params, dict) and "input:0" in params and isinstance(params["input:0"], dict):
                if "state" in params["input:0"]:
                    self._process_alarm_input(bool(params["input:0"]["state"]))

            events = params.get("events", []) if isinstance(params, dict) else []
            if isinstance(events, list):
                for ev in events:
                    if isinstance(ev, dict) and ev.get("component") == "input:0":
                        if "state" in ev:
                            self._process_alarm_input(bool(ev["state"]))
                        ev_type = ev.get("event", "")
                        if ev_type in ["btn_down", "single_push"]:
                            self._process_alarm_input(True)
                        elif ev_type in ["btn_up"]:
                            self._process_alarm_input(False)

        # --- 2. STATO SERRANDA GARAGE (Voltmeter 100 su AllarmeIO) ---
        if "voltmeter:100" in res_dict and isinstance(res_dict["voltmeter:100"], dict):
            v = res_dict["voltmeter:100"].get("voltage")
            if v is not None:
                try:
                    self._process_voltmeter(float(v))
                except ValueError:
                    pass
        elif "voltmeter" in topic_lower:
            v = json_data.get("voltage") if json_data and "voltage" in json_data else None
            if json_data and "voltmeter:100" in json_data and isinstance(json_data["voltmeter:100"], dict):
                v = json_data["voltmeter:100"].get("voltage")
            if v is not None:
                try:
                    self._process_voltmeter(float(v))
                except ValueError:
                    pass

        params = json_data.get("params", {}) if json_data else {}
        if isinstance(params, dict) and "voltmeter:100" in params and isinstance(params["voltmeter:100"], dict):
            v = params["voltmeter:100"].get("voltage")
            if v is not None:
                try:
                    self._process_voltmeter(float(v))
                except ValueError:
                    pass

    def request_shelly_status(self):
        """Interroga Shelly AllarmeIO per sincronizzazione immediata dello stato"""
        if not self.client or not self.connected:
            return
        try:
            req = json.dumps({"id": 1, "src": "AllarmeIO.shelly/rpc", "method": "Shelly.GetStatus"})
            self.client.publish("AllarmeIO.shelly/rpc", req)
        except Exception as e:
            print(f"[!] Errore richiesta status AllarmeIO: {e}", flush=True)

    def start(self):
        try:
            try:
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="garage_bots_monitor")
            except AttributeError:
                self.client = mqtt.Client(client_id="garage_bots_monitor")

            if MQTT_USERNAME and MQTT_PASSWORD:
                self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

            def on_connect(client, userdata, flags, rc, properties=None):
                is_ok = (rc == 0) or (getattr(rc, "value", None) == 0)
                if is_ok:
                    print(f"[+] Connesso al Broker MQTT locale su {MQTT_BROKER}:{MQTT_PORT}", flush=True)
                    self.connected = True
                    client.subscribe("AllarmeIO.shelly/#")
                    print("[+] Sottoscritto al topic 'AllarmeIO.shelly/#'", flush=True)
                    # Richiesta iniziale di stato
                    threading.Timer(1.0, self.request_shelly_status).start()
                else:
                    print(f"[!] Connessione MQTT fallita (rc={rc})", flush=True)

            def on_disconnect(client, userdata, rc, properties=None):
                self.connected = False
                print(f"[!] Disconnesso da Broker MQTT (rc={rc}). Riconnessione...", flush=True)

            def on_message(client, userdata, msg):
                try:
                    payload = msg.payload.decode("utf-8", errors="ignore")
                    self._parse_message(msg.topic, payload)
                except Exception as e:
                    print(f"[!] Errore parsing messaggio MQTT {msg.topic}: {e}", flush=True)

            self.client.on_connect = on_connect
            self.client.on_disconnect = on_disconnect
            self.client.on_message = on_message

            print(f"[...] Connessione MQTT a {MQTT_BROKER}:{MQTT_PORT}...", flush=True)
            self.client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
            self.client.loop_start()

            # Thread di sincronizzazione periodica ogni 60 secondi
            def sync_worker():
                while True:
                    time.sleep(60)
                    self.request_shelly_status()

            threading.Thread(target=sync_worker, daemon=True, name="GarageMQTTSyncThread").start()

        except Exception as e:
            print(f"[!] Impossibile avviare client MQTT Garage: {e}", flush=True)


# --- MAIN ENTRYPOINT ---
def main():
    print("=" * 65)
    print("       GARAGE TELEGRAM BOTS & NOTIFIER SERVICE (2FA)")
    print("=" * 65)

    user_manager = GarageUserManager()
    acsa_client = ACSAHomeUtilsClient()

    # Creazione istanze dei due bot Telegram
    allarme_bot = DedicatedTelegramBot(
        token=ALLARME_GARAGE_BOT_TOKEN,
        bot_type="allarme_garage",
        bot_label="AllarmeGarage",
        user_manager=user_manager,
        acsa_client=acsa_client
    )

    serranda_bot = DedicatedTelegramBot(
        token=SERRANDA_BOT_TOKEN,
        bot_type="serranda",
        bot_label="Serranda",
        user_manager=user_manager,
        acsa_client=acsa_client
    )

    # Inizializzazione monitor MQTT
    mqtt_monitor = GarageMQTTMonitor(user_manager, allarme_bot, serranda_bot)
    allarme_bot.get_status_callback = mqtt_monitor.get_alarm_state
    serranda_bot.get_status_callback = mqtt_monitor.get_shutter_state

    # Avvio MQTT
    mqtt_monitor.start()

    # Avvio polling dei due bot in thread separati
    if allarme_bot.is_configured():
        t_allarme = threading.Thread(target=allarme_bot.start_polling, daemon=True, name="AllarmeGarageBotThread")
        t_allarme.start()
    else:
        print("[!] Token bot AllarmeGarage non configurato in .env. Inserisci ALLARME_GARAGE_BOT_TOKEN per attivarlo.", flush=True)

    if serranda_bot.is_configured():
        t_serranda = threading.Thread(target=serranda_bot.start_polling, daemon=True, name="SerrandaBotThread")
        t_serranda.start()
    else:
        print("[!] Token bot Serranda non configurato in .env. Inserisci SERRANDA_BOT_TOKEN per attivarlo.", flush=True)

    print("[*] Servizio Garage Bot avviato e in esecuzione.", flush=True)
    print(f"[*] Admin Chat ID configurato: {ADMIN_CHAT_ID}", flush=True)

    # Mantieni vivo il processo principale
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        print("\n[*] Arresto servizio Garage Bot...", flush=True)


if __name__ == "__main__":
    main()
