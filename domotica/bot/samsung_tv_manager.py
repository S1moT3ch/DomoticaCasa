import time
import os
import sys
import json
import threading
import paho.mqtt.client as mqtt

# Import librerie per TV e Sinric
try:
    from samsungtvws import SamsungTVWS
    from sinric import SinricPro, SinricProConstants
except ImportError as e:
    print(f"[!] Errore di importazione librerie: {e}")
    print("    Esegui: pip install samsungtvws sinricpro==2.7.7")
    import sys
    sys.exit(1)

import config

# Configurazione MQTT
MQTT_BROKER = config.MQTT_BROKER
MQTT_PORT = config.MQTT_PORT
MQTT_USER = config.MQTT_USERNAME
MQTT_PASS = config.MQTT_PASSWORD

# Configurazione SinricPro
APP_KEY = config.SINRIC_APP_KEY
APP_SECRET = config.SINRIC_APP_SECRET
TV_CUCINA_ID = config.SINRIC_TV_CUCINA_ID
TV_SOGGIORNO_ID = config.SINRIC_TV_SOGGIORNO_ID

# TV Mapping (Device ID -> Tuple(IP, MAC, Topic, Name))
TVS = {}
if TV_CUCINA_ID:
    TVS[TV_CUCINA_ID] = {
        "ip": config.TV_CUCINA_IP,
        "mac": config.TV_CUCINA_MAC,
        "topic": "TVCucina.samsung",
        "name": "TV Cucina",
        "token_file": os.path.join(config.BASE_DIR, "tv_cucina_token.txt"),
        "obj": None
    }
if TV_SOGGIORNO_ID:
    TVS[TV_SOGGIORNO_ID] = {
        "ip": config.TV_SOGGIORNO_IP,
        "mac": config.TV_SOGGIORNO_MAC,
        "topic": "TVSoggiorno.samsung",
        "name": "TV Soggiorno",
        "token_file": os.path.join(config.BASE_DIR, "tv_soggiorno_token.txt"),
        "obj": None
    }

mqtt_client = None

def get_tv(device_id):
    """Restituisce l'oggetto TV o prova a connettersi"""
    if device_id not in TVS:
        return None
    tv_info = TVS[device_id]
    
    if tv_info.get("obj") is None:
        try:
            tv_info["obj"] = SamsungTVWS(
                host=tv_info["ip"],
                port=8002,
                token_file=tv_info["token_file"],
                timeout=3,
                name="Domotica Nighthawk"
            )
        except Exception as e:
            print(f"[TV] Errore connessione a {tv_info['name']}: {e}")
            return None
    return tv_info["obj"]

def reconnect_tv(device_id):
    """Forza la ricreazione dell'oggetto TV (es. se la connessione cade)"""
    if device_id in TVS:
        old_obj = TVS[device_id].get("obj")
        if old_obj:
            try:
                old_obj.close()
            except:
                pass
        TVS[device_id]["obj"] = None
        return get_tv(device_id)
    return None

def wake_tv(device_id):
    """Accende la TV tramite Wake-on-LAN/WLAN e comando Power via IP"""
    tv_info = TVS.get(device_id)
    if not tv_info or not tv_info["mac"]:
        return
    import wakeonlan
    
    # 1. Invia il pacchetto WoL in broadcast
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            wakeonlan.send_magic_packet(tv_info["mac"])
            wakeonlan.send_magic_packet(tv_info["mac"], ip_address="192.168.1.255")
        except:
            pass

    # 2. La TV si sveglia in "Network Standby". Attendiamo che sia veramente pronta
    def completa_accensione(dev_id):
        import time
        t_info = TVS.get(dev_id)
        if not t_info: return
        
        # Pausa lunga iniziale: diamo a Tizen il tempo di caricare i driver dello schermo
        print(f"[TV] Inviato WOL per {t_info['name']}. Attendo 8 secondi prima di collegarmi...", flush=True)
        time.sleep(8)
        
        from samsungtvws import SamsungTVWS
        
        # Ritentiamo per un massimo di 5 volte
        for attempt in range(1, 6):
            try:
                tv_temp = SamsungTVWS(
                    host=t_info["ip"],
                    port=8002,
                    token_file=t_info["token_file"],
                    timeout=3,
                    name="Domotica Nighthawk"
                )
                tv_temp.send_key("KEY_POWER")
                try:
                    tv_temp.close()
                except:
                    pass
                print(f"[TV] Accensione schermo completata al tentativo {attempt} per {t_info['name']}", flush=True)
                break
            except Exception as e:
                print(f"[TV] Attesa TV in rete (Tentativo {attempt}/5) per {t_info['name']}...", flush=True)
                time.sleep(3)
            
    import threading
    threading.Thread(target=completa_accensione, args=(device_id,), daemon=True).start()

