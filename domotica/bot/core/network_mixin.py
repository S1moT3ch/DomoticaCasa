import json
import time
import sys
import socket
import concurrent.futures
import os
import subprocess
import requests
import threading
from datetime import datetime, timedelta
import paho.mqtt.client as mqtt
import config

class NetworkMixin:
    def _ping_single_ip(self, ip, timeout=1.5):
        """Esegue un veloce ping ICMP di verifica stato di un singolo IP"""
        param = '-n' if sys.platform.startswith('win') else '-c'
        timeout_param = '-w' if sys.platform.startswith('win') else '-W'
        timeout_val = str(int(timeout * 1000)) if sys.platform.startswith('win') else str(int(timeout))
        cmd = ['ping', param, '1', timeout_param, timeout_val, ip]
        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout + 1)
            return res.returncode == 0
        except Exception:
            return False


    def _get_raw_server_metrics(self):
        """Estrae i valori raw di CPU (1m), RAM usata (MB) e Temperatura (°C)"""
        cpu_load = None
        ram_mb = None
        temp_c = None

        try:
            import glob
            for tz in glob.glob('/sys/class/thermal/thermal_zone*/temp'):
                with open(tz, 'r') as f:
                    val = int(f.read().strip())
                    temp_c = val / 1000.0 if val > 1000 else float(val)
                    break
        except Exception:
            pass

        try:
            with open('/proc/loadavg', 'r') as f:
                load1 = f.read().split()[0]
                cpu_load = float(load1)
        except Exception:
            pass

        swap_mb = None
        usb_pct = None
        try:
            mem_info = {}
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = int(parts[1].strip().split()[0])
                        mem_info[key] = val
            mem_total = mem_info.get('MemTotal', 0)
            mem_free = mem_info.get('MemFree', 0) + mem_info.get('Buffers', 0) + mem_info.get('Cached', 0)
            if mem_total > 0:
                ram_mb = (mem_total - mem_free) / 1024.0
            
            swap_total = mem_info.get('SwapTotal', 0)
            swap_free = mem_info.get('SwapFree', 0)
            if swap_total > 0:
                swap_mb = (swap_total - swap_free) / 1024.0
        except Exception:
            pass
            
        try:
            import subprocess
            result = subprocess.run(['df', '-k', '/dev/sda5'], capture_output=True, text=True)
            if result.returncode == 0:
                lines_df = result.stdout.strip().split('\n')
                if len(lines_df) > 1:
                    parts = lines_df[1].split()
                    total_kb = int(parts[1])
                    used_kb = int(parts[2])
                    if total_kb > 0:
                        usb_pct = (used_kb / total_kb) * 100.0
        except Exception:
            pass

        return cpu_load, ram_mb, temp_c, swap_mb, usb_pct

    def run_iperf_speedtest(self, target_ip="192.168.1.1", duration=5):
        """Esegue un test di banda reale in tempo reale verso un target IP tramite iperf3"""
        now_str = datetime.now().strftime("%H:%M:%S")
        target_ip = (target_ip or "").strip()
        if not target_ip:
            target_ip = "192.168.1.1"

        iperf_cmd = "iperf3"
        cmd = [iperf_cmd, "-c", target_ip, "-t", str(duration)]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 5)
            if res.returncode != 0 and ("not found" in res.stderr.lower() or "not recognized" in res.stderr.lower()):
                return (
                    f"🚀 *TEST BANDA REALE iperf3* (ore {now_str})\n\n"
                    f"⚠️ `iperf3` non risulta installato sul router.\n"
                    f"Per installarlo esegui da terminale:\n`opkg update && opkg install iperf3`"
                )

            if res.returncode != 0:
                err_msg = res.stderr.strip() or res.stdout.strip()
                if not err_msg:
                    err_msg = "Nessuna risposta. Connessione interrotta o porta 5201 bloccata."
                return (
                    f"🚀 *TEST BANDA REALE iperf3* (ore {now_str})\n\n"
                    f"🎯 *Target*: `{target_ip}`\n"
                    f"❌ **Test non riuscito**: {err_msg[:200]}\n\n"
                    f"💡 _Assicurati che `iperf3 -s` sia attivo in foreground sul dispositivo target._"
                )

            upload_str = "0 Mbps"
            download_str = "0 Mbps"
            
            # Parsing dell'output standard di iperf3
            for line in res.stdout.split('\n'):
                line_lower = line.lower()
                # Troviamo le righe riassuntive finali (sender e receiver)
                if "sender" in line_lower:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p.lower() in ["bits/sec", "kbits/sec", "mbits/sec", "gbits/sec"]:
                            val = parts[i-1]
                            upload_str = f"{val} {p.replace('bits/sec', 'bps')}"
                elif "receiver" in line_lower:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p.lower() in ["bits/sec", "kbits/sec", "mbits/sec", "gbits/sec"]:
                            val = parts[i-1]
                            download_str = f"{val} {p.replace('bits/sec', 'bps')}"

            lines = [
                f"🚀 *TEST BANDA PASSANTE REALE (iperf3)* (ore {now_str})\n",
                f"🎯 *Target Host*: `{target_ip}`",
                f"⏱️ *Durata Test*: `{duration}s`",
                f"\n⬆️ *Upload (Client ➔ Server)*: 🟢 **{upload_str}**",
                f"⬇️ *Download (Server ➔ Client)*: 🟢 **{download_str}**"
            ]
            return "\n".join(lines)

        except subprocess.TimeoutExpired:
            return (
                f"🚀 *TEST BANDA REALE iperf3* (ore {now_str})\n\n"
                f"🎯 *Target*: `{target_ip}`\n"
                f"⏱️ **Timeout**: Il test iperf3 ha impiegato più di {duration+5} secondi.\n"
                f"Verifica la connettività verso `{target_ip}`."
            )
        except Exception as e:
            return (
                f"🚀 *TEST BANDA REALE iperf3* (ore {now_str})\n\n"
                f"⚠️ **Errore di sistema**: {e}"
            )

        except subprocess.TimeoutExpired:
            return (
                f"🚀 *TEST BANDA REALE iperf3* (ore {now_str})\n\n"
                f"🎯 *Target*: `{target_ip}`\n"
                f"⏱️ **Timeout**: Il test iperf3 ha impiegato più di {duration+5} secondi.\n"
                f"Verifica la connettività verso `{target_ip}`."
            )
        except Exception as e:
            return (
                f"🚀 *TEST BANDA REALE iperf3* (ore {now_str})\n\n"
                f"⚠️ **Errore durante l'esecuzione**: {e}"
            )

    def start_network_watchdog(self, notify_callback=None, interval=10):
        """Avvia il monitoraggio periodico in background per le 3 telecamere NVR ed i nodi di rete"""
        if self.network_watchdog_running:
            return

        self.network_watchdog_running = True

        def _watchdog_loop():
            camera_nodes = [
                ("Camera Soggiorno", "192.168.4.160", "camera_soggiorno", "🛋️"),
                ("Camera Corridoio", "192.168.4.161", "camera_corridoio", "🚪"),
                ("Camera Box / Garage", "192.168.3.130", "camera_garage", "🎥")
            ]

            other_nodes = [
                ("Firewall / Gateway", "192.168.1.1", "system", "🛡️"),
                ("Home Server", "192.168.1.162", "system", "🖥️"),
                ("Access Point Box", "192.168.3.2", "system", "📶"),
                ("Ricircolo Acqua", "192.168.4.111", "ricircolo", "💧"),
                ("Modulo Allarme Casa", "192.168.4.112", "alarm_house", "🛡️"),
                ("Sensori AllarmeIO", "192.168.4.113", "alarm_house", "⚡"),
                ("TapoHomePlug", "192.168.4.114", "tapo_camera", "📹"),
                ("Luce Cucina", "192.168.4.115", "luce_cucina", "💡"),
                ("Comandi Garage", "192.168.3.131", "alarm_garage", "🚪"),
                ("PowerMeterBox", "192.168.3.132", "system", "⚡")
            ]

            def _check_node_online(ip):
                # 1. Prova prima ICMP ping veloce (0.8s timeout)
                if self._ping_single_ip(ip, timeout=0.8):
                    return True
                # 2. Fallback rapido su porte RTSP (554) ed HTTP (80)
                for port in [554, 80, 8080]:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(0.5)
                    try:
                        res = s.connect_ex((ip, port))
                        s.close()
                        if res in (0, 111, 10061, 61):
                            return True
                    except Exception:
                        try:
                            s.close()
                        except Exception:
                            pass
                return False

            print("[NETWORK WATCHDOG] Avviato monitoraggio in background di TUTTI i dispositivi domotici e nodi di rete...")

            all_monitored = camera_nodes + other_nodes
            for name, ip, cat, icon in all_monitored:
                self.network_watchdog_states[ip] = _check_node_online(ip)

            while self.network_watchdog_running:
                time.sleep(interval)
                try:
                    now_str = datetime.now().strftime("%H:%M:%S")

                    # 1. Controlla le 3 Telecamere NVR (invia allerta disconnessione se allarme inserito O se Modalità Vacanza è attiva)
                    alarm_armed, alarm_type = self.is_any_alarm_armed()
                    is_vacation = self.is_vacation_mode()

                    for name, ip, cat, icon in camera_nodes:
                        is_up = _check_node_online(ip)
                        prev_state = self.network_watchdog_states.get(ip)

                        # Se la telecamera passa da ONLINE a OFFLINE, esegui 3 tentativi di conferma a distanza di 1 sec
                        # per eliminare falsi positivi causati da micro-drop Wi-Fi / Powerline dell'Access Point Box
                        if prev_state is True and not is_up:
                            print(f"[NETWORK WATCHDOG] Telecamera {name} ({ip}) non risponde, verifico stabilità con ri-tentativi...")
                            for retry in range(3):
                                time.sleep(1.0)
                                if _check_node_online(ip):
                                    is_up = True
                                    print(f"[NETWORK WATCHDOG] Telecamera {name} ({ip}) confermata ONLINE (falso positivo sventato).")
                                    break

                        if prev_state is not None and prev_state != is_up:
                            cb = notify_callback or self.notification_callback
                            is_garage_cam = (ip == "192.168.3.130")

                            if not is_up:
                                # 1. Telecamera va OFFLINE
                                # Garage/Box o Modalità Vacanza: notifica SEMPRE. Soggiorno & Corridoio: notificano se allarme INSERITO
                                should_notify_offline = is_garage_cam or alarm_armed or is_vacation

                                if should_notify_offline:
                                    self.offline_alerts_sent[ip] = True
                                    vac_str = " 🌴 [MODALITÀ VACANZA]" if is_vacation else ""
                                    alarm_status_str = f"mentre l'**Allarme {alarm_type} è INSERITO 🚨**" if alarm_armed else "(Allarme disinserito)"
                                    msg = (
                                        f"🚨 *ALLERTA SICUREZZA: TELECAMERA DISCONNESSA*{vac_str}\n\n"
                                        f"{icon} *{name}* (`{ip}`)\n"
                                        f"⚠️ Telecamera risultata **OFFLINE** {alarm_status_str}!\n\n"
                                        f"🕒 *Ora:* `{now_str}`"
                                    )
                                    print(f"[NETWORK WATCHDOG] ALLERTA TELECAMERA OFFLINE: {name} ({ip})")
                                    if self.event_logger is not None:
                                        self.event_logger.log_event("camera", name, f"Telecamera risultata OFFLINE ({ip})")
                                    if cb:
                                        try:
                                            cb(cat, msg)
                                        except Exception as e:
                                            print(f"[!] Errore callback notifica camera offline ({name}): {e}")
                                else:
                                    self.offline_alerts_sent[ip] = False
                                    print(f"[NETWORK WATCHDOG] Telecamera {name} ({ip}) offline ignorata poiché nessun allarme è inserito.")

                            else:
                                # 2. Telecamera torna ONLINE
                                # Garage/Box o Modalità Vacanza: notifica SEMPRE.
                                was_offline_notified = self.offline_alerts_sent.get(ip, False)
                                should_notify_online = is_garage_cam or (alarm_armed and was_offline_notified) or is_vacation

                                if should_notify_online:
                                    vac_str = " 🌴 [MODALITÀ VACANZA]" if is_vacation else ""
                                    msg = (
                                        f"✅ *RIPRISTINO TELECAMERA: BACK ONLINE*{vac_str}\n\n"
                                        f"{icon} *{name}* (`{ip}`)\n"
                                        f"🟢 La telecamera è **tornata online** ed operativa!\n\n"
                                        f"🕒 *Ora:* `{now_str}`"
                                    )
                                    print(f"[NETWORK WATCHDOG] Telecamera tornata ONLINE: {name} ({ip})")
                                    if self.event_logger is not None:
                                        self.event_logger.log_event("camera", name, f"Telecamera tornata BACK ONLINE ({ip})")
                                    if cb:
                                        try:
                                            cb(cat, msg)
                                        except Exception as e:
                                            print(f"[!] Errore callback notifica camera online ({name}): {e}")

                                else:
                                    print(f"[NETWORK WATCHDOG] Telecamera {name} ({ip}) ripristino online ignorato (allarme disinserito o notifica offline non inviata).")

                                # Reset del flag di notifica offline
                                self.offline_alerts_sent[ip] = False

                        self.network_watchdog_states[ip] = is_up

                    # 2. Controlla tutti gli altri nodi e dispositivi domotici di rete
                    for name, ip, cat, icon in other_nodes:
                        is_up = _check_node_online(ip)
                        prev_state = self.network_watchdog_states.get(ip)

                        if prev_state is not None and prev_state != is_up:
                            cb = notify_callback or self.notification_callback
                            vac_tag = " 🌴 [MODALITÀ VACANZA]" if is_vacation else ""
                            if not is_up:
                                msg = (
                                    f"🚨 *ALLERTA RETE: DISPOSITIVO OFFLINE*{vac_tag}\n\n"
                                    f"{icon} *{name}* (`{ip}`)\n"
                                    f"⚠️ Il dispositivo domotico **non risponde più alla rete / ping**!\n\n"
                                    f"🕒 *Ora:* `{now_str}`"
                                )
                                if self.event_logger is not None:
                                    self.event_logger.log_event("system", name, f"Dispositivo domotico OFFLINE ({ip})")
                            else:
                                msg = (
                                    f"✅ *RIPRISTINO RETE: BACK ONLINE*{vac_tag}\n\n"
                                    f"{icon} *{name}* (`{ip}`)\n"
                                    f"🟢 Il dispositivo domotico è **tornato online** e risponde regolarmente!\n\n"
                                    f"🕒 *Ora:* `{now_str}`"
                                )
                                if self.event_logger is not None:
                                    self.event_logger.log_event("system", name, f"Dispositivo domotico BACK ONLINE ({ip})")
                            
                            print(f"[NETWORK WATCHDOG] Cambio stato {name} ({ip}): prev={prev_state} -> current={is_up}")
                            if cb:
                                try:
                                    cb(cat, msg)
                                except Exception as e:
                                    print(f"[!] Errore callback notifica watchdog: {e}")

                        self.network_watchdog_states[ip] = is_up

                    # 3. Controllo salute connessione MQTT Broker (Auto-Reconnect)
                    if self.client and (not self.connected or (hasattr(self.client, "is_connected") and not self.client.is_connected())):
                        print("[NETWORK WATCHDOG] Rilevata disconnessione MQTT. Tentativo di riconnessione automatica...")
                        try:
                            self.client.reconnect()
                            self.connected = True
                            print("[NETWORK WATCHDOG] Riconnessione MQTT riuscita!")
                        except Exception as e_rec:
                            print(f"[NETWORK WATCHDOG] Riconnessione MQTT fallita: {e_rec}")

                    # --- POLLING ED ARCHIVIAZIONE TELEMETRIA CLIMA OGNI 15 MINUTI ---
                    now_sec = time.time()
                    if now_sec - getattr(self, "_last_climate_poll", 0) > 900:
                        self._last_climate_poll = now_sec
                        try:
                            if self.client:
                                self.client.publish("Termostato.bticino/request_update", "STATUS")
                                self.client.publish("Canalizzato.daikin/request_update", "STATUS")
                            if self.termostato_data and isinstance(self.termostato_data, dict) and hasattr(self, "event_logger") and self.event_logger:
                                curr_t = self.termostato_data.get("current_temp")
                                hum_v = self.termostato_data.get("humidity")
                                target_t = self.termostato_data.get("target_temp")
                                mode_v = self.termostato_data.get("mode")
                                self.event_logger.log_climate_telemetry("Termostato BTicino", curr_t, hum_v, target_t, mode_v)
                            
                            if hasattr(self, "event_logger") and self.event_logger:
                                cpu, ram, temp, swap, usb = self._get_raw_server_metrics()
                                self.event_logger.log_server_telemetry(cpu, ram, temp, swap, usb)
                        except Exception:
                            pass

                except Exception as loop_err:
                    print(f"[!] Errore nel ciclo Network Watchdog: {loop_err}")


        t = threading.Thread(target=_watchdog_loop, daemon=True, name="NetworkWatchdogThread")
        t.start()





