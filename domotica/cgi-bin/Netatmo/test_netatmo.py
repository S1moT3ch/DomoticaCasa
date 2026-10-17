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

# Credenziali estratte dallo script originale
CLIENT_ID = "68cd33d12bbaa06205099cf0"
CLIENT_SECRET = "q66X8MVs6XUSu5hEFmsjr3Wet1GcT9tuQt"
REFRESH_TOKEN = "67eeeb5f6a703cd05a0f513a|84f5faf97c2f614b269e0a4a1fc93c23"
HOME_ID = "67eeee4f0bc3615f0a0e016a"

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
