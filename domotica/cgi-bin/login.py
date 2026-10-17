#!/usr/bin/env python3
import cgi
import json
import sys

import os

# Configurazione - Carica password da bot/.env se presente
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_file = os.path.abspath(os.path.join(BASE_DIR, "..", "bot", ".env"))
SECRET_PASS = "DomoticaCasa!26"
if os.path.exists(env_file):
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str.startswith("DOMOTICA_WEB_PASS="):
                    SECRET_PASS = line_str.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        pass

SECRET_PASS = os.getenv("DOMOTICA_WEB_PASS", SECRET_PASS)
TOKEN_VALIDO = "session_token_generato_123"

print("Content-Type: application/json\n")

form = cgi.FieldStorage()
password_inviata = form.getvalue("password")

if password_inviata == SECRET_PASS:
    print(json.dumps({"status": "success", "token": TOKEN_VALIDO}))
else:
    print(json.dumps({"status": "error", "message": "Accesso negato"}))