#!/usr/bin/env python3
"""
CoIoT -> MQTT Bridge -- Shelly Gen1 EM (v2.3)
==============================================
Ascolta i pacchetti CoIoT (CoAP su UDP :5683) inviati da dispositivi Shelly Gen1,
decodifica tutti i parametri elettrici, lo stato dei relè e la diagnostica, e li
pubblica su Mosquitto MQTT esclusivamente sotto:
  - PowerMeterHome.shelly/status (payload JSON unificato con emeters, relays, potenze)
  - PowerMeterHome.shelly/online (true)

All'avvio ripulisce automaticamente qualsiasi topic obsoleto o residuo (incluso homeassistant/...)
dal broker MQTT.
"""

import os
import sys
import socket
import struct
import json
import threading
import time
import math
import signal
import argparse
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import config
    MQTT_BROKER = getattr(config, "MQTT_BROKER", "localhost")
    MQTT_PORT   = int(getattr(config, "MQTT_PORT", 1883))
    MQTT_USER   = getattr(config, "MQTT_USERNAME", None) or ""
    MQTT_PASS   = getattr(config, "MQTT_PASSWORD", None) or ""
except Exception:
    MQTT_BROKER = "127.0.0.1"
    MQTT_PORT   = 1883
    MQTT_USER   = ""
    MQTT_PASS   = ""

import paho.mqtt.client as paho_mqtt

COIOT_MCAST_IP = "224.0.1.187"
COIOT_PORT     = 5683
LISTEN_IP      = ""

STATIC_DEVICE_MAP = {
    "192.168.1.1": {
        "actual_ip": "192.168.4.116",
        "device_id": "PowerMeterHome.shelly"
    },
    "C4D8D5008BC6": {
        "actual_ip": "192.168.4.116",
        "device_id": "PowerMeterHome.shelly"
    }
}

SHELLY_EM_FALLBACK_MAP = {
    # emeter 0 (Canale Casa 1)
    4105: ("emeter", 0, "power"),
    4106: ("emeter", 0, "total"),
    4107: ("emeter", 0, "total_returned"),
    4108: ("emeter", 0, "voltage"),
    4110: ("emeter", 0, "pf"),
    # emeter 1 (Canale Casa 2)
    4205: ("emeter", 1, "power"),
    4206: ("emeter", 1, "total"),
    4207: ("emeter", 1, "total_returned"),
    4208: ("emeter", 1, "voltage"),
    4210: ("emeter", 1, "pf"),
    # relay 0 (Contatto integrato)
    1101: ("relay",  0, "state"),
    6102: ("relay",  0, "overpower"),
    # diagnostica device
    9103: ("device", 0, "cfg_changed"),
}

_device_cache          = {}
_device_cache_lock     = threading.Lock()
_last_msg_id           = {}
_last_log_time         = {}
_last_logged_power     = {}
_running               = True
_debug_mode            = False


def parse_coap(data):
    """
    Decodifica completa del pacchetto CoAP / CoIoT (RFC 7252).
    """
    if len(data) < 4:
        return None
    tkl = data[0] & 0x0F
    msg_id = (data[2] << 8) | data[3]
    idx = 4 + tkl

    current_opt = 0
    options = {}

    while idx < len(data):
        if data[idx] == 0xFF:
            idx += 1
            break
        delta = (data[idx] >> 4) & 0x0F
        length = data[idx] & 0x0F
        idx += 1

        if delta == 13:
            if idx >= len(data):
                break
            delta = data[idx] + 13
            idx += 1
        elif delta == 14:
            if idx + 1 >= len(data):
                break
            delta = ((data[idx] << 8) | data[idx+1]) + 269
            idx += 2

        if length == 13:
            if idx >= len(data):
                break
            length = data[idx] + 13
            idx += 1
        elif length == 14:
            if idx + 1 >= len(data):
                break
            length = ((data[idx] << 8) | data[idx+1]) + 269
            idx += 2

        opt_num = current_opt + delta
        current_opt = opt_num
        opt_val = data[idx:idx+length]
        idx += length
        options[opt_num] = opt_val

    payload = None
    if idx < len(data):
        try:
            payload = json.loads(data[idx:].decode('utf-8'))
        except Exception:
            payload = None

    dev_id_str = options.get(3332, b"").decode("latin1", errors="ignore")
    dev_type, dev_mac = None, None
    if dev_id_str and "#" in dev_id_str:
        parts = dev_id_str.split("#")
        dev_type = parts[0]
        if len(parts) > 1:
            dev_mac = parts[1].upper()

    return {
        "msg_id": msg_id,
        "dev_id_str": dev_id_str,
        "dev_type": dev_type,
        "dev_mac": dev_mac,
        "payload": payload
    }


