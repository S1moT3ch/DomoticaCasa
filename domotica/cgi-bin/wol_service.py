#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WOL & Remote Shutdown HTTP Service per OpenWrt
- WOL con selezione OS (Windows / Linux) e verifica attiva dell'avvio (fino a 25s).
- Endpoint Trampolino /get_next_boot per il riavvio automatico UEFI in Linux.
- Endpoint /reboot_to_linux per riavviare direttamente in Ubuntu da Windows.
- Spegnimento controllato con verifica dei programmi e lavori aperti sia per Windows che per Linux.
- Supporto Basic Auth, Token query string (?key=...) e bypass forzato (?force=true).
"""

import os
import sys
import json
import time
import socket
import re
import base64
import subprocess
import csv
from urllib.parse import parse_qs
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.abspath(os.path.join(BASE_DIR, "..", "bot", ".env"))
PORT = 5050
NEXT_BOOT_FILE = "/tmp/next_boot_os.txt"

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
    pc_user_win = _cached_env.get("PC_USER_WIN") or _cached_env.get("PC_USER", "SimoneCantore")
    pc_user_linux = _cached_env.get("PC_USER_LINUX") or _cached_env.get("PC_USER", "SimoneCantore")
    ubuntu_guid = _cached_env.get("UBUNTU_GUID", "{1ac58902-ddea-11f1-9c19-806e6f6e6963}")
    ssh_key = _cached_env.get("SSH_KEY", "/root/.ssh/id_rsa")

    return {
        "SECRET_PASS": os.getenv("WOL_PASS", wol_pass),
        "AUTH_USER": os.getenv("WOL_USER", wol_user),
        "DEFAULT_MAC": os.getenv("PC_MAC", pc_mac),
        "PC_IP": os.getenv("PC_IP", pc_ip),
        "PC_USER_WIN": os.getenv("PC_USER_WIN", pc_user_win),
        "PC_USER_LINUX": os.getenv("PC_USER_LINUX", pc_user_linux),
        "UBUNTU_GUID": os.getenv("UBUNTU_GUID", ubuntu_guid),
        "SSH_KEY": ssh_key
    }


def is_pc_online(ip):
    """Verifica rapida se il PC risponde al ping o alla porta RDP/SSH."""
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

    for port in (3389, 22):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.4)
                s.connect((ip, port))
                return True
        except Exception:
            pass

    return False


def wait_for_pc_state(ip, target_online_state, max_wait=25.0, interval=1.0):
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

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic_packet, (broadcast_ip, port))


def run_ssh_command(ip, user, key, command, timeout=5):
    """Esegue un comando remoto via SSH supportando sia dbclient (Dropbear) che ssh (OpenSSH)."""
    # 1. Prova prima con dbclient (client SSH nativo di OpenWrt con chiavi Dropbear)
    cmd_db = [
        "dbclient",
        "-y",
        "-i", key,
        f"{user}@{ip}",
        command
    ]
    try:
        res_db = subprocess.run(cmd_db, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        if res_db.returncode == 0:
            return 0, res_db.stdout, res_db.stderr
    except Exception:
        pass

    # 2. Se dbclient fallisce o non e' presente, prova con OpenSSH ssh
    cmd_ssh = [
        "ssh",
        "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={min(3, timeout)}",
        f"{user}@{ip}",
        command
    ]
    try:
        res = subprocess.run(cmd_ssh, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        if res.returncode == 0:
            return 0, res.stdout, res.stderr
        else:
            # Ritorna l'output di errore se anche ssh fallisce
            err_msg = res.stderr or (res_db.stderr if 'res_db' in locals() else "Errore esecuzione SSH")
            return res.returncode, res.stdout, err_msg
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout SSH execution"
    except Exception as e:
        return -2, "", str(e)


def detect_pc_os(ip, conf):
    """Rileva se il PC e' attualmente avviato su Windows o Linux analizzando il banner del demone SSH sulla porta 22."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect((ip, 22))
            banner = s.recv(256).decode('utf-8', errors='ignore')
            if 'ubuntu' in banner.lower() or 'linux' in banner.lower():
                return "linux"
            elif 'windows' in banner.lower():
                return "windows"
    except Exception:
        pass

    # Fallback con verifica uname
    rc, out, _ = run_ssh_command(ip, conf["PC_USER_LINUX"], conf["SSH_KEY"], "uname -s", timeout=2)
    if rc == 0 and "Linux" in out:
        return "linux"
    return "windows"


