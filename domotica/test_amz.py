import urllib.request
import json
import ssl

def test():
    share_id = "He22TeS4vzbyssj6gyTJLzHNk0L0rE036CnErmVXP7o"
    url = f"https://www.amazon.it/drive/v1/shares/{share_id}?resourceVersion=V2&ContentType=JSON"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": f"https://www.amazon.it/photos/share/{share_id}",
        "Origin": "https://www.amazon.it"
    }
    
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            print("Status:", r.status)
            data = json.loads(r.read().decode())
            print("Data:", data)
    except Exception as e:
        print("Errore:", e)
        if hasattr(e, 'read'):
            print("Body errore:", e.read().decode('utf-8', errors='ignore')[:300])

if __name__ == "__main__":
    test()
