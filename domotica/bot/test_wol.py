import sys
import time
import warnings
import config

try:
    import wakeonlan
except ImportError:
    print("La libreria wakeonlan non è installata.")
    sys.exit(1)

try:
    from samsungtvws import SamsungTVWS
except ImportError:
    print("La libreria samsungtvws non è installata.")
    sys.exit(1)

mac = config.TV_CUCINA_MAC
ip = config.TV_CUCINA_IP

print("1. Invio Magic Packet per svegliare lo stack di rete della TV...")
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    wakeonlan.send_magic_packet(mac)
    wakeonlan.send_magic_packet(mac, ip_address="192.168.1.255")

print("2. Attendo 3 secondi che il sistema operativo (Tizen) carichi il Wi-Fi/Ethernet...")
time.sleep(3)

print("3. Tento di collegarmi alla porta 8002 per accendere fisicamente lo schermo...")
try:
    tv = SamsungTVWS(
        host=ip,
        port=8002,
        token_file=f"{config.BASE_DIR}/tv_cucina_token.txt",
        timeout=5,
        name="Domotica Nighthawk"
    )
    print("Connessione stabilita! Invio KEY_POWER...")
    tv.send_key("KEY_POWER")
    print("Operazione completata con successo. Lo schermo si è acceso?")
except Exception as e:
    print(f"Errore durante il tentativo di connessione via IP: {e}")
