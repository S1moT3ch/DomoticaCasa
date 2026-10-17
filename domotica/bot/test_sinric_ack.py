import asyncio
import logging
from sinric import SinricPro, SinricProConstants
import config

def on_power_state(device_id, state):
    print(f"\n>>> [TEST CALLBACK] Power state per {device_id}: {state}")
    return True, state

def on_adjust_volume(device_id, volume):
    print(f"\n>>> [TEST CALLBACK] Adjust volume per {device_id}: {volume}")
    return True, volume

def on_set_volume(device_id, volume):
    print(f"\n>>> [TEST CALLBACK] Set volume per {device_id}: {volume}")
    return True, volume

callbacks = {
    SinricProConstants.SET_POWER_STATE: on_power_state,
    SinricProConstants.ADJUST_VOLUME: on_adjust_volume,
    SinricProConstants.SET_VOLUME: on_set_volume
}

if __name__ == '__main__':
    print("=" * 60)
    print(" AVVIO TEST SINRICPRO (In ascolto comandi Alexa con log completi)")
    print("=" * 60)
    
    device_id = config.SINRIC_TV_CUCINA_ID or "66d400e2bf0fa484b9e2858b"
    print(f"Device ID monitorato: {device_id}")
    print(f"App Key: {config.SINRIC_APP_KEY[:6]}...")
    
    client = SinricPro(
        config.SINRIC_APP_KEY,
        [device_id],
        callbacks,
        enable_log=True,
        restore_states=False,
        secret_key=config.SINRIC_APP_SECRET
    )
    
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(client.connect())
    except KeyboardInterrupt:
        print("\nTest terminato dall'utente.")
