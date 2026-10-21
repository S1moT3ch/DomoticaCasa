import urllib.request
import json

def inspect_full():
    share_id = "He22TeS4vzbyssj6gyTJLzHNk0L0rE036CnErmVXP7o"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": f"https://www.amazon.it/photos/share/{share_id}",
        "Origin": "https://www.amazon.it"
    }
    
    # 1. Info share
    url_share = f"https://www.amazon.it/drive/v1/shares/{share_id}?resourceVersion=V2&ContentType=JSON"
    req = urllib.request.Request(url_share, headers=headers)
    with urllib.request.urlopen(req) as r:
        share_data = json.loads(r.read().decode())
        print("Share root node:", json.dumps(share_data.get("nodeInfo"), indent=2))
        node_id = share_data.get("nodeInfo", {}).get("id")

    # 2. Children del root node
    url_children = f"https://www.amazon.it/drive/v1/nodes/{node_id}/children?resourceVersion=V2&ContentType=JSON&shareId={share_id}&limit=50"
    req2 = urllib.request.Request(url_children, headers=headers)
    with urllib.request.urlopen(req2) as r2:
        children_data = json.loads(r2.read().decode())
        print("Total count in children:", children_data.get("count"))
        items = children_data.get("data", [])
        for it in items:
            print(f"- Node ID: {it.get('id')} | Name: {it.get('name')} | Kind: {it.get('kind')}")
            # Se è una collection o folder, guarda i suoi children!
            if it.get('kind') in ['COLLECTION', 'FOLDER', 'GROUP', 'VISUAL_SEARCH_COLLECTION']:
                sub_url = f"https://www.amazon.it/drive/v1/nodes/{it.get('id')}/children?resourceVersion=V2&ContentType=JSON&shareId={share_id}&limit=50"
                try:
                    req_sub = urllib.request.Request(sub_url, headers=headers)
                    with urllib.request.urlopen(req_sub) as r_sub:
                        sub_data = json.loads(r_sub.read().decode())
                        print(f"  -> Contiene {len(sub_data.get('data', []))} elementi figli!")
                        for sub_it in sub_data.get('data', [])[:5]:
                            print(f"     * {sub_it.get('name')} (kind: {sub_it.get('kind')})")
                except Exception as ex:
                    print("  -> Errore sub:", ex)

if __name__ == "__main__":
    inspect_full()
