import urllib.request
import json

def find_download_url():
    node_id = "iGsPX8gdQ0mkk85hl04k8A"
    share_id = "He22TeS4vzbyssj6gyTJLzHNk0L0rE036CnErmVXP7o"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0",
        "Referer": f"https://www.amazon.it/photos/share/{share_id}"
    }

    # Prova 1: tempLink su children
    url1 = f"https://www.amazon.it/drive/v1/nodes/{node_id}?tempLink=true&shareId={share_id}&resourceVersion=V2&ContentType=JSON"
    try:
        with urllib.request.urlopen(urllib.request.Request(url1, headers=headers)) as r:
            d = json.loads(r.read().decode())
            print("tempLink da node:", d.get("tempLink"))
    except Exception as e:
        print("Errore 1:", e)

    # Prova 2: contentUrl o downloadUrl nelle proprietà
    url2 = f"https://www.amazon.it/drive/v1/nodes/sAP4FscvSvugGmUKw80WBA/children?resourceVersion=V2&ContentType=JSON&shareId={share_id}&tempLink=true&limit=2"
    try:
        with urllib.request.urlopen(urllib.request.Request(url2, headers=headers)) as r:
            d = json.loads(r.read().decode())
            first = d.get("data", [])[0]
            print("Chiavi primo elemento con tempLink=true:")
            for k, v in first.items():
                if "url" in k.lower() or "link" in k.lower():
                    print(f"  {k} -> {v}")
            if "tempLink" in first:
                print("TROVATO tempLink DIRETTO:", first["tempLink"])
    except Exception as e:
        print("Errore 2:", e)

if __name__ == "__main__":
    find_download_url()
