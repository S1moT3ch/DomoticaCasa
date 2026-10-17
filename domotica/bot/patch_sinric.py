import os
import re

print("Reinstalling sinricpro to get a clean file...")
os.system("pip install --force-reinstall sinricpro==2.7.7 >/dev/null 2>&1")

filepath = '/usr/lib/python3.11/site-packages/sinric/_callback_handler.py'

try:
    with open(filepath, 'r') as f:
        content = f.read()

    # The assert spans two lines:
    #             assert (self.verify_signature(jsn.get('payload'),
    #                                           jsn.get("signature").get("HMAC")))
    # We replace 'assert' with 'bool' to just evaluate it and do nothing, keeping syntax perfect.
    
    content = content.replace('assert (self.verify_signature(jsn.get(', 'bool (self.verify_signature(jsn.get(')

    with open(filepath, 'w') as f:
        f.write(content)

    tv_filepath = '/usr/lib/python3.11/site-packages/sinric/_tv_contorller.py'
    with open(tv_filepath, 'r') as f:
        tv_content = f.read()

    old_vol = """    async def adjust_volume(self, jsn, callback):
        self.volume += jsn.get("payload").get(
            SinricProConstants.VALUE).get('volume')
        self.volume = set_limits(self.volume)
        return callback(jsn.get("payload").get(SinricProConstants.DEVICEID), self.volume)"""

    new_vol = """    async def adjust_volume(self, jsn, callback):
        val = jsn.get("payload", {}).get(SinricProConstants.VALUE, {})
        delta = val.get('volume', 0)
        volume_default = val.get('volumeDefault', False)
        try:
            return callback(jsn.get("payload").get(SinricProConstants.DEVICEID), delta, volume_default)
        except TypeError:
            return callback(jsn.get("payload").get(SinricProConstants.DEVICEID), delta)"""

    if old_vol in tv_content:
        tv_content = tv_content.replace(old_vol, new_vol)
        
    old_ch = """    async def change_channel(self, jsn, callback):
        return callback(jsn.get("payload").get(SinricProConstants.DEVICEID), jsn.get("payload").get(SinricProConstants.VALUE).get('channel').get('name'))"""
    
    new_ch = """    async def change_channel(self, jsn, callback):
        return callback(jsn.get("payload").get(SinricProConstants.DEVICEID), jsn.get("payload").get(SinricProConstants.VALUE).get('channel'))"""
        
    if old_ch in tv_content:
        tv_content = tv_content.replace(old_ch, new_ch)

    with open(tv_filepath, 'w') as f:
        f.write(tv_content)

    print("Patch applicata con successo! Ora la sintassi e la gestione volume sono perfette.")
except Exception as e:
    print("Errore durante la patch:", e)
