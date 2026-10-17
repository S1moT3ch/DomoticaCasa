import os
from dotenv import load_dotenv

# Carica le variabili dal file .env presente nella stessa directory (~/www/domotica/bot/.env)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_file = os.path.join(BASE_DIR, ".env")
load_dotenv(env_file)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost").strip()
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "").strip() or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "").strip() or None
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "#").strip()

# Tempo di stabilizzazione (in secondi) prima di inviare notifiche per la serranda del garage
SHUTTER_DEBOUNCE_DELAY = float(os.getenv("SHUTTER_DEBOUNCE_DELAY", "4.0"))

# Ritardo (in secondi) prima di notificare l'apertura del cancello (impulso su garage/stato/2)
GATE_OPEN_NOTIFY_DELAY = float(os.getenv("GATE_OPEN_NOTIFY_DELAY", "5.0"))

# Soglia umidità percentuale (%) oltre la quale inviare notifica push
HUMIDITY_ALERT_THRESHOLD = float(os.getenv("HUMIDITY_ALERT_THRESHOLD", "60.0"))

# Configurazione API AdGuardHome locale
ADGUARD_URL = os.getenv("ADGUARD_URL", "http://127.0.0.1:8082").strip()
ADGUARD_USER = os.getenv("ADGUARD_USER", "").strip()
ADGUARD_PASSWORD = os.getenv("ADGUARD_PASSWORD", "").strip()

import hashlib
import hmac
import secrets

# Salt & Hash Password per sicurezza crittografica 
BOT_PASSWORD_SALT = os.getenv("BOT_PASSWORD_SALT", "a8f3b9e1c42d7051").strip()
BOT_PASSWORD_HASH = os.getenv("BOT_PASSWORD_HASH", "8b794ed6b455aca947f5268dc9b83896e63393e9956c9a9e5a603e0a97357a70").strip()

def hash_password(password, salt=None):
    """Calcola l'hash SHA-256 della password con salt"""
    if not salt:
        salt = secrets.token_hex(16)
    salted = f"{salt}:{password}".encode('utf-8')
    pwd_hash = hashlib.sha256(salted).hexdigest()
    return pwd_hash, salt

def verify_password(input_password):
    """Verifica la password fornita in tempo costante rispetto all'hash salvato (anti-timing attack)"""
    if not input_password:
        return False
    computed_hash, _ = hash_password(input_password, BOT_PASSWORD_SALT)
    return hmac.compare_digest(computed_hash, BOT_PASSWORD_HASH)

# Fallback password di default
BOT_PASSWORD = os.getenv("BOT_PASSWORD", "DomoticaPass!2026").strip()
ACTION_PIN = os.getenv("ACTION_PIN", BOT_PASSWORD).strip()



# Secondo Bot Telegram (Admin Bot per 2FA)
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", TELEGRAM_CHAT_ID).strip()

# Orario ed utente destinatario del report mattutino giornaliero in Modalità Vacanza
VACATION_REPORT_TIME = os.getenv("VACATION_REPORT_TIME", "09:00").strip()
VACATION_REPORT_CHAT_ID = os.getenv("VACATION_REPORT_CHAT_ID", TELEGRAM_CHAT_ID).strip()

# Chiamata vocale Telegram / WhatsApp per intrusione serranda garage in vacanza
VACATION_ALARM_CALL_ENABLED = os.getenv("VACATION_ALARM_CALL_ENABLED", "True").strip().lower() in ["true", "1", "yes"]
VACATION_ALARM_CALL_TARGET = os.getenv("VACATION_ALARM_CALL_TARGET", VACATION_REPORT_CHAT_ID).strip()
CALLMEBOT_API_KEY = os.getenv("CALLMEBOT_API_KEY", "").strip()
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY", CALLMEBOT_API_KEY).strip()
WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE", "").strip()

# File di salvataggio persistente degli utenti autorizzati e dello stato vacanza
AUTHORIZED_USERS_FILE = os.path.join(BASE_DIR, "authorized_users.json")
VACATION_MODE_FILE = os.path.join(BASE_DIR, "vacation_mode.json")




def check_config():
    """Verifica che i parametri principali siano stati compilati nel file .env"""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "INSERISCI_QUI_IL_TUO_TOKEN_TELEGRAM":
        print("[!] ATTENZIONE: Il Token del bot Telegram non è ancora stato inserito!")
        print(f"    Si prega di aprire il file: {env_file}")
        print("    E sostituire 'INSERISCI_QUI_IL_TUO_TOKEN_TELEGRAM' con il token reale fornito da @BotFather.\n")
        return False
    return True

