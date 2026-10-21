#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import cgi
import cgitb
import json
import os
import re
import sys
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime

cgitb.enable()

MEDIA_BASE = "/mnt/sda5/media"
MOVIES_DIR = os.path.join(MEDIA_BASE, "Movies")
PICTURES_DIR = os.path.join(MEDIA_BASE, "Pictures")
LOG_DIR = "/tmp/media_downloader_logs"
os.makedirs(LOG_DIR, exist_ok=True)

def send_json(data, status=200):
    print("Content-Type: application/json; charset=utf-8")
    print(f"Status: {status}")
    print()
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(0)

def extract_amazon_share_id(url):
    m = re.search(r'amazon\.[a-z.]+/photos/share/([a-zA-Z0-9_-]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'shareId=([a-zA-Z0-9_-]+)', url)
    if m:
        return m.group(1)
    return None

def extract_gdrive_id(url):
    m = re.search(r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)', url)
    if m:
        return m.group(1), 'file'
    m = re.search(r'drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)', url)
    if m:
        return m.group(1), 'folder'
    m = re.search(r'id=([a-zA-Z0-9_-]+)', url)
    if m:
        return m.group(1), 'file'
    return None, None

def get_amazon_headers(share_id):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": f"https://www.amazon.it/photos/share/{share_id}",
        "Origin": "https://www.amazon.it"
    }

def get_amazon_album_info(share_id):
    api_url = f"https://www.amazon.it/drive/v1/shares/{share_id}?resourceVersion=V2&ContentType=JSON"
    req = urllib.request.Request(api_url, headers=get_amazon_headers(share_id))
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode())
        node = data.get("nodeInfo", {})
        return {
            "name": node.get("name", "Album_Amazon"),
            "node_id": node.get("id"),
            "kind": node.get("kind")
        }

def get_amazon_files(share_id, node_id):
    files = []
    headers = get_amazon_headers(share_id)
    
    # Coda di nodi da esplorare (es. prima la root collection, poi la visual collection dei file)
    nodes_to_process = [node_id]
    
    while nodes_to_process:
        current_node = nodes_to_process.pop(0)
        start_token = ""
        while True:
            url = f"https://www.amazon.it/drive/v1/nodes/{current_node}/children?resourceVersion=V2&ContentType=JSON&shareId={share_id}&tempLink=true&limit=200"
            if start_token:
                url += f"&startToken={start_token}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                items = data.get("data", [])
                for it in items:
                    kind = it.get("kind", "")
                    # Se è un contenitore (VISUAL_COLLECTION, ALBUM, GROUP, FOLDER), aggiungilo alla coda
                    if kind in ["VISUAL_COLLECTION", "COLLECTION", "FOLDER", "GROUP", "ALBUM"]:
                        nodes_to_process.append(it.get("id"))
                    else:
                        name = it.get("name", "file.jpg")
                        content_type = it.get("contentProperties", {}).get("contentType", "")
                        download_url = it.get("tempLink") or f"https://www.amazon.it/drive/v1/nodes/{it.get('id')}/content?download=true&shareId={share_id}"
                        size = it.get("contentProperties", {}).get("size", 0)
                        is_video = "video" in content_type.lower() or name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.ts', '.m4v'))
                        files.append({
                            "id": it.get("id"),
                            "name": name,
                            "url": download_url,
                            "size": size,
                            "is_video": is_video,
                            "content_type": content_type
                        })
                start_token = data.get("nextToken")
                if not start_token or not items:
                    break
    return files

