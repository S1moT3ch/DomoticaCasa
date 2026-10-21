import time
import sys
import threading
import requests
import config
from user_manager import UserManager
from mqtt_client import MQTTDeviceTracker
from telegram_bot import TelegramBot
from admin_bot import AdminBot

def main():
    print("=" * 65)
    print("      MQTT DEVICE MONITOR & TELEGRAM BOT 2FA (~/www/domotica/bot)")
    print("=" * 65)

    # Controllo configurazione .env
    if not config.check_config():
        print("[!] Configurazione incompleta. Modifica ~/www/domotica/bot/.env e riavvia.")
        sys.exit(1)

    # 1. Inizializzazione gestore utenti autorizzati
    user_manager = UserManager()

    # 2. Inizializzazione client MQTT
    tracker = MQTTDeviceTracker()
    tracker.start()

    # 2b. Inizializzazione gestore presenza dispositivi (OpenWRT 192.168.1.1)
    from core.presence import PresenceManager
    presence_manager = PresenceManager(
        router_ip=getattr(config, "OPENWRT_ROUTER_IP", "192.168.1.1"),
        ssh_key=getattr(config, "OPENWRT_SSH_KEY", "/root/.ssh/id_openwrt"),
        poll_interval=15
    )
    presence_manager.start()
    tracker.presence_manager = presence_manager

    # Attesa iniziale per la sincronizzazione dei primi topic MQTT
    time.sleep(2)

    # 3. Inizializzazione Bot Admin per approvazione 2FA
    admin_bot = AdminBot(user_manager)

    # 4. Inizializzazione Bot Main Domotica
    main_bot = TelegramBot(tracker, user_manager, admin_bot, presence_manager=presence_manager)
    admin_bot.set_main_bot(main_bot)

    # Collegamento notifiche push MQTT a tutti gli utenti autorizzati
    tracker.set_notification_callback(main_bot.notify_chat)

    # Sincronizzazione ISTANTANEA verso Vercel ad ogni modifica di utenti/preferenze
    def _trigger_instant_vercel_sync():
        try:
            remote_url = getattr(config, "REMOTE_SERVER_URL", "https://powermonitor-one.vercel.app/api/heartbeat")
            secret = getattr(config, "HEARTBEAT_SECRET", "SecretHeartbeatKey2026")
            subscribers = user_manager.get_subscribed_users("power")
            payload = {
                "secret": secret,
                "timestamp": time.time(),
                "subscribers": subscribers,
                "event": "user_sync_immediate"
            }
            headers = {"x-heartbeat-secret": secret, "Content-Type": "application/json"}
            import requests
            requests.post(remote_url, json=payload, headers=headers, timeout=5)
            print(f"[+] Sincronizzazione ISTANTANEA iscritti blackout verso Vercel: {subscribers}")
        except Exception as e:
            print(f"[!] Errore sincronizzazione istantanea Vercel: {e}")

    user_manager.set_user_change_callback(_trigger_instant_vercel_sync)

    # 5. Avvio del thread per il polling del Bot Admin 2FA (se configurato)
    if admin_bot.is_configured():
        t_admin = threading.Thread(target=admin_bot.start_polling, daemon=True)
        t_admin.start()
        print(f"[+] Bot Admin 2FA in ascolto su Chat ID Admin: {config.ADMIN_CHAT_ID}")
    else:
        print("[!] Bot Admin 2FA non configurato in .env (ADMIN_BOT_TOKEN vuoto).")

    # 6. Avvio unico del thread in background per Heartbeat Blackout Vercel (con sync iscritti e telemetria Alexa)
    if getattr(config, "REMOTE_SERVER_URL", None):
        _start_heartbeat_thread(user_manager, tracker)

    # 6b. Avvio del thread in background per Monitor Chiamate Perse VoIP ZTE
    if getattr(config, "VOIP_MISSED_CALL_NOTIFY", True):
        _start_voip_monitor_thread(main_bot, tracker)

    # 6c. Avvio del thread in background per Notifiche Real-Time Chiamate In Arrivo (Squilli) VoIP ZTE
    if getattr(config, "VOIP_INCOMING_CALL_NOTIFY", True):
        _start_voip_incoming_thread(main_bot, tracker)

    # 7. Invio messaggio di sistema operativo all'avvio (esclusivamente sul Bot Utilities al superuser)
    def _notify_startup():
        time.sleep(3.5)
        start_msg = (
            "🤖 *Home Server & Bot Domotico ONLINE*\n\n"
            "✅ Il server e tutti i servizi domotici sono operativi e connessi alla rete."
        )
        try:
            target_chat_id = getattr(config, "ADMIN_CHAT_ID", None)
            if not target_chat_id:
                auth_users = user_manager.get_authorized_list()
                for cid, u in auth_users.items():
                    if u.get("name", "").lower() == "cantore" or u.get("username", "").lower() in ["cantore", "s1mot3ch"]:
                        target_chat_id = cid
                        break
                if not target_chat_id:
                    target_chat_id = "283084390"

            if admin_bot and admin_bot.is_configured():
                admin_bot.send_message(target_chat_id, start_msg)
                print(f"[+] Notifica di avvio inviata con successo su Bot Utilities a @Cantore ({target_chat_id})")
            else:
                main_bot.send_message(target_chat_id, start_msg)
                print(f"[+] Notifica di avvio inviata su Bot Main a @Cantore ({target_chat_id})")
        except Exception as e:
            print(f"[!] Errore invio notifica di avvio: {e}")

    threading.Thread(target=_notify_startup, daemon=True, name="StartupNotifyThread").start()

    # 8. Avvio polling Bot Main (thread principale)
    main_bot.start_polling()


