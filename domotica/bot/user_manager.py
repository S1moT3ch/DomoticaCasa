import os
import json
import time
import threading
import config


NOTIFICATION_CATEGORIES = {
    # Resoconti & Report (Default: Attivo)
    "vacation_report": "☀️ Report Giornaliero Mattutino (ore 09:00)",

    # Notifiche di Sistema & Sicurezza (Default: Attive)
    "humidity": "💧 Umidità (> 60%)",
    "power": "⚡ Assenza Corrente (Blackout)",
    "voip_missed_calls": "📞 Chiamate Perse (Fisso WindTre)",
    "voip_incoming_calls": "🔔 Telefono Fisso in Squillo (Real-Time)",
    "voip_outgoing_calls": "📤 Chiamate in Uscita (Fisso WindTre)",
    "camera_soggiorno": "🛋️ Camera Soggiorno (192.168.4.160)",
    "camera_corridoio": "🚪 Camera Corridoio (192.168.4.161)",
    "camera_garage": "🎥 Camera Box / Garage (192.168.3.130)",
    
    # Allarmi & Sensori Ingressi (Default: Disattive)
    "alarm_house": "🛡️ Allarme Casa",
    "alarm_garage": "🚗 Allarme Garage",
    "shutter": "🚪 Serranda Garage",
    "gate": "🚧 Cancello Garage",

    # Luci & Relè Shelly (Default: Disattive)
    "luce_cucina": "💡 Luce Cucina",
    "led_andrea": "💡 Led Andrea",
    "ricircolo": "💧 Ricircolo Acqua",
    "tapo_camera": "📹 Telecamera Casa",

    # Clima & Termostato (Default: Disattive)
    "daikin": "❄️ Clima Daikin",
    "bticino": "🌡️ Termostato BTicino",
    
    # Elettrodomestici
    "bosch": "🍽️ Lavastoviglie Bosch",

    # Stampante HP (Default: Attivo)
    "printer": "🖨️ Stampante HP (Errori & Carta)"
}

DEFAULT_NOTIFICATIONS = {
    "vacation_report": True,
    "humidity": True,
    "power": True,
    "voip_missed_calls": True,
    "voip_incoming_calls": True,
    "voip_outgoing_calls": True,
    "camera_soggiorno": True,
    "camera_corridoio": True,
    "camera_garage": True,
    "printer": True,
    "gate": False,
    "shutter": False,
    "alarm_house": False,
    "alarm_garage": False,
    "luce_cucina": False,
    "led_andrea": False,
    "ricircolo": False,
    "tapo_camera": False,
    "daikin": False,
    "bticino": False,
    "bosch": False
}







