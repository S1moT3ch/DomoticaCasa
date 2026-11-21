#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script di collaudo per il sistema di Outage Prevention & Allarmi Vocali Alexa (EchoPop_Cucina).
Verifica:
  1. Lettura dinamica parametri da .env (soglia kW, isteresi, cooldown, device target)
  2. Rilevamento sovraccarico con simulazione (> soglia kW da .env)
  3. Cooldown anti-spam
  4. Rientro sotto la soglia di sicurezza (isteresi)
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
    
    threshold_kw = float(config.POWER_ALERT_THRESHOLD_KW)
    threshold_w = threshold_kw * 1000.0
    hysteresis_kw = float(config.POWER_ALERT_HYSTERESIS_KW)
    reset_kw = threshold_kw - hysteresis_kw
    reset_w = reset_kw * 1000.0

    print(f"[CONFIG] Abilitato:                  {config.OUTAGE_PREVENTION_ENABLED}")
    print(f"[CONFIG] Soglia allarme contatore:    {threshold_kw:.1f} kW ({threshold_w:.0f} W)")
    print(f"[CONFIG] Isteresi ripristino:         {hysteresis_kw:.1f} kW (Soglia reset: {reset_kw:.1f} kW / {reset_w:.0f} W)")
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

    # 1. Consumo normale (metà soglia)
    val_normal_w = round(threshold_w * 0.5)
    print(f"\n--- TEST 1: Consumo Normale ({val_normal_w:.0f} W / {val_normal_w/1000:.2f} kW) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", float(val_normal_w), voltage=225.0, current=val_normal_w/225.0)
    assert not tracker.outage_alert_active, f"ERRORE: L'allarme non doveva scattare per {val_normal_w/1000:.2f} kW!"
    print("  [OK] Nessun allarme scattato.")

    # 2. Consumo appena sotto la soglia (soglia - 50 W)
    val_sub_w = round(threshold_w - 50.0)
    print(f"\n--- TEST 2: Consumo a Ridosso Soglia ({val_sub_w:.0f} W / {val_sub_w/1000:.2f} kW < {threshold_kw:.1f} kW) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", float(val_sub_w), voltage=224.0, current=val_sub_w/224.0)
    assert not tracker.outage_alert_active, f"ERRORE: L'allarme non doveva scattare sotto {threshold_kw:.1f} kW!"
    print("  [OK] Nessun allarme scattato.")

    # 3. Sovraccarico superiore alla soglia (soglia + 300 W)
    val_over_w = round(threshold_w + 300.0)
    print(f"\n--- TEST 3: Sovraccarico Superiore alla Soglia ({val_over_w:.0f} W / {val_over_w/1000:.2f} kW > {threshold_kw:.1f} kW) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", float(val_over_w), voltage=221.0, current=val_over_w/221.0)
    assert tracker.outage_alert_active, f"ERRORE: L'allarme doveva scattare per {val_over_w/1000:.2f} kW!"
    assert len(captured_notifications) == 1, "ERRORE: Notifica Telegram non recapitata!"
    print("  [OK] Allarme scattato con successo e notifica generata.")

    # 4. Cooldown anti-spam (secondo picco dopo pochi secondi)
    val_second_w = round(threshold_w + 400.0)
    print(f"\n--- TEST 4: Cooldown Anti-Spam (Secondo picco a {val_second_w:.0f} W dopo pochi secondi) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", float(val_second_w), voltage=220.0, current=val_second_w/220.0)
    assert len(captured_notifications) == 1, "ERRORE: Il cooldown doveva bloccare notifiche ripetute!"
    print("  [OK] Cooldown rispettato, nessuna notifica duplicata inviata.")

    # 5. Discesa nel range di isteresi (soglia - metà isteresi) -> allarme ancora armato
    val_hyst_w = round((threshold_kw - (hysteresis_kw * 0.5)) * 1000.0)
    print(f"\n--- TEST 5: Discesa nel range di isteresi ({val_hyst_w:.0f} W / {val_hyst_w/1000:.2f} kW > {reset_kw:.1f} kW soglia reset) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", float(val_hyst_w), voltage=223.0, current=val_hyst_w/223.0)
    assert tracker.outage_alert_active, "ERRORE: L'allarme deve rimanere armato finché non scende sotto (soglia - isteresi)!"
    print("  [OK] Isteresi attiva, l'allarme resta armato evitando continui rimbalzi.")

    # 6. Rientro completo di sicurezza sotto soglia di reset (reset - 200 W)
    val_recov_w = round(reset_w - 200.0)
    print(f"\n--- TEST 6: Rientro Completo di Sicurezza ({val_recov_w:.0f} W / {val_recov_w/1000:.2f} kW <= {reset_kw:.1f} kW) ---")
    tracker._check_power_outage_alert("PowerMeterHome.shelly", float(val_recov_w), voltage=225.0, current=val_recov_w/225.0)
    assert not tracker.outage_alert_active, "ERRORE: Il sistema doveva riarmarsi / tornare normale!"
    print("  [OK] Rientro rilevato con successo e sistema riarmato per future allerte.")

    print("\n" + "=" * 65)
    print("  TUTTI I TEST DELLA LOGICA OUTAGE PREVENTION SUPERATI CON SUCCESSO!")
    print("=" * 65)

    # Test diretto di invio vocale Alexa se richiesto con argomento --voice
    if "--voice" in sys.argv or "--test-alexa" in sys.argv:
        print(f"\n[+] Invio annuncio vocale di test verso '{config.ALEXA_TARGET_DEVICE}'...")
        test_msg = f"Attenzione! Questa è una prova del sistema di allarme contatore su Echo Pop Cucina. Soglia impostata a {threshold_kw:.1f} kilowatt."
        tracker.trigger_alexa_speech(test_msg, target_device=config.ALEXA_TARGET_DEVICE)
        time.sleep(2)
        print("[+] Test invio completato.")

if __name__ == "__main__":
    test_outage_system()