def get_open_programs_linux(ip, user, key):
    """Verifica le applicazioni aperte e sessioni attive su Linux."""
    check_cmd = """ps -u "$USER" -o comm=,args= 2>/dev/null"""
    rc, out, err = run_ssh_command(ip, user, key, check_cmd, timeout=4)
    if rc != 0:
        return False, []

    ignore_prefixes = (
        'gnome-', 'gsd-', 'evolution-', 'xdg-', 'ibus-', 'goa-', 'at-spi',
        'pipewire', 'wireplumber', 'systemd', 'tracker-', 'gvfs', 'snapd-',
        'dconf-', 'localsearch', 'gcr-', 'update-notifier', 'user-session-',
        'gdm-', '(sd-pam)', 'gjs', 'dbus-', 'pulseaudio', 'glycin-', 'ubuntu-'
    )
    ignore_exact = {
        'ssh', 'sshd', 'sshd-session', 'bash', 'sh', 'ps', 'sort', 'which',
        'dbclient', 'login', 'sudo', 'systemd', 'dbus-daemon', 'ssh-agent',
        'gcr-ssh-agent', 'ptyxis-agent', 'snap', 'mpris-proxy', 'bwrap',
        'fusermount', 'fusermount3', 'whoopsie', 'wsdd'
    }

    programs = []
    seen = set()
    lines = [line.strip() for line in out.strip().splitlines() if line.strip()]
    for line in lines:
        parts = line.split(None, 1)
        if not parts:
            continue
        p = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        if p in ignore_exact:
            continue
        if any(p.startswith(prefix) for prefix in ignore_prefixes):
            continue

        # Filtra demoni interni Python (come wsdd per network discovery Samba o updater di sistema)
        if p in ('python', 'python3'):
            if any(x in args for x in ('wsdd', '/usr/share/', '/usr/lib/', 'update-manager', 'ubuntu-drivers')):
                continue

        # Filtra qualsiasi helper interno eseguito da libexec o da snap desktop integration
        if '/usr/libexec/' in args or '/snap/snapd-desktop-integration/' in args:
            continue

        if p not in seen:
            seen.add(p)
            programs.append({"process": p, "title": f"Processo attivo: {p}"})

    # Controlla terminali e sessioni interattive attive (escludendo sessioni di sistema e OpenWrt)
    rc_w, out_w, _ = run_ssh_command(ip, user, key, "w -h 2>/dev/null", timeout=3)
    if rc_w == 0 and out_w.strip():
        for line in out_w.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                # Escludi le connessioni provenienti dal router stesso
                if "192.168.1.162" in line:
                    continue
                tty_name = parts[1]
                # tty1, tty2, seat0 rappresentano la sessione del desktop grafico, non terminali aperti
                if tty_name.startswith("tty") or tty_name.startswith("seat"):
                    continue
                # Cattura solo pseudo-terminali reali aperti (pts/0, pts/1, ecc.)
                if tty_name.startswith("pts"):
                    user_name = parts[0]
                    programs.append({"process": "terminal_session", "title": f"Terminale aperto: {user_name} su {tty_name}"})

    return True, programs


