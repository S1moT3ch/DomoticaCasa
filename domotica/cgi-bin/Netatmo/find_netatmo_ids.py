import requests

import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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

CLIENT_ID = os.getenv("NETATMO_CLIENT_ID", "68cd33d12bbaa06205099cf0")
CLIENT_SECRET = os.getenv("NETATMO_CLIENT_SECRET", "q66X8MVs6XUSu5hEFmsjr3Wet1GcT9tuQt")
REFRESH_TOKEN = os.getenv("NETATMO_REFRESH_TOKEN", "67eeeb5f6a703cd05a0f513a|84f5faf97c2f614b269e0a4a1fc93c23")

def get_ids():
    # 1. Ottieni un Access Token fresco
    url_auth = "https://api.netatmo.com/oauth2/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    
    try:
        auth_resp = requests.post(url_auth, data=payload).json()
        access_token = auth_resp["access_token"]
        
        # 2. Recupera i dati delle case
        url_homes = "https://api.netatmo.com/api/homesdata"
        headers = {"Authorization": f"Bearer {access_token}"}
        homes_resp = requests.get(url_homes, headers=headers).json()
        
        print("\n--- ELENCO CASE E STANZE NETATMO/BTICINO ---")
        for home in homes_resp.get("body", {}).get("homes", []):
            print(f"\nCASA: {home['name']}")
            print(f"ID CASA (home_id): {home['id']}")
            print("-" * 30)
            
            for room in home.get("rooms", []):
                print(f"  > STANZA: {room['name']}")
                print(f"    ID STANZA (room_id): {room['id']}")
                
    except Exception as e:
        print(f"Errore durante la scansione: {e}")

if __name__ == "__main__":
    get_ids()