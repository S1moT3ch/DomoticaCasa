#!/usr/bin/env python3
"""
Script per testare manualmente da shell la chiamata vocale Telegram di emergenza.
Utilizzo:
    python3 /www/domotica/bot/test_voice_call.py
"""

import sys
import os
import requests
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import config

def main():
    print("[+] Test manuale Chiamata Vocale Telegram di Emergenza...")
    target = getattr(config, "VACATION_ALARM_CALL_TARGET", "")
    
    clean_target = str(target).strip()
    if clean_target.isdigit():
        print(f"\n[!] ATTENZIONE: Il parametro VACATION_ALARM_CALL_TARGET in .env è attualmente un ID numerico ({clean_target}).")
        print("    Le chiamate vocali Telegram di CallMeBot richiedono un Username Telegram (es. @tuousername) o Numero (+39...).")
        print("    Imposta VACATION_ALARM_CALL_TARGET=@tuousername nel file .env!")
        return

    if not clean_target.startswith("@") and not clean_target.startswith("+"):
        clean_target = f"@{clean_target}"

    text = "ATTENZIONE! PROVA DI CHIAMATA VOCALE TELEGRAM DAL BOT DOMOTICA CASA!"
    encoded_text = urllib.parse.quote(text)
    api_key = getattr(config, "CALLMEBOT_API_KEY", "").strip()
    url = f"https://api.callmebot.com/start.php?user={clean_target}&text={encoded_text}&lang=it-IT"
    if api_key:
        url += f"&apikey={api_key}"

    print(f"[+] Destinatario configurato: {clean_target}")
    print(f"[+] Invocazione API CallMeBot Voice Call...")

    try:
        resp = requests.get(url, timeout=15)
        if resp.ok:
            if "Too many requests" in resp.text:
                print("\n[!] ATTENZIONE: Troppe richieste ravvicinate su CallMeBot (Rate-Limit).")
                print("    Attendi 2-3 minuti e riesegui il test!")
            elif "Authorization" in resp.text and "not received" in resp.text:
                print("\n[!] ATTENZIONE: Utente non ancora autorizzato su CallMeBot!")
                print("    Per autorizzare le chiamate (procedura valida 1 sola volta):")
                print("    1. Apri Telegram e cerca il bot: @CallMeBot_txtbot")
                print("    2. Clicca su START / AVVIA ed invia un messaggio.")
                print("    3. Oppure apri: https://api2.callmebot.com/txt/auth.php")
            else:
                print(f"[+] Chiamata vocale inviata con successo a {clean_target}!")
        else:
            print(f"[!] Errore HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[!] Errore connessione durante il test di chiamata: {e}")

    wa_phone = getattr(config, "WHATSAPP_PHONE", "").strip()
    wa_apikey = getattr(config, "WHATSAPP_API_KEY", "2047375").strip()

    if wa_phone:
        clean_phone = wa_phone.strip()
        if not clean_phone.startswith("+"):
            if clean_phone.startswith("3"):
                clean_phone = f"+39{clean_phone}"
            else:
                clean_phone = f"+{clean_phone}"
        wa_url = f"https://api.callmebot.com/whatsapp.php?phone={clean_phone}&text={encoded_text}&apikey={wa_apikey}"
        print(f"\n[+] Invocazione API CallMeBot WhatsApp verso {clean_phone}...")
        try:
            r_wa = requests.get(wa_url, timeout=15)
            if r_wa.ok:
                print(f"[+] Notifica di allarme WhatsApp inviata con successo a {clean_phone}!")
            else:
                print(f"[!] Errore WhatsApp ({r_wa.status_code}): {r_wa.text[:200]}")
        except Exception as e_wa:
            print(f"[!] Errore connessione WhatsApp: {e_wa}")

if __name__ == "__main__":
    main()