def _build_sensor_map_from_cit(cit_desc):
    blk_map = {}
    for blk in cit_desc.get("blk", []):
        b_id = blk.get("I")
        b_desc = (blk.get("D") or "").lower()
        parts = b_desc.split("_")
        category = parts[0]
        ch = 0
        if len(parts) > 1 and parts[1].isdigit():
            ch = int(parts[1])
        blk_map[b_id] = (category, ch)

    sensor_map = {}
    for sen in cit_desc.get("sen", []):
        s_id = sen.get("I")
        desc = (sen.get("D") or "").lower()
        unit = (sen.get("T") or "").lower()
        blk_id = sen.get("L")
        if isinstance(blk_id, list) and blk_id:
            blk_id = blk_id[0]
        category, ch = blk_map.get(blk_id, ("device", 0))

        if category == "emeter":
            if "power" in desc and "factor" not in desc and "reactive" not in desc and "apparent" not in desc:
                field = "power"
            elif "factor" in desc or desc in ("pf", "powerfactor", "cos phi"):
                field = "pf"
            elif "reactive" in desc or unit in ("var", "kvar"):
                field = "reactive_power"
            elif "apparent" in desc or unit in ("va", "kva"):
                field = "apparent_power"
            elif "voltage" in desc or unit == "v":
                field = "voltage"
            elif "current" in desc or unit in ("a", "ma"):
                field = "current"
            elif "return" in desc:
                field = "total_returned"
            elif "energy" in desc or "consumption" in desc or unit in ("wh", "kwh"):
                field = "total"
            else:
                continue
            sensor_map[s_id] = ("emeter", ch, field)

        elif category == "relay":
            if "output" in desc or desc == "state":
                sensor_map[s_id] = ("relay", ch, "state")
            elif "overpower" in desc:
                sensor_map[s_id] = ("relay", ch, "overpower")
            elif "power" in desc and "factor" not in desc:
                sensor_map[s_id] = ("relay", ch, "power")

        elif category == "device":
            if "cfgchanged" in desc or "changed" in desc:
                sensor_map[s_id] = ("device", 0, "cfg_changed")

    return sensor_map


def fetch_device_info(src_ip, dev_mac=None):
    static = {}
    if dev_mac and dev_mac in STATIC_DEVICE_MAP:
        static = STATIC_DEVICE_MAP[dev_mac]
    elif src_ip in STATIC_DEVICE_MAP:
        static = STATIC_DEVICE_MAP[src_ip]

    fetch_ip  = static.get("actual_ip", src_ip)
    device_id = static.get("device_id", None)

    info = {
        "device_id": device_id,
        "actual_ip": fetch_ip,
        "mac": dev_mac or "C4D8D5008BC6",
        "model": "SHEM",
        "desc": dict(SHELLY_EM_FALLBACK_MAP)
    }

    if device_id:
        print(f"[CoIoT] Device mappato: {src_ip} -> {device_id} @ {fetch_ip}")
    else:
        try:
            resp = requests.get(f"http://{fetch_ip}/settings", timeout=4)
            s = resp.json()
            mqtt_id = (s.get("mqtt") or {}).get("id", "")
            if not mqtt_id:
                mqtt_id = s.get("name", "") or (s.get("device") or {}).get("hostname", "")
            if not mqtt_id and dev_mac:
                mqtt_id = f"shellyem-{dev_mac}"
            if mqtt_id:
                info["device_id"] = mqtt_id
                print(f"[CoIoT] Rilevato da /settings: {info['device_id']} @ {fetch_ip}")
        except Exception as e:
            if _debug_mode:
                print(f"[CoIoT] Impossibile leggere /settings da {fetch_ip}: {e}")
            if dev_mac:
                info["device_id"] = f"shellyem-{dev_mac}"

    try:
        resp = requests.get(f"http://{fetch_ip}/cit/d", timeout=4)
        cit_map = _build_sensor_map_from_cit(resp.json())
        if cit_map:
            info["desc"].update(cit_map)
            if _debug_mode:
                print(f"[CoIoT] /cit/d OK: {len(info['desc'])} sensori mappati")
    except Exception as e:
        if _debug_mode:
            print(f"[CoIoT] /cit/d non disponibile su {fetch_ip} ({e}) -> uso mappa fallback Shelly EM")

    return info