class UserManager:
    def __init__(self, filepath=None):
        self.filepath = filepath or config.AUTHORIZED_USERS_FILE
        self.lock = threading.RLock()
        self.authorized_users = {}
        self.pending_users = {}
        self.unauthorized_rate_limiter = {}  # { chat_id: [timestamps] }
        self.blacklisted_ids = set()

        self._load_users()

    def is_blacklisted(self, chat_id):
        with self.lock:
            return str(chat_id) in self.blacklisted_ids

    def check_unauthorized_rate_limit(self, chat_id, max_requests=3, window_seconds=10):
        """Monitora i tentativi ad alta frequenza per identificare e bloccare gli scanner bot automatici"""
        with self.lock:
            cid = str(chat_id)
            if cid in self.blacklisted_ids:
                return True

            now = time.time()
            if cid not in self.unauthorized_rate_limiter:
                self.unauthorized_rate_limiter[cid] = []

            # Filtra solo i messaggi negli ultimi window_seconds
            self.unauthorized_rate_limiter[cid] = [
                t for t in self.unauthorized_rate_limiter[cid] if now - t <= window_seconds
            ]
            self.unauthorized_rate_limiter[cid].append(now)

            if len(self.unauthorized_rate_limiter[cid]) > max_requests:
                self.blacklisted_ids.add(cid)
                return True  # Scanner / Bot automatico rilevato!
            return False


    def _load_users(self):
        with self.lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        self.authorized_users = json.load(f)
                except Exception as e:
                    print(f"[!] Errore lettura {self.filepath}: {e}")
                    self.authorized_users = {}

            # Assicura che tutti gli utenti esistenti abbiano la chiave "notifications"
            updated = False

            for cid, udata in self.authorized_users.items():
                if not isinstance(udata, dict):
                    self.authorized_users[cid] = {"name": str(udata), "notifications": dict(DEFAULT_NOTIFICATIONS)}
                    updated = True
                elif "notifications" not in udata or not isinstance(udata["notifications"], dict):
                    udata["notifications"] = dict(DEFAULT_NOTIFICATIONS)
                    updated = True
                else:
                    # Assicura che eventuali nuove categorie di notifica siano presenti con i valori di default
                    for k, v in DEFAULT_NOTIFICATIONS.items():
                        if k not in udata["notifications"]:
                            udata["notifications"][k] = v
                            updated = True

                if "devices" not in self.authorized_users[cid]:
                    self.authorized_users[cid]["devices"] = []
                    updated = True

            if updated:
                self._save_users_unlocked()

    def _save_users_unlocked(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.authorized_users, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[!] Errore salvataggio {self.filepath}: {e}")

    def get_user_devices(self, chat_id):
        """Restituisce la lista di identificatori di dispositivi (MAC o alias) per l'utente specificato"""
        with self.lock:
            cid = str(chat_id)
            udata = self.authorized_users.get(cid, {})
            devs = udata.get("devices", [])
            if isinstance(devs, str):
                devs = [d.strip() for d in devs.split(",") if d.strip()]
            return [str(d).strip().lower() for d in devs if str(d).strip()]

    def set_user_devices(self, chat_id, devices):
        """Imposta o aggiorna i dispositivi associati all'utente"""
        with self.lock:
            cid = str(chat_id)
            if cid in self.authorized_users:
                if isinstance(devices, str):
                    devices = [d.strip() for d in devices.split(",") if d.strip()]
                elif isinstance(devices, (list, set)):
                    devices = [str(d).strip() for d in devices if str(d).strip()]
                else:
                    devices = []
                self.authorized_users[cid]["devices"] = devices
                self._save_users_unlocked()
                self._notify_change()
                return True
            return False

    def is_authorized(self, chat_id):
        with self.lock:
            return str(chat_id) in self.authorized_users

    def is_pending(self, chat_id):
        with self.lock:
            return str(chat_id) in self.pending_users

    def set_pending(self, chat_id, user_info):
        with self.lock:
            cid = str(chat_id)
            self.pending_users[cid] = user_info
            return self.pending_users[cid]

    def approve_user(self, chat_id, user_info_fallback=None):
        with self.lock:
            cid = str(chat_id)
            info = self.pending_users.pop(cid, user_info_fallback or {"name": f"User {cid}"})
            if not isinstance(info, dict):
                info = {"name": str(info)}

            info["chat_id"] = str(info.get("chat_id", cid))
            if "name" not in info or not info["name"]:
                info["name"] = f"User {cid}"
                
            if "notifications" not in info or not isinstance(info.get("notifications"), dict):
                info["notifications"] = dict(DEFAULT_NOTIFICATIONS)
            else:
                notifs = dict(DEFAULT_NOTIFICATIONS)
                notifs.update(info["notifications"])
                info["notifications"] = notifs

            self.authorized_users[cid] = info
            self._save_users_unlocked()
            self._notify_change()
            return info

    def set_user_change_callback(self, callback):
        """Registra un callback invocato istantaneamente ad ogni modifica di utenti o preferenze"""
        with self.lock:
            self.on_user_change_callback = callback

    def _notify_change(self):
        cb = getattr(self, "on_user_change_callback", None)
        if cb:
            try:
                threading.Thread(target=cb, daemon=True, name="UserChangeCallbackThread").start()
            except Exception as e:
                print(f"[!] Errore callback notifica modifica utente: {e}")

    def reject_user(self, chat_id):
        with self.lock:
            cid = str(chat_id)
            return self.pending_users.pop(cid, None)

    def get_pending(self):
        with self.lock:
            return dict(self.pending_users)

    def get_authorized_list(self):
        with self.lock:
            return dict(self.authorized_users)

    def get_user_notifications(self, chat_id):
        with self.lock:
            cid = str(chat_id)
            udata = self.authorized_users.get(cid, {})
            notifs = udata.get("notifications", {})
            # Ritorna dict con tutte le chiavi compilate
            res = dict(DEFAULT_NOTIFICATIONS)
            res.update(notifs)
            return res

    def toggle_user_notification(self, chat_id, notif_type):
        with self.lock:
            cid = str(chat_id)
            if cid in self.authorized_users:
                udata = self.authorized_users[cid]
                if "notifications" not in udata or not isinstance(udata["notifications"], dict):
                    udata["notifications"] = dict(DEFAULT_NOTIFICATIONS)
                
                curr = udata["notifications"].get(notif_type, True)
                new_val = not curr
                udata["notifications"][notif_type] = new_val
                self._save_users_unlocked()
                self._notify_change()
                return new_val
            return False

    def get_subscribed_users(self, category=None):
        with self.lock:
            if not category:
                return list(self.authorized_users.keys())
            
            subscribers = []
            for cid, udata in self.authorized_users.items():
                notifs = udata.get("notifications", DEFAULT_NOTIFICATIONS)
                default_val = DEFAULT_NOTIFICATIONS.get(category, True)
                if notifs.get(category, default_val) is True:
                    subscribers.append(cid)
            return subscribers

    def is_vacation_mode(self):
        with self.lock:
            v_file = getattr(config, "VACATION_MODE_FILE", os.path.join(config.BASE_DIR, "vacation_mode.json"))
            if os.path.exists(v_file):
                try:
                    with open(v_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        return data.get("vacation_mode", False)
                except Exception:
                    pass
            return False

    def set_vacation_mode(self, active: bool):
        with self.lock:
            v_file = getattr(config, "VACATION_MODE_FILE", os.path.join(config.BASE_DIR, "vacation_mode.json"))
            data = {
                "vacation_mode": bool(active),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            try:
                with open(v_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                print(f"[!] Errore salvataggio {v_file}: {e}")
            return bool(active)