# =========================================================
# CALLBACKS SINRICPRO (ALEXA)
# =========================================================

def on_power_state(device_id, state):
    try:
        tv_info = TVS.get(device_id)
        if not tv_info:
            print(f"[Errore] Device ID sconosciuto: {device_id}")
            return False, state
            
        # Nelle librerie vecchie, state può essere una stringa "On" o "Off"
        is_on = True
        if isinstance(state, str):
            is_on = state.lower() == "on"
        else:
            is_on = bool(state)
            
        print(f"[Alexa] Richiesta Power {'ON' if is_on else 'OFF'} per {tv_info['name']} (Valore grezzo: {state})")
        
        if is_on:
            wake_tv(device_id)
        else:
            tv = get_tv(device_id)
            if tv:
                try:
                    tv.shortcuts().power()
                except Exception as ex:
                    print(f"[TV] Errore power: {ex}")
        return True, state
    except Exception as e:
        print(f"[Eccezione Critica on_power_state]: {e}")
        import traceback
        traceback.print_exc()
        return False, state

def on_set_volume(device_id, volume):
    tv_info = TVS.get(device_id)
    print(f"[Alexa] Richiesta Volume assoluto {volume} per {tv_info['name']}")
    tv = get_tv(device_id)
    if tv:
        try:
            # L'API base non permette di impostare il volume assoluto direttamente senza upnp
            # Proviamo a usare upnp, se fallisce lo ignoriamo
            tv.upnp().set_volume(int(volume))
        except Exception as e:
            print(f"[TV] Impossibile impostare volume assoluto: {e}")
    return True, volume

def on_adjust_volume(device_id, volume_delta, volume_default=False):
    tv_info = TVS.get(device_id)
    tv_name = tv_info['name'] if tv_info else device_id
    delta = int(volume_delta)
    
    if volume_default:
        # Comando generico ("Alexa, alza il volume" / "abbassa il volume")
        steps = 3
        print(f"[Alexa] Variazione Volume standard per {tv_name}: {steps} step ({'UP' if delta > 0 else 'DOWN'})")
    else:
        # Comando con valore esplicito ("Alexa, alza il volume di 3" / "di 5")
        steps = abs(delta)
        if steps > 15:
            steps = 15
            print(f"  -> Limitato a 15 step per sicurezza")
        print(f"[Alexa] Variazione Volume esplicita per {tv_name}: {steps} step ({'UP' if delta > 0 else 'DOWN'})")

    tv = get_tv(device_id)
    if tv:
        def esegui_volume(dev_id, is_up, st):
            tv_obj = get_tv(dev_id)
            if not tv_obj: return
            
            def esegui(obj):
                for _ in range(st):
                    if is_up: obj.shortcuts().volume_up()
                    else: obj.shortcuts().volume_down()
                    time.sleep(0.2)
            try:
                esegui(tv_obj)
            except Exception as e:
                print(f"[TV] Errore adjust volume (ritento riconnessione): {e}")
                tv_reconnected = reconnect_tv(dev_id)
                if tv_reconnected:
                    try:
                        esegui(tv_reconnected)
                    except:
                        pass
        
        is_up = (delta > 0)
        threading.Thread(target=esegui_volume, args=(device_id, is_up, steps), daemon=True).start()
        
    return True, delta

def on_set_mute(device_id, mute):
    tv_info = TVS.get(device_id)
    print(f"[Alexa] Richiesta Mute {mute} per {tv_info['name']}")
    tv = get_tv(device_id)
    if tv:
        try:
            tv.shortcuts().mute()
        except Exception as e:
            print(f"[TV] Errore mute (ritento riconnessione): {e}")
            tv_reconnected = reconnect_tv(device_id)
            if tv_reconnected:
                try:
                    tv_reconnected.shortcuts().mute()
                except Exception as e2:
                    pass
    return True, mute

