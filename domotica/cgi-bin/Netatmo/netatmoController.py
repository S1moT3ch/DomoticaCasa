# -*- coding: utf-8 -*-
import requests
import time
import os
import json

class NetatmoController:
    def __init__(self, client_id, client_secret, refresh_token, token_file=None):
        self.client_id = client_id
        self.client_secret = client_secret
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.token_file = token_file or os.path.join(base_dir, "netatmo_tokens.json")
        
        # Prova a caricare il refresh_token salvato precedentemente su disco
        self.refresh_token = self._load_refresh_token(default_token=refresh_token)
        self.access_token = None
        self.token_expires_at = 0
        self.base_url = "https://api.netatmo.com"

    def _load_refresh_token(self, default_token):
        try:
            if os.path.exists(self.token_file):
                with open(self.token_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    saved = data.get("refresh_token")
                    if saved:
                        print(f"[NetatmoController] Caricato refresh_token da file: {self.token_file}")
                        return saved
        except Exception as e:
            print(f"[!] Errore lettura {self.token_file}: {e}")
        return default_token

    def _save_refresh_token(self, new_token):
        try:
            with open(self.token_file, "w", encoding="utf-8") as f:
                json.dump({"refresh_token": new_token, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=2)
            print(f"[NetatmoController] Salvato nuovo refresh_token su file: {self.token_file}")
        except Exception as e:
            print(f"[!] Errore salvataggio {self.token_file}: {e}")

    def _refresh_access_token(self):
        url = f"{self.base_url}/oauth2/token"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        try:
            resp = requests.post(url, data=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self.access_token = data.get("access_token")
            new_rf = data.get("refresh_token") or self.refresh_token
            if new_rf:
                self.refresh_token = new_rf
                self._save_refresh_token(new_rf)
            
            # Scadenza access token con margine di sicurezza
            self.token_expires_at = int(time.time()) + data.get("expires_in", 3600) - 60
            print("Token Netatmo aggiornato correttamente.")
        except Exception as e:
            # Emula un cooldown di 180s in caso di fallimento temporaneo per evitare rate-limit 429
            self.token_expires_at = int(time.time()) + 180
            print(f"[!] Errore refresh token Netatmo: {e}. Prossimo tentativo tra 3 minuti per evitare rate-limiting.")

    def get_access_token(self):
        if not self.access_token or time.time() >= self.token_expires_at:
            self._refresh_access_token()
        return self.access_token

    def get_home_status(self, home_id):
        token = self.get_access_token()
        if not token:
            print("[!] Impostazione get_home_status annullata: Access Token non disponibile.")
            return {}

        url = f"{self.base_url}/api/homestatus"
        params = {"home_id": home_id}
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            res_json = resp.json()
            if isinstance(res_json, dict):
                body = res_json.get("body")
                if isinstance(body, dict):
                    return body
                if "error" in res_json:
                    print(f"[!] Errore API Netatmo get_home_status: {res_json.get('error')}")
            return {}
        except Exception as e:
            print(f"Errore get_home_status: {e}")
            return {}

    def set_room_thermostat(self, home_id, room_id, mode, temp=None, duration=None):
        token = self.get_access_token()
        if not token:
            print("[!] set_room_thermostat annullata: Access Token non disponibile.")
            return None

        url = f"{self.base_url}/api/setstate"
        headers = {"Authorization": f"Bearer {token}"}
        room_data = {"id": room_id, "therm_setpoint_mode": mode}
        if mode == "manual":
            if temp: room_data["therm_setpoint_temperature"] = temp
            seconds = (int(duration) * 60) if duration else 7200
            room_data["therm_setpoint_end_time"] = int(time.time()) + seconds
        payload = {"home": {"id": home_id, "rooms": [room_data]}}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            return resp.json()
        except Exception as e:
            print(f"Errore set_room_thermostat: {e}")
            return None

    def sync_home_schedule(self, home_id, schedule_data):
        """Invia una pianificazione completa (zones + timetable)"""
        token = self.get_access_token()
        if not token:
            print("[!] sync_home_schedule annullata: Access Token non disponibile.")
            return None

        url = f"{self.base_url}/api/synchomeschedule"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"home_id": home_id, "therm_schedule": schedule_data}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            return resp.json()
        except Exception as e:
            print(f"Errore synchomeschedule: {e}")
            return None