def get_device(src_ip, dev_mac=None):
    cache_key = dev_mac if dev_mac else src_ip
    with _device_cache_lock:
        if cache_key in _device_cache:
            return _device_cache[cache_key]

    def _fetch():
        info = fetch_device_info(src_ip, dev_mac)
        with _device_cache_lock:
            _device_cache[cache_key] = info
            _device_cache[src_ip]    = info
        print(f"[CoIoT] Inizializzato dispositivo: {info.get('device_id')} (IP: {info.get('actual_ip')})")

    threading.Thread(target=_fetch, daemon=True, name="DevFetcher").start()
    with _device_cache_lock:
        _device_cache[cache_key] = {
            "device_id": STATIC_DEVICE_MAP.get(src_ip, {}).get("device_id"),
            "actual_ip": STATIC_DEVICE_MAP.get(src_ip, {}).get("actual_ip", src_ip),
            "mac": dev_mac or "C4D8D5008BC6",
            "model": "SHEM",
            "desc": dict(SHELLY_EM_FALLBACK_MAP)
        }
    return _device_cache[cache_key]


def process_sensors(payload_g, desc_map):
    """
    Raggruppa le letture e calcola i parametri elettrici completi:
    - Corrente RMS: I = |P| / (V * |cos φ|)
    - Potenza Apparente: S = V * I
    - Potenza Reattiva: Q = ±√(S² - P²)
    - Energia kWh: total_kwh = Wh / 1000.0
    """
    emeters = {}
    relays  = {}
    device  = {}

    for entry in payload_g:
        if len(entry) < 3:
            continue
        _, s_id, val = entry[0], entry[1], entry[2]
        mapping = desc_map.get(s_id)
        if not mapping:
            continue

        cat, ch, field = mapping
        if cat == "emeter":
            if ch not in emeters:
                emeters[ch] = {}
            emeters[ch][field] = val
        elif cat == "relay":
            if ch not in relays:
                relays[ch] = {}
            relays[ch][field] = val
        elif cat == "device":
            device[field] = val

    # Elaborazione elettrica completa per ciascun canale emeter
    for ch, data in emeters.items():
        v  = float(data.get("voltage", 0.0))
        p  = float(data.get("power", 0.0))
        pf = float(data.get("pf", 1.0))

        abs_pf = abs(pf)
        if abs_pf < 0.05:
            abs_pf = 1.0

        if v > 10.0 and abs(p) > 0.05:
            current = round(abs(p) / (v * abs_pf), 3)
        else:
            current = 0.0

        apparent_power = round(v * current, 2)
        s2 = apparent_power ** 2
        p2 = p ** 2
        if s2 > p2:
            q = math.sqrt(s2 - p2)
            if pf < 0:
                q = -q
            reactive_power = round(q, 2)
        else:
            reactive_power = 0.0

        data["current"]        = current
        data["apparent_power"] = apparent_power
        data["reactive_power"] = reactive_power

        if "total" in data:
            data["total_kwh"] = round(float(data["total"]) / 1000.0, 3)

    return emeters, relays, device



