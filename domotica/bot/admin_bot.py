import os
import json
import time
import requests
import threading
import config

class AdminBot:
    def __init__(self, user_manager, main_bot=None):
        self.user_manager = user_manager
        self.main_bot = main_bot
        self.token = config.ADMIN_BOT_TOKEN
        self.admin_chat_id = config.ADMIN_CHAT_ID
        self.api_url = f"https://api.telegram.org/bot{self.token}" if self.token else None
        self.last_update_id = 0

    def set_main_bot(self, main_bot):
        self.main_bot = main_bot

    def is_configured(self):
        return bool(self.token and self.token != "INSERISCI_QUI_IL_TUO_TOKEN_ADMIN_BOT")

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="Markdown"):
        if not self.is_configured():
            print("[AdminBot] Token non configurato. Impossibile inviare il messaggio.")
            return False

        url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if not resp.ok:
                payload.pop("parse_mode", None)
                resp = requests.post(url, json=payload, timeout=10)
            return resp.ok
        except Exception as e:
            print(f"[!] Errore invio messaggio su Admin Bot: {e}")
            return False

    def edit_message_text(self, chat_id, message_id, text, parse_mode="Markdown"):
        if not self.is_configured():
            return False
        url = f"{self.api_url}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            return resp.ok
        except Exception as e:
            print(f"[!] Errore modifica messaggio su Admin Bot: {e}")
            return False

    def answer_callback_query(self, callback_query_id, text=""):
        if not self.is_configured():
            return False
        url = f"{self.api_url}/answerCallbackQuery"
        payload = {
            "callback_query_id": callback_query_id,
            "text": text
        }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception:
            pass

    def send_approval_request(self, target_chat_id, user_info):
        """
        Invia una notifica 2FA all'Amministratore con pulsanti inline [Approva] e [Rifiuta]
        """
        target_cid = str(target_chat_id)
        name = user_info.get("name", "Utente Sconosciuto")
        username = user_info.get("username", "nessuno")

        text = (
            "🔐 *NUOVA RICHIESTA REGISTRAZIONE 2FA*\n\n"
            f"👤 *Utente:* {name}\n"
            f"🏷️ *Username:* @{username}\n"
            f"🆔 *Chat ID:* `{target_cid}`\n\n"
            "L'utente ha inserito la password corretta. Vuoi autorizzarlo ad accedere al Bot Domotica?"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "Approva ✅", "callback_data": f"approve:{target_cid}"},
                    {"text": "Rifiuta ❌", "callback_data": f"reject:{target_cid}"}
                ]
            ]
        }

        # Invio notifica al Bot Admin se configurato, altrimenti avviso console
        if self.is_configured() and self.admin_chat_id:
            return self.send_message(self.admin_chat_id, text, reply_markup=reply_markup)
        else:
            print(f"[2FA ADMIN] Richiesta 2FA per Chat ID {target_cid} (@{username}). Bot Admin non configurato in .env!")
            return False

    def send_unauthorized_attempt_alert(self, target_chat_id, user_info, attempted_command):
        """
        Invia una notifica INFORMATIVA all'Amministratore sul Bot Utils/Admin quando un utente non registrato tenta di inviare un comando (senza password)
        """
        target_cid = str(target_chat_id)
        name = user_info.get("name", "Utente Sconosciuto")
        username = user_info.get("username", "nessuno")
        user_tag = f"@{username}" if username and username != "nessuno" else f"ID {target_cid}"

        text = (
            "⚠️ *SEGNALAZIONE ACCESSO NON AUTORIZZATO*\n\n"
            f"👤 *Utente:* {name} ({user_tag})\n"
            f"🆔 *Chat ID:* `{target_cid}`\n"
            f"💬 *Testo/Comando:* `{attempted_command}`\n\n"
            "ℹ️ _L'utente NON ha inserito la password corretta. L'approvazione 2FA sarà disponibile solo dopo l'inserimento della password._"
        )

        if self.is_configured() and self.admin_chat_id:
            return self.send_message(self.admin_chat_id, text)
        else:
            print(f"[UNAUTHORIZED ALERT] Tentativo da {target_cid} ({attempted_command}). Admin Bot non configurato.")
            return False



    def handle_callback_query(self, callback):
        cb_id = callback["id"]
        data = callback.get("data", "")
        message = callback.get("message", {})
        msg_id = message.get("message_id")
        from_chat_id = message.get("chat", {}).get("id")

        if not data or ":" not in data:
            return

        if data.startswith("garage_approve:") or data.startswith("garage_reject:"):
            self._handle_garage_callback(cb_id, from_chat_id, msg_id, data)
            return

        action, target_cid = data.split(":", 1)

        if action == "approve":
            user_info = self.user_manager.approve_user(target_cid)
            name = user_info.get("name", target_cid)
            self.answer_callback_query(cb_id, text=f"Utente {target_cid} approvato!")
            
            # Aggiorna il messaggio per l'Admin
            new_text = (
                f"✅ *REGISTRAZIONE APPROVATA*\n\n"
                f"👤 *Utente:* {name}\n"
                f"🆔 *Chat ID:* `{target_cid}`\n\n"
                "L'utente è stato aggiunto agli utenti autorizzati."
            )
            self.edit_message_text(from_chat_id, msg_id, new_text)

            # Invia messaggio di benvenuto all'utente sul Bot Main
            if self.main_bot:
                welcome_msg = (
                    "🎉 *REGISTRAZIONE APPROVATA!*\n\n"
                    "L'amministratore ha approvato la tua richiesta. Ora puoi accedere a tutti i comandi della domotica.\n\n"
                    "Invia `/stato` per visualizzare la Dashboard completa."
                )
                self.main_bot.send_message(target_cid, welcome_msg)

        elif action == "reject":
            user_info = self.user_manager.reject_user(target_cid)
            name = user_info.get("name", target_cid) if user_info else target_cid
            self.answer_callback_query(cb_id, text=f"Utente {target_cid} rifiutato.")

            new_text = (
                f"❌ *REGISTRAZIONE RIFIUTATA*\n\n"
                f"👤 *Utente:* {name}\n"
                f"🆔 *Chat ID:* `{target_cid}`\n\n"
                "La richiesta dell'utente è stata rifiutata."
            )
            self.edit_message_text(from_chat_id, msg_id, new_text)

            if self.main_bot:
                reject_msg = (
                    "❌ *ACCESSO NON AUTORIZZATO*\n\n"
                    "La tua richiesta di registrazione è stata rifiutata dall'amministratore."
                )
                self.main_bot.send_message(target_cid, reject_msg)

    def _handle_garage_callback(self, cb_id, from_chat_id, msg_id, data):
        parts = data.split(":")
        if len(parts) < 3:
            return
        action = parts[0]
        bot_type = parts[1]
        target_cid = parts[2]

        bot_label = "AllarmeGarage 🚗" if bot_type == "allarme_garage" else "Serranda 🚪"
        garage_dir = os.path.join(os.path.dirname(config.BASE_DIR), "garage_bot")
        users_file = os.path.join(garage_dir, "authorized_users.json")
        pending_file = os.path.join(garage_dir, "pending_users.json")
        env_file_garage = os.path.join(garage_dir, ".env")

        bot_token = ""
        if os.path.exists(env_file_garage):
            try:
                with open(env_file_garage, "r", encoding="utf-8") as f:
                    for line in f:
                        if bot_type == "allarme_garage" and line.startswith("ALLARME_GARAGE_BOT_TOKEN="):
                            bot_token = line.split("=", 1)[1].strip()
                        elif bot_type == "serranda" and line.startswith("SERRANDA_BOT_TOKEN="):
                            bot_token = line.split("=", 1)[1].strip()
            except Exception:
                pass

        if action == "garage_approve":
            users = {}
            if os.path.exists(users_file):
                try:
                    with open(users_file, "r", encoding="utf-8") as f:
                        users = json.load(f)
                except Exception:
                    users = {}

            user_data = users.get(str(target_cid), {"name": f"User {target_cid}", "chat_id": str(target_cid)})
            if bot_type == "allarme_garage":
                user_data["allarme_garage"] = True
            elif bot_type == "serranda":
                user_data["serranda"] = True
            else:
                user_data["allarme_garage"] = True
                user_data["serranda"] = True
            users[str(target_cid)] = user_data

            try:
                with open(users_file, "w", encoding="utf-8") as f:
                    json.dump(users, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"[!] Errore salvataggio {users_file}: {e}")

            if os.path.exists(pending_file):
                try:
                    with open(pending_file, "r", encoding="utf-8") as f:
                        pend = json.load(f)
                    if str(target_cid) in pend:
                        pend[str(target_cid)].pop(bot_type, None)
                        if not pend[str(target_cid)]:
                            pend.pop(str(target_cid), None)
                        with open(pending_file, "w", encoding="utf-8") as f:
                            json.dump(pend, f, indent=2)
                except Exception:
                    pass

            self.answer_callback_query(cb_id, text=f"Utente {target_cid} approvato per {bot_label}!")
            new_text = (
                f"✅ *REGISTRAZIONE GARAGE APPROVATA*\n\n"
                f"🤖 *Bot:* {bot_label}\n"
                f"🆔 *Chat ID:* `{target_cid}`\n\n"
                "L'utente è stato aggiunto agli utenti autorizzati del garage."
            )
            self.edit_message_text(from_chat_id, msg_id, new_text)

            if bot_token and not bot_token.startswith("INSERISCI_QUI"):
                welcome_msg = (
                    f"🎉 *REGISTRAZIONE APPROVATA!*\n\n"
                    f"L'amministratore ha approvato la tua richiesta di accesso su *{bot_label}*.\n"
                    "Ora riceverai le notifiche in tempo reale."
                )
                try:
                    requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json={
                        "chat_id": target_cid,
                        "text": welcome_msg,
                        "parse_mode": "Markdown"
                    }, timeout=10)
                except Exception:
                    pass

        elif action == "garage_reject":
            if os.path.exists(users_file):
                try:
                    with open(users_file, "r", encoding="utf-8") as f:
                        users = json.load(f)
                    if str(target_cid) in users:
                        if bot_type == "allarme_garage":
                            users[str(target_cid)]["allarme_garage"] = False
                        elif bot_type == "serranda":
                            users[str(target_cid)]["serranda"] = False
                        with open(users_file, "w", encoding="utf-8") as f:
                            json.dump(users, f, indent=2)
                except Exception:
                    pass

            self.answer_callback_query(cb_id, text=f"Utente {target_cid} rifiutato.")
            new_text = (
                f"❌ *REGISTRAZIONE GARAGE RIFIUTATA*\n\n"
                f"🤖 *Bot:* {bot_label}\n"
                f"🆔 *Chat ID:* `{target_cid}`\n\n"
                "La richiesta dell'utente per questo bot è stata rifiutata."
            )
            self.edit_message_text(from_chat_id, msg_id, new_text)

            if bot_token and not bot_token.startswith("INSERISCI_QUI"):
                reject_msg = f"❌ La tua richiesta di registrazione per *{bot_label}* è stata rifiutata dall'amministratore."
                try:
                    requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json={
                        "chat_id": target_cid,
                        "text": reject_msg,
                        "parse_mode": "Markdown"
                    }, timeout=10)
                except Exception:
                    pass

    def handle_admin_command(self, chat_id, text):
        clean_text = text.strip()
        parts = clean_text.split()
        cmd = parts[0].lower()

        if cmd in ["/start", "/help", "/aiuto"]:
            help_text = (
                "🛡️ *Bot Admin Domotica 2FA*\n\n"
                "Questo bot riceve le richieste di approvazione registrazione degli utenti.\n\n"
                "📌 *Comandi disponibili:*\n"
                "✅ `/approve <chat_id>` - Approva manualmente un utente\n"
                "❌ `/reject <chat_id>` - Rifiuta un utente\n"
                "📋 `/utenti` - Mostra la lista degli utenti autorizzati\n"
                "⏳ `/pending` - Mostra le richieste in attesa"
            )
            self.send_message(chat_id, help_text)

        elif cmd in ["/approve", "/accetta"] and len(parts) > 1:
            target_cid = parts[1]
            info = self.user_manager.approve_user(target_cid)
            self.send_message(chat_id, f"✅ Utente `{target_cid}` ({info.get('name')}) approvato!")
            if self.main_bot:
                self.main_bot.send_message(target_cid, "🎉 *REGISTRAZIONE APPROVATA!*\nInvia `/stato` per accedere alla domotica.")

        elif cmd in ["/reject", "/rifiuta"] and len(parts) > 1:
            target_cid = parts[1]
            self.user_manager.reject_user(target_cid)
            self.send_message(chat_id, f"❌ Utente `{target_cid}` rifiutato.")
            if self.main_bot:
                self.main_bot.send_message(target_cid, "❌ La tua richiesta di registrazione è stata rifiutata.")

        elif cmd in ["/utenti", "/users", "/lista"]:
            auth_list = self.user_manager.get_authorized_list()
            lines = [f"👥 *UTENTI AUTORIZZATI ({len(auth_list)} totali):*\n"]
            for cid, inf in auth_list.items():
                name = inf.get("name", "User")
                lines.append(f"• `{cid}` - *{name}*")
            self.send_message(chat_id, "\n".join(lines))

        elif cmd in ["/pending", "/attesa"]:
            pending_list = self.user_manager.get_pending()
            if not pending_list:
                self.send_message(chat_id, "ℹ️ Nessuna richiesta in attesa di approvazione.")
                return
            lines = ["⏳ *RICHIESTE IN ATTESA:*\n"]
            for cid, inf in pending_list.items():
                lines.append(f"• `{cid}` - *{inf.get('name')}* (@{inf.get('username')})")
            self.send_message(chat_id, "\n".join(lines))

    def start_polling(self):
        if not self.is_configured():
            print("[!] Admin Bot non configurato in .env (ADMIN_BOT_TOKEN vuoto). Polling Admin Bot disattivato.")
            return

        print(f"[+] Avvio del polling Admin Bot 2FA in corso...")

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

                            # Gestione Callback Query (Pulsanti inline async)
                            if "callback_query" in update:
                                cb_data = update["callback_query"]
                                threading.Thread(target=self.handle_callback_query, args=(cb_data,), daemon=True).start()

                            # Gestione comandi direct message dall'admin (async)
                            elif "message" in update and "text" in update["message"]:
                                msg = update["message"]
                                chat_id = str(msg["chat"]["id"])
                                text = msg["text"]
                                # Rispondi solo all'admin configurato
                                if not self.admin_chat_id or chat_id == str(self.admin_chat_id):
                                    threading.Thread(target=self.handle_admin_command, args=(chat_id, text), daemon=True).start()
                else:
                    time.sleep(5)
            except Exception as e:
                print(f"[!] Errore polling Admin Bot: {e}")
                time.sleep(5)