def parse_windows_tasklist_csv(csv_text):
    """Analizza l'output CSV di tasklist /v estraendo le finestre utente e schede browser attive."""
    try:
        lines = [l for l in csv_text.strip().splitlines() if l.strip().startswith('"')]
        if not lines:
            return []
        reader = csv.reader(lines)
        rows = list(reader)
        if not rows or len(rows) < 2:
            return []

        header = [h.strip().lower() for h in rows[0]]
        proc_idx = 0
        title_idx = -1
        for idx, col in enumerate(header):
            if 'immagine' in col or 'image' in col:
                proc_idx = idx
            elif 'titolo' in col or 'window' in col or 'title' in col:
                title_idx = idx

        if title_idx == -1:
            title_idx = 8 if len(rows[0]) > 8 else -1

        if title_idx == -1:
            return []

        ignore_procs = {
            'dwm.exe', 'svchost.exe', 'taskhostw.exe', 'searchhost.exe',
            'startmenuexperiencehost.exe', 'shellexperiencehost.exe', 'shellhost.exe',
            'lockapp.exe', 'textinputhost.exe', 'widgetboard.exe', 'widgetservice.exe',
            'runtimebroker.exe', 'splwow64.exe', 'unsecapp.exe', 'conhost.exe',
            'atieclxx.exe', 'crossdeviceresume.exe', 'crossdeviceservice.exe',
            'phoneexperiencehost.exe', 'reconsentnotification.exe', 'desktopextension.exe',
            'monotificationux.exe', 'appactions.exe', 'overlayhelper.exe',
            'rtkauduservice64.exe', 'hpsystemeventutilitybackground.exe', 'hpmedianetwork.exe',
            'amdrsserv.exe', 'onedrive.sync.service.exe', 'onedrive.exe', 'onedrivelauncher.exe',
            'sihost.exe', 'ctfmon.exe', 'vncagent.exe', 'rvncagent.exe',
            'rvncserverui.exe', 'vncserverui.exe', 'synptenh.exe', 'securityhealthsystray.exe',
            'radeonsoftware.exe', 'amdrssrcext.exe', 'csrss.exe', 'winlogon.exe', 'fontdrvhost.exe',
            'logonui.exe', 'amdpmfserviceuser.exe', 'asusoptimizationstartuptask.exe',
            'defendersessionhelper.exe', 'rdpclip.exe', 'rdpinput.exe', 'smartscreen.exe',
            'igoswserver.exe', 'aixhost.exe', 'clicktodo.exe', 'aihost.exe', 'ghelper.exe',
            'msedgewebview2.exe', 'microsoftstartfeedprovider.exe', 'avpui.exe', 'b4notify.exe',
            'tabtip.exe', 'tailscale-ipn.exe', 'hppusg.exe', 'kpm_tray.exe', 'cceventapp.exe',
            'cncmd.exe', 'applicationframehost.exe', 'ksdeui.exe', 'explorer.exe', 'amdow.exe'
        }
        ignore_titles = {
            'n/d', 'n/a', '', 'olemainthreadwndname', 'olechannelwnd',
            'task host window', 'dwm notification window', 'smartdc', 'wuiconwindow',
            'amd:dvr-capturingwindow', 'kpm.rootwindow', 'ad_msg_wnd',
            'realvnc connect inbound - chat', 'nuova notifica', 'cerca', 'start',
            'widget di windows', 'esperienza input di windows',
            'schermata di blocco predefinita di windows', 'rapporti', 'kaspersky vpn',
            'controlcenter4', 'brother status monitor', 'quick settings', 'copilot quick view',
            'impostazioni', 'settings'
        }

        programs = []
        seen = set()
        for row in rows[1:]:
            if len(row) <= max(proc_idx, title_idx):
                continue
            p = row[proc_idx].strip()
            t = row[title_idx].strip()
            p_lower = p.lower()
            t_lower = t.lower()

            if p_lower in ignore_procs:
                if p_lower == 'explorer.exe' and t and t_lower not in ignore_titles and t_lower not in ('n/d', 'n/a', ''):
                    display_title = f"Esplora risorse: {t}"
                else:
                    continue
            else:
                if t and t_lower not in ignore_titles and t_lower not in ('n/d', 'n/a'):
                    display_title = t
                elif t_lower in ('n/d', 'n/a', ''):
                    display_title = f"Programma attivo: {p}"
                else:
                    continue

            if t.startswith('.NET-Broadcast') or t.startswith('GDI+ Window'):
                continue
            if 'notification' in t_lower:
                continue

            if p_lower not in seen:
                seen.add(p_lower)
                programs.append({
                    "process": p,
                    "title": display_title
                })

        return programs
    except Exception:
        return []


