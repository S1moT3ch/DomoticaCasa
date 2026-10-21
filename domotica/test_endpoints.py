import urllib.request
import json

def inspect_visual():
    share_id = "He22TeS4vzbyssj6gyTJLzHNk0L0rE036CnErmVXP7o"
    node_id = "sAP4FscvSvugGmUKw80WBA" # La VISUAL_COLLECTION
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": f"https://www.amazon.it/photos/share/{share_id}",
        "Origin": "https://www.amazon.it"
    }
    
    # Prove diversi endpoint per estrarre gli elementi di una VISUAL_COLLECTION
    endpoints = [
        f"https://www.amazon.it/drive/v1/nodes/{node_id}/children?resourceVersion=V2&ContentType=JSON&shareId={share_id}&limit=100",
        f"https://www.amazon.it/drive/v1/visualSearch/nodes/{node_id}?resourceVersion=V2&ContentType=JSON&shareId={share_id}",
        f"https://www.amazon.it/drive/v1/shares/{share_id}/nodes?resourceVersion=V2&ContentType=JSON&limit=100",
        f"https://www.amazon.it/drive/v1/nodes?searchOnFamily=INCLUDE&tempLink=true&shareId={share_id}&limit=100",
        f"https://www.amazon.it/drive/v1/nodes?filters=parents:{node_id}&shareId={share_id}&limit=100"
    ]
    
    for ep in endpoints:
        print(f"\n--- TEST ENDPOINT: {ep[:80]}... ---")
        try:
            req = urllib.request.Request(ep, headers=headers)
            with urllib.request.urlopen(req) as r:
                print("STATUS:", r.status)
                d = json.loads(r.read().decode())
                data = d.get("data", [])
                count = d.get("count", len(data))
                print(f"COUNT: {count} | ELEMENTI RESTITUITI: {len(data)}")
                if data:
                    print(f"Primo elemento: {data[0].get('name')} (kind: {data[0].get('kind')})")
        except Exception as e:
            print("ERRORE:", e)

if __name__ == "__main__":
    inspect_visual()