def trigger_download_job(target_dir, files, job_id):
    # Genera script bash di download eseguito in background
    job_sh = os.path.join(LOG_DIR, f"{job_id}.sh")
    job_log = os.path.join(LOG_DIR, f"{job_id}.log")
    job_status = os.path.join(LOG_DIR, f"{job_id}.status")

    with open(job_status, "w") as f:
        f.write(json.dumps({"status": "RUNNING", "total": len(files), "completed": 0, "current": "", "error": None}))

    with open(job_sh, "w") as f:
        f.write("#!/bin/sh\n")
        f.write(f"mkdir -p '{target_dir}'\n")
        f.write(f"cd '{target_dir}'\n")
        for idx, fi in enumerate(files, 1):
            fname = fi['name'].replace("'", "'\\''")
            furl = fi['url'].replace("'", "'\\''")
            f.write(f"echo '[FILE {idx}/{len(files)}] Scaricando {fname}...' >> '{job_log}'\n")
            f.write(f"python3 -c 'import json; p=\"{job_status}\"; d=json.load(open(p)); d[\"completed\"]={idx-1}; d[\"current\"]=\"{fname}\"; json.dump(d, open(p, \"w\"))'\n")
            f.write(f"curl -sSL -A 'Mozilla/5.0' -o '{fname}' '{furl}' >> '{job_log}' 2>&1\n")
        
        # Fine download: aggiorna minidlna
        f.write(f"echo 'Tutti i file scaricati con successo!' >> '{job_log}'\n")
        f.write(f"python3 -c 'import json; p=\"{job_status}\"; d=json.load(open(p)); d[\"status\"]=\"DONE\"; d[\"completed\"]={len(files)}; d[\"current\"]=\"\"; json.dump(d, open(p, \"w\"))'\n")
        f.write("/usr/bin/minidlna -R 2>/dev/null || /etc/init.d/minidlna restart >/dev/null 2>&1\n")

    os.chmod(job_sh, 0o755)
    subprocess.Popen([job_sh], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

def main():
    form = cgi.FieldStorage()
    action = form.getvalue("action", "inspect")

    if action == "inspect":
        url = form.getvalue("url", "").strip()
        if not url:
            send_json({"error": "URL non specificato"}, 400)

        amazon_share = extract_amazon_share_id(url)
        if amazon_share:
            try:
                info = get_amazon_album_info(amazon_share)
                files = get_amazon_files(amazon_share, info["node_id"])
                video_count = sum(1 for f in files if f["is_video"])
                photo_count = len(files) - video_count
                send_json({
                    "provider": "amazon",
                    "title": info["name"],
                    "share_id": amazon_share,
                    "total_files": len(files),
                    "video_count": video_count,
                    "photo_count": photo_count,
                    "suggested_type": "movies" if video_count >= photo_count and video_count > 0 else "pictures",
                    "files_preview": files[:5]
                })
            except Exception as e:
                send_json({"error": f"Errore lettura album Amazon: {e}"}, 500)

        g_id, g_type = extract_gdrive_id(url)
        if g_id:
            send_json({
                "provider": "gdrive",
                "title": f"GoogleDrive_{g_id[:8]}",
                "file_id": g_id,
                "type": g_type,
                "suggested_type": "movies"
            })

        send_json({"error": "Link non riconosciuto. Inserire un link di condivisione valido di Amazon Photos o Google Drive."}, 400)

    elif action == "start":
        url = form.getvalue("url", "").strip()
        folder_name = form.getvalue("folder_name", "").strip()
        media_type = form.getvalue("media_type", "movies").strip() # 'movies' o 'pictures'

        if not url or not folder_name:
            send_json({"error": "URL e nome cartella obbligatori"}, 400)

        # Sanifica nome cartella
        folder_name = re.sub(r'[^a-zA-Z0-9_\- ]', '_', folder_name).strip()
        target_dir = os.path.join(MOVIES_DIR if media_type == "movies" else PICTURES_DIR, folder_name)

        job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        amazon_share = extract_amazon_share_id(url)
        if amazon_share:
            try:
                info = get_amazon_album_info(amazon_share)
                files = get_amazon_files(amazon_share, info["node_id"])
                trigger_download_job(target_dir, files, job_id)
                send_json({
                    "status": "STARTED",
                    "job_id": job_id,
                    "target_dir": target_dir,
                    "total_files": len(files)
                })
            except Exception as e:
                send_json({"error": f"Errore avvio download Amazon: {e}"}, 500)

        g_id, g_type = extract_gdrive_id(url)
        if g_id:
            # Download da Google Drive
            os.makedirs(target_dir, exist_ok=True)
            job_sh = os.path.join(LOG_DIR, f"{job_id}.sh")
            job_log = os.path.join(LOG_DIR, f"{job_id}.log")
            job_status = os.path.join(LOG_DIR, f"{job_id}.status")

            with open(job_status, "w") as f:
                f.write(json.dumps({"status": "RUNNING", "total": 1, "completed": 0, "current": folder_name, "error": None}))

            with open(job_sh, "w") as f:
                f.write("#!/bin/sh\n")
                f.write(f"cd '{target_dir}'\n")
                f.write(f"echo '[GDRIVE] Avvio download file {g_id}...' > '{job_log}'\n")
                # Download diretto file gdrive con curl/python
                f.write(f"curl -sSL -A 'Mozilla/5.0' 'https://drive.usercontent.google.com/download?id={g_id}&export=download&confirm=t' -o '{folder_name}.mp4' >> '{job_log}' 2>&1\n")
                f.write(f"python3 -c 'import json; p=\"{job_status}\"; d=json.load(open(p)); d[\"status\"]=\"DONE\"; d[\"completed\"]=1; d[\"current\"]=\"\"; json.dump(d, open(p, \"w\"))'\n")
                f.write("/usr/bin/minidlna -R 2>/dev/null || /etc/init.d/minidlna restart >/dev/null 2>&1\n")

            os.chmod(job_sh, 0o755)
            subprocess.Popen([job_sh], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

            send_json({
                "status": "STARTED",
                "job_id": job_id,
                "target_dir": target_dir,
                "total_files": 1
            })

    elif action == "status":
        job_id = form.getvalue("job_id", "").strip()
        job_status = os.path.join(LOG_DIR, f"{job_id}.status")
        job_log = os.path.join(LOG_DIR, f"{job_id}.log")

        if not os.path.exists(job_status):
            send_json({"error": "Job non trovato"}, 404)

        try:
            with open(job_status, "r") as f:
                st = json.load(f)
            log_tail = ""
            if os.path.exists(job_log):
                with open(job_log, "r", errors="ignore") as f:
                    log_tail = "\n".join(f.readlines()[-8:])
            st["log"] = log_tail
            send_json(st)
        except Exception as e:
            send_json({"error": str(e)}, 500)

    elif action == "list_folders":
        # Elenca le cartelle esistenti con numero file e dimensione
        def get_dir_info(base_dir):
            res = []
            if not os.path.exists(base_dir):
                return res
            for d in sorted(os.listdir(base_dir)):
                d_path = os.path.join(base_dir, d)
                if os.path.isdir(d_path) and not d.startswith('.'):
                    files_in_dir = []
                    total_size = 0
                    for root, _, fs in os.walk(d_path):
                        for f in fs:
                            if not f.startswith('.'):
                                fp = os.path.join(root, f)
                                sz = os.path.getsize(fp) if os.path.exists(fp) else 0
                                total_size += sz
                                files_in_dir.append({"name": f, "size": sz})
                    res.append({
                        "name": d,
                        "count": len(files_in_dir),
                        "total_size": total_size,
                        "files": files_in_dir
                    })
            return res

        send_json({
            "movies": get_dir_info(MOVIES_DIR),
            "pictures": get_dir_info(PICTURES_DIR)
        })

    elif action == "delete_folder":
        import shutil
        media_type = form.getvalue("media_type", "").strip()
        folder_name = form.getvalue("folder_name", "").strip()

        if not folder_name or folder_name in ['.', '..', '/']:
            send_json({"error": "Nome cartella non valido"}, 400)

        base_dir = MOVIES_DIR if media_type == "movies" else PICTURES_DIR
        target_path = os.path.abspath(os.path.join(base_dir, folder_name))

        # Sicurezza path traversal
        if not target_path.startswith(os.path.abspath(base_dir)):
            send_json({"error": "Accesso non autorizzato fuori dalla directory media"}, 403)

        if not os.path.exists(target_path):
            send_json({"error": "Cartella non trovata"}, 404)

        try:
            shutil.rmtree(target_path)
            # Forza scansione minidlna
            subprocess.run(["/usr/bin/minidlna", "-R"], capture_output=True)
            send_json({"status": "DELETED", "message": f"Cartella '{folder_name}' rimossa con successo."})
        except Exception as e:
            send_json({"error": f"Errore cancellazione: {e}"}, 500)

    elif action == "delete_file":
        media_type = form.getvalue("media_type", "").strip()
        folder_name = form.getvalue("folder_name", "").strip()
        file_name = form.getvalue("file_name", "").strip()

        if not folder_name or not file_name:
            send_json({"error": "Parametri mancanti"}, 400)

        base_dir = MOVIES_DIR if media_type == "movies" else PICTURES_DIR
        target_file = os.path.abspath(os.path.join(base_dir, folder_name, file_name))

        if not target_file.startswith(os.path.abspath(base_dir)):
            send_json({"error": "Accesso non autorizzato"}, 403)

        if not os.path.exists(target_file):
            send_json({"error": "File non trovato"}, 404)

        try:
            os.remove(target_file)
            subprocess.run(["/usr/bin/minidlna", "-R"], capture_output=True)
            send_json({"status": "DELETED", "message": f"File '{file_name}' rimosso con successo."})
        except Exception as e:
            send_json({"error": f"Errore cancellazione file: {e}"}, 500)

    else:
        send_json({"error": "Azione non valida"}, 400)

if __name__ == "__main__":
    main()
