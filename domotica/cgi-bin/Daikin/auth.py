# -*- coding: utf-8 -*-
import requests
import json
import os
from requests_oauthlib import OAuth2Session

# --- CONFIGURAZIONE ---
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

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

client_id = os.getenv("DAIKIN_CLIENT_ID", "")
client_secret = os.getenv("DAIKIN_CLIENT_SECRET", "")
redirect_uri = os.getenv("DAIKIN_REDIRECT_URI", 'https://acsahomeconnectv2-production.up.railway.app/auth/daikin/callback')

token_url = "https://idp.onecta.daikineurope.com/v1/oidc/token"
authorization_base_url = "https://idp.onecta.daikineurope.com/v1/oidc/authorize"
scope = ["openid", "onecta:basic.integration", "onecta:basic.control"]

def run_setup():
    # Creazione sessione
    oauth = OAuth2Session(client_id, redirect_uri=redirect_uri, scope=scope)
    authorization_url, state = oauth.authorization_url(authorization_base_url)

    print("\n" + "="*30)
    print("FASE 1: AUTORIZZAZIONE")
    print("="*30)
    print("1. Apri questo link e loggati:")
    print(authorization_url)

    print("\n" + "="*30)
    print("FASE 2: SCAMBIO TOKEN")
    print("="*30)
    user_input = raw_input("2. Incolla qui il codice o l'URL intera: ").strip() if hasattr(__builtins__, 'raw_input') else input("2. Incolla qui il codice o l'URL intera: ").strip()

    # Estrazione codice avanzata con Regex
    import re
    from urllib.parse import unquote

    code_match = re.search(r'code=([^&"\'\s\n]+)', user_input) or re.search(r'code%3D([^&"\'\s\n]+)', user_input)
    if code_match:
        code = unquote(code_match.group(1))
    else:
        if "proxy.html" in user_input:
            print("\n[!] ATTENZIONE: Hai incollato l'URL della pagina intermedia di consenso di Daikin.")
            print("Attendi che il browser completi il reindirizzamento ed incolla l'URL finale della barra in alto (che contiene '?code=').")
            return
        code = user_input



    try:
        payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': client_id,
            'client_secret': client_secret
        }
        
        print("\nRichiesta token in corso...")
        response = requests.post(token_url, data=payload)
        
        if response.status_code != 200:
            print("Errore dal server Daikin: " + response.text)
            return

        token = response.json()

        # Salvataggio file
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(BASE_DIR, "tokens.json")
        with open(file_path, "w") as f:
            json.dump(token, f, indent=4)

        
        print("\n[OK] Successo! Il file tokens.json e' stato creato in:")
        print(file_path)

    except Exception as e:
        print("\n[ERRORE] Errore: " + str(e))

if __name__ == "__main__":
    run_setup()