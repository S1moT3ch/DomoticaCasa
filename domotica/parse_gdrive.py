import urllib.request
import re
import json

def parse_ivd():
    folder_id = "11cfA0xidp3u8Z_BWSe0zevuo8MQYWywd"
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    })
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    
    # Estrarre il titolo cartella
    title_m = re.search(r"<title>(.*?)</title>", html)
    title = title_m.group(1).replace("- Google Drive", "").strip() if title_m else "GoogleDrive_Folder"
    print("Titolo:", title)
    
    # Estrazione pattern elementi [id, null, name, mime, ...]
    # Nei dati di Google Drive, ogni file compare con una tupla caratteristica:
    # ["ID_DEL_FILE",null,"NOME_DEL_FILE.ext"
    matches = re.findall(r'\["([a-zA-Z0-9_-]{25,})",null,"([^"]+)"', html)
    
    seen = set()
    files = []
    for fid, fname in matches:
        if fid not in seen and "." in fname and not fname.startswith("http"):
            seen.add(fid)
            is_video = fname.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.ts', '.m4v'))
            files.append({
                "id": fid,
                "name": fname,
                "is_video": is_video,
                "url": f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
            })
            
    print(f"File unici trovati nella cartella GDrive: {len(files)}")
    for f in files[:8]:
        print(f"- {f['name']} (ID: {f['id']})")

if __name__ == "__main__":
    parse_ivd()
