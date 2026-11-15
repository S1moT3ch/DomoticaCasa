#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WOL & Remote Shutdown HTTP Service per OpenWrt
Espone endpoint per accensione (WOL), spegnimento (SSH) e health check.
Supporta Basic Auth, Token query string e verifica dello stato effettivo (acceso/spento).
"""

import os
import sys
import json
import time
import socket
import re
import base64
import subprocess
from urllib.parse import parse_qs
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.abspath(os.path.join(BASE_DIR, "..", "bot", ".env"))
PORT = 5050

_last_mtime = 0
_cached_env = {}

def get_config():
    global _last_mtime, _cached_env
    if os.path.exists(ENV_FILE):
        try:
            mtime = os.path.getmtime(ENV_FILE)
            if mtime != _last_mtime:
                new_env = {}
                with open(ENV_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line_str = line.strip()
                        if "=" in line_str and not line_str.startswith("#"):
                            k, v = line_str.split("=", 1)
                            new_env[k.strip()] = v.strip().strip('"').strip("'")
                _cached_env = new_env
                _last_mtime = mtime
        except Exception:
            pass

    wol_pass = _cached_env.get("WOL_PASS") or _cached_env.get("DOMOTICA_WEB_PASS") or "DomoticaCasa!26"
    wol_user = _cached_env.get("WOL_USER", "admin")
    pc_mac = _cached_env.get("PC_MAC", "30:C5:99:31:CC:D2")
    pc_ip = _cached_env.get("PC_IP", "192.168.1.205")
    pc_user = _cached_env.get("PC_USER", "SimoneCantore")
    ssh_key = _cached_env.get("SSH_KEY", "/root/.ssh/id_rsa")

    return {
        "SECRET_PASS": os.getenv("WOL_PASS", wol_pass),
        "AUTH_USER": os.getenv("WOL_USER", wol_user),
        "DEFAULT_MAC": os.getenv("PC_MAC", pc_mac),
        "PC_IP": os.getenv("PC_IP", pc_ip),
        "PC_USER": os.getenv("PC_USER", pc_user),
        "SSH_KEY": ssh_key
    }


def is_pc_online(ip):
    """Verifica rapida della presenza in rete del PC."""
    try:
        res = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.2
        )
        if res.returncode == 0:
            return True
    except Exception:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            s.connect((ip, 3389)) # porta RDP
            return True
    except Exception:
        pass

    return False


def wait_for_pc_state(ip, target_online_state, max_wait=7.0, interval=0.7):
    """Attende che il PC raggiunga lo stato desiderato (True=acceso, False=spento)."""
    start_t = time.time()
    while (time.time() - start_t) < max_wait:
        current_state = is_pc_online(ip)
        if current_state == target_online_state:
            return current_state, round(time.time() - start_t, 1)
        time.sleep(interval)
    return is_pc_online(ip), round(time.time() - start_t, 1)


def send_magic_packet(mac_address, broadcast_ip="192.168.1.255", port=9):
    mac_clean = re.sub(r'[^a-fA-F0-9]', '', mac_address)
    if len(mac_clean) != 12:
        raise ValueError(f"MAC address non valido: '{mac_address}'")

    hw_addr = bytes.fromhex(mac_clean)
    magic_packet = b'\xff' * 6 + hw_addr * 16

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for dest in [broadcast_ip, "255.255.255.255"]:
            try:
                s.sendto(magic_packet, (dest, port))
            except Exception:
                pass

    formatted_mac = ":".join(mac_clean[i:i+2] for i in range(0, 12, 2))
    try:
        subprocess.run(
            ["etherwake", "-i", "br-lan", formatted_mac],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2
        )
    except Exception:
        pass

    return formatted_mac


def run_ssh_command(cmd_str, pc_ip, pc_user="SimoneCantore", key_path="/root/.ssh/id_rsa"):
    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=4",
        "-o", "BatchMode=yes"
    ]
    if os.path.exists(key_path):
        cmd.extend(["-i", key_path])
    cmd.extend([f"{pc_user}@{pc_ip}", cmd_str])

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
    if res.returncode == 0:
        return True, res.stdout.strip()

    try:
        cmd_db = ["dbclient", "-y"]
        if os.path.exists(key_path):
            cmd_db.extend(["-i", key_path])
        cmd_db.extend([f"{pc_user}@{pc_ip}", cmd_str])
        res_db = subprocess.run(cmd_db, capture_output=True, text=True, timeout=6)
        if res_db.returncode == 0:
            return True, res_db.stdout.strip()
    except Exception:
        pass

    err_msg = res.stderr.strip() or res.stdout.strip() or f"Codice errore: {res.returncode}"
    return False, err_msg


def check_auth(environ, query_params, config):
    """Verifica autenticazione: supporta sia Basic Auth che Query Param (?key=...)."""
    secret = config["SECRET_PASS"]
    user = config["AUTH_USER"]

    # 1. Parametro URL ?key= o ?token=
    key = query_params.get("key", query_params.get("token", [None]))[0]
    if key and key == secret:
        return True

    # 2. HTTP Basic Auth
    auth = environ.get("HTTP_AUTHORIZATION", "")
    if auth.startswith("Basic "):
        try:
            raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
            u, p = raw.split(":", 1)
            if (u == user or u == "admin") and p == secret:
                return True
        except Exception:
            pass

    return False


def application(environ, start_response):
    try:
        config = get_config()
        path = environ.get("PATH_INFO", "")
        query_str = environ.get("QUERY_STRING", "")
        params = parse_qs(query_str)

        # 1. Radice /
        if path in ["/", ""]:
            body = b"WOL & Shutdown Service is running OK\n"
            start_response("200 OK", [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body)))
            ])
            return [body]

        # 2. /health (e /status, /ping)
        if path in ["/health", "/status", "/ping"]:
            pc_on = is_pc_online(config["PC_IP"])
            res_data = {
                "status": "ok",
                "service": "wol_service",
                "pc_online": pc_on,
                "pc_status": "acceso" if pc_on else "spento",
                "pc_ip": config["PC_IP"],
                "pc_user": config["PC_USER"],
                "default_mac": config["DEFAULT_MAC"]
            }
            body = json.dumps(res_data, indent=2).encode("utf-8") + b"\n"
            start_response("200 OK", [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*"),
                ("Cache-Control", "no-cache")
            ])
            return [body]

        # 3. /test_ssh
        if path == "/test_ssh":
            ok, output = run_ssh_command("whoami", config["PC_IP"], config["PC_USER"], config["SSH_KEY"])
            res_data = {
                "status": "success" if ok else "error",
                "action": "test_ssh",
                "result": output,
                "pc_ip": config["PC_IP"],
                "pc_user": config["PC_USER"]
            }
            body = json.dumps(res_data, indent=2).encode("utf-8") + b"\n"
            status_code = "200 OK" if ok else "500 Internal Server Error"
            start_response(status_code, [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body)))
            ])
            return [body]

        # 4. /wol (e /wake) -> Accensione con verifica stato post-invio
        if path in ["/wol", "/wake"]:
            if not check_auth(environ, params, config):
                err_data = {"status": "error", "message": "Accesso negato. Autenticazione richiesta"}
                body = json.dumps(err_data).encode("utf-8") + b"\n"
                start_response("401 Unauthorized", [
                    ("WWW-Authenticate", 'Basic realm="WOL Service"'),
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Length", str(len(body)))
                ])
                return [body]

            target_mac = params.get("mac", [config["DEFAULT_MAC"]])[0]
            
            # Se era già acceso, ritorna subito
            already_on = is_pc_online(config["PC_IP"])
            if already_on:
                res_data = {
                    "status": "success",
                    "action": "wake",
                    "pc_status": "acceso",
                    "pc_online": True,
                    "message": "Il PC era già acceso e raggiungibile",
                    "mac": target_mac
                }
                body = json.dumps(res_data, indent=2).encode("utf-8") + b"\n"
                start_response("200 OK", [
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Access-Control-Allow-Origin", "*")
                ])
                return [body]

            # Invia Magic Packet
            try:
                sent_mac = send_magic_packet(target_mac)
            except Exception as e:
                err_data = {"status": "error", "message": str(e)}
                body = json.dumps(err_data).encode("utf-8") + b"\n"
                start_response("500 Internal Server Error", [
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Length", str(len(body)))
                ])
                return [body]

            # Attende fino a 7.5s per verificare se il PC risponde
            final_online, elapsed = wait_for_pc_state(config["PC_IP"], True, max_wait=7.5)
            
            if final_online:
                pc_status_str = "acceso"
                msg_str = f"Magic packet inviato a {sent_mac}. Il PC risulta ora ACCESO (dopo {elapsed}s)"
            else:
                pc_status_str = "avvio_in_corso"
                msg_str = f"Magic packet inviato a {sent_mac}. Avvio del sistema operativo in corso"

            res_data = {
                "status": "success",
                "action": "wake",
                "pc_status": pc_status_str,
                "pc_online": final_online,
                "message": msg_str,
                "mac": sent_mac
            }
            body = json.dumps(res_data, indent=2).encode("utf-8") + b"\n"
            start_response("200 OK", [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*")
            ])
            return [body]

        # 5. /shutdown (e /off) -> Spegnimento con verifica stato effettivo post-arresto
        if path in ["/shutdown", "/off"]:
            if not check_auth(environ, params, config):
                err_data = {"status": "error", "message": "Accesso negato. Autenticazione richiesta"}
                body = json.dumps(err_data).encode("utf-8") + b"\n"
                start_response("401 Unauthorized", [
                    ("WWW-Authenticate", 'Basic realm="WOL Service"'),
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Length", str(len(body)))
                ])
                return [body]

            # Se è già spento prima del comando
            if not is_pc_online(config["PC_IP"]):
                res_data = {
                    "status": "success",
                    "action": "shutdown",
                    "pc_status": "spento",
                    "pc_online": False,
                    "message": "Il PC risultava già spento o non raggiungibile in rete"
                }
                body = json.dumps(res_data, indent=2).encode("utf-8") + b"\n"
                start_response("200 OK", [
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Access-Control-Allow-Origin", "*")
                ])
                return [body]

            # Invia comando di spegnimento via SSH
            ok, msg = run_ssh_command("shutdown /s /t 0", config["PC_IP"], config["PC_USER"], config["SSH_KEY"])
            if not ok:
                err_data = {
                    "status": "error",
                    "action": "shutdown",
                    "message": "Impossibile eseguire lo spegnimento via SSH",
                    "error_details": msg
                }
                body = json.dumps(err_data).encode("utf-8") + b"\n"
                start_response("500 Internal Server Error", [
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Length", str(len(body)))
                ])
                return [body]

            # Attende fino a 7.5s che il PC si spenga effettivamente (smetta di rispondere al ping)
            final_online, elapsed = wait_for_pc_state(config["PC_IP"], False, max_wait=7.5)

            if not final_online:
                pc_status_str = "spento"
                msg_str = f"Comando di spegnimento eseguito. Il PC risulta ora SPENTO (dopo {elapsed}s)"
            else:
                pc_status_str = "arresto_in_corso"
                msg_str = "Comando di spegnimento inviato con successo. Chiusura processi e arresto in corso"

            res_data = {
                "status": "success",
                "action": "shutdown",
                "pc_status": pc_status_str,
                "pc_online": final_online,
                "message": msg_str
            }
            body = json.dumps(res_data, indent=2).encode("utf-8") + b"\n"
            start_response("200 OK", [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Access-Control-Allow-Origin", "*")
            ])
            return [body]

        if path == "/favicon.ico":
            start_response("204 No Content", [("Content-Length", "0")])
            return []

        err_data = {"status": "error", "message": f"Endpoint '{path}' non trovato"}
        body = json.dumps(err_data).encode("utf-8") + b"\n"
        start_response("404 Not Found", [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body)))
        ])
        return [body]

    except Exception as e:
        err_data = {"status": "error", "server_error": str(e)}
        body = json.dumps(err_data).encode("utf-8") + b"\n"
        start_response("500 Internal Server Error", [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body)))
        ])
        return [body]


if __name__ == "__main__":
    print(f"[*] WOL & Shutdown Service in ascolto su http://0.0.0.0:{PORT}", flush=True)
    server = make_server("0.0.0.0", PORT, application, server_class=ThreadedWSGIServer)
    server.serve_forever()