def publish_all_mqtt(mqtt_pub, dev_info, emeters, relays, device_diag):
    """
    Pubblica tutti i dati estratti dal CoIoT su MQTT esclusivamente sul topic status:
      <device_id>/status  (payload JSON unificato con emeters, relays, potenze, diagnostica)
      <device_id>/online  ('true')
    """
    device_id = dev_info.get("device_id", "PowerMeterHome.shelly")
    actual_ip = dev_info.get("actual_ip", "")
    mac       = dev_info.get("mac", "C4D8D5008BC6")

    total_power   = 0.0
    total_current = 0.0
    total_energy  = 0.0

    # 1. Costruzione array emeters (canali 0 e 1)
    emeters_list = []
    for ch in [0, 1]:
        fields = emeters.get(ch, {})
        p = float(fields.get("power", 0.0))
        c = float(fields.get("current", 0.0))
        t = float(fields.get("total", 0.0))
        total_power   += p
        total_current += c
        total_energy  += t

        emeters_list.append({
            "power": round(p, 2),
            "reactive": round(float(fields.get("reactive_power", 0.0)), 2),
            "voltage": round(float(fields.get("voltage", 0.0)), 2),
            "current": round(c, 3),
            "pf": round(float(fields.get("pf", 0.0)), 2),
            "is_valid": True,
            "total": round(t, 1),
            "total_returned": round(float(fields.get("total_returned", 0.0)), 1)
        })

    # 2. Costruzione array relays
    relays_list = []
    for ch in sorted(relays.keys()) if relays else [0]:
        fields = relays.get(ch, {})
        st_bool = (fields.get("state") == 1)
        st_str  = "on" if st_bool else "off"
        op_bool = (fields.get("overpower") == 1)

        relays_list.append({
            "ison": st_bool,
            "state": st_str,
            "has_timer": False,
            "timer_started": 0,
            "timer_duration": 0,
            "timer_remaining": 0,
            "overpower": op_bool,
            "is_valid": True
        })

    ch0_data = emeters.get(0, {})
    ch0_power = float(ch0_data.get("power", total_power))
    ch0_volt  = float(ch0_data.get("voltage", 0.0))
    ch0_curr  = float(ch0_data.get("current", total_current))

    status_obj = {
        "wifi_sta": {
            "connected": True,
            "ip": actual_ip
        },
        "time": time.strftime("%H:%M:%S"),
        "unixtime": int(time.time()),
        "mac": mac,
        "relays": relays_list,
        "emeters": emeters_list,
        "total_power": round(total_power, 2),
        "power": round(ch0_power, 2),
        "voltage": round(ch0_volt, 2),
        "current": round(ch0_curr, 3),
        "energy_kwh": round(total_energy / 1000.0, 3),
        "cfg_changed_cnt": device_diag.get("cfg_changed", 0)
    }

    # 1. Pubblica il payload unificato su PowerMeterHome.shelly/status (retained per disponibilità immediata)
    mqtt_pub.publish(f"{device_id}/status", json.dumps(status_obj), retain=True)

    # 2. Stato Online & Keepalive
    mqtt_pub.publish(f"{device_id}/online", "true", retain=True)


def log_summary(device_id, emeters, relays):
    now = time.time()
    last_t = _last_log_time.get(device_id, 0)

    ch0 = emeters.get(0, {})
    pw  = ch0.get("power")
    v   = ch0.get("voltage")
    i   = ch0.get("current")
    pf  = ch0.get("pf")
    tot = ch0.get("total")

    if pw is None:
        return

    last_p = _last_logged_power.get(device_id, -999)
    is_changed = abs(float(pw) - last_p) >= 3.0
    time_elapsed = (now - last_t) >= 20.0

    if not is_changed and not time_elapsed and not _debug_mode:
        return

    _last_log_time[device_id]     = now
    _last_logged_power[device_id] = float(pw)

    tot_kwh = f"{float(tot)/1000.0:.2f} kWh" if tot is not None else "? kWh"
    curr_s  = f"{float(i):.2f} A" if i is not None else "? A"
    volt_s  = f"{float(v):.1f} V" if v is not None else "? V"
    pf_s    = f"PF: {float(pf):.2f}" if pf is not None else ""

    line = f"[CoIoT] ⚡ {device_id}/status: {float(pw):.1f} W | {volt_s} | {curr_s} ({pf_s}) | {tot_kwh} -> MQTT"
    print(line)

    if 1 in emeters and abs(float(emeters[1].get("power", 0))) > 0.5:
        ch1 = emeters[1]
        p1 = float(ch1.get("power", 0))
        v1 = float(ch1.get("voltage", 0))
        print(f"[CoIoT] ⚡ {device_id}/status [ch1]: {p1:.1f} W | {v1:.1f} V")