def _start_heartbeat_thread(user_manager=None, tracker=None):
    """Thread in background per l'invio dell'heartbeat Vercel con la lista dinamica degli iscritti e telemetria per Alexa"""
    remote_url = getattr(config, "REMOTE_SERVER_URL", "https://powermonitor-one.vercel.app/api/heartbeat")
    secret = getattr(config, "HEARTBEAT_SECRET", "SecretHeartbeatKey2026")
    interval = int(getattr(config, "PING_INTERVAL_SECONDS", 25))

    def _loop():
        import requests
        session = requests.Session()
        headers = {"x-heartbeat-secret": secret, "Content-Type": "application/json"}
        print(f"[+] Heartbeat Thread integrato avviato verso {remote_url} (intervallo {interval}s)")

        while True:
            subscribers = []
            if user_manager:
                try:
                    subscribers = user_manager.get_subscribed_users("power")
                except Exception as e:
                    print(f"[!] Errore recupero iscritti blackout: {e}")

            payload = {
                "secret": secret,
                "timestamp": time.time(),
                "subscribers": subscribers,
                "node": "server"
            }

            if tracker and getattr(tracker, "latest_home_power", None) is not None:
                payload["power_w"] = float(tracker.latest_home_power)
                payload["voltage"] = float(tracker.latest_home_voltage)
                payload["current"] = float(tracker.latest_home_current)
                payload["power_timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
            else:
                try:
                    from power_heartbeat import get_latest_power
                    p_info = get_latest_power()
                    if p_info:
                        payload.update(p_info)
                except Exception:
                    pass


            for attempt in range(1, 4):
                try:
                    resp = session.post(remote_url, json=payload, headers=headers, timeout=8)
                    if resp.status_code == 200:
                        try:
                            res_data = resp.json()
                            recovered = res_data.get("recovered_events", [])
                            if recovered:
                                from datetime import datetime
                                for ev in recovered:
                                    ev_type = str(ev.get("type", "unknown")).lower()
                                    ev_desc = ev.get("description", ev_type)
                                    dur = ev.get("duration_seconds", 0)
                                    started_ts = ev.get("started_at")
                                    ended_ts = ev.get("ended_at")

                                    # Calcola orari leggibili per SQLite
                                    if started_ts:
                                        try:
                                            s_str = datetime.fromtimestamp(float(started_ts) / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
                                        except Exception:
                                            s_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    else:
                                        s_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                                    if ended_ts:
                                        try:
                                            e_str = datetime.fromtimestamp(float(ended_ts) / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
                                        except Exception:
                                            e_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    else:
                                        e_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                                    if "blackout" in ev_type:
                                        dev_name = "Rete Elettrica"
                                        down_det = "Assenza corrente / Blackout"
                                        up_det = f"Ripristino corrente (durata {dur}s)"
                                    elif "server" in ev_type:
                                        dev_name = "Home Server"
                                        down_det = "Down Home Server"
                                        up_det = f"Ripristino Home Server (durata {dur}s)"
                                    elif "firewall" in ev_type:
                                        dev_name = "Firewall"
                                        down_det = "Down Firewall"
                                        up_det = f"Ripristino Firewall (durata {dur}s)"
                                    else:
                                        dev_name = "Nodo Domotico"
                                        down_det = f"Down {ev_desc}"
                                        up_det = f"Ripristino {ev_desc} (durata {dur}s)"

                                    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [RECOVERY] {dev_name}: {down_det} [{s_str}] -> {up_det} [{e_str}]")

                                    if tracker and hasattr(tracker, "event_logger") and tracker.event_logger:
                                        # 1. Registra evento DOWN al timestamp di inizio
                                        tracker.event_logger.log_event(
                                            category="power",
                                            device=dev_name,
                                            details=down_det,
                                            timestamp=s_str
                                        )
                                        # 2. Registra evento RIPRISTINO al timestamp di fine
                                        tracker.event_logger.log_event(
                                            category="power",
                                            device=dev_name,
                                            details=up_det,
                                            timestamp=e_str
                                        )
                        except Exception as parse_e:
                            pass
                        break
                    elif resp.status_code == 429 or "quota_exceeded" in resp.text:
                        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [!] Upstash Redis quota mensile superata (500k). Thread heartbeat in pausa (controllo ogni 5 min fino al reset).")
                        time.sleep(300)
                        break
                    else:
                        print(f"[!] Heartbeat Vercel risponde HTTP {resp.status_code} (tentativo {attempt}/3)")
                except Exception as e:
                    if attempt < 3:
                        time.sleep(2)
                    else:
                        print(f"[!] Errore invio heartbeat verso {remote_url}: {e}")

            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="PowerHeartbeatThread")
    t.start()


def _start_voip_monitor_thread(main_bot=None, tracker=None):
    """Thread in background per il monitoraggio e notifica tempestiva delle chiamate perse sul telefono fisso (modem ZTE)"""
    interval = int(getattr(config, "VOIP_POLL_INTERVAL_SECONDS", 30))

    def _loop():
        print(f"[+] VoIP Missed Call Monitor avviato (intervallo polling: {interval}s)")
        time.sleep(10)

        while True:
            try:
                if tracker and hasattr(tracker, "zte_client") and tracker.zte_client:
                    # Controlla solo se la password è configurata
                    if getattr(config, "ZTE_ROUTER_PASSWORD", ""):
                        new_missed = tracker.zte_client.check_new_missed_calls()
                        for call in new_missed:
                            num = call.get("number", "Sconosciuto")
                            dt = call.get("date", "")
                            port = call.get("phone_port", "phone1")

                            try:
                                from datetime import datetime
                                dt_obj = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
                                dt_formatted = dt_obj.strftime("%d/%m/%Y alle %H:%M:%S")
                            except Exception:
                                dt_formatted = dt

                            # Risoluzione contatto da rubrica locale
                            from core.phonebook import PhonebookManager
                            import html
                            contact_name = None
                            if tracker and hasattr(tracker, "phonebook") and tracker.phonebook:
                                contact_name = tracker.phonebook.lookup(num)

                            formatted_num = PhonebookManager.format_phone(num)
                            who_display = html.escape(contact_name) if contact_name else "Numero Sconosciuto"

                            msg = (
                                f"🔴 <b>{who_display}</b>\n"
                                f"📱 <code>{formatted_num}</code>\n"
                                "━━━━━━━━━━━━━━━━━━━━\n"
                                "📞 <b>Chiamata Persa sul Fisso</b>\n"
                                f"🕒 <b>Data e Ora:</b> {dt_formatted}\n\n"
                                "💡 <i>Digita /chiamate per il registro completo.</i>"
                            )
                            if main_bot:
                                main_bot.notify_chat(msg, category="voip_missed_calls")
                                print(f"[VoIP] Notifica chiamata persa inviata per {who_display} ({formatted_num})")
            except Exception as e:
                print(f"[!] Errore nel loop VoIP Missed Call Monitor: {e}")

            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="VoIPMissedCallThread")
    t.start()


def _start_voip_incoming_thread(main_bot=None, tracker=None):
    """Thread demone per assicurare la configurazione Syslog su ZTE e ascoltare gli eventi di squillo in real time"""
    if not getattr(config, "ZTE_ROUTER_PASSWORD", ""):
        return

    from core.zte_syslog_listener import ZTESyslogListener

    target_ip = getattr(config, "ZTE_SYSLOG_TARGET_IP", "192.168.0.2")
    port = int(getattr(config, "VOIP_SYSLOG_PORT", 514))

    def _on_ring(caller_num, raw_msg):
        def _process():
            active_info = None
            if tracker and hasattr(tracker, "zte_client") and tracker.zte_client:
                for attempt in range(8):
                    active_info = tracker.zte_client.get_active_call_info(max_age_seconds=15)
                    if active_info and active_info.get("peer_number"):
                        break
                    time.sleep(0.4)

            final_caller = None
            direction = "incoming"

            if active_info and active_info.get("peer_number"):
                final_caller = active_info.get("peer_number")
                direction = active_info.get("direction", "incoming")
            elif caller_num and caller_num != "Sconosciuto":
                final_caller = caller_num
                direction = "incoming"

            if not final_caller:
                print("[VoIP Real-Time] Evento RTP rilevato ma nessun numero attivo identificato.")
                return
            
            # Risoluzione contatto da rubrica locale
            from core.phonebook import PhonebookManager
            import html
            contact_name = None
            if tracker and hasattr(tracker, "phonebook") and tracker.phonebook:
                contact_name = tracker.phonebook.lookup(final_caller)

            now_str = time.strftime("%H:%M:%S")
            formatted_num = PhonebookManager.format_phone(final_caller)

            if direction == "outgoing":
                # --- CASISTICA 1: CHIAMATA IN USCITA DALLA RETE DOMESTICA ---
                who_display = html.escape(contact_name) if contact_name else "Destinatario Sconosciuto"
                msg = (
                    f"📤 <b>{who_display}</b>\n"
                    f"📱 <code>{formatted_num}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📞 <b>Chiamata in Uscita dal Fisso</b>\n"
                    f"🕒 <b>Orario:</b> {now_str}\n\n"
                    "⚡ <i>Qualcuno sta chiamando da casa verso l'esterno...</i>"
                )
                if main_bot:
                    main_bot.notify_chat(msg, category="voip_outgoing_calls")
                    print(f"[VoIP Real-Time] Notifica chiamata in uscita inviata: {who_display} ({formatted_num})")
            else:
                # --- CASISTICA 2: CHIAMATA IN ARRIVO DALL'ESTERNO (SQUILLO) ---
                who_display = html.escape(contact_name) if contact_name else "Numero Sconosciuto"
                msg = (
                    f"🔔 <b>{who_display}</b>\n"
                    f"📱 <code>{formatted_num}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📞 <b>Telefono Fisso in Squillo!</b>\n"
                    f"🕒 <b>Orario:</b> {now_str}\n\n"
                    "⚡ <i>Il telefono sta squillando adesso...</i>"
                )
                if main_bot:
                    main_bot.notify_chat(msg, category="voip_incoming_calls")
                    print(f"[VoIP Real-Time] Notifica squillo in arrivo inviata: {who_display} ({formatted_num})")

        threading.Thread(target=_process, daemon=True, name="VoIPRingWorker").start()



    # 1. Avvia il listener UDP locale sulla porta 514
    listener = ZTESyslogListener(port=port, on_incoming_call=_on_ring)
    listener.start()

    # 2. In un loop periodico, assicura che il router ZTE mantenga attivo il Syslog Voice verso target_ip
    def _keepalive_zte_syslog():
        time.sleep(15)
        while True:
            try:
                if tracker and hasattr(tracker, "zte_client") and tracker.zte_client:
                    tracker.zte_client.ensure_syslog_voice_enabled(target_ip=target_ip, port=port)
            except Exception as e:
                print(f"[!] Errore keepalive Syslog ZTE: {e}")
            time.sleep(600)

    t_cfg = threading.Thread(target=_keepalive_zte_syslog, daemon=True, name="ZTESyslogKeepaliveThread")
    t_cfg.start()


if __name__ == "__main__":
    main()