def on_change_channel(device_id, channel_name):
    tv_info = TVS.get(device_id)
    
    nome_canale = ""
    numero_canale = ""
    if isinstance(channel_name, dict):
        nome_canale = channel_name.get("name", "")
        numero_canale = channel_name.get("number", "")
    else:
        nome_canale = str(channel_name)
        
    print(f"[Alexa] Richiesta Cambio Canale a '{nome_canale}' (numero: '{numero_canale}') per {tv_info['name']}")
    tv = get_tv(device_id)
    if tv:
        def esegui_cambio_canale(dev_id, ch_name, ch_num):
            tv_obj = get_tv(dev_id)
            if not tv_obj: return
            
            try:
                ch_name_str = str(ch_name).strip().lower()
                ch_num_str = str(ch_num).strip()
                
                # Sostituiamo numeri testuali comuni in italiano
                mappa_numeri = {
                    "uno": "1", "due": "2", "tre": "3", "quattro": "4", "cinque": "5",
                    "sei": "6", "sette": "7", "otto": "8", "nove": "9", "dieci": "10",
                    "undici": "11", "dodici": "12", "tredici": "13", "quattordici": "14",
                    "quindici": "15", "sedici": "16", "diciassette": "17", "diciotto": "18",
                    "diciannove": "19", "venti": "20"
                }
                for parola, numero in mappa_numeri.items():
                    import re
                    ch_name_str = re.sub(rf'\b{parola}\b', numero, ch_name_str)
                
                import re
                solo_numeri = re.sub(r'\D', '', ch_name_str)
                
                # Se abbiamo un numero esplicito da Alexa, usiamo quello!
                if ch_num_str and ch_num_str.isdigit():
                    solo_numeri = ch_num_str
                
                def invia_comandi(t):
                    if solo_numeri:
                        for digit in solo_numeri:
                            t.send_key(f"KEY_{digit}")
                            time.sleep(0.5)
                        t.send_key('KEY_ENTER')
                    elif ch_name_str and ch_name_str != "none" and ch_name_str != "":
                        t.shortcuts().channel_up()

                try:
                    invia_comandi(tv_obj)
                except Exception as e:
                    print(f"[TV] Errore cambio canale (ritento riconnessione): {e}")
                    tv_reconnected = reconnect_tv(dev_id)
                    if tv_reconnected:
                        try:
                            invia_comandi(tv_reconnected)
                        except:
                            pass
            except Exception as e:
                print(f"[TV] Eccezione in esegui_cambio_canale: {e}")

        threading.Thread(target=esegui_cambio_canale, args=(device_id, nome_canale, numero_canale), daemon=True).start()
    return True, channel_name

def on_media_control(device_id, control):
    tv_info = TVS.get(device_id)
    print(f"[Alexa] Richiesta Media Control {control} per {tv_info['name']}")
    tv = get_tv(device_id)
    if tv:
        try:
            if control == 'Play': tv.send_key('KEY_PLAY')
            elif control == 'Pause': tv.send_key('KEY_PAUSE')
            elif control == 'Stop': tv.send_key('KEY_STOP')
            elif control == 'Next': tv.send_key('KEY_FF')
            elif control == 'Previous': tv.send_key('KEY_REWIND')
        except:
            pass
    return True, control

def on_current_temperature(device_id):
    """Callback SinricPro per lettura consumo/potenza da Alexa"""
    try:
        from power_heartbeat import get_latest_power
        p = get_latest_power()
        val = p.get("power_w", 0.0)
        print(f"[Alexa SinricPro] Richiesta lettura potenza: {val:.1f} W")
        return True, round(val, 1)
    except Exception as e:
        print(f"[SinricPro] Errore lettura potenza: {e}")
        return False, 0.0

def on_range_value(device_id, range_value):
    """Callback per RangeController"""
    return True, range_value

callbacks = {
    'setPowerState': on_power_state,
    'setVolume': on_set_volume,
    'adjustVolume': on_adjust_volume,
    'setMute': on_set_mute,
    'changeChannel': on_change_channel,
    'mediaControl': on_media_control,
    'currentTemperature': on_current_temperature,
    'setRangeValue': on_range_value
}


# =========================================================
# MQTT LOGIC
# =========================================================

def on_mqtt_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connesso al broker locale (Codice {rc})")
    for d_id, t_info in TVS.items():
        topic = f"domotica/{t_info['topic']}/command"
        client.subscribe(topic)
        print(f"[MQTT] In ascolto su: {topic}")