def listen_loop(mqtt_pub):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass

    sock.bind((LISTEN_IP, COIOT_PORT))

    mreq = struct.pack("4s4s",
                       socket.inet_aton(COIOT_MCAST_IP),
                       socket.inet_aton("0.0.0.0"))
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        print(f"[CoIoT] Multicast {COIOT_MCAST_IP}:{COIOT_PORT} attivo")
    except Exception as e:
        print(f"[CoIoT] Multicast non disponibile ({e}) -- ricezione unicast attiva")

    sock.settimeout(2.0)
    print(f"[CoIoT] In ascolto su UDP :{COIOT_PORT}...")

    while _running:
        try:
            raw_data, (src_ip, _) = sock.recvfrom(8192)
        except socket.timeout:
            continue
        except Exception as e:
            if _running:
                print(f"[CoIoT] Errore socket: {e}")
                time.sleep(1)
            continue

        coap_pkt = parse_coap(raw_data)
        if not coap_pkt or not coap_pkt.get("payload") or "G" not in coap_pkt["payload"]:
            continue

        msg_id = coap_pkt["msg_id"]
        if _last_msg_id.get(src_ip) == msg_id:
            continue
        _last_msg_id[src_ip] = msg_id

        dev_mac = coap_pkt.get("dev_mac")
        dev     = get_device(src_ip, dev_mac)
        did     = dev.get("device_id")
        desc    = dev.get("desc", {})

        if not did:
            continue

        emeters, relays, device_diag = process_sensors(coap_pkt["payload"]["G"], desc)

        if emeters or relays:
            try:
                publish_all_mqtt(mqtt_pub, dev, emeters, relays, device_diag)
                log_summary(did, emeters, relays)
            except Exception as e:
                print(f"[CoIoT] Errore pubblicazione MQTT: {e}")

    try:
        sock.close()
    except Exception:
        pass


def purge_retained_topics(client):
    """
    Rimuove ed elimina definitivamente qualsiasi messaggio retained presente
    sotto i topic 'homeassistant/...' e 'shellies/PowerMeterHome.shelly/...' sul broker MQTT.
    """
    topics_to_purge = [
        "homeassistant/sensor/PowerMeterHome/emeter0_power/config",
        "homeassistant/sensor/PowerMeterHome/emeter0_voltage/config",
        "homeassistant/sensor/PowerMeterHome/emeter0_current/config",
        "homeassistant/sensor/PowerMeterHome/emeter0_pf/config",
        "homeassistant/sensor/PowerMeterHome/emeter0_total_kwh/config",
        "homeassistant/sensor/PowerMeterHome/emeter1_power/config",
        "homeassistant/sensor/PowerMeterHome/emeter1_voltage/config",
        "homeassistant/sensor/PowerMeterHome/emeter1_current/config",
        "homeassistant/sensor/PowerMeterHome/emeter1_pf/config",
        "homeassistant/sensor/PowerMeterHome/emeter1_total_kwh/config",
        "homeassistant/switch/PowerMeterHome/relay0/config",
        "homeassistant/sensor/powermeterhome/emeter0_power/config",
        "homeassistant/sensor/powermeterhome/emeter0_voltage/config",
        "homeassistant/sensor/powermeterhome/emeter0_current/config",
        "homeassistant/sensor/powermeterhome/emeter0_pf/config",
        "homeassistant/sensor/powermeterhome/emeter0_total_kwh/config",
        "homeassistant/switch/powermeterhome/relay0/config",
        "shellies/PowerMeterHome.shelly/status",
        "shellies/PowerMeterHome.shelly/online",
        "shellies/PowerMeterHome.shelly/emeter/0/power",
        "shellies/PowerMeterHome.shelly/emeter/0/voltage",
        "shellies/PowerMeterHome.shelly/emeter/0/current",
        "shellies/PowerMeterHome.shelly/emeter/0/pf",
        "shellies/PowerMeterHome.shelly/emeter/0/total",
        "shellies/PowerMeterHome.shelly/emeter/0/total_kwh",
        "shellies/PowerMeterHome.shelly/emeter/0/total_returned",
        "shellies/PowerMeterHome.shelly/emeter/1/power",
        "shellies/PowerMeterHome.shelly/emeter/1/voltage",
        "shellies/PowerMeterHome.shelly/emeter/1/current",
        "shellies/PowerMeterHome.shelly/emeter/1/pf",
        "shellies/PowerMeterHome.shelly/emeter/1/total",
        "shellies/PowerMeterHome.shelly/emeter/1/total_kwh",
        "shellies/PowerMeterHome.shelly/emeter/1/total_returned",
        "shellies/PowerMeterHome.shelly/relay/0",
    ]
    for top in topics_to_purge:
        client.publish(top, payload="", retain=True)

    # Ascolto temporaneo per catturare ed eliminare all'istante eventuali altri topic discovery
    client.subscribe("homeassistant/#")
    client.subscribe("shellies/PowerMeterHome.shelly/#")