def get_open_programs_win(ip, user, key):
    """Verifica le finestre e schede aperte su Windows in tempo reale."""
    # 1. Metodo primario e più accurato: lettura del file .open_apps.json
    # generato in tempo reale (ogni 3s) da monitor_apps.ps1 nella sessione grafica utente
    cmd_json = 'type "%USERPROFILE%\\.open_apps.json" 2>nul || type "C:\\Users\\canto\\.open_apps.json" 2>nul'
    rc_f, out_f, _ = run_ssh_command(ip, user, key, cmd_json, timeout=3)
    if rc_f == 0 and out_f.strip():
        clean_out = out_f.strip().lstrip('\ufeff').lstrip('ï»¿')
        try:
            raw_data = json.loads(clean_out)
            if isinstance(raw_data, dict):
                raw_apps = [raw_data]
            elif isinstance(raw_data, list):
                raw_apps = raw_data
            else:
                raw_apps = []

            ignore_titles = {
                'n/d', 'n/a', '', 'olemainthreadwndname', 'olechannelwnd',
                'task host window', 'dwm notification window', 'smartdc', 'wuiconwindow',
                'amd:dvr-capturingwindow', 'kpm.rootwindow', 'ad_msg_wnd',
                'realvnc connect inbound - chat', 'nuova notifica', 'cerca', 'start',
                'widget di windows', 'esperienza input di windows',
                'schermata di blocco predefinita di windows', 'rapporti', 'kaspersky vpn',
                'controlcenter4', 'brother status monitor', 'quick settings', 'copilot quick view',
                'impostazioni', 'settings'
            }
            ignore_procs = {
                'applicationframehost', 'systemsettings', 'explorer', 'textinputhost',
                'searchhost', 'startmenuexperiencehost', 'shellexperiencehost',
                'lockapp', 'ghelper', 'onedrive', 'rtkuwp_rs5', 'backgroundtaskhost',
                'powershell', 'cmd', 'conhost'
            }
            apps = []
            seen = set()
            for item in raw_apps:
                if not isinstance(item, dict):
                    continue
                p = str(item.get('process', '')).strip()
                t = str(item.get('title', '')).strip()
                if not p:
                    continue
                p_lower = p.lower()
                t_lower = t.lower()
                if p_lower in ignore_procs and (not t or t_lower in ignore_titles):
                    continue
                if t_lower in ignore_titles:
                    continue
                if p_lower not in seen:
                    seen.add(p_lower)
                    apps.append({
                        "process": p,
                        "title": t if t else f"Programma attivo: {p}"
                    })
            return True, apps
        except Exception:
            pass

    # 2. Fallback: interrogazione tasklist /v se .open_apps.json non è leggibile
    cmd_task = 'tasklist /v /fi "SESSION gt 0" /fo csv'
    rc, out, err = run_ssh_command(ip, user, key, cmd_task, timeout=10)
    if rc == 0 and out.strip():
        apps = parse_windows_tasklist_csv(out)
        return True, apps

    return False, []


def get_open_programs(ip, conf):
    """Verifica le app aperte sul sistema operativo correntemente attivo."""
    os_name = detect_pc_os(ip, conf)
    if os_name == "linux":
        ok, apps = get_open_programs_linux(ip, conf["PC_USER_LINUX"], conf["SSH_KEY"])
        return ok, apps, "linux"
    else:
        ok, apps = get_open_programs_win(ip, conf["PC_USER_WIN"], conf["SSH_KEY"])
        return ok, apps, "windows"


def perform_shutdown(ip, conf, force=False):
    """Esegue lo spegnimento sicuro o forzato."""
    os_name = detect_pc_os(ip, conf)
    if os_name == "linux":
        user = conf["PC_USER_LINUX"]
        cmd = "sudo /usr/bin/systemctl poweroff || sudo /usr/sbin/poweroff || sudo /usr/sbin/shutdown -h now"
    else:
        user = conf["PC_USER_WIN"]
        cmd = "shutdown /s /f /t 0"

    rc, out, err = run_ssh_command(ip, user, conf["SSH_KEY"], cmd, timeout=5)
    return (rc == 0), os_name, err


def json_response(start_response, status_code, data):
    status_str = f"{status_code} OK" if status_code == 200 else f"{status_code} Error"
    if status_code == 401:
        status_str = "401 Unauthorized"
    elif status_code == 400:
        status_str = "400 Bad Request"
    elif status_code == 404:
        status_str = "404 Not Found"
    elif status_code == 500:
        status_str = "500 Internal Server Error"

    body = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
    headers = [
        ('Content-Type', 'application/json; charset=utf-8'),
        ('Content-Length', str(len(body)))
    ]
    if status_code == 401:
        headers.append(('WWW-Authenticate', 'Basic realm="WOL Service"'))

    start_response(status_str, headers)
    return [body]


def check_auth(environ, qs, conf):
    key = qs.get("key", [None])[0]
    if key and key == conf["SECRET_PASS"]:
        return True

    auth_header = environ.get('HTTP_AUTHORIZATION')
    if auth_header and auth_header.startswith('Basic '):
        try:
            encoded = auth_header.split(' ', 1)[1]
            decoded = base64.b64decode(encoded).decode('utf-8')
            u, p = decoded.split(':', 1)
            if u == conf["AUTH_USER"] and p == conf["SECRET_PASS"]:
                return True
        except Exception:
            pass

    return False


