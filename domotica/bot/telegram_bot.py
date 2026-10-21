import os
import re
import html
import json
import time
import threading
import uuid
from datetime import datetime
import requests
import config
from user_manager import NOTIFICATION_CATEGORIES
from core.printer_manager import PrinterManager

class TelegramBot:

    def __init__(self, tracker, user_manager, admin_bot=None, presence_manager=None):
        self.tracker = tracker
        self.user_manager = user_manager
        self.admin_bot = admin_bot
        self.presence_manager = presence_manager or getattr(tracker, "presence_manager", None)
        self.printer_mgr = PrinterManager()
        self.api_url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        self.last_update_id = 0
        self.pending_actions = {}  # { chat_id: {"device": ..., "action": ..., "name": ..., "expires": timestamp} }
        self.pending_prints = {}   # { print_id: {"file_id": ..., "file_name": ..., "file_size": ..., "chat_id": ..., "time": ...} }
        self.pending_page_inputs = {}  # { chat_id: {"print_id": print_id, "msg_id": msg_id, "time": timestamp} }
        self.pending_copies_inputs = {}  # { chat_id: {"print_id": print_id, "msg_id": msg_id, "time": timestamp} }
        self.pending_resumes = {}  # { resume_id: {...} }

    def set_admin_bot(self, admin_bot):
        self.admin_bot = admin_bot

    def set_presence_manager(self, presence_manager):
        self.presence_manager = presence_manager

    def _resolve_category_and_text(self, text_or_key, cat_or_text, is_on=None, extra_cat=None):
        """Standardizza gli argomenti delle notifiche provenienti da mqtt_client"""
        category = extra_cat or cat_or_text or "system"
        text = text_or_key

        if text_or_key in NOTIFICATION_CATEGORIES:
            category = text_or_key
            text = cat_or_text
        elif cat_or_text in NOTIFICATION_CATEGORIES:
            category = cat_or_text
            text = text_or_key

        return text, category

    def notify_chat(self, text_or_key, cat_or_text=None, reply_markup=None, is_on=None, category=None):
        """Invia una notifica push a tutti gli utenti autorizzati ed iscritti alla specifica categoria"""
        def _task():
            text, final_category = self._resolve_category_and_text(text_or_key, cat_or_text, is_on, category)
            subscribers = self.user_manager.get_subscribed_users(final_category)
            print(f"[BROADCAST] Notifica (categoria '{final_category}') a {len(subscribers)} utenti: {subscribers}")
            for cid in subscribers:
                # Se è una chiamata VoIP (squillo in entrata o chiamata in uscita) e il filtro presenza è attivo,
                # invia la notifica solo se il dispositivo dell'utente è connesso alla rete di casa (OpenWRT)
                if final_category in ["voip_incoming_calls", "voip_outgoing_calls"] and getattr(config, "VOIP_RING_FILTER_PRESENCE", True):
                    pm = getattr(self, "presence_manager", None) or (self.tracker and getattr(self.tracker, "presence_manager", None))
                    if pm:
                        user_devs = self.user_manager.get_user_devices(cid)
                        if user_devs:
                            is_home = pm.is_user_home(user_devs)
                            u_name = self.user_manager.authorized_users.get(str(cid), {}).get("name", cid)
                            call_desc = "Squillo entrante" if final_category == "voip_incoming_calls" else "Chiamata in uscita"
                            if not is_home:
                                print(f"[VoIP Presenza] 🚫 {call_desc} NON inviato a {u_name} ({cid}): dispositivo non rilevato a casa ({user_devs})")
                                continue
                            else:
                                print(f"[VoIP Presenza] 🟢 Dispositivo di {u_name} ({cid}) rilevato a casa: invio {call_desc}...")

                self.send_message(cid, text, reply_markup=reply_markup)
                
        threading.Thread(target=_task, daemon=True).start()



    def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "INSERISCI_QUI_IL_TUO_TOKEN_TELEGRAM":
            print("[!] Impossibile inviare il messaggio: TOKEN Telegram non impostato in .env")
            return False

        if parse_mode is None:
            # Auto-detect formato HTML se contiene tag comuni
            if any(t in text for t in ["<b>", "</b>", "<code>", "</code>", "<i>", "</i>", "<pre>", "</pre>"]):
                parse_mode = "HTML"
            else:
                parse_mode = "Markdown"

        url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if not resp.ok:
                print(f"[Telegram] Invio fallito con parse_mode={parse_mode}: {resp.text}")
                # Fallback pulito a testo semplice: rimuove i tag HTML per non mostrare testo sporco
                fallback_text = re.sub(r'<[^>]+>', '', text) if parse_mode == "HTML" else text
                payload["text"] = fallback_text
                payload.pop("parse_mode", None)
                requests.post(url, json=payload, timeout=10)
            return True
        except Exception as e:
            print(f"[!] Errore nell'invio del messaggio su Telegram: {e}")
            return False

    def send_message_get_id(self, chat_id, text, parse_mode=None, reply_markup=None):
        """Invia un messaggio di testo e restituisce il message_id generato da Telegram"""
        if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "INSERISCI_QUI_IL_TUO_TOKEN_TELEGRAM":
            return None
        if parse_mode is None:
            if any(t in text for t in ["<b>", "</b>", "<code>", "</i>", "<pre>"]):
                parse_mode = "HTML"
            else:
                parse_mode = "Markdown"
        url = f"{self.api_url}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if not resp.ok:
                payload.pop("parse_mode", None)
                resp = requests.post(url, json=payload, timeout=10)
            if resp.ok:
                return resp.json().get("result", {}).get("message_id")
        except Exception:
            pass
        return None

    def send_photo(self, chat_id, photo_source, caption=None, parse_mode="Markdown", reply_markup=None):
        """Invia un'immagine (foto da path o bytes) ad un utente Telegram"""
        if not photo_source:
            return False

        url = f"{self.api_url}/sendPhoto"
        try:
            import io
            if isinstance(photo_source, bytes):
                files = {"photo": ("preview.jpg", io.BytesIO(photo_source), "image/jpeg")}
            elif hasattr(photo_source, "read"):
                files = {"photo": ("preview.jpg", photo_source, "image/jpeg")}
            elif isinstance(photo_source, str) and os.path.exists(photo_source):
                files = {"photo": open(photo_source, "rb")}
            else:
                print(f"[!] Origine immagine non valida o file non trovato: {photo_source}")
                return False

            data = {"chat_id": str(chat_id)}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = parse_mode
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)

            resp = requests.post(url, data=data, files=files, timeout=20)
            if not resp.ok and parse_mode:
                data.pop("parse_mode", None)
                if isinstance(photo_source, bytes):
                    files = {"photo": ("preview.jpg", io.BytesIO(photo_source), "image/jpeg")}
                elif isinstance(photo_source, str) and os.path.exists(photo_source):
                    files = {"photo": open(photo_source, "rb")}
                resp = requests.post(url, data=data, files=files, timeout=20)
            return resp.ok
        except Exception as e:
            print(f"[!] Errore nell'invio della foto su Telegram: {e}")
            return False

    def _edit_message_media(self, chat_id, message_id, photo_source, caption=None, parse_mode="Markdown", reply_markup=None):
        """Modifica un media (foto da path o bytes) in un messaggio esistente"""
        if not photo_source:
            return False

        url = f"{self.api_url}/editMessageMedia"
        try:
            import io
            if isinstance(photo_source, bytes):
                files = {"photo": ("preview.jpg", io.BytesIO(photo_source), "image/jpeg")}
            elif hasattr(photo_source, "read"):
                files = {"photo": ("preview.jpg", photo_source, "image/jpeg")}
            elif isinstance(photo_source, str) and os.path.exists(photo_source):
                files = {"photo": open(photo_source, "rb")}
            else:
                print(f"[!] File o buffer foto non valido: {photo_source}")
                return False

            media_dict = {
                "type": "photo",
                "media": "attach://photo"
            }
            if caption:
                media_dict["caption"] = caption
                media_dict["parse_mode"] = parse_mode

            data = {
                "chat_id": str(chat_id),
                "message_id": str(message_id),
                "media": json.dumps(media_dict)
            }
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)

            resp = requests.post(url, data=data, files=files, timeout=20)
            if not resp.ok and parse_mode:
                media_dict.pop("parse_mode", None)
                data["media"] = json.dumps(media_dict)
                if isinstance(photo_source, bytes):
                    files = {"photo": ("preview.jpg", io.BytesIO(photo_source), "image/jpeg")}
                elif isinstance(photo_source, str) and os.path.exists(photo_source):
                    files = {"photo": open(photo_source, "rb")}
                resp = requests.post(url, data=data, files=files, timeout=20)
            return resp.ok
        except Exception as e:
            print(f"[!] Errore in _edit_message_media su Telegram: {e}")
            return False

    def _build_notifications_keyboard(self, chat_id):
        user_notifs = self.user_manager.get_user_notifications(chat_id)
        keyboard = []
        for cat_key, cat_label in NOTIFICATION_CATEGORIES.items():
            is_active = user_notifs.get(cat_key, True)
            icon = "✅" if is_active else "❌"
            btn_text = f"{icon} {cat_label}"
            callback_data = f"toggle_notif:{cat_key}"
            keyboard.append([{"text": btn_text, "callback_data": callback_data}])
        return {"inline_keyboard": keyboard}

    def _build_control_keyboard(self):
        keyboard = [
            [
                {"text": "💡 Accendi Cucina", "callback_data": "cmd_ask:LuceCucina:on:Luce Cucina (Accendi)"},
                {"text": "💡 Spegni Cucina", "callback_data": "cmd_ask:LuceCucina:off:Luce Cucina (Spegni)"}
            ],
            [
                {"text": "💡 Accendi Led Andrea", "callback_data": "cmd_ask:LedAndrea:on:Led Andrea (Accendi)"},
                {"text": "💡 Spegni Led Andrea", "callback_data": "cmd_ask:LedAndrea:off:Led Andrea (Spegni)"}
            ],
            [
                {"text": "💧 Accendi Ricircolo", "callback_data": "cmd_ask:Ricircolo:on:Ricircolo Acqua (Accendi)"},
                {"text": "💧 Spegni Ricircolo", "callback_data": "cmd_ask:Ricircolo:off:Ricircolo Acqua (Spegni)"}
            ],
            [
                {"text": "📹 Accendi Camera Soggiorno", "callback_data": "cmd_ask:TapoHome:on:Camera Soggiorno (Accendi)"},
                {"text": "📹 Spegni Camera Soggiorno", "callback_data": "cmd_ask:TapoHome:off:Camera Soggiorno (Spegni)"}
            ],
            [
                {"text": "📹 Accendi Camera Corridoio", "callback_data": "cmd_ask:AllarmeGarage:on:Camera Corridoio (Accendi)"},
                {"text": "📹 Spegni Camera Corridoio", "callback_data": "cmd_ask:AllarmeGarage:off:Camera Corridoio (Spegni)"}
            ],
            [
                {"text": "❄️ Daikin Raffrescamento", "callback_data": "cmd_ask:Daikin:cool:Clima Daikin (Cool)"},
                {"text": "💧 Daikin Deumidifica", "callback_data": "cmd_ask:Daikin:dry:Clima Daikin (Dry)"}
            ],
            [
                {"text": "🔴 Spegni Daikin", "callback_data": "cmd_ask:Daikin:off:Clima Daikin (Spegni)"}
            ],
            [
                {"text": "🚪 Apri Serranda Garage", "callback_data": "cmd_ask:Serranda:on:Serranda Garage (Apri)"},
                {"text": "🚗 Apri Cancello Garage", "callback_data": "cmd_ask:Cancello:on:Cancello Garage (Apri)"}
            ]
        ]
        return {"inline_keyboard": keyboard}

    def _build_logs_keyboard(self, current_category="all", current_page=0, total_pages=1, view_mode="table"):
        cats = [
            ("📄 Tutti", "all"),
            ("🛡️ Allarmi", "allarmi"),
            ("📹 Telecamere", "telecamere"),
            ("🚪 Varchi", "varchi"),
            ("💡 Luci", "luci"),
            ("💧 Ricircolo", "ricircolo"),
            ("⚡ Blackout", "power"),
            ("🤖 Comandi", "command")
        ]
        keyboard = []
        curr = (current_category or "all").lower().strip()
        v_mode = "list" if view_mode in ["list", "mobile", "elenco"] else "table"
        
        # Badge con pulsante di reset se è attiva una ricerca personalizzata
        preset_keys = [c[1] for c in cats]
        if curr not in preset_keys and curr not in ["all", "tutti", "tutto"]:
            keyboard.append([{"text": f"🔍 Filtro: \"{curr[:20]}\"  ✖ Azzera", "callback_data": f"filter_logs:all:{v_mode}"}])

        row = []
        for label, cat_key in cats:
            pref = "▶️ " if curr == cat_key else ""
            btn_text = f"{pref}{label}"
            row.append({"text": btn_text, "callback_data": f"filter_logs:{cat_key}:{v_mode}"})
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        # Pulsanti di Navigazione Paginata (Precedenti / Pagina / Successivi)
        nav_row = []
        safe_curr = curr[:25]
        if current_page > 0:
            nav_row.append({"text": "⬅️ Precedenti", "callback_data": f"logs_page:{safe_curr}:{current_page - 1}:{v_mode}"})
        else:
            nav_row.append({"text": "⏮️ Inizio", "callback_data": "nop"})

        nav_row.append({"text": f"📄 {current_page + 1}/{total_pages}", "callback_data": f"logs_page:{safe_curr}:{current_page}:{v_mode}"})

        if current_page < total_pages - 1:
            nav_row.append({"text": "➡️ Successivi", "callback_data": f"logs_page:{safe_curr}:{current_page + 1}:{v_mode}"})
        else:
            nav_row.append({"text": "⏭️ Fine", "callback_data": "nop"})

        keyboard.append(nav_row)

        # Pulsante Toggle Vista Tabella (Mobile-first) / Elenco Esteso
        target_mode = "list" if v_mode == "table" else "table"
        mode_btn_text = "📱 Passa a Vista Elenco" if v_mode == "table" else "📊 Passa a Vista Tabella"
        keyboard.append([{"text": mode_btn_text, "callback_data": f"logs_view:{safe_curr}:{current_page}:{target_mode}"}])

        return {"inline_keyboard": keyboard}










    def _answer_callback_query(self, callback_query_id, text=None):
        url = f"{self.api_url}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception:
            pass

    def _edit_message_reply_markup(self, chat_id, message_id, reply_markup):
        url = f"{self.api_url}/editMessageReplyMarkup"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup
        }
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"[!] Errore editMessageReplyMarkup: {e}")

    def _edit_message_text(self, chat_id, message_id, text, parse_mode="Markdown", reply_markup=None):
        if not message_id:
            return self.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
        url = f"{self.api_url}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if not resp.ok:
                # Se fallisce con parse_mode (es. caratteri speciali), tenta senza
                if parse_mode:
                    payload.pop("parse_mode", None)
                    resp2 = requests.post(url, json=payload, timeout=10)
                    if resp2.ok:
                        return True
                # Se fallisce perché il messaggio è un media (es. Foto dell'anteprima),
                # eliminiamo il media e inviamo un nuovo messaggio di testo per mostrare l'avanzamento!
                self._delete_message(chat_id, message_id)
                self.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
            return True
        except Exception as e:
            print(f"[!] Errore editMessageText: {e}")
            try:
                self._delete_message(chat_id, message_id)
                self.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
            except Exception:
                pass
            return False

    def _delete_message(self, chat_id, message_id):
        url = f"{self.api_url}/deleteMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "message_id": message_id}, timeout=5)
        except Exception:
            pass


    def _get_user_display_name(self, from_user, chat_id):
        """Restituisce il nome utente Telegram preferendo @username, poi il nome registrato o first_name"""
        cid_str = str(chat_id)
        tg_username = from_user.get("username") if from_user else ""
        
        # Se presente un username Telegram valido, aggiornalo anche in user_manager se mancava
        if tg_username:
            if hasattr(self, "user_manager") and cid_str in self.user_manager.authorized_users:
                if self.user_manager.authorized_users[cid_str].get("username") != tg_username:
                    with self.user_manager.lock:
                        self.user_manager.authorized_users[cid_str]["username"] = tg_username
                        self.user_manager._save_users_unlocked()
            return f"@{tg_username}"
            
        # Fallback: nome registrato in authorized_users.json
        if hasattr(self, "user_manager") and cid_str in self.user_manager.authorized_users:
            reg_name = self.user_manager.authorized_users[cid_str].get("name")
            if reg_name:
                return reg_name
                
        # Fallback: first_name / full_name Telegram
        first_name = from_user.get("first_name", "") if from_user else ""
        last_name = from_user.get("last_name", "") if from_user else ""
        full = f"{first_name} {last_name}".strip()
        if full:
            return full
            
        return f"Chat {chat_id}"

    def _format_command_action(self, device_key, action_val, name_label=""):
        """Genera una stringa sintetica e chiara dell'azione eseguita per i log"""
        dev_k = (device_key or "").lower()
        act = (action_val or "").lower()
        
        if "led" in dev_k or "andrea" in dev_k:
            return "Led Andrea ACCESO 🟢" if act == "on" else "Led Andrea SPENTO 🔴"
        elif "cucina" in dev_k or "luce" in dev_k:
            return "Luce Cucina ACCESA 🟢" if act == "on" else "Luce Cucina SPENTA 🔴"
        elif "ricircolo" in dev_k:
            return "Ricircolo Acqua ATTIVO 🟢" if act == "on" else "Ricircolo Acqua SPENTO 🔴"
        elif "tapo" in dev_k:
            return "Camera Soggiorno ACCESA 🟢" if act == "on" else "Camera Soggiorno SPENTA 🔴"
        elif "allarme" in dev_k or "corridoio" in dev_k:
            return "Camera Corridoio ACCESA 🟢" if act == "on" else "Camera Corridoio SPENTA 🔴"
        elif "serranda" in dev_k:
            return "Serranda Garage APRI 🚪"
        elif "cancello" in dev_k:
            return "Cancello Garage APRI 🚗"
        elif "daikin" in dev_k:
            if act == "cool": return "Clima Daikin COOL ❄️"
            if act == "dry": return "Clima Daikin DRY 💧"
            if act == "off": return "Clima Daikin SPENTO 🔴"
            return f"Clima Daikin {act.upper()}"
            
        if name_label:
            clean_lbl = name_label.replace(" (Accendi)", " ACCESO 🟢").replace(" (Spegni)", " SPENTO 🔴").replace(" (Apri)", " APRI 🚪")
            return clean_lbl
        return f"{device_key} {action_val}"

    def handle_callback_query(self, callback):
        cb_id = callback.get("id")
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        msg_id = callback.get("message", {}).get("message_id")
        data = callback.get("data", "")

        if not self.user_manager.is_authorized(chat_id):
            self._answer_callback_query(cb_id, "Non sei autorizzato!")
            return

        if data.startswith("toggle_notif:"):
            cat_key = data.split(":", 1)[1]
            new_state = self.user_manager.toggle_user_notification(chat_id, cat_key)
            cat_label = NOTIFICATION_CATEGORIES.get(cat_key, cat_key)
            
            st_text = "ATTIVATA ✅" if new_state else "DISATTIVATA ❌"
            self._answer_callback_query(cb_id, f"Notifica '{cat_label}' {st_text}")

            new_markup = self._build_notifications_keyboard(chat_id)
            self._edit_message_reply_markup(chat_id, msg_id, new_markup)

        elif data.startswith("cmd_ask:"):
            parts = data.split(":", 3)
            if len(parts) == 4:
                _, device_key, action_val, name_label = parts
                self._answer_callback_query(cb_id, "Conferma richiesta!")

                confirm_text = (
                    f"⚠️ *CONFERMA COMANDO RAPIDO*\n\n"
                    f"Sei sicuro di voler eseguire l'operazione:\n"
                    f"⚡ *{name_label}*?"
                )
                confirm_keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": "✅ CONFERMA ESECUZIONE", "callback_data": f"cmd_exec:{device_key}:{action_val}:{name_label}"},
                            {"text": "❌ ANNULLA", "callback_data": "cmd_cancel"}
                        ]
                    ]
                }
                self.send_message(chat_id, confirm_text, reply_markup=confirm_keyboard)

        elif data == "nop":
            self._answer_callback_query(cb_id)

        elif data.startswith("consumi_area:"):
            area = data.split(":", 1)[1]  # "casa" o "garage"
            is_casa = (area == "casa")
            device   = "PowerMeterHome.shelly" if is_casa else "PowerMeterBox.shelly"
            label    = "Consumi elettrici Casa" if is_casa else "Consumi elettrici Garage"
            cb_prefix = "casa_page" if is_casa else "garage_page"
            base_dir = os.path.dirname(os.path.abspath(__file__))
            sub_dir  = os.path.join("charts", "power", "casa" if is_casa else "garage")
            fname    = "power_chart_casa.png" if is_casa else "power_chart_garage.png"
            out_path = os.path.join(base_dir, sub_dir, fname)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)

            self._answer_callback_query(cb_id, f"Caricamento consumi {'Casa' if is_casa else 'Garage'}...")

            if hasattr(self.tracker, "event_logger") and self.tracker.event_logger:
                # Riepilogo testuale potenza attuale
                pw_live = None
                with self.tracker.lock:
                    dev_data = None
                    for s_id, s_data in self.tracker.shelly_devices.items():
                        if device.lower() in s_id.lower() or s_id.lower() in device.lower():
                            dev_data = s_data
                            break
                    if dev_data:
                        pw_live = dev_data.get("channels", {}).get("0", {}).get("power")

                if pw_live is None and self.tracker.event_logger:
                    hist = self.tracker.event_logger.get_power_history(hours=24, device=device)
                    if hist:
                        pw_live = hist[-1][1]

                chart_path, total_pages, current_page = self.tracker.event_logger.generate_power_chart(
                    hours=24, device=device, label=label, output_path=out_path
                )
                if chart_path and os.path.exists(chart_path):
                    pw_str = f"⚡ Potenza attuale: `{pw_live:.1f} W`\n" if pw_live is not None else ""
                    caption = f"{pw_str}📊 {label} — Pagina {current_page}/{total_pages} (Finestra 6h)"
                    keyboard_rows = []
                    if total_pages > 1:
                        btns = []
                        if current_page > 1:
                            
                            btns.append({"text": "⬅️ Indietro", "callback_data": f"{cb_prefix}:{current_page-1}"})
                        if current_page < total_pages:
                            btns.append({"text": "Avanti ➡️", "callback_data": f"{cb_prefix}:{current_page+1}"})
                        if btns:
                            keyboard_rows.append(btns)

                    switch_btn = {"text": "🚗 Mostra Garage" if is_casa else "🏠 Mostra Casa",
                                  "callback_data": f"consumi_area:{'garage' if is_casa else 'casa'}"}
                    keyboard_rows.append([switch_btn])

                    keyboard = {"inline_keyboard": keyboard_rows}
                    self.send_photo(chat_id, chart_path, caption=caption, reply_markup=keyboard)
                else:
                    self.send_message(chat_id, f"⚠️ Nessun dato storico disponibile per *{label}*. I dati verranno raccolti a partire da ora.")
            else:
                self.send_message(chat_id, "⚠️ Event logger non disponibile.")

        elif data.startswith("consumi_page:") or data.startswith("garage_page:") or data.startswith("casa_page:"):
            try:
                prefix, page_str = data.split(":", 1)
                target_page = int(page_str)
                is_casa    = (prefix == "casa_page")
                is_garage  = (prefix == "garage_page")
                device     = "PowerMeterHome.shelly" if is_casa else "PowerMeterBox.shelly"
                label      = "Consumi elettrici Casa" if is_casa else "Consumi elettrici Garage"
                cb_prefix  = prefix  # stessa stringa
                base_dir   = os.path.dirname(os.path.abspath(__file__))
                sub_dir    = os.path.join("charts", "power", "casa" if is_casa else "garage")
                fname      = "power_chart_casa.png" if is_casa else "power_chart_garage.png"
                out_path   = os.path.join(base_dir, sub_dir, fname)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)

                self._answer_callback_query(cb_id, f"Caricamento Pagina {target_page}...")

                if hasattr(self.tracker, "event_logger") and self.tracker.event_logger:
                    chart_path, total_pages, current_page = self.tracker.event_logger.generate_power_chart(
                        hours=24, device=device, label=label, output_path=out_path, page=target_page
                    )
                    if chart_path and os.path.exists(chart_path):
                        keyboard_rows = []
                        if total_pages > 1:
                            btns = []
                            if current_page > 1:
                                btns.append({"text": "⬅️ Indietro", "callback_data": f"{cb_prefix}:{current_page-1}"})
                            if current_page < total_pages:
                                btns.append({"text": "Avanti ➡️", "callback_data": f"{cb_prefix}:{current_page+1}"})
                            if btns:
                                keyboard_rows.append(btns)

                        switch_btn = {"text": "🚗 Mostra Garage" if is_casa else "🏠 Mostra Casa",
                                      "callback_data": f"consumi_area:{'garage' if is_casa else 'casa'}"}
                        keyboard_rows.append([switch_btn])

                        keyboard = {"inline_keyboard": keyboard_rows}
                        self._edit_message_media(
                            chat_id, msg_id, photo_path=chart_path,
                            caption=f"📊 {label} — Pagina {current_page}/{total_pages} (Finestra 6h)",
                            reply_markup=keyboard
                        )
            except Exception as e:
                print(f"[!] Errore navigazione grafico: {e}")
                self._answer_callback_query(cb_id, "Errore.")


        elif data.startswith("filter_logs:"):
            parts = data.split(":")
            cat_key = parts[1]
            v_mode = parts[2] if len(parts) > 2 else "table"
            self._answer_callback_query(cb_id, f"Filtro log: {cat_key}")
            report, total_pages, curr_p = self.tracker.event_logger.format_logs_for_telegram(limit=10, category=cat_key, page=0, view_mode=v_mode)
            reply_markup = self._build_logs_keyboard(current_category=cat_key, current_page=curr_p, total_pages=total_pages, view_mode=v_mode)
            self._edit_message_text(chat_id, msg_id, report, reply_markup=reply_markup)

        elif data == "bosch_status":
            self._answer_callback_query(cb_id, "Recupero stato lavastoviglie...")
            b_data = getattr(self.tracker, "bosch_data", None)
            if b_data:
                pow_raw = b_data.get('power', '').lower()
                power = "Accesa 🟢" if pow_raw == "on" else "Spenta 🔴" if pow_raw == "off" else pow_raw
                
                door_raw = b_data.get('door', '').lower()
                door = "Chiusa 🔒" if door_raw == "closed" else "Aperta 🔓" if door_raw == "open" else door_raw
                
                op_raw = b_data.get('operation_state', '').lower()
                op_state = "Inattiva 💤" if op_raw == "inactive" else "In Funzione ⚙️" if op_raw == "run" else "Terminato ✅" if op_raw == "finished" else "Pronta ⏳" if op_raw == "ready" else op_raw
                
                state_txt = (
                    "🍽️ *STATO LAVASTOVIGLIE BOSCH*\n\n"
                    f"🔌 *Alimentazione:* {power}\n"
                    f"🚪 *Porta:* {door}\n"
                    f"⚙️ *Stato Operativo:* {op_state}\n"
                )
                if b_data.get('program'):
                    prog = b_data['program'].replace('BSH.Common.Root.ActiveProgram.', '')
                    
                    # Traduzione Programmi Frequenti
                    if "Eco50" in prog: prog = "Eco 50°C 🌱"
                    elif "Auto2" in prog: prog = "Auto 45-65°C 🤖"
                    elif "Intensiv70" in prog: prog = "Intensivo 70°C 🍳"
                    elif "Quick65" in prog: prog = "Rapido 65°C ⏱️"
                    elif "Quick45" in prog: prog = "Rapido 45°C ⏱️"
                    
                    state_txt += f"🔄 *Programma Attivo:* {prog}\n"
                    
                time_rem = b_data.get('remaining_time')
                if time_rem:
                    total_mins = int(time_rem) // 60
                    if total_mins >= 60:
                        ore = total_mins // 60
                        minuti = total_mins % 60
                        ore_str = "ora" if ore == 1 else "ore"
                        if minuti == 0:
                            state_txt += f"⏳ *Tempo Rimanente:* {ore} {ore_str}\n"
                        else:
                            minuti_str = "minuto" if minuti == 1 else "minuti"
                            state_txt += f"⏳ *Tempo Rimanente:* {ore} {ore_str} e {minuti} {minuti_str}\n"
                    else:
                        minuti_str = "minuto" if total_mins == 1 else "minuti"
                        state_txt += f"⏳ *Tempo Rimanente:* {total_mins} {minuti_str}\n"
                    
                prog_pct = b_data.get('progress')
                if prog_pct:
                    state_txt += f"📊 *Progresso:* {prog_pct}%\n"
            else:
                state_txt = "🍽️ *LAVASTOVIGLIE BOSCH*\n\nNessun dato attualmente ricevuto dal cloud (Lavastoviglie disconnessa o spenta da molto tempo)."
                
            # Mostra una tastiera con un bottone per tornare ai comandi o aggiornare
            keyboard = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "bosch_status"}]]}
            self.send_message(chat_id, state_txt, reply_markup=keyboard)

        elif data.startswith("logs_view:"):
            parts = data.split(":")
            if len(parts) >= 4:
                _, cat_key, page_str, v_mode = parts[:4]
                page_num = int(page_str)
                self._answer_callback_query(cb_id, f"Vista: {'Tabella Mobile' if v_mode == 'table' else 'Elenco Esteso'}")
                report, total_pages, curr_p = self.tracker.event_logger.format_logs_for_telegram(limit=10, category=cat_key, page=page_num, view_mode=v_mode)
                reply_markup = self._build_logs_keyboard(current_category=cat_key, current_page=curr_p, total_pages=total_pages, view_mode=v_mode)
                self._edit_message_text(chat_id, msg_id, report, reply_markup=reply_markup)

        elif data.startswith("logs_page:"):
            parts = data.split(":")
            if len(parts) >= 3:
                cat_key = parts[1]
                page_num = int(parts[2])
                v_mode = parts[3] if len(parts) > 3 else "table"
                self._answer_callback_query(cb_id, f"Pagina {page_num + 1}")
                report, total_pages, curr_p = self.tracker.event_logger.format_logs_for_telegram(limit=10, category=cat_key, page=page_num, view_mode=v_mode)
                reply_markup = self._build_logs_keyboard(current_category=cat_key, current_page=curr_p, total_pages=total_pages, view_mode=v_mode)
                self._edit_message_text(chat_id, msg_id, report, reply_markup=reply_markup)

        elif data.startswith("cmd_exec:"):
            parts = data.split(":", 3)
            if len(parts) == 4:
                _, device_key, action_val, name_label = parts
                self._answer_callback_query(cb_id, "Esecuzione in corso...")
                
                # Esegue il comando MQTT reale sul broker di casa
                ok, res_txt = self.tracker.send_device_command(device_key, action_val)
                if hasattr(self.tracker, "event_logger") and self.tracker.event_logger:
                    user_tag = self._get_user_display_name(callback.get("from", {}), chat_id)
                    synthetic_action = self._format_command_action(device_key, action_val, name_label)
                    self.tracker.event_logger.log_event("command", user_tag, f"Eseguito comando {synthetic_action}")

                if ok:
                    result_text = f"✅ *COMANDO ESEGUITO CON SUCCESSO*\n\n{res_txt}"
                else:
                    result_text = f"❌ *ERRORE ESECUZIONE MQTT*\n\n{res_txt}"



                # Aggiorna il messaggio in-place rimuovendo i pulsanti
                self._edit_message_text(chat_id, msg_id, result_text)

        elif data == "cmd_cancel":
            self._answer_callback_query(cb_id, "Comando annullato.")
            self._edit_message_text(chat_id, msg_id, "❌ *Comando annullato dall'utente.*")

        elif data.startswith("ask_vacation:"):
            act = data.split(":", 1)[1]
            self._answer_callback_query(cb_id, "Richiesta conferma...")
            report_time = getattr(config, "VACATION_REPORT_TIME", "09:00")
            if act == "on":
                confirm_text = (
                    "⚠️ *CONFERMA ATTIVAZIONE MODALITÀ VACANZA*\n\n"
                    "Sei sicuro di voler attivare la **Modalità Vacanza**?\n\n"
                    "• 🚨 **Notifiche Rete**: Qualsiasi disconnessione o ripristino dei nodi verrà *SEMPRE* notificato.\n"
                    f"• ☀️ **Resoconto Mattutino**: Riceverai il report della casa *ogni mattina alle `{report_time}`*."
                )
                btn_cb = "confirm_vacation:on"
            else:
                confirm_text = (
                    "⚠️ *CONFERMA DISATTIVAZIONE MODALITÀ VACANZA*\n\n"
                    "Sei sicuro di voler disattivare la **Modalità Vacanza**?\n\n"
                    "• Verrà ripristinato il comportamento di rete standard."
                )
                btn_cb = "confirm_vacation:off"

            confirm_keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "✅ CONFERMA ESECUZIONE", "callback_data": btn_cb},
                        {"text": "❌ ANNULLA", "callback_data": "cancel_vacation"}
                    ]
                ]
            }
            self.send_message(chat_id, confirm_text, reply_markup=confirm_keyboard)

        elif data.startswith("confirm_vacation:"):
            act = data.split(":", 1)[1]
            self._answer_callback_query(cb_id, "Aggiornamento stato...")
            report_time = getattr(config, "VACATION_REPORT_TIME", "09:00")
            user_tag = self._get_user_display_name(callback.get("from", {}), chat_id)
            if act == "on":
                self.tracker.set_vacation_mode(True)
                if hasattr(self.tracker, "event_logger") and self.tracker.event_logger:
                    self.tracker.event_logger.log_event("command", user_tag, "Modalità Vacanza ATTIVATA 🌴")
                
                # Scansione istantanea stato nodi & dispositivi domotici
                all_devs = [
                    ("🛡️ Firewall Gateway", "192.168.1.1"),
                    ("🖥️ Home Server", "192.168.1.162"),
                    ("📶 Access Point Box", "192.168.3.2"),
                    ("🛋️ Camera Soggiorno NVR", "192.168.4.160"),
                    ("🚪 Camera Corridoio NVR", "192.168.4.161"),
                    ("🎥 Camera Garage NVR", "192.168.3.130"),
                    ("💧 Ricircolo Acqua", "192.168.4.111"),
                    ("🛡️ Modulo Allarme Casa", "192.168.4.112"),
                    ("⚡ Sensori AllarmeIO", "192.168.4.113"),
                    ("📹 TapoHomePlug", "192.168.4.114"),
                    ("💡 Luce Cucina", "192.168.4.115"),
                    ("🚪 Comandi Garage", "192.168.3.131")
                ]
                status_lines = []
                for d_name, d_ip in all_devs:
                    is_up = self.tracker.network_watchdog_states.get(d_ip)
                    if is_up is None:
                        is_up = self.tracker._ping_single_ip(d_ip)
                    st_txt = "🟢 **ONLINE**" if is_up else "🔴 **OFFLINE**"
                    status_lines.append(f"• {d_name} (`{d_ip}`): {st_txt}")

                cams_summary = "\n".join(status_lines)

                result_text = (
                    "🌴 *MODALITÀ VACANZA ATTIVATA CON SUCCESSO 🟢*\n\n"
                    "📡 *Stato Iniziale Nodi & Dispositivi Domotici:*\n"
                    f"{cams_summary}\n\n"
                    "• Se un qualsiasi dispositivo domotico o telecamera **smette di rispondere alla rete / ping**, riceverai un'allerta immediata.\n"
                    f"• Riceverai il resoconto completo della casa **ogni mattina alle {report_time}**."
                )
            else:
                self.tracker.set_vacation_mode(False)
                if hasattr(self.tracker, "event_logger") and self.tracker.event_logger:
                    self.tracker.event_logger.log_event("command", user_tag, "Modalità Vacanza DISATTIVATA 🏠")
                result_text = (
                    "🏠 *MODALITÀ VACANZA DISATTIVATA 🔴*\n\n"
                    "Ripristinato il comportamento standard per notifiche ed allarmi."
                )
            self._edit_message_text(chat_id, msg_id, result_text)

        elif data == "cancel_vacation":
            self._answer_callback_query(cb_id, "Operazione annullata.")
            self._edit_message_text(chat_id, msg_id, "❌ *Operazione Modalità Vacanza annullata.*")

        elif data == "cable_refresh":
            self._answer_callback_query(cb_id, "Aggiornamento parametri cavo...")
            self._edit_message_text(chat_id, msg_id, "⏳ _Rilevazione parametri fisici del cavo dorsale in corso..._")

            def _worker_cable():
                try:
                    report = self.tracker.get_backbone_cable_report()
                    cable_kb = {
                        "inline_keyboard": [
                            [{"text": "🔄 Aggiorna Parametri", "callback_data": "cable_refresh"}],
                            [{"text": "🚀 Test Velocità iperf3 (Box)", "callback_data": "iperf_target:192.168.3.2"}]
                        ]
                    }
                    self._edit_message_text(chat_id, msg_id, report, reply_markup=cable_kb)
                except Exception as e_cable:
                    print(f"[!] Errore _worker_cable: {e_cable}")
                    self._edit_message_text(chat_id, msg_id, f"⚠️ *Errore diagnostica dorsale*: `{e_cable}`")

            threading.Thread(target=_worker_cable, daemon=True).start()

        elif data.startswith("iperf_target:"):
            target_ip = data.split(":", 1)[1]
            self._answer_callback_query(cb_id, f"Avvio test verso {target_ip}...")
            self._edit_message_text(chat_id, msg_id, f"⏳ _Esecuzione test banda passante reale iperf3 verso_ `{target_ip}` _(5s)..._")

            def _worker_iperf():
                report = self.tracker.run_iperf_speedtest(target_ip=target_ip, duration=5)
                self._edit_message_text(chat_id, msg_id, report)

            threading.Thread(target=_worker_iperf, daemon=True).start()

        elif data == "iperf_cancel":
            self._answer_callback_query(cb_id, "Test annullato.")
            self._edit_message_text(chat_id, msg_id, "❌ *Test iperf3 annullato.*")

        elif data.startswith("print_exec:"):
            print_id = data.split(":", 1)[1]
            p_data = self.pending_prints.pop(print_id, None)
            if not p_data:
                self._answer_callback_query(cb_id, "Richiesta di stampa scaduta o già elaborata.")
                self._edit_message_text(chat_id, msg_id, "⚠️ *Richiesta di stampa scaduta o non trovata.*")
                return

            self.pending_page_inputs.pop(chat_id, None)
            self.pending_copies_inputs.pop(chat_id, None)
            self._answer_callback_query(cb_id, "Elaborazione stampa avviata...")
            file_id = p_data["file_id"]
            file_name = p_data["file_name"]
            print_opts = {
                "pages": p_data.get("pages", "all"),
                "orientation": p_data.get("orientation", "portrait"),
                "fit": p_data.get("fit", "fit"),
                "pos": p_data.get("pos", "center")
            }
            total_copies = p_data.get("copies", 1)

            # Rimuove la foto dell'anteprima e genera un messaggio di stato testuale per mostrare l'avanzamento
            self._delete_message(chat_id, msg_id)
            track_msg_id = self.send_message_get_id(chat_id, f"⏳ *Avvio elaborazione stampa per* `{file_name}`...") or msg_id

            def _init_worker():
                try:
                    self._edit_message_text(chat_id, track_msg_id, f"⏳ *Download del file* `{file_name}` *in corso...*")
                    # 1. Download file da Telegram
                    res = requests.get(f"{self.api_url}/getFile?file_id={file_id}", timeout=15)
                    if not res.ok or not res.json().get("ok"):
                        self._edit_message_text(chat_id, track_msg_id, "❌ Impossibile recuperare il file dai server Telegram.")
                        return
                    f_path = res.json()["result"]["file_path"]
                    download_url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{f_path}"
                    f_resp = requests.get(download_url, timeout=30)
                    if not f_resp.ok:
                        self._edit_message_text(chat_id, track_msg_id, f"❌ Errore scaricamento file da Telegram (HTTP {f_resp.status_code}).")
                        return
                    doc_bytes = f_resp.content

                    # 2. Risoluzione pagine
                    is_img = any(file_name.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"])
                    import importlib, core.printer_manager
                    importlib.reload(core.printer_manager)
                    self.printer_mgr = core.printer_manager.PrinterManager()

                    if is_img:
                        target_pages = [1]
                        doc_total_pages = 1
                    else:
                        doc_total_pages = self.printer_mgr.get_remote_page_count(file_url=download_url, filename=file_name)
                        if not doc_total_pages:
                            doc_total_pages = self.printer_mgr.get_pdf_page_count(doc_bytes)
                        target_pages = self.printer_mgr.resolve_pages_list(print_opts.get("pages", "all"), doc_total_pages)

                    self._execute_print_job(
                        chat_id=chat_id,
                        msg_id=track_msg_id,
                        file_name=file_name,
                        download_url=download_url,
                        doc_bytes=doc_bytes,
                        target_pages=target_pages,
                        is_img=is_img,
                        doc_total_pages=doc_total_pages,
                        print_opts=print_opts,
                        total_copies=total_copies,
                        start_copy=1,
                        start_page_idx=0
                    )
                except Exception as e:
                    self._edit_message_text(chat_id, track_msg_id, f"❌ *Errore durante l'avvio della stampa:*\n`{e}`")

            threading.Thread(target=_init_worker, daemon=True).start()

        elif data.startswith("print_resume:"):
            resume_id = data.split(":", 1)[1]
            res_data = self.pending_resumes.get(resume_id)
            if not res_data:
                self._answer_callback_query(cb_id, "Sessione di stampa scaduta o non trovata.")
                self._edit_message_text(chat_id, msg_id, "⚠️ *Sessione di stampa scaduta o già conclusa.*")
                return

            import importlib, core.printer_manager
            importlib.reload(core.printer_manager)
            self.printer_mgr = core.printer_manager.PrinterManager()

            # Controllo preliminare dello stato del motore hardware
            is_ready, ready_msg = self.printer_mgr.check_engine_online()
            if not is_ready:
                self._answer_callback_query(
                    cb_id,
                    f"⚠️ Stampante non pronta!\n{ready_msg}\n\nRisolvi prima il problema (carta/sportello/inceppamento) e riprova.",
                    show_alert=True
                )
                return

            self._answer_callback_query(cb_id, "Ripresa della stampa in corso...")
            self.pending_resumes.pop(resume_id, None)

            self._execute_print_job(
                chat_id=res_data["chat_id"],
                msg_id=res_data["msg_id"],
                file_name=res_data["file_name"],
                download_url=res_data["download_url"],
                doc_bytes=res_data["doc_bytes"],
                target_pages=res_data["target_pages"],
                is_img=res_data["is_img"],
                doc_total_pages=res_data["doc_total_pages"],
                print_opts=res_data["print_opts"],
                total_copies=res_data["total_copies"],
                start_copy=res_data["current_copy"],
                start_page_idx=res_data["start_page_idx"]
            )

        elif data.startswith("print_cancel_resume:"):
            resume_id = data.split(":", 1)[1]
            self.pending_resumes.pop(resume_id, None)
            self._answer_callback_query(cb_id, "Stampa annullata definitivamente")
            self._edit_message_text(chat_id, msg_id, "❌ *Stampa interrotta annullata definitivamente dall'utente.*")

        elif data.startswith("print_opt:") or data.startswith("print_preview:"):
            print_id = data.split(":", 1)[1]
            self.pending_page_inputs.pop(chat_id, None)
            self.pending_copies_inputs.pop(chat_id, None)
            self._answer_callback_query(cb_id, "Caricamento anteprima...")
            threading.Thread(target=self._render_or_update_preview_card, args=(chat_id, msg_id, print_id, True), daemon=True).start()

        elif data.startswith("print_nav:"):
            parts = data.split(":")
            if len(parts) >= 3:
                _, print_id, target_idx = parts[:3]
                item = self.pending_prints.get(print_id)
                if item:
                    item["preview_idx"] = int(target_idx)
                    self._answer_callback_query(cb_id, f"Foglio {int(target_idx) + 1}")
                    threading.Thread(target=self._render_or_update_preview_card, args=(chat_id, msg_id, print_id, False), daemon=True).start()
                else:
                    self._answer_callback_query(cb_id, "Richiesta scaduta")

        elif data == "nop":
            self._answer_callback_query(cb_id)

        elif data.startswith("print_page_menu:"):
            print_id = data.split(":", 1)[1]
            self.pending_copies_inputs.pop(chat_id, None)
            txt, kb = self._render_page_selection_menu(print_id)
            if txt:
                self._answer_callback_query(cb_id, "Scrivi in chat o tocca un'opzione")
                self._delete_message(chat_id, msg_id)
                new_m = self.send_message(chat_id, txt, reply_markup=kb)
                self.pending_page_inputs[chat_id] = {"print_id": print_id, "time": time.time()}
            else:
                self._answer_callback_query(cb_id, "Richiesta scaduta")

        elif data.startswith("print_set_page:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                _, print_id, page_val = parts
                item = self.pending_prints.get(print_id)
                if item:
                    total_pages = self._ensure_total_pages(item)
                    import importlib, core.printer_manager
                    importlib.reload(core.printer_manager)
                    self.printer_mgr = core.printer_manager.PrinterManager()

                    is_valid, clean_spec, err_msg = self.printer_mgr.validate_pages_spec(page_val, total_pages)
                    if not is_valid:
                        self._answer_callback_query(cb_id, err_msg.replace("*", "").replace("`", ""), show_alert=True)
                        return

                    item["pages"] = clean_spec
                    item["preview_idx"] = 0
                    self.pending_page_inputs.pop(chat_id, None)
                    p_disp = "Tutte" if clean_spec == "all" else f"Pag. {clean_spec}"
                    self._answer_callback_query(cb_id, f"Pagine: {p_disp}")
                    threading.Thread(target=self._render_or_update_preview_card, args=(chat_id, msg_id, print_id, True), daemon=True).start()

        elif data.startswith("print_copies_menu:"):
            print_id = data.split(":", 1)[1]
            self.pending_page_inputs.pop(chat_id, None)
            txt, kb = self._render_copies_selection_menu(print_id)
            if txt:
                self._answer_callback_query(cb_id, "Scrivi in chat o tocca il numero di copie")
                self._delete_message(chat_id, msg_id)
                self.send_message(chat_id, txt, reply_markup=kb)
                self.pending_copies_inputs[chat_id] = {"print_id": print_id, "time": time.time()}
            else:
                self._answer_callback_query(cb_id, "Richiesta scaduta")

        elif data.startswith("print_set_copies:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                _, print_id, count_val = parts
                item = self.pending_prints.get(print_id)
                if item:
                    try:
                        c_val = max(1, min(100, int(count_val)))
                        item["copies"] = c_val
                    except Exception:
                        item["copies"] = 1
                    self.pending_copies_inputs.pop(chat_id, None)
                    c_disp = f"{item['copies']} {'copia' if item['copies'] == 1 else 'copie'}"
                    self._answer_callback_query(cb_id, f"Copie: {c_disp}")
                    threading.Thread(target=self._render_or_update_preview_card, args=(chat_id, msg_id, print_id, True), daemon=True).start()

        elif data.startswith("print_toggle:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                _, print_id, setting = parts
                item = self.pending_prints.get(print_id)
                if not item:
                    self._answer_callback_query(cb_id, "Richiesta scaduta")
                    return

                if setting == "orientation":
                    curr = item.get("orientation", "portrait")
                    item["orientation"] = "landscape" if curr == "portrait" else "portrait"
                elif setting == "fit":
                    cycle = ["fit", "original", "fill"]
                    curr = item.get("fit", "fit")
                    item["fit"] = cycle[(cycle.index(curr) + 1) % len(cycle)] if curr in cycle else "fit"
                elif setting == "pos":
                    cycle = ["center", "top", "bottom"]
                    curr = item.get("pos", "center")
                    item["pos"] = cycle[(cycle.index(curr) + 1) % len(cycle)] if curr in cycle else "center"

                self._answer_callback_query(cb_id, "Opzione aggiornata")
                threading.Thread(target=self._render_or_update_preview_card, args=(chat_id, msg_id, print_id, False), daemon=True).start()

        elif data.startswith("print_back:"):
            print_id = data.split(":", 1)[1]
            item = self.pending_prints.get(print_id)
            if not item:
                self._answer_callback_query(cb_id, "Richiesta scaduta")
                return
            self.pending_page_inputs.pop(chat_id, None)
            self.pending_copies_inputs.pop(chat_id, None)
            self._answer_callback_query(cb_id)
            self._delete_message(chat_id, msg_id)
            f_name = item.get("file_name", "Documento")
            f_size = item.get("file_size", 0)
            size_str = f"{f_size / 1024:.1f} KB" if f_size >= 1024 else f"{f_size} B"
            text = (
                "📄 *DOCUMENTO RICEVUTO*\n\n"
                f"📁 *Nome:* `{f_name}`\n"
                f"⚖️ *Dimensione:* `{size_str}`\n\n"
                "🖨️ *Desideri stampare questo file sulla HP LaserJet P1102w?*"
            )
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🖨️ Stampa", "callback_data": f"print_exec:{print_id}"}],
                    [
                        {"text": "⚙️ Opzioni Avanzate", "callback_data": f"print_opt:{print_id}"},
                        {"text": "❌ Annulla", "callback_data": f"print_cancel:{print_id}"}
                    ]
                ]
            }
            self.send_message(chat_id, text, reply_markup=keyboard)

        elif data.startswith("print_cancel:"):
            print_id = data.split(":", 1)[1]
            self.pending_prints.pop(print_id, None)
            self.pending_page_inputs.pop(chat_id, None)
            self.pending_copies_inputs.pop(chat_id, None)
            self._answer_callback_query(cb_id, "Stampa annullata")
            self._delete_message(chat_id, msg_id)
            self.send_message(chat_id, "❌ *Stampa annullata dall'utente.*")

        elif data == "printer_err_log":
            self._answer_callback_query(cb_id, "Lettura registro errori in corso...")
            import importlib, core.printer_manager
            importlib.reload(core.printer_manager)
            self.printer_mgr = core.printer_manager.PrinterManager()
            err_msg = self.printer_mgr.format_error_log_message()
            self.send_message(chat_id, err_msg)

        elif data == "check_printer_status":
            self._answer_callback_query(cb_id, "Verifica stato stampante...")
            import importlib, core.printer_manager
            importlib.reload(core.printer_manager)
            self.printer_mgr = core.printer_manager.PrinterManager()
            status_msg = self.printer_mgr.get_printer_status()
            kb = {
                "inline_keyboard": [
                    [{"text": "📜 Registro Errori Hardware", "callback_data": "printer_err_log"}]
                ]
            }
            self.send_message(chat_id, status_msg, reply_markup=kb)

    def _render_or_update_preview_card(self, chat_id, msg_id, print_id, replace_msg=False):
        """Genera e visualizza (o aggiorna in-place) la scheda interattiva con foto dell'anteprima e controlli di stampa"""
        try:
            item = self.pending_prints.get(print_id)
            if not item:
                self.send_message(chat_id, "⚠️ Richiesta di stampa scaduta o non trovata.")
                return

            file_id = item["file_id"]
            file_name = item["file_name"]

            # Recupera l'URL diretto del file da Telegram se non ancora presente
            download_url = item.get("download_url")
            if not download_url:
                res = requests.get(f"{self.api_url}/getFile?file_id={file_id}", timeout=15)
                if res.ok and res.json().get("ok"):
                    f_path = res.json()["result"]["file_path"]
                    download_url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{f_path}"
                    item["download_url"] = download_url
                else:
                    self.send_message(chat_id, "❌ Impossibile recuperare il file dai server Telegram.")
                    return

            is_img = any(file_name.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"])
            item["is_img"] = is_img

            import importlib, core.printer_manager
            importlib.reload(core.printer_manager)
            self.printer_mgr = core.printer_manager.PrinterManager()

            # Calcolo totale pagine del documento se non ancora effettuato
            if "total_pages" not in item:
                if is_img:
                    total_pages = 1
                else:
                    total_pages = self.printer_mgr.get_remote_page_count(file_url=download_url, filename=file_name) or 1
                item["total_pages"] = total_pages
            total_pages = item["total_pages"]

            # Risoluzione pagine da stampare in base alla selezione utente (es. "all", "1", "1-3")
            target_pages = self.printer_mgr.resolve_pages_list(item.get("pages", "all"), total_pages)
            item["target_pages"] = target_pages

            # Pagina dell'anteprima attiva
            preview_idx = item.get("preview_idx", 0)
            if preview_idx < 0:
                preview_idx = 0
            elif preview_idx >= len(target_pages):
                preview_idx = max(0, len(target_pages) - 1)
            item["preview_idx"] = preview_idx
            curr_page_num = target_pages[preview_idx]

            single_opts = {
                "orientation": item.get("orientation", "portrait"),
                "fit": item.get("fit", "fit"),
                "pos": item.get("pos", "center")
            }

            preview_jpeg = self.printer_mgr.get_print_preview_jpeg(
                filename=file_name,
                options=single_opts,
                file_url=download_url,
                preview_page=curr_page_num
            )

            if not preview_jpeg:
                self.send_message(chat_id, "❌ Impossibile generare l'anteprima di stampa.")
                return

            orient_labels = {"portrait": "Verticale 📄", "landscape": "Orizzontale 🔄"}
            fit_labels = {"fit": "Adatta al foglio 🔍", "original": "100% Originale 📏", "fill": "Riempi foglio 📐"}
            pos_labels = {"center": "Centrato ⏺️", "top": "In Alto ⬆️", "bottom": "In Basso ⬇️"}

            p_val = item.get("pages", "all")
            p_disp = "Tutte 📑" if p_val in ["all", "tutte", "*"] else f"Pag. {p_val} 📄"
            copies = item.get("copies", 1)
            c_disp = f"{copies} {'copia' if copies == 1 else 'copie'}"

            page_nav_line = ""
            if len(target_pages) > 1:
                page_nav_line = f"📄 *Foglio Visualizzato:* Pagina `{curr_page_num}` _({preview_idx + 1} di {len(target_pages)})_\n"

            caption = (
                "👁️ *ANTEPRIMA E OPZIONI DI STAMPA* 📄\n\n"
                f"📁 *Documento:* `{file_name}`\n"
                f"{page_nav_line}"
                f"• 📑 *Pagine:* `{p_disp}`\n"
                f"• 🔢 *Copie:* `{c_disp}` 🔢\n"
                f"• 🔄 *Orientamento:* `{orient_labels.get(item.get('orientation', 'portrait'))}`\n"
                f"• 📐 *Adattamento:* `{fit_labels.get(item.get('fit', 'fit'))}`\n"
                f"• 📍 *Posizione:* `{pos_labels.get(item.get('pos', 'center'))}`\n\n"
                "👇 _Tocca i tasti sottostanti per modificare le opzioni in tempo reale o sfogliare le pagine:_"
            )

            kb_rows = []
            # Riga 1: Navigazione tra le pagine se multi-pagina
            if len(target_pages) > 1:
                nav_row = []
                if preview_idx > 0:
                    nav_row.append({"text": "⬅️ Prec", "callback_data": f"print_nav:{print_id}:{preview_idx - 1}"})
                else:
                    nav_row.append({"text": "⏮️ Inizio", "callback_data": "nop"})

                nav_row.append({"text": f"📄 {preview_idx + 1}/{len(target_pages)}", "callback_data": "nop"})

                if preview_idx < len(target_pages) - 1:
                    nav_row.append({"text": "Succ ➡️", "callback_data": f"print_nav:{print_id}:{preview_idx + 1}"})
                else:
                    nav_row.append({"text": "⏭️ Fine", "callback_data": "nop"})
                kb_rows.append(nav_row)

            # Riga 2: Pagine e Copie
            kb_rows.append([
                {"text": f"📑 Pagine: {p_disp}", "callback_data": f"print_page_menu:{print_id}"},
                {"text": f"🔢 Copie: {c_disp}", "callback_data": f"print_copies_menu:{print_id}"}
            ])

            # Riga 3: Orientamento e Scala
            kb_rows.append([
                {"text": f"🔄 {orient_labels.get(item.get('orientation', 'portrait'))}", "callback_data": f"print_toggle:{print_id}:orientation"},
                {"text": f"📐 {fit_labels.get(item.get('fit', 'fit'))}", "callback_data": f"print_toggle:{print_id}:fit"}
            ])

            # Riga 4: Posizione
            kb_rows.append([
                {"text": f"📍 {pos_labels.get(item.get('pos', 'center'))}", "callback_data": f"print_toggle:{print_id}:pos"}
            ])

            # Riga 5: Azioni
            kb_rows.append([
                {"text": "🖨️ Avvia Stampa", "callback_data": f"print_exec:{print_id}"},
                {"text": "🔙 Indietro", "callback_data": f"print_back:{print_id}"},
                {"text": "❌ Annulla", "callback_data": f"print_cancel:{print_id}"}
            ])

            keyboard = {"inline_keyboard": kb_rows}

            if replace_msg:
                if msg_id:
                    self._delete_message(chat_id, msg_id)
                self.send_photo(chat_id, preview_jpeg, caption=caption, reply_markup=keyboard)
            else:
                ok = self._edit_message_media(chat_id, msg_id, photo_source=preview_jpeg, caption=caption, reply_markup=keyboard)
                if not ok:
                    if msg_id:
                        self._delete_message(chat_id, msg_id)
                    self.send_photo(chat_id, preview_jpeg, caption=caption, reply_markup=keyboard)

        except Exception as e:
            print(f"[!] Errore _render_or_update_preview_card: {e}")
            self.send_message(chat_id, f"❌ *Errore anteprima di stampa:*\n`{e}`")

    def _handle_print_failure(self, chat_id, msg_id, file_name, download_url, doc_bytes, target_pages, is_img, doc_total_pages, print_opts, total_copies, copy_num, page_idx, diag):
        """Salva lo stato di interruzione ed invia la notifica con il pulsante per riprendere la stampa"""
        resume_id = uuid.uuid4().hex[:8]
        p_num = target_pages[page_idx]

        self.pending_resumes[resume_id] = {
            "chat_id": chat_id,
            "msg_id": msg_id,
            "file_name": file_name,
            "download_url": download_url,
            "doc_bytes": doc_bytes,
            "target_pages": target_pages,
            "is_img": is_img,
            "doc_total_pages": doc_total_pages,
            "print_opts": print_opts,
            "total_copies": total_copies,
            "current_copy": copy_num,
            "start_page_idx": page_idx,
            "failed_page": p_num,
            "time": time.time()
        }

        title = diag.get("title", "⚠️ Errore Hardware durante la Stampa")
        code = diag.get("code", "N/D")
        desc = diag.get("detail") or diag.get("description", "Si è verificato un problema hardware sulla stampante.")
        action = diag.get("action", "Verifica la stampante e premi il tasto Riprendi/Go.")

        copy_info = f"🔢 *Copia:* `{copy_num}` di `{total_copies}`\n" if total_copies > 1 else ""
        page_info = f"📑 *Interruzione a:* Pagina `{p_num}` (foglio {page_idx + 1} di {len(target_pages)})\n"

        err_text = (
            f"❌ *{title}* 🔴\n\n"
            f"📄 *Documento:* `{file_name}`\n"
            f"{copy_info}"
            f"{page_info}"
            f"⚠️ *Codice Errore:* `{code}`\n"
            f"📝 *Dettagli:* {desc}\n\n"
            f"👉 *Cosa fare adesso:*\n"
            f"{action}\n\n"
            "💡 _Dopo aver risolto (es. ricaricato la carta o rimosso l'inceppamento), premi il pulsante qui sotto per riprendere la stampa da questo punto esatto senza sprecare i fogli già usciti!_"
        )

        keyboard = {
            "inline_keyboard": [
                [{"text": f"▶️ Riprendi Stampa da Pagina {p_num}", "callback_data": f"print_resume:{resume_id}"}],
                [{"text": "❌ Annulla Stampa", "callback_data": f"print_cancel_resume:{resume_id}"}]
            ]
        }
        self._edit_message_text(chat_id, msg_id, err_text, reply_markup=keyboard)

    def _execute_print_job(self, chat_id, msg_id, file_name, download_url, doc_bytes, target_pages, is_img, doc_total_pages, print_opts, total_copies=1, start_copy=1, start_page_idx=0):
        """Esegue il ciclo di stampa multipagina e multicopia con rilevamento guasti e possibilità di ripresa"""
        def _worker():
            try:
                import importlib, core.printer_manager
                importlib.reload(core.printer_manager)
                self.printer_mgr = core.printer_manager.PrinterManager()
                self.printer_mgr.is_printing_active = True
                ev_logger = getattr(self.tracker, "event_logger", None)

                total_pages_count = len(target_pages)

                for copy_num in range(start_copy, total_copies + 1):
                    # Se è la copia iniziale di ripresa, parte dall'indice interrotto; altrimenti dall'inizio
                    from_idx = start_page_idx if copy_num == start_copy else 0

                    for idx in range(from_idx, total_pages_count):
                        p_num = target_pages[idx]
                        curr_step = idx + 1

                        copy_label = f"Copia {copy_num}/{total_copies} • " if total_copies > 1 else ""
                        if total_pages_count > 1 and doc_total_pages > 1:
                            page_label = f"pagina {curr_step} di {total_pages_count} (foglio doc {p_num}/{doc_total_pages})"
                        elif total_pages_count > 1:
                            page_label = f"pagina {curr_step} di {total_pages_count}"
                        else:
                            page_label = "documento"

                        detail_label = f"{copy_label}{page_label}"

                        self._edit_message_text(chat_id, msg_id, f"☁️ *Conversione cloud ({detail_label})...*\n`{file_name}`")

                        single_opts = dict(print_opts)
                        single_opts["pages"] = str(p_num)

                        try:
                            pclm_pages = self.printer_mgr.convert_via_vercel(
                                doc_bytes=doc_bytes,
                                filename=file_name,
                                options=single_opts,
                                file_url=download_url
                            )
                        except Exception as ve:
                            self._edit_message_text(chat_id, msg_id, f"❌ *Errore durante la conversione su Vercel ({detail_label}):*\n`{ve}`")
                            return

                        if not pclm_pages:
                            self._edit_message_text(chat_id, msg_id, f"❌ *Errore conversione: nessuna pagina PCLm generata per {detail_label}.*")
                            return

                        page_bytes = pclm_pages[0]
                        self._edit_message_text(chat_id, msg_id, f"🖨️ *Stampa in corso ({detail_label})...*\n`{file_name}`")

                        job_label = f"{file_name}_c{copy_num}_p{p_num}" if (total_pages_count > 1 or total_copies > 1) else file_name
                        skip_chk = (idx > 0 or copy_num > start_copy)
                        ok, p_msg = self.printer_mgr.print_document_via_ipp(page_bytes, job_name=job_label, is_image=is_img, skip_check=skip_chk)
                        if not ok:
                            diag = self.printer_mgr.diagnose_printer_issue(failure_reason=p_msg)
                            self._handle_print_failure(
                                chat_id, msg_id, file_name, download_url, doc_bytes,
                                target_pages, is_img, doc_total_pages, print_opts,
                                total_copies, copy_num, idx, diag
                            )
                            return

                        # Monitoraggio stato hardware sul motore (PJL)
                        is_last_page = (curr_step == total_pages_count)
                        wait_t = 25 if is_last_page else 18
                        is_clean, diag = self.printer_mgr.monitor_print_progress(max_wait=wait_t, event_logger=ev_logger)
                        if not is_clean:
                            self._handle_print_failure(
                                chat_id, msg_id, file_name, download_url, doc_bytes,
                                target_pages, is_img, doc_total_pages, print_opts,
                                total_copies, copy_num, idx, diag
                            )
                            return

                        if not is_last_page:
                            time.sleep(1.0)

                    if copy_num < total_copies:
                        time.sleep(1.0)

                # Completamento con successo di tutte le pagine e copie!
                copies_str = f" ({total_copies} copie)" if total_copies > 1 else ""
                pages_str = f"{total_pages_count} pagine" if total_pages_count > 1 else "1 pagina"
                success_text = (
                    "✅ *STAMPA COMPLETATA CON SUCCESSO!* 🖨️📄\n\n"
                    f"📄 *Documento:* `{file_name}` _({pages_str}{copies_str})_\n"
                    f"🏷️ *Stampante:* `HP LaserJet Professional P1102w`\n"
                    f"🌐 *Destinazione:* `{self.printer_mgr.ip}:631`\n\n"
                    "✨ _Tutte le copie e le pagine sono state stampate regolarmente!_"
                )
                self._edit_message_text(chat_id, msg_id, success_text)
                if ev_logger and hasattr(ev_logger, "log_event"):
                    ev_logger.log_event("printer", "HP LaserJet P1102w", f"Stampa completata con successo: {file_name} ({pages_str}{copies_str})")

            except Exception as e:
                self._edit_message_text(chat_id, msg_id, f"❌ *Errore imprevisto durante la stampa:*\n`{e}`")
            finally:
                if hasattr(self, "printer_mgr") and self.printer_mgr:
                    self.printer_mgr.is_printing_active = False

        threading.Thread(target=_worker, daemon=True).start()

    def _ensure_total_pages(self, item):
        """Assicura che item['total_pages'] sia calcolato e memorizzato"""
        if "total_pages" in item and item["total_pages"]:
            return item["total_pages"]

        file_name = item.get("file_name", "")
        is_img = any(file_name.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"])
        if is_img:
            item["total_pages"] = 1
            return 1

        download_url = item.get("download_url")
        if not download_url:
            file_id = item.get("file_id")
            try:
                res = requests.get(f"{self.api_url}/getFile?file_id={file_id}", timeout=10)
                if res.ok and res.json().get("ok"):
                    f_path = res.json()["result"]["file_path"]
                    download_url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{f_path}"
                    item["download_url"] = download_url
            except Exception:
                pass

        if download_url:
            try:
                import importlib, core.printer_manager
                importlib.reload(core.printer_manager)
                self.printer_mgr = core.printer_manager.PrinterManager()
                t_pages = self.printer_mgr.get_remote_page_count(file_url=download_url, filename=file_name)
                if t_pages:
                    item["total_pages"] = t_pages
                    return t_pages
            except Exception:
                pass

        item["total_pages"] = item.get("total_pages", 1)
        return item["total_pages"]

    def _render_page_selection_menu(self, print_id):
        """Genera il menu per impostare le pagine (da tastiera in chat o da pulsanti rapidi)"""
        item = self.pending_prints.get(print_id)
        if not item:
            return None, None

        total_pages = self._ensure_total_pages(item)
        p_val = item.get("pages", "all")
        p_disp = "Tutte le pagine 📑" if p_val in ["all", "tutte", "*"] else f"Pagina/e `{p_val}` 📄"

        limit_info = f"(limiti consentiti: da **1** a **{total_pages}**)" if total_pages > 1 else "(il documento ha 1 sola pagina)"

        text = (
            "📑 *SELEZIONE PAGINE DI STAMPA*\n\n"
            f"📁 *Documento:* `{item.get('file_name', 'File')}`\n"
            f"📄 *Totale Pagine Documento:* `{total_pages}`\n"
            f"🎯 *Attuale:* {p_disp}\n\n"
            f"✍️ *Scrivi ora in chat i numeri delle pagine* {limit_info}:\n\n"
            "*Esempi validi:*\n"
            f"• `1` _(solo pagina 1)_\n"
            f"• `1-{min(3, total_pages)}` _(intervallo di pagine)_\n"
            f"• `all` o `tutte` _(stampa tutte le {total_pages} pagine)_\n\n"
            f"⛔ *Controllo Limiti:* Non sono ammessi valori inferiori a `1` o superiori a `{total_pages}`.\n\n"
            "👇 _Oppure tocca una delle scelte rapide qui sotto:_"
        )

        kb_rows = []
        if total_pages <= 1:
            kb_rows.append([{"text": "📑 Pagina 1 (Tutto)", "callback_data": f"print_set_page:{print_id}:1"}])
        elif total_pages == 2:
            kb_rows.append([
                {"text": "📑 Tutte (1-2)", "callback_data": f"print_set_page:{print_id}:all"},
                {"text": "📄 Pag. 1", "callback_data": f"print_set_page:{print_id}:1"},
                {"text": "📄 Pag. 2", "callback_data": f"print_set_page:{print_id}:2"}
            ])
        else:
            kb_rows.append([
                {"text": f"📑 Tutte (1-{total_pages})", "callback_data": f"print_set_page:{print_id}:all"},
                {"text": "📄 Pag. 1", "callback_data": f"print_set_page:{print_id}:1"}
            ])
            mid_p = min(3, total_pages)
            kb_rows.append([
                {"text": "📄 Pag. 2", "callback_data": f"print_set_page:{print_id}:2"},
                {"text": f"📑 Pag. 1-{mid_p}", "callback_data": f"print_set_page:{print_id}:1-{mid_p}"}
            ])

        kb_rows.append([{"text": "🔙 Torna all'Anteprima", "callback_data": f"print_opt:{print_id}"}])

        keyboard = {"inline_keyboard": kb_rows}
        return text, keyboard

    def _render_copies_selection_menu(self, print_id):
        """Genera il menu per impostare il numero di copie (da tastiera in chat o da pulsanti rapidi)"""
        item = self.pending_prints.get(print_id)
        if not item:
            return None, None
        copies = item.get("copies", 1)
        copies_disp = f"{copies} {'copia' if copies == 1 else 'copie'}"

        text = (
            "🔢 *NUMERO DI COPIE DI STAMPA*\n\n"
            f"📁 *Documento:* `{item.get('file_name', 'File')}`\n"
            f"🎯 *Attuale:* `{copies_disp}` 🔢\n\n"
            "✍️ *Scrivi ora direttamente in questa chat il numero di copie da stampare!*\n\n"
            "*Esempi validi:*\n"
            "• `1` _(stampa 1 sola copia)_\n"
            "• `2` _(stampa 2 copie identiche)_\n"
            "• `5` _(stampa 5 copie)_\n\n"
            "👇 _Oppure tocca una delle scelte rapide qui sotto:_"
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "1 copia", "callback_data": f"print_set_copies:{print_id}:1"},
                    {"text": "2 copie", "callback_data": f"print_set_copies:{print_id}:2"},
                    {"text": "3 copie", "callback_data": f"print_set_copies:{print_id}:3"}
                ],
                [
                    {"text": "4 copie", "callback_data": f"print_set_copies:{print_id}:4"},
                    {"text": "5 copie", "callback_data": f"print_set_copies:{print_id}:5"},
                    {"text": "10 copie", "callback_data": f"print_set_copies:{print_id}:10"}
                ],
                [
                    {"text": "🔙 Torna alle Opzioni", "callback_data": f"print_opt:{print_id}"}
                ]
            ]
        }
        return text, keyboard

    def _render_print_options_menu(self, print_id):
        """Genera il menu interattivo per la configurazione avanzata della stampa"""
        item = self.pending_prints.get(print_id)
        if not item:
            return None, None

        p_val = item.get("pages", "all")
        copies_val = item.get("copies", 1)
        o_val = item.get("orientation", "portrait")
        f_val = item.get("fit", "fit")
        pos_val = item.get("pos", "center")

        if p_val in ["all", "tutte", "*"]:
            pages_display = "Tutte 📑"
        elif p_val == "1":
            pages_display = "Pag. 1 📄"
        else:
            pages_display = f"Pag. {p_val} 📄"

        copies_display = f"{copies_val} {'copia' if copies_val == 1 else 'copie'}"

        orient_labels = {"portrait": "Verticale 📄", "landscape": "Orizzontale 🔄"}
        fit_labels = {"fit": "Adatta al foglio 🔍", "original": "100% Originale 📏", "fill": "Riempi foglio 📐"}
        pos_labels = {"center": "Centrato ⏺️", "top": "In Alto ⬆️", "bottom": "In Basso ⬇️"}

        text = (
            "⚙️ *OPZIONI DI STAMPA AVANZATE*\n\n"
            f"📁 *Documento:* `{item.get('file_name', 'File')}`\n\n"
            f"• 📑 *Pagine:* `{pages_display}`\n"
            f"• 🔢 *Copie:* `{copies_display}`\n"
            f"• 🔄 *Orientamento:* `{orient_labels.get(o_val, o_val)}`\n"
            f"• 📐 *Adattamento:* `{fit_labels.get(f_val, f_val)}`\n"
            f"• 📍 *Posizione:* `{pos_labels.get(pos_val, pos_val)}`\n\n"
            "👉 _Tocca [📑 Pagine] o [🔢 Copie] per impostare i valori o tocca i pulsanti per regolare le impostazioni:_"
        )

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": f"📑 Pagine: {pages_display}", "callback_data": f"print_page_menu:{print_id}"},
                    {"text": f"🔢 Copie: {copies_display}", "callback_data": f"print_copies_menu:{print_id}"}
                ],
                [{"text": f"🔄 Orientamento: {orient_labels.get(o_val, o_val)}", "callback_data": f"print_toggle:{print_id}:orientation"}],
                [{"text": f"📐 Scala: {fit_labels.get(f_val, f_val)}", "callback_data": f"print_toggle:{print_id}:fit"}],
                [{"text": f"📍 Posizione: {pos_labels.get(pos_val, pos_val)}", "callback_data": f"print_toggle:{print_id}:pos"}],
                [{"text": "👁️ Anteprima di Stampa", "callback_data": f"print_preview:{print_id}"}],
                [
                    {"text": "🖨️ Avvia Stampa", "callback_data": f"print_exec:{print_id}"},
                    {"text": "🔙 Indietro", "callback_data": f"print_back:{print_id}"}
                ]
            ]
        }
        return text, keyboard

    def _prompt_print_confirmation(self, chat_id, message, file_id, file_name, file_size):
        """Richiede all'utente la conferma prima di inviare un file alla stampante HP"""
        now = time.time()
        # Pulizia richieste più vecchie di 30 minuti
        self.pending_prints = {k: v for k, v in self.pending_prints.items() if now - v.get("time", 0) < 1800}

        print_id = uuid.uuid4().hex[:8]
        self.pending_prints[print_id] = {
            "file_id": file_id,
            "file_name": file_name,
            "file_size": file_size,
            "chat_id": chat_id,
            "time": now,
            "pages": "all",
            "copies": 1,
            "orientation": "portrait",
            "fit": "fit",
            "pos": "center"
        }

        # Formatta dimensione
        if file_size < 1024:
            size_str = f"{file_size} B"
        elif file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.1f} KB"
        else:
            size_str = f"{file_size / (1024 * 1024):.1f} MB"

        caption = message.get("caption", "").strip()
        caption_info = f"\n💬 *Didascalia:* _{caption}_" if caption else ""

        text = (
            "📄 *DOCUMENTO RICEVUTO*\n\n"
            f"📁 *Nome:* `{file_name}`\n"
            f"⚖️ *Dimensione:* `{size_str}`{caption_info}\n\n"
            "🖨️ *Desideri stampare questo file sulla HP LaserJet P1102w?*"
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🖨️ Stampa", "callback_data": f"print_exec:{print_id}"}
                ],
                [
                    {"text": "⚙️ Opzioni Avanzate", "callback_data": f"print_opt:{print_id}"},
                    {"text": "❌ Annulla", "callback_data": f"print_cancel:{print_id}"}
                ]
            ]
        }
        self.send_message(chat_id, text, reply_markup=keyboard)

    def handle_incoming_document_or_contact(self, message):
        """Gestisce file allegati (es. rubrica .vcf, documenti e foto da stampare) e contatti Telegram condivisi"""
        chat_id = message.get("chat", {}).get("id")
        if not chat_id or not self.user_manager.is_authorized(chat_id):
            return

        # 1. GESTIONE CONTATTO TELEGRAM CONDIVISO (Icona Graffetta -> Contatto)
        contact = message.get("contact")
        if contact:
            raw_phone = contact.get("phone_number", "")
            f_name = contact.get("first_name", "")
            l_name = contact.get("last_name", "")
            name = f"{f_name} {l_name}".strip() or "Contatto"
            
            if hasattr(self.tracker, "phonebook") and self.tracker.phonebook:
                ok = self.tracker.phonebook.add_contact(name, raw_phone)
                if ok:
                    self.send_message(chat_id, f"👤 *Contatto aggiunto alla rubrica!*\n\n• *Nome:* *{name}*\n• *Numero:* `{raw_phone}`")
                else:
                    self.send_message(chat_id, "⚠️ Numero di telefono non valido.")
            return

        # 2. GESTIONE FILE DOCUMENTO (vCard oppure Documenti da stampare: PDF, JPG, PNG)
        doc = message.get("document")
        if doc:
            raw_fname = doc.get("file_name", "documento")
            file_name = raw_fname.lower()
            mime_type = doc.get("mime_type", "").lower()

            # Rubrica vCard (.vcf)
            if file_name.endswith(".vcf") or file_name.endswith(".vcard") or "vcard" in mime_type:
                file_id = doc.get("file_id")
                self.send_message(chat_id, "⏳ _Download e importazione della rubrica in corso..._")
                
                try:
                    res = requests.get(f"{self.api_url}/getFile?file_id={file_id}", timeout=10)
                    if res.ok and res.json().get("ok"):
                        f_path = res.json()["result"]["file_path"]
                        download_url = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{f_path}"
                        f_resp = requests.get(download_url, timeout=20)
                        
                        if f_resp.ok:
                            try:
                                vcf_text = f_resp.content.decode("utf-8")
                            except UnicodeDecodeError:
                                vcf_text = f_resp.content.decode("latin-1", errors="ignore")

                            if hasattr(self.tracker, "phonebook") and self.tracker.phonebook:
                                c_cnt, n_cnt = self.tracker.phonebook.import_vcf_content(vcf_text)
                                stats = self.tracker.phonebook.get_stats()
                                
                                ok_msg = (
                                    "📇 *RUBRICA AGGIORNATA CON SUCCESSO!*\n\n"
                                    f"✅ *Importati dal file:* `{c_cnt}` contatti (`{n_cnt}` numeri)\n"
                                    f"📊 *Totale in rubrica:* `{stats['total_names']}` persone (`{stats['total_numbers']}` numeri)\n"
                                    f"📁 *File elaborato:* `{raw_fname}`\n\n"
                                    "💡 _Da questo momento, tutte le chiamate in arrivo sul telefono fisso, "
                                    "le chiamate perse e il comando /chiamate mostreranno direttamente il nome del contatto!_"
                                )
                                self.send_message(chat_id, ok_msg)
                            else:
                                self.send_message(chat_id, "❌ Modulo rubrica non disponibile sul server.")
                        else:
                            self.send_message(chat_id, f"❌ Errore scaricamento file da Telegram (HTTP {f_resp.status_code}).")
                    else:
                        self.send_message(chat_id, "❌ Impossibile recuperare il file dai server Telegram.")
                except Exception as e:
                    self.send_message(chat_id, f"❌ Errore durante l'elaborazione del file vCard: `{e}`")
                return

            # Documenti stampabili
            allowed_exts = [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".bmp"]
            is_printable = any(file_name.endswith(ext) for ext in allowed_exts) or "pdf" in mime_type or "image" in mime_type
            if not is_printable:
                self.send_message(
                    chat_id,
                    f"⚠️ *Formato non supportato per la stampa:* `{raw_fname}`\n"
                    "I formati supportati per la stampa diretta su HP LaserJet P1102w sono: **PDF, JPG, PNG**."
                )
                return

            file_id = doc.get("file_id")
            file_size = doc.get("file_size", 0)
            self._prompt_print_confirmation(chat_id, message, file_id, raw_fname, file_size)
            return

        # 3. GESTIONE FOTO DIRETTE (Fotocamera o Galleria inviata come immagine)
        photos = message.get("photo")
        if photos and isinstance(photos, list):
            best_photo = photos[-1]  # Massima risoluzione disponibile
            file_id = best_photo.get("file_id")
            file_size = best_photo.get("file_size", 0)
            file_name = f"foto_{int(time.time())}.jpg"
            self._prompt_print_confirmation(chat_id, message, file_id, file_name, file_size)
            return

    def handle_incoming_message(self, message):
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()
        from_user = message.get("from", {})
        
        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip() or f"User {chat_id}"
        username = from_user.get("username", "")

        user_info = {
            "name": full_name,
            "username": username,
            "chat_id": str(chat_id)
        }

        # Se presente un username Telegram valido, aggiornalo anche in user_manager se mancava
        cid_str = str(chat_id)
        if username and cid_str in self.user_manager.authorized_users:
            if self.user_manager.authorized_users[cid_str].get("username") != username:
                with self.user_manager.lock:
                    self.user_manager.authorized_users[cid_str]["username"] = username
                    self.user_manager._save_users_unlocked()

        # 1. UTENTE GIA' AUTORIZZATO
        if self.user_manager.is_authorized(chat_id):


            # Controllo se c'è un'azione pendente in attesa di conferma PIN
            p_act = self.pending_actions.get(chat_id)
            if p_act and time.time() < p_act["expires"]:
                self.pending_actions.pop(chat_id, None)
                expected_pin = getattr(config, "ACTION_PIN", config.BOT_PASSWORD)
                
                if config.verify_password(text) or text == getattr(config, "ACTION_PIN", ""):
                    self.send_message(chat_id, "⏳ _Autenticazione riuscita! Esecuzione del comando MQTT in corso..._")
                    ok, res_txt = self.tracker.send_device_command(p_act["device"], p_act["action"])
                    if hasattr(self.tracker, "event_logger") and self.tracker.event_logger:
                        user_tag = self._get_user_display_name(from_user, chat_id)
                        synthetic_action = self._format_command_action(p_act["device"], p_act["action"])
                        self.tracker.event_logger.log_event("command", user_tag, f"Eseguito comando {synthetic_action}")
                    if ok:
                        self.send_message(chat_id, f"✅ **COMANDO ESEGUITO CON SUCCESSO**\n\n{res_txt}")
                    else:
                        self.send_message(chat_id, f"❌ **ERRORE COMANDO MQTT**\n\n{res_txt}")
                else:
                    self.send_message(chat_id, "❌ **AUTENTICAZIONE FALLITA**\n\nPassword / PIN di sicurezza non corretto. Comando annullato per sicurezza.")
                return

            # Controllo se l'utente sta digitando le pagine di stampa in chat
            p_input = self.pending_page_inputs.get(chat_id)
            if p_input and (time.time() - p_input.get("time", 0) < 600):
                if text.startswith("/"):
                    self.pending_page_inputs.pop(chat_id, None)
                else:
                    print_id = p_input["print_id"]
                    item = self.pending_prints.get(print_id)
                    if not item:
                        self.pending_page_inputs.pop(chat_id, None)
                        self.send_message(chat_id, "⚠️ *Richiesta di stampa scaduta.* Invia nuovamente il documento.")
                        return

                    total_pages = self._ensure_total_pages(item)
                    import importlib, core.printer_manager
                    importlib.reload(core.printer_manager)
                    self.printer_mgr = core.printer_manager.PrinterManager()

                    is_valid, clean_spec, err_msg = self.printer_mgr.validate_pages_spec(text, total_pages)
                    if not is_valid:
                        self.send_message(
                            chat_id,
                            f"{err_msg}\n\n_Invia un nuovo valore o digita /annulla per uscire._"
                        )
                        return

                    item["pages"] = clean_spec
                    item["preview_idx"] = 0
                    self.pending_page_inputs.pop(chat_id, None)
                    disp_p = "Tutte le pagine 📑" if clean_spec == "all" else f"Pagina/e `{clean_spec}` 📄"
                    self.send_message(chat_id, f"✅ *Pagine di stampa configurate:* {disp_p}")
                    threading.Thread(target=self._render_or_update_preview_card, args=(chat_id, None, print_id, True), daemon=True).start()
                    return
            elif p_input:
                self.pending_page_inputs.pop(chat_id, None)

            # Controllo se l'utente sta digitando il numero di copie in chat
            c_input = self.pending_copies_inputs.get(chat_id)
            if c_input and (time.time() - c_input.get("time", 0) < 600):
                if text.startswith("/"):
                    self.pending_copies_inputs.pop(chat_id, None)
                else:
                    raw_c = text.strip()
                    if raw_c.isdigit() and 1 <= int(raw_c) <= 100:
                        count = int(raw_c)
                        print_id = c_input["print_id"]
                        item = self.pending_prints.get(print_id)
                        if item:
                            item["copies"] = count
                            self.pending_copies_inputs.pop(chat_id, None)
                            c_disp = f"{count} {'copia' if count == 1 else 'copie'}"
                            self.send_message(chat_id, f"✅ *Numero di copie impostato:* `{c_disp}` 🔢")
                            threading.Thread(target=self._render_or_update_preview_card, args=(chat_id, None, print_id, True), daemon=True).start()
                            return
                        else:
                            self.pending_copies_inputs.pop(chat_id, None)
                            self.send_message(chat_id, "⚠️ *Richiesta di stampa scaduta.* Invia nuovamente il documento.")
                            return
                    else:
                        self.send_message(
                            chat_id,
                            "⚠️ *Numero di copie non valido.*\n\n"
                            "Inserisci un numero intero compreso tra `1` e `100` (es. `2` o `5`).\n\n"
                            "_Oppure invia /annulla per uscire._"
                        )
                        return
            elif c_input:
                self.pending_copies_inputs.pop(chat_id, None)

            # Altrimenti esegui i comandi normali
            self.handle_command(chat_id, text)

        # 2. UTENTE IN ATTESA DI APPROVAZIONE (PENDING)
        elif self.user_manager.is_pending(chat_id):
            pending_msg = (
                "⏳ *RICHIESTA IN ATTESA DI APPROVAZIONE 2FA*\n\n"
                "Hai inserito la password corretta! La tua richiesta di registrazione è attualmente in attesa di approvazione da parte dell'amministratore sul Bot Admin.\n"
                "Riceverai una notifica automatica non appena confermata."
            )
            self.send_message(chat_id, pending_msg)

        # 3. UTENTE NON AUTORIZZATO
        else:
            # A. Controllo se il Chat ID è in Blacklist per scansione/bot automatico
            if self.user_manager.is_blacklisted(chat_id):
                print(f"[SECURITY SILENT DROP] Ignorato messaggio da Chat ID in Blacklist: {chat_id}")
                return

            # B. Controllo Rate Limit Anti-Scanner (Più di 3 comandi in 10s)
            if self.user_manager.check_unauthorized_rate_limit(chat_id):
                print(f"[SECURITY ALERT] Rilevato scanner bot automatico da Chat ID {chat_id}!")
                if self.admin_bot and self.admin_bot.admin_chat_id:
                    alert_text = (
                        "🚨 *ALLERTA SICUREZZA: RILEVATO SCANNER / BOT AUTOMATICO*\n\n"
                        f"🤖 *Utente Sospetto:* {full_name} (@{username or 'nessuno'})\n"
                        f"🆔 *Chat ID:* `{chat_id}`\n"
                        f"💬 *Comando Inviato:* `{text}`\n"
                        f"⚡ *Frequenza:* Più di 3 comandi in 10s (Attacco Scan)\n\n"
                        "🛡️ *Azione:* Chat ID inserito in Blacklist e silenziato."
                    )
                    self.admin_bot.send_message(self.admin_bot.admin_chat_id, alert_text)
                return

            # Caso A: L'utente invia la password esatta (Verifica SHA-256 + Salt in tempo costante)
            if config.verify_password(text):
                self.user_manager.set_pending(chat_id, user_info)
                
                # Password corretta! Invia la richiesta di approvazione 2FA (CON pulsanti Approva/Rifiuta) all'Admin Bot
                # NOTE: Il testo della password NON viene MAI notificato o inviato in chiaro!
                if self.admin_bot:
                    self.admin_bot.send_approval_request(chat_id, user_info)

                success_pwd_msg = (
                    "🔑 *PASSWORD CORRETTA!*\n\n"
                    "La tua richiesta di registrazione è stata inoltrata.\n"
                    "Riceverai una notifica su questa chat non appena la richiesta verrà confermata."
                )
                self.send_message(chat_id, success_pwd_msg)

            # Caso B: L'utente NON ha inviato la password -> Notifica l'avviso di tentativo all'Admin Bot
            else:
                # Segnala SILENZIOSAMENTE l'azione tentata da questo utente NON REGISTRATO solo all'Amministratore sul Bot Utils/Admin
                if self.admin_bot:
                    self.admin_bot.send_unauthorized_attempt_alert(chat_id, user_info, text)

                if text.lower() in ["/start", "/help", "/aiuto"]:
                    prompt_pwd_msg = (
                        "🔒 *ACCESSO RISERVATO - BOT DOMOTICA*\n\n"
                        "Benvenuto! Questo bot è ad accesso riservato e richiede autorizzazione.\n\n"
                        "🔑 Per richiedere l'accesso, invia la **password di registrazione**."
                    )
                    self.send_message(chat_id, prompt_pwd_msg)
                else:
                    generic_msg = "❌ Non sei autorizzato ad utilizzare questo bot."
                    self.send_message(chat_id, generic_msg)






    def handle_command(self, chat_id, text):
        text_clean = text.strip()
        cmd = text_clean.split()[0].lower() if text_clean else ""

        if cmd in ["/start", "/help", "/aiuto"]:
            welcome = (
                "🤖 *Bot Domotica MQTT - Controllo Dispositivi*\n\n"
                "Benvenuto! Sei autorizzato a controllare e comandare i dispositivi domotici.\n\n"
                "📌 *Comandi Rapidi:*\n"
                "🎮 /controllo - **Pannello di Controllo Dispositivi** (Accendi/Spegni con PIN)\n"
                "🍽️ /lavastoviglie - **Stato Lavastoviglie Bosch** (Aperta/Chiusa, Programma, Minuti)\n"
                "🔌 /shelly - **Stato Dispositivi Shelly** (Accesi 🟢 / Spenti 🔴)\n"
                "⚡ /consumi - **Consumi Elettrici** (Watt istantanei e grafico 24h)\n"
                "📊 /stato - **Dashboard Domotica Completa** (Shelly + Sensori + Altri device)\n"
                "🌡️ /clima - **Stato Dettagliato Clima & Termostato** (Daikin + BTicino)\n"
                "⚡ /powerstatus - **Stato Alimentazione Corrente & Home Server**\n"
                "🖥️ /serverstatus - **Salute e Diagnostica Hardware Home Server NVR**\n"
                "🌐 /networkstatus - **Stato Nodi di Rete** (Firewall, Home Server, AP Box)\n"
                "🛡️ /adblockerstats - **Statistiche AdGuardHome** (Query DNS e Blocco Ads)\n"
                "🔌 /linkspeed - **Velocità Link Fisici Ethernet** (Interfacce OpenWRT)\n"
                "🔌 /dorsale - **Parametri Fisici Cavo Dorsale** (Diagnostica bidirezionale Gateway + DGA Box)\n"
                "📊 /bandainuso - **Banda Passante Istantanea & Processi di Rete** (MB/s, KB/s)\n"
                "🚀 /iperf `[IP]` - **Test Banda Passante Reale** (Test iperf3 in Mbps/Gbps)\n"
                "📱 /dispositivi - **Stato Connettività Ping Dispositivi Domotici** (Casa & Garage)\n"
                "⚡ /speeds - **Velocità & Latenza di Comunicazione Dispositivi** (ms)\n"
                "📜 /logs - **Registro Eventi Domotici** (Storico allarmi, varchi, cams, comandi)\n"
                "🔔 /notifiche - **Personalizza le Notifiche Push** (Accendi/Spegni avvisi)\n"
                "📞 /chiamate `[perse/rifiutate/in/out]` - **Registro Chiamate Fisso** (Ultime chiamate WindTre)\n"
                "🌐 /linea - **Stato Linea Internet WAN** (Portante VDSL, Uptime sessione, DNS, Traffico)\n"
                "📖 /rubrica - **Rubrica Telefonica** (Cerca contatti o importa file .vcf da Android)\n"
                "🖨️ /stampante - **Stato Stampante HP LaserJet P1102w** (Toner, Pagine totali, Cartuccia)\n"
                "🌴 /vacanza - **Modalità Vacanza** (Report mattutino ore 09:00 & avvisi rete sempre attivi)\n"
                "❓ /aiuto - Mostra questa guida\n\n"
                "💡 _Tip: Invia qualsiasi documento (PDF, JPG, PNG) in questa chat per stamparlo direttamente sulla stampante!_"
            )
            self.send_message(chat_id, welcome)

        elif cmd in ["/logs", "/log", "/registro", "/storico", "/history"]:
            args = text_clean.split()[1:]
            cat_words = []
            limit = 10
            v_mode = "table"

            if args:
                for a in args:
                    if a.isdigit():
                        limit = max(5, min(30, int(a)))
                    elif a.lower() in ["table", "tabella", "grid"]:
                        v_mode = "table"
                    elif a.lower() in ["mobile", "cards", "elenco", "list"]:
                        v_mode = "list"
                    else:
                        cat_words.append(a.strip())

            cat_arg = " ".join(cat_words).strip() if cat_words else None

            report, total_pages, curr_p = self.tracker.event_logger.format_logs_for_telegram(limit=limit, category=cat_arg, page=0, view_mode=v_mode)
            reply_markup = self._build_logs_keyboard(current_category=cat_arg or "all", current_page=curr_p, total_pages=total_pages, view_mode=v_mode)
            self.send_message(chat_id, report, reply_markup=reply_markup)

        elif cmd in ["/stampante", "/printer", "/hp"]:
            self.send_message(chat_id, "⏳ Interrogazione stampante in corso...")
            import importlib, core.printer_manager
            importlib.reload(core.printer_manager)
            self.printer_mgr = core.printer_manager.PrinterManager()
            status_msg = self.printer_mgr.get_printer_status()
            kb = {
                "inline_keyboard": [
                    [{"text": "📜 Registro Errori Hardware", "callback_data": "printer_err_log"}]
                ]
            }
            self.send_message(chat_id, status_msg, reply_markup=kb)

        elif cmd in ["/stampante_log", "/printer_log", "/hp_log", "/errori_stampante"]:
            self.send_message(chat_id, "⏳ Lettura registro hardware della stampante...")
            import importlib, core.printer_manager
            importlib.reload(core.printer_manager)
            self.printer_mgr = core.printer_manager.PrinterManager()
            err_msg = self.printer_mgr.format_error_log_message()
            self.send_message(chat_id, err_msg)

        elif cmd in ["/consumi", "/consumo", "/energia", "/watt", "/powerconsumption"]:
            self.send_message(chat_id, "⏳ _Recupero dati consumi Casa e Garage in corso..._")

            # 1. Dati Casa (PowerMeterHome.shelly)
            casa_p, casa_v, casa_i, casa_e = None, None, None, None
            with self.tracker.lock:
                for s_id, s_data in self.tracker.shelly_devices.items():
                    if "powermeterhome" in s_id.lower():
                        ch0 = s_data.get("channels", {}).get("0", {})
                        casa_p = ch0.get("power")
                        casa_v = ch0.get("voltage")
                        casa_i = ch0.get("current")
                        casa_e = ch0.get("energy_kwh")
                        break

            # Fallback dal database per Casa se non in RAM
            if (casa_p is None or casa_v is None) and self.tracker.event_logger:
                hist_casa = self.tracker.event_logger.get_power_history(hours=24, device="PowerMeterHome.shelly")
                if hist_casa:
                    last_c = hist_casa[-1]
                    try:
                        if casa_p is None and last_c[1] is not None: casa_p = float(last_c[1])
                        if casa_v is None and len(last_c) > 2 and last_c[2] is not None: casa_v = float(last_c[2])
                        if casa_i is None and len(last_c) > 3 and last_c[3] is not None: casa_i = float(last_c[3])
                        if casa_e is None and len(last_c) > 4 and last_c[4] is not None: casa_e = float(last_c[4])
                    except Exception:
                        pass

            # 2. Dati Garage (PowerMeterBox.shelly)
            garage_p, garage_v, garage_i, garage_e = None, None, None, None
            with self.tracker.lock:
                for s_id, s_data in self.tracker.shelly_devices.items():
                    if "powermeterbox" in s_id.lower():
                        ch0 = s_data.get("channels", {}).get("0", {})
                        garage_p = ch0.get("power")
                        garage_v = ch0.get("voltage")
                        garage_i = ch0.get("current")
                        garage_e = ch0.get("energy_kwh")
                        break

            # Fallback dal database per Garage se non in RAM
            if (garage_p is None or garage_v is None) and self.tracker.event_logger:
                hist_garage = self.tracker.event_logger.get_power_history(hours=24, device="PowerMeterBox.shelly")
                if hist_garage:
                    last_g = hist_garage[-1]
                    try:
                        if garage_p is None and last_g[1] is not None: garage_p = float(last_g[1])
                        if garage_v is None and len(last_g) > 2 and last_g[2] is not None: garage_v = float(last_g[2])
                        if garage_i is None and len(last_g) > 3 and last_g[3] is not None: garage_i = float(last_g[3])
                        if garage_e is None and len(last_g) > 4 and last_g[4] is not None: garage_e = float(last_g[4])
                    except Exception:
                        pass

            # Formattazione stringhe
            c_p_str = f"*{float(casa_p):.1f} W*" if casa_p is not None else "_N/D_"
            c_v_str = f"*{float(casa_v):.1f} V*" if casa_v is not None else "_N/D_"
            c_i_str = f"*{float(casa_i):.2f} A*" if casa_i is not None else "_N/D_"
            c_e_str = f"*{float(casa_e):.2f} kWh*" if casa_e is not None else "_N/D_"

            g_p_str = f"*{float(garage_p):.1f} W*" if garage_p is not None else "_N/D_"
            g_v_str = f"*{float(garage_v):.1f} V*" if garage_v is not None else "_N/D_"
            g_i_str = f"*{float(garage_i):.2f} A*" if garage_i is not None else "_N/D_"
            g_e_str = f"*{float(garage_e):.2f} kWh*" if garage_e is not None else "_N/D_"

            txt_msg = (
                "⚡ *CONSUMI ELETTRICI IN TEMPO REALE*\n\n"
                "🏠 *Casa*\n"
                f"• Potenza Attiva: {c_p_str}\n"
                f"• Tensione: {c_v_str}\n"
                f"• Corrente: {c_i_str}\n"
                f"• Energia Totale: {c_e_str}\n\n"
                "🚗 *Garage*\n"
                f"• Potenza Attiva: {g_p_str}\n"
                f"• Tensione: {g_v_str}\n"
                f"• Corrente: {g_i_str}\n"
                f"• Energia Totale: {g_e_str}\n\n"
                "_Seleziona un'area per visualizzare il grafico storico:_"
            )


            sel_keyboard = {
                "inline_keyboard": [[
                    {"text": "🏠 Grafico Casa", "callback_data": "consumi_area:casa"},
                    {"text": "🚗 Grafico Garage", "callback_data": "consumi_area:garage"}
                ]]
            }
            self.send_message(chat_id, txt_msg, reply_markup=sel_keyboard)

        elif cmd in ["/lavastoviglie", "/bosch", "/dishwasher"]:
            b_data = getattr(self.tracker, "bosch_data", None)
            if b_data:
                pow_raw = b_data.get('power', '').lower()
                power = "Accesa 🟢" if pow_raw == "on" else "Spenta 🔴" if pow_raw == "off" else pow_raw
                
                door_raw = b_data.get('door', '').lower()
                door = "Chiusa 🔒" if door_raw == "closed" else "Aperta 🔓" if door_raw == "open" else door_raw
                
                op_raw = b_data.get('operation_state', '').lower()
                op_state = "Inattiva 💤" if op_raw == "inactive" else "In Funzione ⚙️" if op_raw == "run" else "Terminato ✅" if op_raw == "finished" else "Pronta ⏳" if op_raw == "ready" else op_raw
                
                state_txt = (
                    "🍽️ *STATO LAVASTOVIGLIE BOSCH*\n\n"
                    f"🔌 *Alimentazione:* {power}\n"
                    f"🚪 *Porta:* {door}\n"
                    f"⚙️ *Stato Operativo:* {op_state}\n"
                )
                if b_data.get('program'):
                    prog = b_data['program'].replace('BSH.Common.Root.ActiveProgram.', '')
                    if "Eco50" in prog: prog = "Eco 50°C 🌱"
                    elif "Auto2" in prog: prog = "Auto 45-65°C 🤖"
                    elif "Intensiv70" in prog: prog = "Intensivo 70°C 🍳"
                    elif "Quick65" in prog: prog = "Rapido 65°C ⏱️"
                    elif "Quick45" in prog: prog = "Rapido 45°C ⏱️"
                    state_txt += f"🔄 *Programma Attivo:* {prog}\n"
                    
                time_rem = b_data.get('remaining_time')
                if time_rem:
                    total_mins = int(time_rem) // 60
                    if total_mins >= 60:
                        ore = total_mins // 60
                        minuti = total_mins % 60
                        ore_str = "ora" if ore == 1 else "ore"
                        if minuti == 0:
                            state_txt += f"⏳ *Tempo Rimanente:* {ore} {ore_str}\n"
                        else:
                            minuti_str = "minuto" if minuti == 1 else "minuti"
                            state_txt += f"⏳ *Tempo Rimanente:* {ore} {ore_str} e {minuti} {minuti_str}\n"
                    else:
                        minuti_str = "minuto" if total_mins == 1 else "minuti"
                        state_txt += f"⏳ *Tempo Rimanente:* {total_mins} {minuti_str}\n"
                    
                prog_pct = b_data.get('progress')
                if prog_pct:
                    state_txt += f"📊 *Progresso:* {prog_pct}%\n"
            else:
                state_txt = "🍽️ *LAVASTOVIGLIE BOSCH*\n\nNessun dato attualmente ricevuto dal cloud (Lavastoviglie disconnessa o spenta da molto tempo)."
                
            keyboard = {"inline_keyboard": [[{"text": "🔄 Aggiorna", "callback_data": "bosch_status"}]]}
            self.send_message(chat_id, state_txt, reply_markup=keyboard)

        elif cmd in ["/controllo", "/control", "/cmd", "/comandi", "/interagisci"]:


            text_ctrl = (
                "🎮 *PANNELLO DI CONTROLLO DISPOSITIVI DOMOTICI*\n\n"
                "Seleziona il dispositivo ed il comando che desideri inviare.\n"
                "🔒 *Per sicurezza, ogni singola azione richiederà la conferma tramite PIN/Password.*"
            )
            reply_markup = self._build_control_keyboard()
            self.send_message(chat_id, text_ctrl, reply_markup=reply_markup)

        elif cmd in ["/shelly", "/shellies"]:
            self.send_message(chat_id, "⏳ _Recupero stato dei dispositivi Shelly in corso..._")
            report = self.tracker.get_shelly_report()
            self.send_message(chat_id, report)

        elif cmd in ["/stato", "/status", "/dashboard"]:
            self.send_message(chat_id, "⏳ _Generazione Dashboard Domotica MQTT in corso..._")
            report = self.tracker.get_summary_report(include_network_and_anomalies=False)
            self.send_message(chat_id, report)

        elif cmd in ["/clima", "/climate", "/termostato", "/daikin"]:
            self.send_message(chat_id, "⏳ _Recupero informazioni dettagliate clima e termoregolazione in corso..._")
            report = self.tracker.get_climate_report()
            self.send_message(chat_id, report)
            if hasattr(self.tracker, "event_logger") and self.tracker.event_logger:
                chart_path = self.tracker.event_logger.generate_climate_chart(hours=24, tracker=self.tracker)
                if chart_path and os.path.exists(chart_path):
                    self.send_photo(chat_id, chart_path)


        elif cmd in ["/powerstatus", "/power", "/corrente", "/blackout"]:
            self.send_message(chat_id, "⏳ _Verifica stato alimentazione ed heartbeat Home Server in corso..._")
            report = self.tracker.get_power_status_report()
            self.send_message(chat_id, report)

        elif cmd in ["/cameras", "/camera", "/telecamere", "/cams", "/cam"]:
            self.send_message(chat_id, "⏳ _Verifica stato in tempo reale delle 3 telecamere NVR in corso..._")
            report = self.tracker.get_cameras_status_report()
            self.send_message(chat_id, report)

        elif cmd in ["/networkstatus", "/netstatus", "/rete", "/network"]:
            self.send_message(chat_id, "⏳ _Verifica stato nodi ed infrastruttura di rete in corso (ping)..._")
            report = self.tracker.get_network_status_report()
            self.send_message(chat_id, report)
            
        elif cmd in ["/adblockerstats", "/adguard", "/adblock"]:
            self.send_message(chat_id, "⏳ _Recupero statistiche di blocco DNS da AdGuardHome..._")
            report = self.tracker.get_adguard_report()
            self.send_message(chat_id, report)

        elif cmd in ["/serverstatus", "/server", "/salute", "/health"]:
            args = text_clean.split()[1:]
            show_usb = any(a.lower() in ["mem", "usb", "full"] for a in args)

            self.send_message(chat_id, "⏳ _Recupero diagnostica e parametri vitali dell'Home Server NVR..._")
            report = self.tracker.get_server_health_report()
            self.send_message(chat_id, report)
            if hasattr(self.tracker, "event_logger") and self.tracker.event_logger:
                c_cpu, c_ram, c_temp, c_swap, c_usb = self.tracker._get_raw_server_metrics()
                chart_path = self.tracker.event_logger.generate_server_chart(hours=24, current_metrics=(c_cpu, c_ram, c_temp))
                if chart_path and os.path.exists(chart_path):
                    self.send_photo(chat_id, chart_path)
                
                if show_usb:
                    usb_chart_path = self.tracker.event_logger.generate_usb_chart(hours=24)
                    if usb_chart_path and os.path.exists(usb_chart_path):
                        self.send_photo(chat_id, usb_chart_path)
        elif cmd == "/wh":
            import subprocess
            args = text_clean.split()[1:]
            
            aliases_path = os.path.join(os.path.dirname(__file__), 'aliases.json')
            aliases = {}
            if os.path.exists(aliases_path):
                try:
                    with open(aliases_path, 'r', encoding='utf-8') as f:
                        aliases = json.load(f)
                except Exception:
                    pass
            
            active_macs = []
            pm = getattr(self, "presence_manager", None) or (self.tracker and getattr(self.tracker, "presence_manager", None))
            if pm:
                active_macs = [m.lower().strip() for m in pm.get_active_macs(max_age=15)]
            else:
                try:
                    # Selezioniamo i MAC da verificare (tutti o solo quello richiesto)
                    macs_to_check = []
                    if not args:
                        macs_to_check = [m.lower().strip() for m in aliases.values()]
                    else:
                        target_alias = args[0].lower()
                        for a, m in aliases.items():
                            if a.lower() == target_alias:
                                macs_to_check.append(m.lower().strip())
                                break

                    if macs_to_check:
                        mac_regex = "|".join(macs_to_check)
                        
                        # Lo script bash sul router:
                        # 1. Trova gli IP associati ai MAC che ci interessano.
                        # 2. Cancella forzatamente le vecchie cache ARP per quegli IP.
                        # 3. Lancia un ping per forzare la rete a cercare di nuovo quei dispositivi.
                        # 4. Attende 2 secondi e stampa la tabella ARP aggiornata.
                        sh_script = (
                            f"ips=$(cat /proc/net/arp | grep -iE '{mac_regex}' | awk '{{print $1}}'); "
                            "for ip in $ips; do ip neigh flush to $ip 2>/dev/null || arp -d $ip 2>/dev/null; ping -c 1 $ip >/dev/null 2>&1 & done; "
                            "sleep 2; "
                            "cat /proc/net/arp"
                        )

                        arp_output = subprocess.check_output(
                            ['ssh', '-i', getattr(config, 'OPENWRT_SSH_KEY', '/root/.ssh/id_openwrt'), '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=3', '-o', 'BatchMode=yes', 'root@192.168.1.1', sh_script],
                            stderr=subprocess.DEVNULL, timeout=6
                        ).decode('utf-8').lower()

                        for line in arp_output.split('\n'):
                            parts = line.split()
                            # Se è 0x0 significa che il ping non ha risvegliato l'ARP (dispositivo disconnesso)
                            if len(parts) >= 4 and parts[2] != "0x0" and parts[2] != "00":
                                active_macs.append(parts[3])

                except Exception as e:
                    print(f"[!] Errore Active ARP scan: {e}")
                    pass

            msg_lines = []
            if not args:
                if aliases:
                    for alias, mac in aliases.items():
                        if mac.lower().strip() in active_macs:
                            msg_lines.append(f"{alias} 🟢")
                        else:
                            msg_lines.append(f"{alias} 🔴")
            else:
                target_alias = args[0].lower()
                target_mac = None
                for a, m in aliases.items():
                    if a.lower() == target_alias:
                        target_mac = m.lower().strip()
                        break
                
                if target_mac:
                    if target_mac in active_macs:
                        msg_lines.append(f"{target_alias} 🟢")
                    else:
                        msg_lines.append(f"{target_alias} 🔴")

            if msg_lines:
                self.send_message(chat_id, "\n".join(msg_lines))

        elif cmd in ["/notifiche", "/notif", "/settings", "/pref"]:
            text_notif = (
                "🔔 *GESTIONE PREFERENZE NOTIFICHE TELEGRAM*\n\n"
                "Seleziona le notifiche push che desideri ricevere in tempo reale sul tuo dispositivo.\n"
                "Clicca sui pulsanti per **attivare (✅)** o **disattivare (❌)** le singole categorie:"
            )
            reply_markup = self._build_notifications_keyboard(chat_id)
            self.send_message(chat_id, text_notif, reply_markup=reply_markup)

        elif cmd in ["/dispositivi", "/devices", "/devs"]:
            self.send_message(chat_id, "⏳ _Verifica connettività ping dei dispositivi domotici in corso..._")
            report = self.tracker.get_devices_ping_report()
            self.send_message(chat_id, report)

        elif cmd in ["/speeds", "/speed", "/velocita", "/latenza", "/pingtime"]:
            self.send_message(chat_id, "⏳ _Misurazione velocità e latenza di comunicazione dispositivi in corso..._")
            report = self.tracker.get_device_speeds_report()
            self.send_message(chat_id, report)

        elif cmd in ["/linkspeed", "/link", "/interfacce", "/links"]:
            self.send_message(chat_id, "⏳ _Lettura velocità dei link fisici delle interfacce Ethernet OpenWRT..._")
            report = self.tracker.get_openwrt_link_speeds_report()
            self.send_message(chat_id, report)

        elif cmd in ["/dorsale", "/cavo", "/cavodorsale", "/cable", "/dga", "/linkbox"]:
            self.send_message(chat_id, "⏳ _Interrogazione router per diagnostica parametri fisici cavo dorsale..._")

            def _do_cable():
                try:
                    report = self.tracker.get_backbone_cable_report()
                    cable_kb = {
                        "inline_keyboard": [
                            [{"text": "🔄 Aggiorna Parametri", "callback_data": "cable_refresh"}],
                            [{"text": "🚀 Test Velocità iperf3 (Box)", "callback_data": "iperf_target:192.168.3.2"}]
                        ]
                    }
                    self.send_message(chat_id, report, reply_markup=cable_kb)
                except Exception as e_cable:
                    print(f"[!] Errore _do_cable: {e_cable}")
                    self.send_message(chat_id, f"⚠️ *Errore diagnostica dorsale*: `{e_cable}`")

            threading.Thread(target=_do_cable, daemon=True).start()

        elif cmd in ["/iperf", "/speedtest"]:
            parts = text_clean.split()
            if len(parts) > 1:
                target_ip = parts[1].strip()
                def _do_iperf():
                    self.send_message(chat_id, f"⏳ _Esecuzione test banda passante reale iperf3 verso_ `{target_ip}` _(5s)..._")
                    report = self.tracker.run_iperf_speedtest(target_ip=target_ip, duration=5)
                    self.send_message(chat_id, report)
                threading.Thread(target=_do_iperf, daemon=True).start()
            else:
                iperf_kb = {
                    "inline_keyboard": [
                        [{"text": "🛡️ Gateway (192.168.1.1)", "callback_data": "iperf_target:192.168.1.1"}],
                        [{"text": "📶 Access Point Box (192.168.3.2)", "callback_data": "iperf_target:192.168.3.2"}],
                        [{"text": "❌ Annulla", "callback_data": "iperf_cancel"}]
                    ]
                }
                msg_txt = (
                    "🚀 *TEST BANDA PASSANTE REALE (iperf3)*\n\n"
                    "Seleziona il dispositivo di destinazione verso cui effettuare il test di velocità, oppure scrivi `/iperf <IP>`:"
                )
                self.send_message(chat_id, msg_txt, reply_markup=iperf_kb)

        elif cmd in ["/bandainuso", "/banda", "/traffico", "/traffic", "/livebanda", "/consumo"]:
            self.send_message(chat_id, "⏳ _Calcolo della banda passante istantanea in uso e scansione processi in corso..._")
            report = self.tracker.get_live_bandwidth_report(sample_sec=1.0)
            self.send_message(chat_id, report)

        elif cmd in ["/chiamate", "/calls", "/voip", "/telefono"]:
            args = text_clean.split()[1:]
            limit = 10
            filter_type = None

            for a in args:
                if a.isdigit():
                    limit = max(1, min(50, int(a)))
                elif a.lower() in ["perse", "persa", "missed"]:
                    filter_type = "persa"
                elif a.lower() in ["rifiutate", "rifiutata", "rifiutato", "rejected", "declined"]:
                    filter_type = "rifiutata"
                elif a.lower() in ["ricevute", "ricevuta", "entranti", "entrante", "in"]:
                    filter_type = "entrante"
                elif a.lower() in ["uscenti", "uscente", "effettuate", "out"]:
                    filter_type = "uscente"

            self.send_message(chat_id, "⏳ <i>Recupero registro chiamate dal modem ZTE in corso...</i>")
            report = self.tracker.get_voip_calls_report(limit=limit, filter_type=filter_type)
            self.send_message(chat_id, report)

        elif cmd in ["/linea", "/wan", "/vdsl", "/adsl", "/statistiche_linea"]:
            self.send_message(chat_id, "⏳ <i>Interrogazione statistiche di linea WAN dal modem ZTE...</i>")
            report = self.tracker.get_wan_statistics_report()
            self.send_message(chat_id, report)

        elif cmd in ["/rubrica", "/contatti", "/phonebook"]:
            args = text_clean.split()[1:]
            if not hasattr(self.tracker, "phonebook") or not self.tracker.phonebook:
                self.send_message(chat_id, "❌ <b>Modulo Rubrica non inizializzato nel server.</b>")
                return

            if not args:
                stats = self.tracker.phonebook.get_stats()
                names_cnt = stats["total_names"]
                nums_cnt = stats["total_numbers"]
                msg = (
                    "📖 <b>RUBRICA TELEFONICA DOMOTICA</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>Contatti memorizzati:</b> <code>{names_cnt}</code> persone (<code>{nums_cnt}</code> numeri)\n\n"
                    "📲 <b>Come importare o aggiornare i contatti:</b>\n"
                    "1. Apri l'app <b>Contatti</b> sul tuo smartphone Android\n"
                    "2. Tocca <i>Correggi e gestisci</i> (o <i>Impostazioni</i>) ➔ <b>Esporta in file</b>\n"
                    "3. Invia il file <code>.vcf</code> (vCard) direttamente in questa chat!\n\n"
                    "📎 <i>Puoi anche condividere singoli contatti toccando l'icona graffetta ➔ Contatto.</i>\n\n"
                    "🛠 <b>Comandi Rapidi Rubrica:</b>\n"
                    "• <code>/rubrica cerca &lt;nome o numero&gt;</code> - Cerca un contatto\n"
                    "• <code>/rubrica aggiungi &lt;numero&gt; &lt;nome&gt;</code> - Salva o aggiorna un contatto\n"
                    "• <code>/rubrica elimina &lt;numero o nome&gt;</code> - Elimina un contatto"
                )
                self.send_message(chat_id, msg)

            elif args[0].lower() in ["cerca", "search", "trova"]:
                query = " ".join(args[1:]).strip()
                if not query:
                    self.send_message(chat_id, "⚠️ <b>Specifica un nome o un numero da cercare:</b>\nEs: <code>/rubrica cerca Mario</code>")
                    return
                results = self.tracker.phonebook.search(query)
                if not results:
                    self.send_message(chat_id, f"🔍 Nessun contatto trovato per \"<code>{html.escape(query)}</code>\".")
                else:
                    lines = [
                        f"🔍 <b>Risultati ricerca per</b> \"<code>{html.escape(query)}</code>\" ({len(results)} trovati):",
                        "━━━━━━━━━━━━━━━━━━━━"
                    ]
                    for name, nums in results[:15]:
                        clean_n = html.escape(self.tracker.phonebook.clean_name(name))
                        num_list = ", ".join(f"<code>{self.tracker.phonebook.format_phone(n)}</code>" for n in nums)
                        lines.append(f"👤 <b>{clean_n}</b>\n   📞 {num_list}\n")
                    self.send_message(chat_id, "\n".join(lines))

            elif args[0].lower() in ["aggiungi", "add", "nuovo"]:
                if len(args) < 3:
                    self.send_message(chat_id, "⚠️ <b>Formato non corretto!</b>\nUsa: <code>/rubrica aggiungi &lt;numero&gt; &lt;Nome e Cognome&gt;</code>\nEs: <code>/rubrica aggiungi 3391234567 Mario Rossi</code>")
                    return
                num = args[1]
                name = " ".join(args[2:]).strip()
                ok = self.tracker.phonebook.add_contact(name, num)
                if ok:
                    clean_n = html.escape(self.tracker.phonebook.clean_name(name))
                    fmt_num = self.tracker.phonebook.format_phone(num)
                    self.send_message(chat_id, f"✅ <b>Contatto salvato in rubrica!</b>\n━━━━━━━━━━━━━━━━━━━━\n👤 <b>Nome:</b> {clean_n}\n📞 <b>Numero:</b> <code>{fmt_num}</code>")
                else:
                    self.send_message(chat_id, "❌ Impossibile salvare il contatto. Verifica che il numero contenga cifre valide.")

            elif args[0].lower() in ["elimina", "cancella", "del", "remove"]:
                target = " ".join(args[1:]).strip()
                if not target:
                    self.send_message(chat_id, "⚠️ <b>Specifica il nome o il numero da eliminare:</b>\n<code>/rubrica elimina 3391234567</code>")
                    return
                ok = self.tracker.phonebook.remove_contact(target)
                if ok:
                    self.send_message(chat_id, f"🗑 <b>Contatto \"{html.escape(target)}\" eliminato dalla rubrica.</b>")
                else:
                    self.send_message(chat_id, f"⚠️ Nessun contatto corrispondente a \"{html.escape(target)}\" trovato in rubrica.")

            else:
                # Ricerca diretta per parola chiave
                query = " ".join(args).strip()
                results = self.tracker.phonebook.search(query)
                if results:
                    lines = [
                        f"🔍 <b>Risultati per</b> \"<code>{html.escape(query)}</code>\":",
                        "━━━━━━━━━━━━━━━━━━━━"
                    ]
                    for name, nums in results[:15]:
                        clean_n = html.escape(self.tracker.phonebook.clean_name(name))
                        num_list = ", ".join(f"<code>{self.tracker.phonebook.format_phone(n)}</code>" for n in nums)
                        lines.append(f"👤 <b>{clean_n}</b>\n   📞 {num_list}\n")
                    self.send_message(chat_id, "\n".join(lines))
                else:
                    self.send_message(chat_id, f"🔍 Nessun contatto trovato per \"<code>{html.escape(query)}</code>\".\nUsa <code>/rubrica</code> per la guida.")

        elif cmd in ["/vacanza", "/vacanzamode", "/holiday", "/vacation", "/ferie"]:
            is_active = self.tracker.is_vacation_mode()
            report_time = getattr(config, "VACATION_REPORT_TIME", "09:00")
            st_str = "🟢 **ATTIVA**" if is_active else "🔴 **DISATTIVATA**"
            
            vac_text = (
                f"🌴 *GESTIONE MODALITÀ VACANZA*\n\n"
                f"Stato Attuale: {st_str}\n\n"
                f"📋 *Funzionalità della Modalità Vacanza:*\n"
                f"• 🚨 **Notifiche Rete**: Qualsiasi disconnessione o ripristino di dispositivi/telecamere di rete verrà *SEMPRE* notificato su Telegram.\n"
                f"• ☀️ **Resoconto Mattutino**: Ogni mattina alle `{report_time}` riceverai il resoconto completo dello stato della casa.\n\n"
                f"Seleziona l'azione desiderata:"
            )
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "🌴 Attiva Modalità Vacanza", "callback_data": "ask_vacation:on"},
                        {"text": "🏠 Disattiva Modalità Vacanza", "callback_data": "ask_vacation:off"}
                    ]
                ]
            }
            self.send_message(chat_id, vac_text, reply_markup=keyboard)

        else:
            self.send_message(
                chat_id,
                f"⚠️ Comando non riconosciuto: `{text_clean}`\nUsa `/stato` per la lista dei dispositivi o `/aiuto` per la guida."
            )


    def notify_device_event(self, category, text, reply_markup=None):
        """Invia notifica ai soli utenti iscritti alla categoria specificata in modo asincrono"""
        def _task():
            nonlocal category, text
            # Controllo difensivo: se gli argomenti arrivano invertiti (text al posto di category)
            if isinstance(category, str) and ("\n" in category or len(category) > 50):
                category, text = text, category

            subscribers = self.user_manager.get_subscribed_users(category)

            # Se Modalità Vacanza è attiva, garantisce l'invio al destinatario VACATION_REPORT_CHAT_ID
            if self.tracker.is_vacation_mode():
                target_vac = getattr(config, "VACATION_REPORT_CHAT_ID", getattr(config, "TELEGRAM_CHAT_ID", None))
                if target_vac and str(target_vac) not in [str(s) for s in subscribers]:
                    subscribers.append(str(target_vac))

            print(f"[BROADCAST] Notifica '{category}' a {len(subscribers)} utenti: {subscribers}")
            for cid in subscribers:
                self.send_message(cid, text, reply_markup=reply_markup)
                
        threading.Thread(target=_task, daemon=True).start()


    def start_polling(self):
        self.tracker.set_notification_callback(self.notify_device_event)
        self.tracker.start_network_watchdog(notify_callback=self.notify_device_event)

        # Avvio del watchdog proattivo per la stampante HP (rileva errori da PC, Smartphone, rete o bot)
        if hasattr(self, "printer_mgr") and self.printer_mgr:
            self.printer_mgr.start_printer_watchdog(
                notify_callback=self.notify_device_event,
                interval=12,
                event_logger=getattr(self.tracker, "event_logger", None)
            )

        # Scheduler per il report mattutino in Modalità Vacanza
        def _vacation_scheduler_loop():
            print("[+] Avvio Scheduler Report Mattutino Vacanza...")
            last_sent_date = ""
            while True:
                try:
                    now = datetime.now()
                    target_time_str = getattr(config, "VACATION_REPORT_TIME", "09:00")
                    current_time_str = now.strftime("%H:%M")
                    current_date_str = now.strftime("%Y-%m-%d")

                    if self.tracker.is_vacation_mode() and current_time_str == target_time_str:
                        if last_sent_date != current_date_str:
                            last_sent_date = current_date_str
                            # Recupera gli utenti che hanno attiva la notifica "vacation_report"
                            recipients = self.user_manager.get_subscribed_users("vacation_report")
                            print(f"[VACATION SCHEDULER] Invio resoconto mattutino delle {current_time_str} a {len(recipients)} utenti iscritti: {recipients}...")
                            if recipients:
                                summary_text = f"🌴 *RESOCONTO GIORNALIERO MATTUTINO (MODALITÀ VACANZA)*\n\n" + self.tracker.get_summary_report(include_network_and_anomalies=True)
                                for cid in recipients:
                                    self.send_message(cid, summary_text)
                                    if hasattr(self.tracker, "event_logger") and self.tracker.event_logger:
                                        chart_path = self.tracker.event_logger.generate_climate_chart(hours=24, tracker=self.tracker)
                                        if chart_path and os.path.exists(chart_path):
                                            self.send_photo(cid, chart_path)

                    time.sleep(30)
                    
                except Exception as e:
                    print(f"[!] Errore nello scheduler vacanza: {e}")
                    time.sleep(30)

        t_vac = threading.Thread(target=_vacation_scheduler_loop, daemon=True, name="VacationSchedulerThread")
        t_vac.start()

        
        while True:
            try:
                url = f"{self.api_url}/getUpdates"
                params = {"offset": self.last_update_id + 1, "timeout": 20}
                resp = requests.get(url, params=params, timeout=25)

                if resp.ok:
                    data = resp.json()
                    if data.get("ok"):
                        for update in data.get("result", []):
                            self.last_update_id = update["update_id"]
                            
                            # Gestione Messaggi di Testo e Allegati (Async Threading)
                            message = update.get("message")
                            if message:
                                if "text" in message:
                                    chat_id = message["chat"]["id"]
                                    text = message["text"]
                                    print(f"[Telegram Main] Messaggio da Chat {chat_id}: {text}")
                                    threading.Thread(target=self.handle_incoming_message, args=(message,), daemon=True).start()
                                elif "document" in message or "contact" in message or "photo" in message:
                                    threading.Thread(target=self.handle_incoming_document_or_contact, args=(message,), daemon=True).start()

                            # Gestione Click Pulsanti InLinea (Async Threading)
                            cb = update.get("callback_query")
                            if cb:
                                print(f"[Telegram Main] CallbackQuery: {cb.get('data')}")
                                threading.Thread(target=self.handle_callback_query, args=(cb,), daemon=True).start()
                else:
                    print(f"[!] Errore API Telegram HTTP {resp.status_code}")
                    time.sleep(5)
            except Exception as e:
                print(f"[!] Errore di rete durante il polling Telegram: {e}")
                time.sleep(5)
