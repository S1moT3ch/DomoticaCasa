import sys
import os
import json

# Assicuriamoci di poter importare la classe
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

try:
    from netatmoController import NetatmoController
except ImportError as e:
    print(f"Errore di importazione: {e}")
    sys.exit(1)

# Carica variabili dal file .env centrale se presente
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

CLIENT_ID = os.getenv("NETATMO_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("NETATMO_CLIENT_SECRET", "")
REFRESH_TOKEN = os.getenv("NETATMO_REFRESH_TOKEN", "")
HOME_ID = os.getenv("NETATMO_HOME_ID", "")

print("==================================================")
print(" TEST DI CONNESSIONE DIRETTA SERVER NETATMO/BTICINO")
print("==================================================")

try:
    print("\n1. Inizializzazione NetatmoController...")
    netatmo = NetatmoController(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)
    
    print("\n2. Generazione/Recupero Access Token in corso...")
    token = netatmo.get_access_token()
    
    if not token:
        print("\n[ERRORE] Impossibile recuperare il token di accesso. Controlla i messaggi sopra.")
        sys.exit(1)
        
    print(f"[OK] Token ottenuto con successo (inizia con {token[:15]}...)")
    
    print("\n3. Interrogazione API get_home_status per l'Home ID specificato...")
    status = netatmo.get_home_status(HOME_ID)
    
    if not status:
        print("\n[ERRORE] Il server non ha restituito i dati o ha generato un errore API.")
        sys.exit(1)
        
    print("\n[OK] Dati recuperati correttamente dal server! Ecco la risposta grezza:")
    print(json.dumps(status, indent=4))
    
    print("\nTest completato con successo. La comunicazione con i server Bticino funziona.")
    
except Exception as e:
    print(f"\n[ERRORE CRITICO] Il test è fallito con un'eccezione Python: {e}")
    import traceback
    traceback.print_exc()
