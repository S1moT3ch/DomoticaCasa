import sys
import time
from samsungtvws import SamsungTVWS
import config

print("Connecting...")
tv = SamsungTVWS(host=config.TV_CUCINA_IP, port=8002, token_file=f"{config.BASE_DIR}/tv_cucina_token.txt", name="Domotica Nighthawk")
print("Sending KEY_3...")
tv.send_key("KEY_3")
time.sleep(0.5)
print("Sending KEY_ENTER...")
tv.send_key("KEY_ENTER")
print("Done")