# SinricPro & Samsung TV Integration
SINRIC_APP_KEY = os.getenv("SINRIC_APP_KEY", "").strip()
SINRIC_APP_SECRET = os.getenv("SINRIC_APP_SECRET", "").strip()
SINRIC_TV_CUCINA_ID = os.getenv("SINRIC_TV_CUCINA_ID", "").strip()
SINRIC_TV_SOGGIORNO_ID = os.getenv("SINRIC_TV_SOGGIORNO_ID", "").strip()

TV_CUCINA_IP = os.getenv("TV_CUCINA_IP", "192.168.1.130").strip()
TV_SOGGIORNO_IP = os.getenv("TV_SOGGIORNO_IP", "192.168.1.124").strip()
TV_CUCINA_MAC = os.getenv("TV_CUCINA_MAC", "").strip()
TV_SOGGIORNO_MAC = os.getenv("TV_SOGGIORNO_MAC", "").strip()

# Outage Prevention & Allerta Sovraccarico Alexa
OUTAGE_PREVENTION_ENABLED = os.getenv("OUTAGE_PREVENTION_ENABLED", "True").strip().lower() in ["true", "1", "yes"]
POWER_ALERT_THRESHOLD_KW = float(os.getenv("POWER_ALERT_THRESHOLD_KW", "2.9"))
POWER_ALERT_HYSTERESIS_KW = float(os.getenv("POWER_ALERT_HYSTERESIS_KW", "0.4"))
POWER_ALERT_COOLDOWN_SECONDS = int(os.getenv("POWER_ALERT_COOLDOWN_SECONDS", "180"))
ALEXA_TARGET_DEVICE = os.getenv("ALEXA_TARGET_DEVICE", "EchoPop_Cucina").strip()

# Provider Vocale Alexa (Voice Monkey / Sinric Pro)
ALEXA_ALERT_PROVIDER = os.getenv("ALEXA_ALERT_PROVIDER", "voicemonkey").strip().lower()
VOICEMONKEY_TOKEN = os.getenv("VOICEMONKEY_TOKEN", "").strip()
SINRIC_POWER_DEVICE_ID = os.getenv("SINRIC_POWER_DEVICE_ID", "").strip()

# Server Remoto Vercel per Heartbeat & Rilevamento Blackout
REMOTE_SERVER_URL = os.getenv("REMOTE_SERVER_URL", "https://powermonitor-one.vercel.app/api/heartbeat").strip()
HEARTBEAT_SECRET = os.getenv("HEARTBEAT_SECRET", "SecretHeartbeatKey2026").strip()
PING_INTERVAL_SECONDS = int(os.getenv("PING_INTERVAL_SECONDS", "60"))

# Monitor VoIP Telefono Fisso (Modem-Router ZTE Wind3 HUB)
ZTE_ROUTER_IP = os.getenv("ZTE_ROUTER_IP", "192.168.0.1").strip()
ZTE_ROUTER_USER = os.getenv("ZTE_ROUTER_USER", "admin").strip()
ZTE_ROUTER_PASSWORD = os.getenv("ZTE_ROUTER_PASSWORD", "").strip()
VOIP_MISSED_CALL_NOTIFY = os.getenv("VOIP_MISSED_CALL_NOTIFY", "True").strip().lower() in ["true", "1", "yes"]
VOIP_POLL_INTERVAL_SECONDS = int(os.getenv("VOIP_POLL_INTERVAL_SECONDS", "30"))
VOIP_INCOMING_CALL_NOTIFY = os.getenv("VOIP_INCOMING_CALL_NOTIFY", "True").strip().lower() in ["true", "1", "yes"]
VOIP_SYSLOG_PORT = int(os.getenv("VOIP_SYSLOG_PORT", "514"))
ZTE_SYSLOG_TARGET_IP = os.getenv("ZTE_SYSLOG_TARGET_IP", "192.168.0.2").strip()
# Rilevamento Presenza Dispositivi di Casa (Router OpenWRT)
OPENWRT_ROUTER_IP = os.getenv("OPENWRT_ROUTER_IP", "192.168.1.1").strip()
OPENWRT_SSH_KEY = os.getenv("OPENWRT_SSH_KEY", "/root/.ssh/id_rsa").strip()
VOIP_RING_FILTER_PRESENCE = os.getenv("VOIP_RING_FILTER_PRESENCE", "True").strip().lower() in ["true", "1", "yes"]
