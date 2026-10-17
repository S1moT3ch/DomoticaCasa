# -*- coding: utf-8 -*-
import hashlib
import requests
import time
import base64
from wsgiref.simple_server import make_server

# --- CONFIGURAZIONE SHELLY & WSGI ---
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_file = os.path.abspath(os.path.join(BASE_DIR, "..", "bot", ".env"))
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

SHELLY_IP = os.getenv("SHELLY_ALARM_IP", "192.168.4.112")
SHELLY_USER = os.getenv("SHELLY_ALARM_USER", "admin")
SHELLY_PASS = os.getenv("SHELLY_ALARM_PASS", "AllarmeCasa!26")

# Credenziali per l'iPhone
PROXY_USER = os.getenv("PROXY_ALARM_USER", "iphone")
PROXY_PASS = os.getenv("PROXY_ALARM_PASS", "domotica2026")

def get_sha256_digest_auth():
    uri = "/rpc/Switch.Set?id=0&on=true"
    url = "http://" + SHELLY_IP + uri
    
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 401:
            auth_header = r.headers.get('WWW-Authenticate', '')
            if not auth_header:
                return "Errore: Lo Shelly non ha inviato l'header di autenticazione", 500
            
            params = {}
            for item in auth_header.replace('Digest ', '').split(', '):
                if '=' in item:
                    k, v = item.split('=', 1)
                    params[k] = v.strip('"')

            realm = params.get('realm')
            nonce = params.get('nonce')
            nc = "00000001"
            cnonce = hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]
            qop = "auth"
            
            ha1 = hashlib.sha256((SHELLY_USER + ":" + realm + ":" + SHELLY_PASS).encode()).hexdigest()
            ha2 = hashlib.sha256(("GET:" + uri).encode()).hexdigest()
            response_hash = hashlib.sha256((ha1 + ":" + nonce + ":" + nc + ":" + cnonce + ":" + qop + ":" + ha2).encode()).hexdigest()
            
            auth_str = (
                'Digest username="' + SHELLY_USER + '", ' +
                'realm="' + realm + '", ' +
                'nonce="' + nonce + '", ' +
                'uri="' + uri + '", ' +
                'algorithm=SHA-256, ' +
                'qop=' + qop + ', ' +
                'nc=' + nc + ', ' +
                'cnonce="' + cnonce + '", ' +
                'response="' + response_hash + '"'
            )
            
            return requests.get(url, headers={'Authorization': auth_str}, timeout=5)
        return r
    except Exception as e:
        return "Errore connessione verso Shelly: " + str(e), 500

def application(environ, start_response):
    path = environ.get('PATH_INFO', '')
    if path != '/allarmToggle':
        start_response('404 Not Found', [('Content-Type', 'text/plain')])
        return [b'404 Not Found']

    # Verifica Basic Auth dall'iPhone
    auth_header = environ.get('HTTP_AUTHORIZATION', '')
    auth_valid = False
    if auth_header.startswith('Basic '):
        try:
            auth_decoded = base64.b64decode(auth_header.split(' ')[1]).decode('utf-8')
            u, p = auth_decoded.split(':', 1)
            if u == PROXY_USER and p == PROXY_PASS:
                auth_valid = True
        except Exception:
            pass

    if not auth_valid:
        print("[-] Accesso NEGATO: Credenziali iPhone errate o mancanti", flush=True)
        start_response('401 Unauthorized', [('WWW-Authenticate', 'Basic realm="Proxy Shelly"'), ('Content-Type', 'text/plain')])
        return [b'Login richiesto']

    print("[+] Accesso OK: iPhone autenticato correttamente", flush=True)
    try:
        shelly_res = get_sha256_digest_auth()
        if isinstance(shelly_res, tuple):
            body, status = shelly_res
            start_response(f'{status} Error', [('Content-Type', 'text/plain')])
            return [body.encode('utf-8')]
        
        status_code = shelly_res.status_code
        start_response(f'{status_code} OK', [('Content-Type', 'text/plain')])
        return [shelly_res.text.encode('utf-8')]
    except Exception as e:
        start_response('500 Internal Server Error', [('Content-Type', 'text/plain')])
        return [str(e).encode('utf-8')]

if __name__ == '__main__':
    print("[*] Lightweight WSGI Proxy avviato su http://0.0.0.0:5000/allarmToggle", flush=True)
    print("[*] In attesa di comandi...", flush=True)
    httpd = make_server('0.0.0.0', 5000, application)
    httpd.serve_forever()