def application(environ, start_response):
    try:
        conf = get_config()
        path = environ.get('PATH_INFO', '')
        qs = parse_qs(environ.get('QUERY_STRING', ''))

        # 1. Endpoint /get_next_boot (per lo script di trampolino Windows al boot)
        if path in ('/get_next_boot', '/next_boot'):
            target_os = "windows"
            if os.path.exists(NEXT_BOOT_FILE):
                try:
                    with open(NEXT_BOOT_FILE, "r") as f:
                        target_os = f.read().strip()
                except Exception:
                    pass
            # Se era linux, resettiamo a windows per i boot successivi
            if target_os == "linux":
                try:
                    with open(NEXT_BOOT_FILE, "w") as f:
                        f.write("windows")
                except Exception:
                    pass
            return json_response(start_response, 200, {
                "status": "ok",
                "target_os": target_os
            })

        # 2. Controllo autenticazione per le altre azioni
        if not check_auth(environ, qs, conf):
            return json_response(start_response, 401, {
                "status": "error",
                "message": "Autenticazione richiesta. Fornisci Basic Auth o parametro ?key=..."
            })

        # Endpoint Root
        if path == '/' or path == '':
            body = b"WOL & Dual-Boot Shutdown Service is running OK"
            start_response('200 OK', [('Content-Type', 'text/plain'), ('Content-Length', str(len(body)))])
            return [body]

        # Endpoint Health / Status
        if path in ('/health', '/status'):
            online = is_pc_online(conf["PC_IP"])
            detected_os = detect_pc_os(conf["PC_IP"], conf) if online else None
            return json_response(start_response, 200, {
                "status": "ok",
                "service": "wol_service",
                "pc_online": online,
                "pc_status": "acceso" if online else "spento",
                "current_os": detected_os,
                "pc_ip": conf["PC_IP"],
                "pc_user_win": conf["PC_USER_WIN"],
                "pc_user_linux": conf["PC_USER_LINUX"],
                "default_mac": conf["DEFAULT_MAC"]
            })

        # Endpoint /check_apps
        if path == '/check_apps':
            online = is_pc_online(conf["PC_IP"])
            if not online:
                return json_response(start_response, 200, {
                    "status": "ok",
                    "pc_online": False,
                    "pc_status": "spento",
                    "message": "Il PC e' spento o non raggiungibile."
                })

            ok, apps, os_name = get_open_programs(conf["PC_IP"], conf)
            return json_response(start_response, 200, {
                "status": "ok",
                "pc_online": True,
                "os": os_name,
                "open_programs": apps,
                "count": len(apps)
            })

        # Endpoint /wol (supporta ?os=linux o ?os=windows)
        if path == '/wol':
            mac = qs.get("mac", [conf["DEFAULT_MAC"]])[0]
            req_os = qs.get("os", ["windows"])[0].lower()
            target_os = "linux" if req_os in ("linux", "ubuntu") else "windows"

            # Salva la preferenza per il trampolino UEFI
            try:
                with open(NEXT_BOOT_FILE, "w") as f:
                    f.write(target_os)
            except Exception:
                pass

            send_magic_packet(mac)

            wait_str = qs.get("wait", ["22"])[0]
            try:
                wait_timeout = float(wait_str)
            except ValueError:
                wait_timeout = 22.0

            is_online, elapsed = wait_for_pc_state(conf["PC_IP"], True, max_wait=wait_timeout)
            return json_response(start_response, 200, {
                "status": "ok",
                "action": "wake_on_lan",
                "target_os": target_os,
                "mac": mac,
                "pc_ip": conf["PC_IP"],
                "pc_online": is_online,
                "pc_status": "acceso" if is_online else "in_avvio",
                "boot_seconds": elapsed,
                "message": f"WOL inviato (target: {target_os}). PC {'acceso con successo' if is_online else 'in fase di avvio'}."
            })

        # Endpoint /reboot_to_linux (riavvio immediato in Ubuntu da Windows)
        if path == '/reboot_to_linux':
            online = is_pc_online(conf["PC_IP"])
            if not online:
                return json_response(start_response, 400, {
                    "status": "error",
                    "message": "Il PC e' spento. Usa /wol?os=linux per accenderlo direttamente in Ubuntu."
                })

            os_name = detect_pc_os(conf["PC_IP"], conf)
            if os_name == "linux":
                return json_response(start_response, 200, {
                    "status": "ok",
                    "message": "Il PC e' gia' avviato in Linux Ubuntu!"
                })

            cmd = f'bcdedit /set "{{fwbootmgr}}" bootsequence "{conf["UBUNTU_GUID"]}" && shutdown /r /f /t 0'
            rc, out, err = run_ssh_command(conf["PC_IP"], conf["PC_USER_WIN"], conf["SSH_KEY"], cmd, timeout=6)
            if rc == 0:
                return json_response(start_response, 200, {
                    "status": "ok",
                    "action": "reboot_to_linux",
                    "message": "Sequenza UEFI Ubuntu impostata. Riavvio in corso..."
                })
            else:
                return json_response(start_response, 500, {
                    "status": "error",
                    "message": "Impossibile impostare la sequenza di avvio UEFI.",
                    "details": err or out
                })

        # Endpoint /check_programs (controllo app aperte senza spegnere)
        if path in ('/check_programs', '/programs'):
            online = is_pc_online(conf["PC_IP"])
            if not online:
                return json_response(start_response, 200, {
                    "status": "ok",
                    "pc_online": False,
                    "pc_status": "spento",
                    "open_programs": [],
                    "count": 0
                })
            ok, apps, os_name = get_open_programs(conf["PC_IP"], conf)
            return json_response(start_response, 200, {
                "status": "ok",
                "pc_online": True,
                "os": os_name,
                "open_programs": apps,
                "count": len(apps),
                "safe_to_shutdown": len(apps) == 0
            })

        # Endpoint /shutdown
        if path == '/shutdown':
            force = qs.get("force", ["false"])[0].lower() in ("true", "1", "yes")
            online = is_pc_online(conf["PC_IP"])
            if not online:
                return json_response(start_response, 200, {
                    "status": "ok",
                    "action": "shutdown",
                    "pc_online": False,
                    "pc_status": "spento",
                    "message": "Il PC e' gia' spento."
                })

            # Verifica programmi e lavori aperti
            ok, apps, os_name = get_open_programs(conf["PC_IP"], conf)
            if not ok and not force:
                return json_response(start_response, 500, {
                    "status": "error",
                    "reason": "check_failed",
                    "os": os_name,
                    "message": f"Impossibile verificare con sicurezza lo stato dei programmi aperti su {os_name} (errore o timeout SSH).",
                    "hint": "Invia ?force=true per forzare lo spegnimento immediato."
                })

            if apps and not force:
                return json_response(start_response, 200, {
                    "status": "error",
                    "reason": "shutdown_blocked",
                    "os": os_name,
                    "message": f"Spegnimento bloccato: sono presenti {len(apps)} programmi o finestre aperti su {os_name}.",
                    "open_programs": apps,
                    "count": len(apps),
                    "hint": "Invia ?force=true per forzare lo spegnimento immediato."
                })

            # Esecuzione spegnimento
            success, os_name, err = perform_shutdown(conf["PC_IP"], conf, force=force)
            if not success:
                return json_response(start_response, 500, {
                    "status": "error",
                    "os": os_name,
                    "message": "Errore durante l'esecuzione del comando di spegnimento.",
                    "details": err
                })

            is_online, elapsed = wait_for_pc_state(conf["PC_IP"], False, max_wait=20.0)
            return json_response(start_response, 200, {
                "status": "ok",
                "action": "shutdown",
                "os": os_name,
                "forced": force,
                "pc_online": is_online,
                "pc_status": "spento" if not is_online else "in_spegnimento",
                "shutdown_seconds": elapsed,
                "message": f"PC ({os_name}) {'spento con successo' if not is_online else 'ancora in fase di arresto'}."
            })

        return json_response(start_response, 404, {"status": "error", "message": "Endpoint non trovato"})

    except Exception as e:
        return json_response(start_response, 500, {
            "status": "error",
            "message": "Errore interno del server",
            "details": str(e)
        })

if __name__ == '__main__':
    print(f"Starting WOL & Dual-Boot Service on port {PORT}...")
    server = make_server('0.0.0.0', PORT, application, server_class=ThreadedWSGIServer)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nService stopped.")