def on_mqtt_message(client, userdata, msg):
    payload = msg.payload.decode('utf-8').strip()
    topic = msg.topic
    print(f"[MQTT] Comando ricevuto su {topic}: {payload}")
    
    # Identifica quale TV chiamare
    target_id = None
    for d_id, t_info in TVS.items():
        if t_info['topic'] in topic:
            target_id = d_id
            break
            
    if not target_id:
        return
        
    if payload.lower() == "power_on" or payload.lower() == "power":
        wake_tv(target_id)
        return
        
    tv = get_tv(target_id)
    if not tv:
        return
        
    try:
        if payload.lower() == "power_off":
            tv.shortcuts().power()
        elif payload.startswith("KEY_"):
            tv.send_key(payload.upper())
    except Exception as e:
        print(f"[MQTT] Errore esecuzione comando: {e}")

# =========================================================
# MAIN
# =========================================================

def start_mqtt_thread():
    global mqtt_client
    mqtt_client = mqtt.Client(client_id="SamsungTVBridge", protocol=mqtt.MQTTv311)
    if MQTT_USER and MQTT_PASS:
        mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message
    
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        print(f"[MQTT] Errore di connessione: {e}")

def main():
    print("=" * 50)
    print(" SAMSUNG TV & SINRICPRO BRIDGE STARTED")
    print("=" * 50)
    
    start_mqtt_thread()
    
    # Tentativo rapido di Pairing iniziale con le TV (senza bloccare l'avvio se sono spente)
    print("[TV] Avvio procedura di accoppiamento con le TV...")
    for d_id, t_info in TVS.items():
        print(f"     -> Contatto {t_info['name']} ({t_info['ip']})...")
        try:
            SamsungTVWS(host=t_info["ip"], port=8002, token_file=t_info["token_file"], timeout=2, name="Domotica Nighthawk")
            print(f"     [OK] {t_info['name']} Accoppiata e Pronta!")
        except Exception as e:
            print(f"     [INFO] {t_info['name']} non raggiungibile o in standby ({e}) - verrà contattata su richiesta.")

    if not APP_KEY or not APP_SECRET or not TV_CUCINA_ID:
        print("[!] SinricPro non configurato in .env. Avvio solo MQTT...")
        while True:
            time.sleep(10)
    
    global client
    sinric_devices = list(TVS.keys())
    p_dev = getattr(config, "SINRIC_POWER_DEVICE_ID", "").strip()
    if p_dev and p_dev not in sinric_devices:
        sinric_devices.append(p_dev)
        print(f"[SinricPro] Dispositivo monitoraggio potenza registrato: {p_dev}")

    async def sinric_event_loop():
        from asyncio import sleep
        from sinric import SinricProConstants
        await sleep(5)
        while True:
            p_dev_cur = getattr(config, "SINRIC_POWER_DEVICE_ID", "").strip()
            if p_dev_cur and client and getattr(client, "event_handler", None):
                conn = getattr(client, "connection", None)
                if conn is not None and getattr(conn, "open", False):
                    try:
                        from power_heartbeat import get_latest_power
                        p = get_latest_power()
                        w = p.get("power_w", 0.0)
                        client.event_handler.raise_event(
                            p_dev_cur,
                            SinricProConstants.CURRENT_TEMPERATURE,
                            data={'humidity': 0.0, 'temperature': round(w, 1)}
                        )
                        print(f"[SinricPro] Telemetria inviata a {p_dev_cur}: {w:.1f} W")
                    except Exception as e_ev:
                        print(f"[SinricPro] Errore invio evento: {e_ev}")
            await sleep(30)

    # Ciclo di riconnessione infinito per SinricPro
    import asyncio
    while True:
        try:
            client = SinricPro(
                APP_KEY,
                sinric_devices,
                callbacks,
                event_callbacks=sinric_event_loop,
                enable_log=False,
                restore_states=False,
                secret_key=APP_SECRET
            )
            print("[SinricPro] Connessione al cloud SinricPro avviata...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(client.connect())
            print("[SinricPro] Connessione chiusa. Riconnessione tra 10 secondi...")
        except KeyboardInterrupt:
            print("[SinricPro] Arresto richiesto dall'utente.")
            break
        except Exception as e:
            print(f"[SinricPro] Errore connessione / eccezione: {e}. Riconnessione tra 10 secondi...")
        finally:
            try:
                loop.close()
            except Exception:
                pass
        time.sleep(10)


if __name__ == "__main__":
    main()


