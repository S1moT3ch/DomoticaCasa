import sys
import time

try:
    from samsungtvws import SamsungTVWS
except ImportError:
    print("Libreria samsungtvws non trovata. Esegui: pip install samsungtvws")
    sys.exit(1)

IP_TV = "192.168.1.130" # TV Cucina
TOKEN_FILE = "/www/domotica/bot/tv_cucina_token.txt"

print(f"Tentativo di connessione diretta alla TV ({IP_TV})...")
print("Assicurati che la TV sia ACCESA in questo momento!")

try:
    # Inizializziamo la connessione
    tv = SamsungTVWS(host=IP_TV, port=8002, token_file=TOKEN_FILE, name="Domotica Nighthawk", timeout=5)
    
    print("[OK] Connessione riuscita!")
    print("Invio comando 'Volume SU'...")
    tv.shortcuts().volume_up()
    time.sleep(1)
    
    print("Invio comando 'Volume GIU'...")
    tv.shortcuts().volume_down()
    
    print("Test completato con successo. Hai visto il volume muoversi?")
    
except Exception as e:
    print(f"\n[ERRORE FATALE] Impossibile comunicare con la TV: {e}")
    print("\nPossibili cause:")
    print("1. La TV è spenta o scollegata dal Wi-Fi.")
    print("2. L'IP 192.168.1.130 non è più corretto (controlla nel router se è cambiato).")
    print("3. Hai negato i permessi sul popup della TV in passato.")
