import urllib.request
import re
import json

def test_gdrive():
    folder_id = "11cfA0xidp3u8Z_BWSe0zevuo8MQYWywd"
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            title_m = re.search(r"<title>(.*?)</title>", html)
            title = title_m.group(1).replace("- Google Drive", "").strip() if title_m else "GoogleDrive_Folder"
            print("Titolo Cartella GDrive:", title)
            
            # Cerca il blocco di inizializzazione dati di Google Drive
            # Contiene i metadati di tutti i file nella cartella!
            m = re.search(r"window\['_DRIVE_ivd'\]\s*=\s*'(.*?)';", html)
            if not m:
                m = re.search(r"_DRIVE_ivd\s*=\s*'(.*?)';", html)
            
            if m:
                raw_json = m.group(1).encode().decode('unicode-escape')
                print("Trovato blocco _DRIVE_ivd! Lunghezza:", len(raw_json))
            else:
                print("Nessun blocco _DRIVE_ivd, cerchiamo file ID generici...")
                # Metodo fallback: cerca gli ID dei file e i loro nomi
                file_matches = re.findall(r'\["([a-zA-Z0-9_-]{28,})","([^"]+)"', html)
                print("File matches trovati:", len(file_matches))
                for fid, fname in file_matches[:10]:
                    print(f"- {fname} (ID: {fid})")
    except Exception as e:
        print("Errore:", e)

if __name__ == "__main__":
    test_gdrive()
