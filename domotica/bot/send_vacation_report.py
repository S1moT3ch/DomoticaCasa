#!/usr/bin/env python3
"""
Script per inviare manualmente da shell SSH il resoconto dello stato globale della casa a Telegram.
Utilizzo da shell:
    python3 /www/domotica/bot/send_vacation_report.py
    oppure:
    send_vacation_report
"""

import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from datetime import datetime
import time
import config
from mqtt_client import MQTTDeviceTracker
from user_manager import UserManager
from telegram_bot import TelegramBot

def main():
    print("[+] Inizializzazione per invio manuale report domotica...")
    tracker = MQTTDeviceTracker()
    tracker.start()
    time.sleep(0.5)
    tracker.request_active_status()
    print("[+] Sincronizzazione stato dispositivi MQTT dal broker (2 sec)...")
    time.sleep(2.0)

    user_mgr = UserManager()
    bot = TelegramBot(tracker, user_mgr)

    now_str = datetime.now().strftime("%H:%M:%S (%d/%m/%Y)")
    is_vacation = tracker.is_vacation_mode()
    vac_tag = " 🌴 [MODALITÀ VACANZA]" if is_vacation else ""
    summary = tracker.get_summary_report(include_network_and_anomalies=True)
    
    msg = (
        f"🧪 *RESOCONTO STATO CASA MANUALE*{vac_tag} (ore {now_str})\n"
        f"--------------------------------------------------\n\n"
        f"{summary}"
    )

    recipients = user_mgr.get_subscribed_users("vacation_report")
    print(f"[+] Destinatari iscritti al report giornaliero (vacation_report): {recipients}")
    
    success_count = 0
    for cid in recipients:
        if bot.send_message(cid, msg):
            success_count += 1
            print(f"  -> Report inviato con successo a Chat ID: {cid}")
            if hasattr(tracker, "event_logger") and tracker.event_logger:
                chart_path = tracker.event_logger.generate_climate_chart(hours=24, tracker=tracker)
                if chart_path and os.path.exists(chart_path):
                    bot.send_photo(cid, chart_path)
                    print(f"  -> Allegata foto grafico clima (BTicino) a Chat ID: {cid}")
        else:
            print(f"  -> ERRORE invio a Chat ID: {cid}")

    print(f"[+] Operazione completata. Report inviato con successo a {success_count} utenti ({recipients}).")

if __name__ == "__main__":
    main()
