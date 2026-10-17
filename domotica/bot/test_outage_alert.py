#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script di collaudo per il sistema di Outage Prevention & Allarmi Vocali Alexa (EchoPop_Cucina).
Verifica:
  1. Lettura parametri da .env (soglia kW, isteresi, cooldown, device target)
  2. Rilevamento sovraccarico con simulazione (> soglia kW)
  3. Cooldown anti-spam
  4. Rientro sotto la soglia di sicurezza
  5. Invio di un annuncio vocale di test ad Alexa su EchoPop_Cucina
"""

import os
import sys
import time

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


import config
from mqtt_client import MQTTDeviceTracker

def test_outage_system():
    print("=" * 65)
    print("      COLLAUDO SISTEMA OUTAGE PREVENTION & NOTIFICHE ALEXA")
    print("=" * 65)
    
    print(f"[CONFIG] Abilitato:                  {config.OUTAGE_PREVENTION_ENABLED}")
    print(f"[CONFIG] Soglia allarme contatore:    {config.POWER_ALERT_THRESHOLD_KW} kW ({config.POWER_ALERT_THRESHOLD_KW*1000:.0f} W)")
    print(f"[CONFIG] Isteresi ripristino:         {config.POWER_ALERT_HYSTERESIS_KW} kW")
    print(f"[CONFIG] Cooldown notifiche:         {config.POWER_ALERT_COOLDOWN_SECONDS} secondi")
    print(f"[CONFIG] Dispositivo Alexa target:   {config.ALEXA_TARGET_DEVICE}")
    print(f"[CONFIG] Voice Monkey Token:         {'[CONFIGURATO]' if config.VOICEMONKEY_TOKEN else '[NON CONFIGURATO (Simulazione)]'}")
    print(f"[CONFIG] SinricPro Device ID:        {config.SINRIC_POWER_DEVICE_ID or '[NON CONFIGURATO]'}")
    print("-" * 65)

    tracker = MQTTDeviceTracker()

    # Callback di test per le notifiche
    captured_notifications = []
    def mock_notification_cb(category, message, reply_markup=None):
        captured_notifications.append((category, message))
        print(f"[TEST MOCK NOTIFICATION] Ricevuta notifica per {category}:")
        print("  -> " + message.replace("\n", "\n  -> "))

    tracker.notification_callback = mock_notification_cb

    print("\n--- TEST 1: Consumo Normale (1500 W / 1.5 kW) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", 1500.0, voltage=225.0, current=6.6)
    assert not tracker.outage_alert_active, "ERRORE: L'allarme non doveva scattare per 1.5 kW!"
    print("  [OK] Nessun allarme scattato.")

    print("\n--- TEST 2: Consumo a Ridosso Soglia (2850 W / 2.85 kW < 2.9 kW) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", 2850.0, voltage=224.0, current=12.7)
    assert not tracker.outage_alert_active, "ERRORE: L'allarme non doveva scattare sotto 2.9 kW!"
    print("  [OK] Nessun allarme scattato.")

    print("\n--- TEST 3: Sovraccarico Superiore alla Soglia (3200 W / 3.2 kW > 2.9 kW) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", 3200.0, voltage=221.0, current=14.5)
    assert tracker.outage_alert_active, "ERRORE: L'allarme doveva scattare per 3.2 kW!"
    assert len(captured_notifications) == 1, "ERRORE: Notifica Telegram non recapitata!"
    print("  [OK] Allarme scattato con successo e notifica generata.")

    print("\n--- TEST 4: Cooldown Anti-Spam (Secondo picco a 3300 W dopo pochi secondi) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", 3300.0, voltage=220.0, current=15.0)
    assert len(captured_notifications) == 1, "ERRORE: Il cooldown doveva bloccare notifiche ripetute!"
    print("  [OK] Cooldown rispettato, nessuna notifica duplicata inviata.")

    print("\n--- TEST 5: Discesa nel range di isteresi (2700 W / 2.7 kW > 2.5 kW soglia reset) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", 2700.0, voltage=223.0, current=12.1)
    assert tracker.outage_alert_active, "ERRORE: L'allarme deve rimanere armato finché non scende sotto (soglia - isteresi)!"
    print("  [OK] Isteresi attiva, l'allarme resta armato evitando continui rimbalzi.")

    print("\n--- TEST 6: Rientro Completo di Sicurezza (2200 W / 2.2 kW <= 2.5 kW) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", 2200.0, voltage=225.0, current=9.8)
    assert not tracker.outage_alert_active, "ERRORE: Il sistema doveva riarmarsi / tornare normale!"
    print("  [OK] Rientro rilevato con successo e sistema riarmato per future allerte.")

    print("\n" + "=" * 65)
    print("  TUTTI I TEST DELLA LOGICA OUTAGE PREVENTION SUPERATI CON SUCCESSO!")
    print("=" * 65)

    # Test diretto di invio vocale Alexa se richiesto con argomento --voice
    if "--voice" in sys.argv or "--test-alexa" in sys.argv:
        print(f"\n[+] Invio annuncio vocale di test verso '{config.ALEXA_TARGET_DEVICE}'...")
        test_msg = "Attenzione! Questa è una prova del sistema di allarme contatore su Echo Pop Cucina."
        tracker.trigger_alexa_speech(test_msg, target_device=config.ALEXA_TARGET_DEVICE)
        time.sleep(2)
        print("[+] Test invio completato.")

if __name__ == "__main__":
    test_outage_system()