def on_mqtt_message(client, userdata, msg):
    """
    Gestisce comandi in ingresso su MQTT e ripulisce topic indesiderati:
    1. Se arriva un messaggio da 'homeassistant/...' o 'shellies/PowerMeterHome.shelly/...', lo cancella dal broker.
    2. Controllo relè: <id>/relay/<ch>/command -> on/off/toggle
    """
    try:
        topic = msg.topic
        payload_str = msg.payload.decode('utf-8').strip()

        # Pulizia dinamica di qualsiasi topic residuo
        if topic.startswith("homeassistant/") or topic.startswith("shellies/PowerMeterHome.shelly/"):
            if msg.payload:
                client.publish(topic, payload="", retain=True)
                print(f"[CoIoT] Rimosso topic obsoleto dal broker: {topic}")
            return

        # Comando relè
        parts = topic.split("/")
        if len(parts) >= 4 and "relay" in parts and parts[-1] == "command":
            if parts[0] == "shellies" and len(parts) >= 5:
                target_device = parts[1]
                channel = parts[3]
            else:
                target_device = parts[0]
                channel = parts[2]

            cmd = payload_str.lower()

            actual_ip = None
            with _device_cache_lock:
                for v in _device_cache.values():
                    if v.get("device_id") == target_device:
                        actual_ip = v.get("actual_ip")
                        break

            if not actual_ip:
                for entry in STATIC_DEVICE_MAP.values():
                    if entry.get("device_id") == target_device:
                        actual_ip = entry.get("actual_ip")
                        break

            if actual_ip and cmd in ("on", "off", "toggle"):
                url = f"http://{actual_ip}/relay/{channel}?turn={cmd}"
                print(f"[CoIoT] Inoltro comando relè {target_device} -> {cmd} ({url})")
                requests.get(url, timeout=3)

    except Exception as e:
        print(f"[CoIoT] Errore elaborazione messaggio MQTT: {e}")


def setup_mqtt():
    try:
        client = paho_mqtt.Client(paho_mqtt.CallbackAPIVersion.VERSION2, client_id="coiot-shelly-bridge")
    except AttributeError:
        client = paho_mqtt.Client(client_id="coiot-shelly-bridge")

    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)

    def on_connect(c, ud, flags, rc, *args):
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        if code == 0:
            print(f"[CoIoT] MQTT connesso: {MQTT_BROKER}:{MQTT_PORT}")
            purge_retained_topics(c)
            c.subscribe("+/relay/+/command")
        else:
            print(f"[CoIoT] MQTT errore di connessione: {rc}")

    def on_disconnect(c, ud, rc, *args):
        if _running:
            print(f"[CoIoT] MQTT disconnesso (rc={rc}) -- riconnessione automatica...")

    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_mqtt_message
    client.reconnect_delay_set(min_delay=2, max_delay=30)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except Exception as e:
        print(f"[CoIoT] Connessione MQTT iniziale fallita: {e}")

    client.loop_start()
    return client


def signal_handler(sig, frame):
    global _running
    print("\n[CoIoT] Ricevuto segnale di arresto...")
    _running = False


def main():
    global _running, _debug_mode

    parser = argparse.ArgumentParser(description="CoIoT -> MQTT Bridge per Shelly Gen1")
    parser.add_argument("-v", "--debug", action="store_true", help="Abilita log dettagliato dei pacchetti")
    parser.add_argument("-p", "--port", type=int, default=COIOT_PORT, help="Porta UDP CoIoT (default 5683)")
    args = parser.parse_args()

    _debug_mode = args.debug

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=" * 65)
    print("      CoIoT -> MQTT Bridge  v2.3  (Shelly Gen1 EM Status Bridge)")
    print(f"  MQTT Broker : {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  CoIoT UDP   : :{args.port} (Multicast: {COIOT_MCAST_IP})")
    print(f"  Topic Pub   : PowerMeterHome.shelly/status")
    print(f"  Modalità    : {'DEBUG (Verbose)' if _debug_mode else 'Standard'}")
    print("=" * 65)

    mqtt_pub = setup_mqtt()
    time.sleep(1.0)

    t = threading.Thread(target=listen_loop, args=(mqtt_pub,), daemon=True, name="CoIoTListener")
    t.start()

    while _running:
        time.sleep(1.0)

    print("[CoIoT] Arresto completato.")
    mqtt_pub.loop_stop()
    mqtt_pub.disconnect()
    sys.exit(0)


if __name__ == "__main__":
    main()
