import os
import sys
import json
import requests
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE_DIR, "homeconnect_tokens.json")

print("="*60)
print(" AUTENTICAZIONE BOSCH HOME CONNECT (OAuth2) ")
print("="*60)

client_id = input("1) Incolla qui il tuo Client ID: ").strip()
client_secret = input("2) Incolla qui il tuo Client Secret: ").strip()

if not client_id or not client_secret:
    print("[!] Errore: Client ID e Client Secret sono obbligatori.")
    sys.exit(1)

# Scopes necessari per leggere lo stato e inviare comandi
SCOPES = "IdentifyAppliance Monitor Control Settings"
REDIRECT_URI = "https://localhost"

auth_url = f"https://api.home-connect.com/security/oauth/authorize?client_id={client_id}&response_type=code&redirect_uri={urllib.parse.quote(REDIRECT_URI)}&scope={urllib.parse.quote(SCOPES)}"

print("\n--- FASE 1: AUTORIZZAZIONE BROWSER ---")
print("Apri il seguente link nel browser del tuo computer o telefono:")
print(f"\n{auth_url}\n")
print("Fai l'accesso con il tuo account Bosch (Home Connect) e accetta i permessi.")
print("Il browser cercherà di caricare una pagina 'https://localhost/?code=...' che darà ERRORE. È NORMALE!")
print("Devi copiare per intero l'URL (indirizzo) di quella pagina di errore e incollarlo qui sotto.")

redirected_url = input("\n3) Incolla l'intero URL (https://localhost/?code=...) qui: ").strip()

if "code=" not in redirected_url:
    print("[!] Non riesco a trovare il parametro 'code=' nell'URL. Riprova.")
    sys.exit(1)

# Estrae il codice dall'URL
auth_code = redirected_url.split("code=")[1].split("&")[0]
print(f"\n[+] Authorization Code estratto correttamente: {auth_code[:10]}...")

print("\n--- FASE 2: SCAMBIO DEL CODICE CON I TOKEN ---")
token_url = "https://api.home-connect.com/security/oauth/token"
payload = {
    "grant_type": "authorization_code",
    "client_id": client_id,
    "client_secret": client_secret,
    "code": auth_code,
    "redirect_uri": REDIRECT_URI
}

try:
    resp = requests.post(token_url, data=payload)
    if resp.status_code != 200:
        print(f"[!] Errore API: HTTP {resp.status_code}")
        print(resp.text)
        sys.exit(1)
        
    data = resp.json()
    
    # Salva i token e le credenziali per il bridge
    save_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token")
    }
    
    with open(TOKEN_FILE, "w") as f:
        json.dump(save_data, f, indent=4)
        
    print(f"\n[SUCCESS] Token generati e salvati in: {TOKEN_FILE}")
    print("Ora possiamo procedere con l'avvio del bridge MQTT e controllare la lavastoviglie!")
    
except Exception as e:
    print(f"[!] Errore di rete: {e}")
