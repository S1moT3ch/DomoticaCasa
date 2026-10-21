# -*- coding: utf-8 -*-
import requests
import json
import os
import time

# --- CONFIGURAZIONE ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE_DIR, "tokens.json")

# Carica variabili dal file .env centrale se presente
env_file = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "bot", ".env"))
if os.path.exists(env_file):
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
    except ImportError:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if "=" in line_str and not line_str.startswith("#"):
                    k, v = line_str.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

CLIENT_ID = os.getenv("DAIKIN_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DAIKIN_CLIENT_SECRET", "")

TOKEN_URL = "https://idp.onecta.daikineurope.com/v1/oidc/token"
BASE_URL = "https://api.onecta.daikineurope.com/v1"

# --- GESTIONE TOKEN ---

def refresh_daikin_token():
    """Aggiorna l'access_token usando il refresh_token"""
    try:
        with open(TOKEN_FILE, 'r') as f:
            t = json.load(f)
        
        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': t['refresh_token'],
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET
        }
        
        r = requests.post(TOKEN_URL, data=payload, timeout=10)
        if r.status_code == 200:
            new_tokens = r.json()
            with open(TOKEN_FILE, 'w') as f:
                json.dump(new_tokens, f, indent=4)
            return new_tokens['access_token']
        return None
    except Exception as e:
        print(f"Errore durante il refresh: {e}")
        return None

def daikin_request(method, endpoint, payload=None):
    """Esegue una richiesta alle API Daikin (equivalente ai blocchi axios)"""
    try:
        with open(TOKEN_FILE, 'r') as f:
            t = json.load(f)
    except Exception as e:
        print(f"Errore lettura file token: {e}")
        return None
        
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {t['access_token']}",
        "x-api-key": CLIENT_ID,
        "Content-Type": "application/json",
        "accept": "application/json"
    }
    
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=15)
        elif method == "PATCH":
            r = requests.patch(url, json=payload, headers=headers, timeout=15)
            
        # Gestione Token Scaduto (401)
        if r.status_code == 401:
            new_at = refresh_daikin_token()
            if new_at:
                headers["Authorization"] = f"Bearer {new_at}"
                r = requests.get(url, headers=headers, timeout=15) if method == "GET" else requests.patch(url, json=payload, headers=headers, timeout=15)
        
        # Gestione Rate Limit (429)
        if r.status_code == 429:
            print(f"Errore 429: Rate limit raggiunto su {endpoint}. Skip.")
            return None

        r.raise_for_status()
        return r.json() if r.text else {"status": "ok", "code": r.status_code}
    except Exception as e:
        print(f"Errore API Daikin ({endpoint}): {e}")
        return None

# --- CONTROLLER ---

def get_daikin_devices():
    """Equivalente a getDaikinDevices"""
    return daikin_request("GET", "/gateway-devices")

def set_daikin_on_off(device_id, value):
    """Equivalente a DaikinOnOff. value: 'on' | 'off'"""
    endpoint = f"/gateway-devices/{device_id}/management-points/climateControl/characteristics/onOffMode"
    return daikin_request("PATCH", endpoint, {"value": value.lower()})

def set_daikin_mode(device_id, value):
    """Equivalente a DaikinMode. value: 'cooling' | 'heating' | 'auto' | 'fan' | 'dry'"""
    endpoint = f"/gateway-devices/{device_id}/management-points/climateControl/characteristics/operationMode"
    return daikin_request("PATCH", endpoint, {"value": value.lower()})

def set_daikin_temperature(device_id, value, mode="cooling"):
    """Equivalente a DaikinTemperature dinamico per evitare errori in dry/fan"""
    current_mode = mode.lower()
    if current_mode in ["dry", "fan"]:
        print(f"Cambio setpoint saltato: non supportato in modalita {current_mode}")
        return {"status": "skipped"}
        
    endpoint = f"/gateway-devices/{device_id}/management-points/climateControl/characteristics/temperatureControl"
    payload = {
        "value": float(value),
        "path": f"/operationModes/{current_mode}/setpoints/roomTemperature"
    }
    return daikin_request("PATCH", endpoint, payload)

def set_daikin_fan_speed(device_id, value, mode="cooling"):
    """
    Equivalente a DaikinFanSpeed.
    Mappa la struttura del payload ricavata dal tuo codice JS.
    """
    current_mode = mode.lower()
    endpoint = f"/gateway-devices/{device_id}/management-points/climateControl/characteristics/fanControl"
    
    is_auto = str(value).lower() == "auto"
    # Se e' un numero (es. "1", "2", "3") lo convertiamo in int, altrimenti stringa ("quiet")
    target_value = int(value) if str(value).isdigit() else str(value).lower()
    
    payload = {
        "value": {
            "operationModes": {
                current_mode: {
                    "fanSpeed": {
                        "currentMode": { "value": "auto" if is_auto else "fixed" },
                        "modes": { "fixed": { "value": "auto" if is_auto else target_value } }
                    }
                }
            }
        }
    }
    return daikin_request("PATCH", endpoint, payload)

def get_daikin_status(device_id):
    """Equivalente a DaikinStatus"""
    return daikin_request("GET", f"/gateway-devices/{device_id}")