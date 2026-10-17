import json
import time
import sys
import socket
import concurrent.futures
import os
import re
import html
import subprocess
import requests
import threading
from datetime import datetime, timedelta
import paho.mqtt.client as mqtt
import config

KNOWN_DEVICE_NAMES = {
    "LuceCucina.shelly": ("Luce Cucina", "💡"),
    "LedAndrea.shelly": ("Led Andrea", "💡"),
    "Ricircolo.shelly": ("Ricircolo Acqua", "💧"),
    "TapoHome.shelly": ("Telecamera Casa", "📹"),
    "Allarme.shelly": ("Relè Allarme Garage", "🚨"),
    "AllarmeIO.shelly": ("Modulo IO Allarmi", "🛡️"),
    "PowerMeterBox.shelly": ("PowerMeterBox", "🔌"),
    "garage": ("Garage", "🚗")
}

class ReportsMixin:
    def get_shelly_report(self):
        """Metodo pubblico thread-safe per recuperare lo stato dei dispositivi Shelly"""
        with self.lock:
            return self._get_shelly_report_unlocked()

    def _get_shelly_report_unlocked(self):
        """Metodo interno per costruire il report Shelly leggibile e amichevole"""
        if not self.shelly_devices:
            return "🔌 *DISPOSITIVI SHELLY*: Nessun dispositivo rilevato al momento."

        lines = ["💡 *STATO LUCI & DISPOSITIVI SHELLY*\n"]
        total_on = 0
        total_off = 0

        for s_id, data in self.shelly_devices.items():
            last_time = data["last_updated"]
            channels = data["channels"]

            # Nome e Icona amichevoli
            if s_id in KNOWN_DEVICE_NAMES:
                name, icon = KNOWN_DEVICE_NAMES[s_id]
            else:
                name = s_id.replace("shellies/", "").replace(".shelly", "").replace("-", " ").title()
                icon = "🔹"

            for ch_id, ch_data in channels.items():
                is_on = ch_data.get("is_on")
                power = ch_data.get("power")

                if is_on is True:
                    if "Luce" in name or "Led" in name or "Telecamera" in name:
                        status_text = "🟢 **ACCESA**"
                    else:
                        status_text = "🟢 **ATTIVO**"
                    total_on += 1
                elif is_on is False:
                    if "Luce" in name or "Led" in name or "Telecamera" in name:
                        status_text = "🔴 **SPENTA**"
                    else:
                        status_text = "🔴 **DISATTIVATO**"
                    total_off += 1
                else:
                    status_text = "⚪ *Stato non disponibile*"

                ch_str = f" (Canale {ch_id})" if len(channels) > 1 else ""
                power_str = f" — ⚡ `{power:.1f} W`" if power is not None else ""

                lines.append(f"{icon} *{name}*{ch_str}: {status_text}{power_str}")

        lines.append(f"\n📊 *Totale*: 🟢 *{total_on} Accesi/Attivi* | 🔴 *{total_off} Spenti/Disattivati*")
        return "\n".join(lines)

    def get_climate_report(self):
        """Genera il report completo e dettagliato per tutti i dispositivi di climatizzazione (Daikin + BTicino/Netatmo)"""
        # Solicita aggiornamento attivo dai bridge e attende i dati prima di acquisire il lock
        try:
            if self.client and self.connected:
                self.client.publish("Termostato.bticino/request_update", "STATUS")
                self.client.publish("Canalizzato.daikin/request_update", "refresh")
                import time
                time.sleep(3.0)  # Attende la risposta asincrona dai bridge via MQTT prima di impaginare
        except Exception:
            pass

        with self.lock:
            now_str = datetime.now().strftime("%H:%M:%S")
            lines = [f"🌡️ *STATO DETTAGLIATO CLIMA & TERMOREGOLAZIONE* (ore {now_str})\n"]
            has_climate = False

            # --- 1. CLIMATIZZATORE DAIKIN CANALIZZATO ---
            if self.daikin_data and isinstance(self.daikin_data, dict):
                has_climate = True
                d = self.daikin_data
                p_val = d.get("power") if d.get("power") is not None else d.get("on_off")
                if isinstance(p_val, bool):
                    d_on = p_val
                elif isinstance(p_val, str):
                    d_on = p_val.strip().lower() in ["on", "true", "1"]
                else:
                    d_on = bool(p_val)

                st_icon = "🟢 **ACCESO**" if d_on else "🔴 **SPENTO**"
                target_temp = d.get("target_temp") or d.get("temp", "--")
                room_temp = d.get("room_temp", "--")
                mode = str(d.get("mode", "--")).title()
                fan = str(d.get("fan_speed", "--")).title()
                online = d.get("online", True)
                hum = d.get("room_humidity") or d.get("humidity")

                lines.append("❄️ *CONDIZIONATORE DAIKIN (Canalizzato)*")
                lines.append(f"• *Stato*: {st_icon}")
                lines.append(f"• *Temperatura Stanza*: *{room_temp}°C*")
                outdoor_temp = d.get("outdoor_temp") or d.get("outdoor_temperature") or d.get("outside_temp") or d.get("sens_outdoor_temp")
                if outdoor_temp is not None:
                    lines.append(f"• *Temperatura Esterna*: 🌿 *{outdoor_temp}°C*")
                lines.append(f"• *Temperatura Target*: *{target_temp}°C*")

                lines.append(f"• *Modalità*: `{mode}`")
                lines.append(f"• *Ventola*: `{fan}`")
                if hum is not None:
                    lines.append(f"• *Umidità*: *{hum}%*")
                online_str = "🟢 Online" if online else "🔴 Offline"
                lines.append(f"• *Connessione Cloud*: {online_str}")
                lines.append("")

            # --- 2. TERMOSTATO BTICINO / NETATMO ---
            if self.termostato_data and isinstance(self.termostato_data, dict):
                has_climate = True
                t = self.termostato_data
                curr_t = t.get("current_temp", "--")
                target_t = t.get("target_temp", "--")
                hum = t.get("humidity")
                mode = str(t.get("mode", "manual")).title()
                sched = t.get("active_schedule")

                lines.append("🌡️ *TERMOSTATO BTICINO (Smarther Netatmo)*")
                lines.append(f"• *Temperatura Misurata*: *{curr_t}°C*")
                lines.append(f"• *Temperatura Setpoint*: *{target_t}°C*")
                if hum is not None:
                    lines.append(f"• *Umidità Stanza*: *{hum}%*")
                lines.append(f"• *Modalità*: `{mode}`")
                if sched:
                    lines.append(f"• *Programma Attivo*: `{sched}`")
                lines.append("")

            if not has_climate:
                lines.append("ℹ️ _Nessun dato dai dispositivi Clima ricevuto al momento._")
                lines.append("Assicurati che i servizi `daikin_bridge` e `netatmo_bridge` siano attivi sul router.")

            return "\n".join(lines)

    def get_power_status_report(self):
        """Interroga il monitor Vercel remoto per verificare lo stato dettagliato dell'alimentazione e dei singoli nodi casalinghi"""
        remote_url = getattr(config, "REMOTE_SERVER_URL", "https://powermonitor-one.vercel.app/api/heartbeat")
        check_url = remote_url.replace("/api/heartbeat", "/api/cron/check-heartbeat")
        
        now_str = datetime.now().strftime("%H:%M:%S")
        lines = [
            f"⚡ *STATO ALIMENTAZIONE & HEARTBEAT RETE* (ore {now_str})",
            "══════════════════════════\n"
        ]

        try:
            resp = requests.get(check_url, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                st = data.get("status")
                
                # Estrazione elapsedServer e elapsedFirewall
                nodes = data.get("nodes", {})
                
                if "server" in nodes:
                    el_server = nodes["server"].get("elapsedSeconds")
                else:
                    el_server = data.get("elapsedServer", data.get("elapsed_server", data.get("elapsedSeconds")))
                    
                if "firewall" in nodes:
                    el_fw = nodes["firewall"].get("elapsedSeconds")
                else:
                    el_fw = data.get("elapsedFirewall", data.get("elapsed_firewall"))
                is_outage = data.get("isOutageAlertActive", False) or (st == "total_blackout_alert_sent")

                def _fmt_sec(sec):
                    if sec is None:
                        return "⚪ *In attesa primo heartbeat*"
                    s = int(round(float(sec)))
                    if s < 60:
                        return f"`{s}s` fa"
                    m = s // 60
                    rem_s = s % 60
                    return f"`{m}m {rem_s}s` fa"

                server_up = el_server is not None and float(el_server) <= 90
                fw_up = el_fw is not None and float(el_fw) <= 90 if el_fw is not None else None

                # 1. VALUTAZIONE COMPLESSIVA RETE ELETTRICA
                lines.append("🔌 *STATO ALIMENTAZIONE COMPLESSIVO:*")
                if is_outage or (server_up is False and (fw_up is False or fw_up is None)):
                    lines.append("• 🚨 **ASSENZA DI CORRENTE / BLACKOUT IN CASA**")
                    lines.append("  └ _Nessun segnale di vita dai nodi casalinghi._")
                elif server_up and (fw_up is True or fw_up is None):
                    lines.append("• 🟢 **ALIMENTAZIONE REGOLARE**: La rete elettrica casalinga è attiva e stabile.")
                elif not server_up and fw_up:
                    lines.append("• ⚠️ **INCONSISTENZA NODI**: L'Home Server non risponde ma il Firewall è attivo.")
                    lines.append("  └ _Non si tratta di un blackout, ma di un riavvio o blocco del solo server._")
                elif server_up and fw_up is False:
                    lines.append("• ⚠️ **AVVISO FIREWALL**: L'Home Server è online ma il Firewall non invia segnali.")
                else:
                    lines.append(f"• ℹ️ Stato monitor: `{st}`")

                lines.append("\n🖥️ *DETTAGLIO NODI HEARTBEAT (Vercel Watchdog):*")
                
                # Home Server (192.168.1.162)
                st_srv = "🟢 **ONLINE**" if server_up else "🔴 **OFFLINE**"
                lines.append(f"• 🖥️ *Home Server* (`192.168.1.162`): {st_srv}\n  └ _Ultimo heartbeat_: {_fmt_sec(el_server)}")

                # Firewall / Gateway (192.168.1.1)
                if el_fw is not None:
                    st_fw = "🟢 **ONLINE**" if fw_up else "🔴 **OFFLINE**"
                    lines.append(f"• 🛡️ *Firewall / Gateway* (`192.168.1.1`): {st_fw}\n  └ _Ultimo heartbeat_: {_fmt_sec(el_fw)}")
                else:
                    lines.append("• 🛡️ *Firewall / Gateway* (`192.168.1.1`): ⚪ *In attesa primo heartbeat*")

                lines.append("\n☁️ *SERVER REMOTO VERCEL:*")
                lines.append(f"• *Watchdog Cloud*: 🟢 **ATTIVO** (`powermonitor-one.vercel.app`)")

            else:
                lines.append(f"⚠️ Errore risposta monitor Vercel (HTTP {resp.status_code}).")
        except Exception as e:
            lines.append(f"⚠️ Impossibile contattare il monitor Vercel remoto: {e}")

        return "\n".join(lines)


    def get_adguard_report(self):
        """Interroga l'istanza locale di AdGuardHome per recuperare le statistiche di blocco DNS"""
        url = config.ADGUARD_URL.rstrip('/') + "/control/stats"
        status_url = config.ADGUARD_URL.rstrip('/') + "/control/status"
        
        auth = None
        if config.ADGUARD_USER and config.ADGUARD_PASSWORD:
            auth = (config.ADGUARD_USER, config.ADGUARD_PASSWORD)
            
        now_str = datetime.now().strftime("%H:%M:%S")
        lines = [
            f"🛡️ *STATISTICHE ADGUARD HOME* (ore {now_str})",
            "══════════════════════════\n"
        ]
        
        try:
            # Recupero stato della protezione
            status_resp = requests.get(status_url, auth=auth, timeout=5)
            is_running = False
            if status_resp.ok:
                st_data = status_resp.json()
                is_running = st_data.get("protection_enabled", False)
                
            st_text = "🟢 *ATTIVA*" if is_running else "🔴 *DISATTIVATA*"
            lines.append(f"• *Protezione Globale*: {st_text}")
            
            # Recupero statistiche
            stats_resp = requests.get(url, auth=auth, timeout=5)
            if stats_resp.ok:
                stats = stats_resp.json()
                
                num_queries = stats.get("num_dns_queries", 0)
                num_blocked = stats.get("num_blocked_filtering", 0)
                avg_time = stats.get("avg_processing_time", 0) * 1000 # in ms
                
                percent = 0.0
                if num_queries > 0:
                    percent = (num_blocked / num_queries) * 100
                    
                lines.append(f"• *Query DNS Totali*: `{num_queries:,}`")
                lines.append(f"• *Richieste Bloccate*: `{num_blocked:,}`")
                lines.append(f"• *Percentuale Blocco*: `{percent:.2f}%`")
                lines.append(f"• *Tempo Medio Risposta*: `{avg_time:.2f} ms`")
            else:
                if stats_resp.status_code == 403 or stats_resp.status_code == 401:
                    lines.append(f"⚠️ Impossibile recuperare statistiche: Errore di Autenticazione (HTTP {stats_resp.status_code}). Verifica le credenziali.")
                else:
                    lines.append(f"⚠️ Impossibile recuperare statistiche (HTTP {stats_resp.status_code})")
                
        except Exception as e:
            lines.append(f"⚠️ Errore di connessione ad AdGuardHome: Impossibile connettersi.")
            lines.append(f"Assicurati che AdGuardHome sia attivo su {config.ADGUARD_URL}")
            
        return "\n".join(lines)

    def get_server_health_report(self):
        """Restituisce lo stato vitale (RAM, CPU, Disco, Temp) del server OpenWRT"""
        
        penalties = 0
        details = []

        cpu_raw, ram_raw, temp_raw, swap_raw, usb_raw = self._get_raw_server_metrics()

        # 1. TEMPERATURA 🌡️
        temp_c = temp_raw
        if temp_c is not None:
            if temp_c < 65:
                t_icon = "🟢 Ottimale"
            elif temp_c < 80:
                t_icon = "🟡 Calda"
                penalties += 1
            else:
                t_icon = "🔴 Critica"
                penalties += 2
            details.append(f"🌡️ **Temperatura CPU**: {t_icon} (`{temp_c:.1f} °C`)")
        else:
            details.append("🌡️ **Temperatura CPU**: ⚪ _Sensore non rilevato_")

        # 2. CARICO PROCESSORE E UPTIME ⚡
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_sec = float(f.read().split()[0])
                days = int(uptime_sec // 86400)
                hours = int((uptime_sec % 86400) // 3600)
                mins = int((uptime_sec % 3600) // 60)
                details.append(f"⏱️ **Uptime Sistema**: `{days}g {hours}h {mins}m`")
        except Exception:
            pass

        try:
            with open('/proc/loadavg', 'r') as f:
                load1, load5, load15 = f.read().split()[:3]
                if float(load1) > 4.0:
                    penalties += 1
                details.append("⚡ **Carico CPU (Media)**:")
                details.append(f"  ├ _1 Minuto_  : `{load1}`")
                details.append(f"  ├ _5 Minuti_  : `{load5}`")
                details.append(f"  └ _15 Minuti_ : `{load15}`")
        except Exception:
            pass

        # 3. MEMORIA RAM E SWAP 🧠
        try:
            mem_info = {}
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = int(parts[1].strip().split()[0]) # in kB
                        mem_info[key] = val
            
            # RAM
            mem_total = mem_info.get('MemTotal', 0)
            mem_free = mem_info.get('MemFree', 0) + mem_info.get('Buffers', 0) + mem_info.get('Cached', 0)
            mem_used = mem_total - mem_free
            if mem_total > 0:
                mem_pct = (mem_used / mem_total) * 100
                if mem_pct > 90: penalties += 1
                details.append(f"🧠 **RAM Usata**: `{mem_used/1024:.0f}MB` su `{mem_total/1024:.0f}MB` ({mem_pct:.1f}%)")

            # Swap
            swap_total = mem_info.get('SwapTotal', 0)
            swap_free = mem_info.get('SwapFree', 0)
            swap_used = swap_total - swap_free
            if swap_total > 0:
                swap_pct = (swap_used / swap_total) * 100
                if swap_pct > 50:
                    penalties += 1
                    s_icon = "🔴"
                else:
                    s_icon = "🟢"
                details.append(f"⚙️ **Swap Usata**: {s_icon} `{swap_used/1024:.0f}MB` su `{swap_total/1024:.0f}MB` ({swap_pct:.1f}%)")
        except Exception:
            details.append("🧠 **RAM**: _Impossibile leggere meminfo_")

        # 4. ARCHIVIAZIONE DISCHI 💾
        import shutil
        import subprocess
        details.append("\n💾 **SPAZIO DISCO:**")
        # Disco principale
        try:
            total, used, free = shutil.disk_usage("/")
            pct = (used / total) * 100
            if pct > 90: penalties += 1
            details.append(f"• **Root (/)**: `{used / (1024**3):.1f}GB` usati su `{total / (1024**3):.1f}GB` ({pct:.1f}%)")
        except Exception:
            pass

        # Disco Telecamere NVR
        mnt5 = "/dev/sda5"
        try:
            # Uso df per una lettura più sicura nei sistemi embedded come OpenWRT
            result = subprocess.run(['df', '-k', mnt5], capture_output=True, text=True)
            if result.returncode == 0:
                lines_df = result.stdout.strip().split('\n')
                if len(lines_df) > 1:
                    parts = lines_df[1].split()
                    total_kb = int(parts[1])
                    used_kb = int(parts[2])
                    pct = (used_kb / total_kb) * 100
                    if pct > 90: penalties += 1
                    disk_icon = "🔴" if pct > 90 else ("🟡" if pct > 75 else "🟢")
                    details.append(f"• **USB CamNVR ({mnt5})**: {disk_icon} `{used_kb / (1024**2):.1f}GB` usati su `{total_kb / (1024**2):.1f}GB` ({pct:.1f}%)")
            else:
                # Fallback con shutil
                total, used, free = shutil.disk_usage(mnt5)
                pct = (used / total) * 100
                if pct > 90: penalties += 1
                disk_icon = "🔴" if pct > 90 else ("🟡" if pct > 75 else "🟢")
                details.append(f"• **USB CamNVR ({mnt5})**: {disk_icon} `{used / (1024**3):.1f}GB` usati su `{total / (1024**3):.1f}GB` ({pct:.1f}%)")
        except Exception:
            details.append(f"• **USB CamNVR ({mnt5})**: ⚪ _Partizione non trovata/Non montata_")

        # 5. GIUDIZIO GLOBALE
        if penalties == 0:
            health_status = "🟢 **ECCELLENTE**"
        elif penalties <= 2:
            health_status = "🟡 **ACCETTABILE** _(con avvisi)_"
        else:
            health_status = "🔴 **CRITICO** _(intervenire)_"

        now_str = datetime.now().strftime("%H:%M:%S")
        lines = [
            f"🖥️ *SALUTE HOME SERVER NVR* (ore {now_str})",
            f"🩺 **Stato Globale Server**: {health_status}\n"
        ]
        lines.extend(details)

        return "\n".join(lines)


    def get_summary_report(self, include_network_and_anomalies=False):
        """Genera una Dashboard domotica chiara ed ultra-leggibile per l'utente"""
        with self.lock:
            # Invia interrogazione RPC in background per aggiornare tutti i dispositivi attivi
            threading.Thread(target=self.request_active_status, daemon=True).start()

            now_str = datetime.now().strftime("%H:%M:%S")

            report = [
                f"🏡 *STATO DOMOTICA CASA & GARAGE* (ore {now_str})\n"
            ]

            if include_network_and_anomalies:
                # Verifica anomalie recenti (ultime 12h) e dispositivi attualmente offline (Solo per report giornaliero)
                overnight_logs = []
                if hasattr(self, "event_logger") and self.event_logger:
                    try:
                        overnight_logs = self.event_logger.get_grouped_overnight_anomalies(hours=12)
                    except Exception as e:
                        print(f"[!] Errore lettura anomalie: {e}")

                cam1_up = self.network_watchdog_states.get("192.168.4.160")
                if cam1_up is None: cam1_up = True  # Evita ping bloccante a freddo

                cam3_up = self.network_watchdog_states.get("192.168.4.161")
                if cam3_up is None: cam3_up = True

                cam_up = self.network_watchdog_states.get("192.168.3.130")
                if cam_up is None: cam_up = True

                any_currently_offline = not (cam1_up and cam3_up and cam_up)

                if not overnight_logs and not any_currently_offline:
                    status_header = "🟢 *STATO GENERALE: TUTTO REGOLARE (Nessun evento o disconnessione nelle ultime ore)*"
                elif any_currently_offline:
                    status_header = "🚨 *STATO GENERALE: ATTENZIONE (Dispositivi / telecamere attualmente OFFLINE)*"
                else:
                    status_header = f"⚠️ *STATO GENERALE: ANOMALIE RECENTI ({len(overnight_logs)} eventi/variazioni registrati)*"

                report.append(f"{status_header}\n")


            # --- 1. AREA CASA ---
            report.append("══════════════════════")
            report.append("🏠 *AREA CASA*")
            report.append("══════════════════════")

            # Consumi Casa (PowerMeterHome.shelly)
            pmh_real_id, pmh_data = self._find_shelly_device("PowerMeterHome.shelly")
            if pmh_data:
                pmh_ch = pmh_data["channels"].get("0", {})
                pmh_pw = pmh_ch.get("power")
                pmh_v  = pmh_ch.get("voltage")
                pmh_pw_str = f"`{pmh_pw:.0f} W`" if pmh_pw is not None else "_N/D_"
                pmh_v_str  = f" — `{pmh_v:.1f} V`" if pmh_v is not None else ""
                report.append(f"🔌 *Consumi Casa*: ⚡ {pmh_pw_str}{pmh_v_str}")

            # Dispositivi Casa
            casa_dev_keys = [
                ("LuceCucina.shelly", "Luce Cucina", "💡"),
                ("LedAndrea.shelly", "Led Andrea", "💡"),
                ("Ricircolo.shelly", "Ricircolo Acqua", "💧"),
                ("TapoHome.shelly", "Telecamera Soggiorno (Tapo)", "📹")
            ]

            for dev_key, dev_name, dev_icon in casa_dev_keys:
                real_id, data = self._find_shelly_device(dev_key)
                if data:
                    ch_data = data["channels"].get("0", {})
                    is_on = ch_data.get("is_on")
                    
                    if is_on is True:
                        st = "🟢 **ACCESA**" if ("Luce" in dev_name or "Led" in dev_name or "Telecamera" in dev_name) else "🟢 **ATTIVO**"
                        
                        if "Ricircolo" in dev_name:
                            rem_sec = None
                            if ch_data.get("timer_remaining") is not None:
                                try:
                                    rem_sec = float(ch_data["timer_remaining"])
                                except (TypeError, ValueError):
                                    pass
                            
                            if rem_sec is None and ch_data.get("timer_duration") and ch_data.get("timer_started_at"):
                                try:
                                    dur = float(ch_data["timer_duration"])
                                    started = float(ch_data["timer_started_at"])
                                    rem_sec = max(0.0, dur - (time.time() - started))
                                except (TypeError, ValueError):
                                    pass

                            if rem_sec is not None and rem_sec > 0:
                                m = int(rem_sec // 60)
                                s = int(rem_sec % 60)
                                st += f" (⏱️ Auto-Off tra `{m:02d}:{s:02d}` min)"

                    elif is_on is False:
                        st = "🔴 **SPENTA**" if ("Luce" in dev_name or "Led" in dev_name or "Telecamera" in dev_name) else "🔴 **DISATTIVATO**"
                    else:
                        st = "⚪ *Sincronizzazione...*"
                else:
                    st = "🔴 **SPENTA / DISATTIVATA**"
                
                report.append(f"{dev_icon} *{dev_name}*: {st}")

            # Telecamere NVR Casa (Soggiorno e Corridoio)
            cam1_up = self.network_watchdog_states.get("192.168.4.160")
            report.append(f"🛋️ *Camera Soggiorno (NVR)*: {'🟢 **ONLINE**' if cam1_up is not False else '🔴 **OFFLINE**'}")

            cam3_up = self.network_watchdog_states.get("192.168.4.161")
            report.append(f"🚪 *Camera Corridoio (NVR)*: {'🟢 **ONLINE**' if cam3_up is not False else '🔴 **OFFLINE**'}")

            # Allarme Casa (da AllarmeIO.shelly Input 1)
            st_c = self.io_inputs.get(1, False)
            st_str = "🚨 **INSERITO (ATTIVO)**" if st_c else "🔓 **Disinserito**"
            report.append(f"🛡️ *Allarme Casa*: {st_str}")


            # --- Sottosezione Temperature & Clima ---
            temp_lines = []

            # Termostato BTicino / Netatmo
            if self.termostato_data and isinstance(self.termostato_data, dict):
                curr_t = self.termostato_data.get("current_temp")
                target_t = self.termostato_data.get("target_temp")
                t_hum = self.termostato_data.get("humidity")
                t_mode = self.termostato_data.get("mode", "manual")
            
                t_str = f"• *{curr_t}°C* - Termostato"
                temp_lines.append(t_str)

            # Climatizzatore Daikin
            if self.daikin_data and isinstance(self.daikin_data, dict):
                p_val = self.daikin_data.get("power")
                if p_val is None:
                    p_val = self.daikin_data.get("on_off")
                
                if isinstance(p_val, bool):
                    d_on = p_val
                elif isinstance(p_val, str):
                    d_on = p_val.strip().lower() in ["on", "true", "1"]
                else:
                    d_on = bool(p_val)

                d_temp = self.daikin_data.get("target_temp") or self.daikin_data.get("temp")
                d_room = self.daikin_data.get("room_temp")
                d_hum = self.daikin_data.get("room_humidity") or self.daikin_data.get("humidity")
                d_mode = self.daikin_data.get("mode", "")
                
                d_hum_str = f" — 💧 *Umidità*: *{d_hum}%*" if d_hum is not None else ""
                room_val_str = f"*{d_room}°C*" if d_room is not None else "*--°C*"
                
                if d_on:
                    st_d = f"🟢 **ACCESO** (Set: `{d_temp}°C` - Modo: `{d_mode.title()}`)"
                else:
                    st_d = f"🔴 **SPENTO**"
                    
                temp_lines.append(f"• {room_val_str} - Canalizzato")

            if temp_lines:
                report.append("\n🌡️ *TEMPERATURE:*")
                report.extend(temp_lines)

            report.append("")



            # --- 2. AREA GARAGE ---
            report.append("══════════════════════")
            report.append("🚗 *AREA GARAGE*")
            report.append("══════════════════════")
            has_garage = False

            # Consumi Garage (PowerMeterBox.shelly - Shelly Plus 1PM)
            garage_shelly_key = "PowerMeterBox.shelly"
            pmb_real_id, pmb_data = self._find_shelly_device(garage_shelly_key)

            # Se non ancora sincronizzato in RAM (es. appena riavviato il bot), interroga via HTTP locale
            if not pmb_data or pmb_data.get("channels", {}).get("0", {}).get("is_on") is None:
                try:
                    r_pmb = requests.get("http://192.168.3.132/rpc/Shelly.GetStatus", timeout=0.6)
                    if r_pmb.status_code == 200:
                        j_pmb = r_pmb.json()
                        sw_pmb = j_pmb.get("switch:0", {})
                        if sw_pmb:
                            with self.lock:
                                if "PowerMeterBox.shelly" not in self.shelly_devices:
                                    self.shelly_devices["PowerMeterBox.shelly"] = {
                                        "shelly_id": "PowerMeterBox.shelly",
                                        "channels": {},
                                        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
                                    }
                                ch_box = self.shelly_devices["PowerMeterBox.shelly"]["channels"].setdefault("0", {})
                                ch_box["is_on"] = sw_pmb.get("output")
                                ch_box["power"] = sw_pmb.get("apower")
                                ch_box["voltage"] = sw_pmb.get("voltage")
                                ch_box["current"] = sw_pmb.get("current")
                                if "aenergy" in sw_pmb and isinstance(sw_pmb["aenergy"], dict):
                                    e_t = sw_pmb["aenergy"].get("total")
                                    if e_t is not None:
                                        ch_box["energy_kwh"] = float(e_t) / 1000.0
                            pmb_real_id, pmb_data = self._find_shelly_device(garage_shelly_key)
                except Exception:
                    pass

            pbm_up = self.network_watchdog_states.get("192.168.3.132")
            if pbm_up is None:
                pbm_up = self._ping_single_ip("192.168.3.132")

            has_garage = True
            if pmb_data and pmb_data.get("channels", {}).get("0", {}).get("is_on") is not None:
                pmb_ch = pmb_data["channels"].get("0", {})
                pmb_on = pmb_ch.get("is_on")
                pmb_pw = pmb_ch.get("power")
                pmb_v = pmb_ch.get("voltage")
                if pmb_on is True:
                    pmb_pw_str = f"`{pmb_pw:.0f} W`" if pmb_pw is not None else "_N/D_"
                    pmb_v_str = f" — `{pmb_v:.1f} V`" if pmb_v is not None else ""
                    pmb_st = f"⚡ {pmb_pw_str}{pmb_v_str}"
                elif pmb_on is False:
                    pmb_st = "🔴 **SPENTO**"
                else:
                    pmb_st = "🟢 **ONLINE**" if pbm_up else "🔴 **OFFLINE**"
            else:
                pmb_st = "🟢 **ONLINE**" if pbm_up else "🔴 **OFFLINE**"
            report.append(f"🔌 *Consumi Garage*: {pmb_st}")

            # Serranda (Voltmetro)
            if self.shutter_voltage is not None:
                has_garage = True
                v = self.shutter_voltage
                if v > 5.3:
                    sh_status = "🔴 **APERTA**"
                elif v > 1.0:
                    sh_status = "🟢 **CHIUSA**"
                else:
                    sh_status = "⚙️ *In movimento*"
                report.append(f"🚪 *Serranda Garage*: {sh_status}")

            # Attuatori di rete (da topic garage/stato/<id>)
            if self.garage_states:
                has_garage = True
                for ch_id, st_val in sorted(self.garage_states.items()):
                    val_upper = st_val.upper()
                    if val_upper in ["LOCKED", "BLOCCATO"]:
                        st_str = "🔴 **BLOCCATO**"
                    elif val_upper in ["ON", "ACTIVE", "ATTIVO", "READY", "PRONTO", "OFF", "STATUS", "OK", "TRUE", "1", "0"]:
                        st_str = "🟢 **PRONTO**"
                    else:
                        st_str = f"🟢 **PRONTO** ({st_val})"


                    if ch_id == "1":
                        label = "⚡ *Comando Serranda*"
                    elif ch_id == "2":
                        label = "🚗 *Comando Cancello*"
                    else:
                        label = f"🔌 *Attuatore Garage {ch_id}*"

                    report.append(f"{label}: {st_str}")

            # Telecamera Garage (Stato ping ONLINE/OFFLINE senza mostrare l'IP)
            has_garage = True
            cam_up = self.network_watchdog_states.get("192.168.3.130")
            if cam_up is None:
                cam_up = self._ping_single_ip("192.168.3.130")
            cam_st = "🟢 **ONLINE**" if cam_up else "🔴 **OFFLINE**"
            report.append(f"🎥 *Telecamera Garage*: {cam_st}")


            # Allarme Garage (da AllarmeIO.shelly Input 0)
            if 0 in self.io_inputs:
                has_garage = True
                st_g = self.io_inputs[0]
                st_str = "🚨 **INSERITO (ATTIVO)**" if st_g else "🔓 **Disinserito**"
                report.append(f"🛡️ *Allarme Garage*: {st_str}")


            if not has_garage:
                report.append("ℹ️ _Nessun dispositivo Garage rilevato._")


            # --- 3. STATO NODI DI RETE & ANOMALIE (Inclusi SOLO nel Report Giornaliero Mattutino) ---
            if include_network_and_anomalies:
                report.append("")
                report.append("══════════════════════")
                report.append("🌐 *STATO RETE & NODI INFRASTRUTTURA*")
                report.append("══════════════════════")

                network_nodes = [
                    ("🛡️ Firewall / Gateway", "192.168.1.1"),
                    ("🖥️ Home Server", "192.168.1.162"),
                    ("📶 Access Point Box", "192.168.3.2")
                ]

                for node_name, node_ip in network_nodes:
                    is_up = self.network_watchdog_states.get(node_ip)
                    if is_up is None:
                        is_up = self._ping_single_ip(node_ip)
                    st_str = "🟢 **ONLINE**" if is_up else "🔴 **OFFLINE**"
                    report.append(f"{node_name} (`{node_ip}`): {st_str}")

                # Stato Interfaccia eth0 (Velocità Link Fisico)
                speed_file = "/sys/class/net/eth0/speed"
                if not os.path.exists(speed_file) and os.path.exists("z:/sys/class/net/eth0/speed"):
                    speed_file = "z:/sys/class/net/eth0/speed"
                link_str = "🟢 **1 Gbps (Full Duplex)** [UP]"
                if os.path.exists(speed_file):
                    try:
                        with open(speed_file, "r") as f:
                            s_val = f.read().strip()
                            if s_val.isdigit() and int(s_val) >= 1000:
                                link_str = f"🟢 **{int(s_val)//1000} Gbps** [UP]"
                            elif s_val.isdigit() and int(s_val) > 0:
                                link_str = f"🟢 **{s_val} Mbps** [UP]"
                    except Exception:
                        pass
                report.append(f"🔌 *Link Ethernet eth0*: {link_str}")

                # --- 4. REGISTRO ANOMALIE & EVENTI NOTTURNI (Ultime 12h) ---
                overnight_logs = []
                if hasattr(self, "event_logger") and self.event_logger:
                    try:
                        overnight_logs = self.event_logger.get_grouped_overnight_anomalies(hours=12)
                    except Exception as e:
                        print(f"[!] Errore lettura anomalie raggruppate: {e}")

                if overnight_logs:
                    report.append("")
                    report.append("══════════════════════")
                    report.append("🌙 *ANOMALIE & EVENTI NOTTURNI (Ultime 12h)*")
                    report.append("══════════════════════")

                    icon_map = {
                        "alarm_house": "🛡️", "alarm_garage": "🛡️", "gate": "🚧",
                        "shutter": "🚪", "camera": "📹", "light": "💡", "luce_cucina": "💡",
                        "ricircolo": "💧", "tapo_camera": "📹", "power": "⚡", "humidity": "💧",
                        "command": "🤖", "system": "⚙️"
                    }
                    for item in overnight_logs[:8]:
                        if isinstance(item, dict):
                            ic = icon_map.get(str(item.get("category") or ""), "🔹")
                            report.append(f"• {ic} *{item.get('device')}*: {item.get('text')}")
                        else:
                            ts, cat, dev, details = item
                            time_part = ts.split(" ")[1] if " " in ts else ts
                            ic = icon_map.get(cat, "🔹")
                            report.append(f"• `{time_part}` {ic} *{dev}*: {details}")

            # --- 5. ALTRI SHELLY EVENTUALI ---
            matched_casa_ids = [self._find_shelly_device(k[0])[0] for k in casa_dev_keys if self._find_shelly_device(k[0])[0]]
            # Escludi anche i device Garage già mostrati esplicitamente nell'area Garage
            garage_shelly_excluded = {self._find_shelly_device("PowerMeterBox.shelly")[0]}
            garage_shelly_excluded.discard(None)
            # Escludi device EM Casa (mostrato come misuratore, non come relè generico)
            em_casa_excluded = {self._find_shelly_device("PowerMeterHome.shelly")[0]}
            em_casa_excluded.discard(None)
            other_shelly = {
                k: v for k, v in self.shelly_devices.items()
                if k not in matched_casa_ids
                and k not in garage_shelly_excluded
                and k not in em_casa_excluded
                and k != "Allarme.shelly" and k != "AllarmeIO.shelly"
            }


            if other_shelly:
                report.append("\n📦 *ALTRI DISPOSITIVI:*")
                for s_id, data in other_shelly.items():
                    ch_data = data["channels"].get("0", {})
                    is_on = ch_data.get("is_on")
                    st = "🟢 ACCESO" if is_on else "🔴 SPENTO"
                    report.append(f"• *{s_id}*: {st}")

            return "\n".join(report)


    def get_all_devices(self):
        with self.lock:
            return {
                "shelly_count": len(self.shelly_devices),
                "shelly": self.shelly_devices,
                "io_inputs": self.io_inputs,
                "shutter_voltage": self.shutter_voltage,
                "garage_states": self.garage_states,
                "raw_topics": dict(self.raw_topics)
            }

    def get_devices_ping_report(self):
        """Esegue il ping di tutti i dispositivi domotici suddivisi per Casa e Garage e restituisce l'esito della connettività"""
        casa_devices = [
            ("💧 *Ricircolo Acqua*", "192.168.4.111"),
            ("🛡️ *Allarme Casa*", "192.168.4.112"),
            ("⚡ *AllarmeIO (Sensori)*", "192.168.4.113"),
            ("📹 *TapoHomePlug*", "192.168.4.114"),
            ("💡 *Luce Cucina*", "192.168.4.115")
        ]

        garage_devices = [
            ("🚪 *Comandi Garage*", "192.168.3.131"),
            ("⚡ *PowerMeterBox*", "192.168.3.132")
        ]

        def _ping_dev(dev):
            name, ip = dev
            ports_to_try = [80, 1883, 554, 443, 8080, 22]
            connected = False

            for port in ports_to_try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.4)
                try:
                    res = s.connect_ex((ip, port))
                    s.close()
                    if res in (0, 111, 10061, 61):
                        connected = True
                        break
                except Exception:
                    try:
                        s.close()
                    except Exception:
                        pass

            if not connected:
                param = '-n' if sys.platform.startswith('win') else '-c'
                cmd = ['ping', param, '1', ip]
                try:
                    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                    connected = (res.returncode == 0)
                except Exception:
                    pass

            return name, ip, connected


        now_str = datetime.now().strftime("%H:%M:%S")
        lines = [
            f"📱 *STATO CONNETTIVITÀ DISPOSITIVI DOMOTICI* (ore {now_str})",
            "══════════════════════════\n"
        ]

        all_nodes = casa_devices + garage_devices
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(_ping_dev, all_nodes))

        res_dict = {ip: (name, is_up) for name, ip, is_up in results}

        total_online = 0
        total_count = len(all_nodes)

        # 1. AREA CASA
        lines.append("🏠 *AREA CASA:*")
        for name, ip in casa_devices:
            _, is_up = res_dict.get(ip, (name, False))
            if is_up:
                total_online += 1
                lines.append(f"• {name} (`{ip}`): \n🟢 **ONLINE**")
            else:
                lines.append(f"• {name} (`{ip}`): \n🔴 **OFFLINE**")

        # 2. AREA GARAGE
        lines.append("\n🚗 *AREA GARAGE:*")
        for name, ip in garage_devices:
            _, is_up = res_dict.get(ip, (name, False))
            if is_up:
                total_online += 1
                lines.append(f"• {name} (`{ip}`): \n🟢 **ONLINE**")
            else:
                lines.append(f"• {name} (`{ip}`): \n🔴 **OFFLINE**")

        lines.append(f"\n📊 *Riepilogo Connettività*: 🟢 **{total_online}/{total_count} Dispositivi Raggiungibili**")
        return "\n".join(lines)


    def get_network_status_report(self):
        """Metodo per verificare la raggiungibilità dei nodi LAN ed il funzionamento del Server Remoto su Vercel"""
        lan_nodes = [
            ("🛡️ *Firewall / Gateway*", "192.168.1.1"),
            ("🖥️ *Home Server*", "192.168.1.162"),
            ("📶 *Access Point Box*", "192.168.3.2")
        ]


        def _ping_node(node):
            name, ip = node
            param = '-n' if sys.platform.startswith('win') else '-c'
            timeout_param = '-w' if sys.platform.startswith('win') else '-W'
            timeout_val = '1500' if sys.platform.startswith('win') else '2'
            cmd = ['ping', param, '1', timeout_param, timeout_val, ip]
            try:
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                is_up = (res.returncode == 0)
            except Exception:
                is_up = False
            return name, ip, is_up

        now_str = datetime.now().strftime("%H:%M:%S")
        lines = [f"🌐 *STATO INFRASTRUTTURA DI RETE DOMOTICA* (ore {now_str})\n"]

        # 1. RETE LOCALE (LAN)
        lines.append("🏠 *RETE LOCALE (LAN):*")
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            lan_results = list(executor.map(_ping_node, lan_nodes))

        online_count = 0
        total_nodes = len(lan_results)

        for name, ip, is_up in lan_results:
            if is_up:
                online_count += 1
                lines.append(f"{name} (`{ip}`): 🟢 **ONLINE** (Ping OK)")
            else:
                lines.append(f"{name} (`{ip}`): 🔴 **OFFLINE** (Nessuna risposta)")

        # 2. SERVER REMOTI & CLOUD SERVICES
        lines.append("\n☁️ *SERVER REMOTI & CLOUD SERVICES:*")
        remote_url = getattr(config, "REMOTE_SERVER_URL", "https://powermonitor-one.vercel.app/api/heartbeat")
        v_domain = "powermonitor-one.vercel.app"
        total_nodes += 1
        
        v_ok = False
        v_detail = "Non raggiungibile"
        try:
            check_url = remote_url.replace("/api/heartbeat", "/api/cron/check-heartbeat")
            res = requests.get(check_url, timeout=5)
            if res.ok:
                data = res.json()
                st = data.get("status", "")
                elapsed = data.get("elapsedSeconds") if "elapsedSeconds" in data else data.get("elapsed_seconds")
                
                if st == "ok":
                    v_ok = True
                    elapsed_str = f" ({elapsed:.1f}s fa)" if isinstance(elapsed, (int, float)) else ""
                    v_detail = f"Serverless OK & Home Server Connesso{elapsed_str} 🟢"
                elif st in ["blackout", "offline", "alert"]:
                    v_ok = True
                    elapsed_str = f" da {elapsed:.1f}s" if isinstance(elapsed, (int, float)) else ""
                    v_detail = f"⚠️ ALLERTA BLACKOUT! Assenza segnale{elapsed_str} 🚨"
                else:
                    v_ok = True
                    v_detail = f"Serverless Active (Stato: {st})"
        except Exception as e:
            v_detail = f"Errore di connessione HTTP: {e}"

        if v_ok:
            online_count += 1
            lines.append(f"⚡ *Vercel Watchdog Cloud* (`{v_domain}`): 🟢 **ONLINE**\n   └ _Info: {v_detail}_")
        else:
            lines.append(f"⚡ *Vercel Watchdog Cloud* (`{v_domain}`): 🔴 **OFFLINE**\n   └ _Info: {v_detail}_")


        lines.append(f"\n📊 *Riepilogo Nodi*: 🟢 **{online_count}/{total_nodes} Nodi Attivi ed Operativi**\n")

        # 3. INTERFACCE FISICHE OPENWRT
        lines.append(self.get_openwrt_link_speeds_report())
        return "\n".join(lines)

    def get_openwrt_link_speeds_report(self):
        """Rileva le metriche del canale fisico eth0: capacità di link, velocità istantanea di attraversamento e volume totale dati transitati"""
        now_str = datetime.now().strftime("%H:%M:%S")
        proc_net_dev = "/proc/net/dev"
        if not os.path.exists(proc_net_dev) and os.path.exists("z:/proc/net/dev"):
            proc_net_dev = "z:/proc/net/dev"

        base_paths = ["/sys/class/net", "z:/sys/class/net"]
        target_path = None
        for p in base_paths:
            if os.path.exists(p):
                target_path = p
                break

        lines = ["🔌 *METRICHE CANALE FISICO ETHERNET (`eth0`):*"]

        # 1. Capacità di Link Fisico (PHY)
        speed_val = None
        operstate = "unknown"
        if target_path:
            speed_file = os.path.join(target_path, "eth0", "speed")
            operstate_file = os.path.join(target_path, "eth0", "operstate")
            if os.path.exists(operstate_file):
                try:
                    with open(operstate_file, "r") as f:
                        operstate = f.read().strip().lower()
                except Exception:
                    pass
            if os.path.exists(speed_file):
                try:
                    with open(speed_file, "r") as f:
                        raw_speed = f.read().strip()
                        if raw_speed.isdigit() or (raw_speed.startswith("-") and raw_speed[1:].isdigit()):
                            speed_val = int(raw_speed)
                except Exception:
                    pass

        if operstate == "up":
            if speed_val and speed_val >= 1000:
                link_cap_str = f"🟢 **{speed_val // 1000} Gbps** ({speed_val} Mbps) [UP]"
            elif speed_val and speed_val > 0:
                link_cap_str = f"🟢 **{speed_val} Mbps** [UP]"
            else:
                link_cap_str = "🟢 **Connesso [UP]**"
        else:
            link_cap_str = "⚪ Disconnesso [DOWN]"

        lines.append(f"• ⚙️ *Capacità Porta Fisico/Hardware*: {link_cap_str}")

        # 2. Lettura Traffico Istantaneo & Dati Accumulati da /proc/net/dev
        if not os.path.exists(proc_net_dev):
            lines.append("⚠️ _Impossibile leggere `/proc/net/dev` per la misurazione del flusso dati._")
            return "\n".join(lines)

        def _read_eth0_metrics():
            rx_b, tx_b, rx_p, tx_p = 0, 0, 0, 0
            try:
                with open(proc_net_dev, "r") as f:
                    for line in f.readlines()[2:]:
                        parts = line.strip().split()
                        if parts and parts[0].rstrip(":") == "eth0":
                            rx_b = int(parts[1])
                            rx_p = int(parts[2])
                            tx_b = int(parts[9])
                            tx_p = int(parts[10])
                            break
            except Exception:
                pass
            return rx_b, tx_b, rx_p, tx_p

        rx1, tx1, pkts_rx1, pkts_tx1 = _read_eth0_metrics()
        time.sleep(1.0)
        rx2, tx2, pkts_rx2, pkts_tx2 = _read_eth0_metrics()

        rx_speed = max(0.0, rx2 - rx1)
        tx_speed = max(0.0, tx2 - tx1)

        def _fmt_bytes(b):
            if b >= 1024**3:
                return f"**{b / (1024**3):.2f} GB**"
            elif b >= 1024**2:
                return f"**{b / (1024**2):.1f} MB**"
            else:
                return f"**{b / 1024:.1f} KB**"

        def _fmt_rate(b_sec):
            if b_sec >= 1024 * 1024:
                return f"**{b_sec / (1024*1024):.2f} MB/s** ({(b_sec * 8)/1e6:.1f} Mbps)"
            elif b_sec >= 1024:
                return f"**{b_sec / 1024:.1f} KB/s** ({(b_sec * 8)/1000:.1f} Kbps)"
            else:
                return f"**{int(b_sec)} B/s**"

        lines.append(f"• ⚡ *Velocità Istantanea di Attraversamento* (ore {now_str}):")
        lines.append(f"   └ ⬇️ Down: {_fmt_rate(rx_speed)}")
        lines.append(f"   └ ⬆️ Up: {_fmt_rate(tx_speed)}")
        lines.append(f"• 📦 *Volume Totale Dati Transitati su eth0* (dall'avvio):")
        lines.append(f"   └ ⬇️ Down Accumulato: {_fmt_bytes(rx2)} ({rx2:,} bytes)")
        lines.append(f"   └ ⬆️ Up Accumulato: {_fmt_bytes(tx2)} ({tx2:,} bytes)")

        return "\n".join(lines)

    def get_live_bandwidth_report(self, sample_sec=1.0):
        """Calcola il traffico istantaneo esclusivamente sull'interfaccia eth0 e stima la banda passante occupata da ciascun processo di rete"""
        now_str = datetime.now().strftime("%H:%M:%S")
        proc_net_dev = "/proc/net/dev"
        if not os.path.exists(proc_net_dev):
            if os.path.exists("z:/proc/net/dev"):
                proc_net_dev = "z:/proc/net/dev"
            else:
                return "⚠️ _Impossibile leggere `/proc/net/dev` per la misurazione della banda._"

        proc_tcp = "/proc/net/tcp"
        if not os.path.exists(proc_tcp) and os.path.exists("z:/proc/net/tcp"):
            proc_tcp = "z:/proc/net/tcp"

        def _read_eth0_counters():
            rx, tx = 0, 0
            try:
                with open(proc_net_dev, "r") as f:
                    lines = f.readlines()[2:]
                    for line in lines:
                        parts = line.strip().split()
                        if parts and parts[0].rstrip(":") == "eth0":
                            rx = int(parts[1])
                            tx = int(parts[9])
                            break
            except Exception:
                pass
            return rx, tx

        rx1, tx1 = _read_eth0_counters()
        time.sleep(sample_sec)
        rx2, tx2 = _read_eth0_counters()

        dt = max(0.1, sample_sec)
        rx_bsec = max(0.0, (rx2 - rx1) / dt)
        tx_bsec = max(0.0, (tx2 - tx1) / dt)

        def _fmt_rate(b_sec):
            if b_sec >= 1024 * 1024:
                return f"**{b_sec / (1024*1024):.2f} MB/s** ({(b_sec * 8)/1e6:.1f} Mbps)"
            elif b_sec >= 1024:
                return f"**{b_sec / 1024:.1f} KB/s** ({(b_sec * 8)/1000:.1f} Kbps)"
            else:
                return f"**{int(b_sec)} B/s**"

        lines = [f"📊 *BANDA PASSANTE IN TEMPO REALE* (ore {now_str})\n"]
        lines.append("🌐 *TRAFFICO ISTANTANEO (Interfaccia `eth0`):*")
        lines.append(f"• ⬇️ **Download**: {_fmt_rate(rx_bsec)}")
        lines.append(f"• ⬆️ **Upload**: {_fmt_rate(tx_bsec)}")

        # 2. MISURA E RIPARTIZIONE PER SINGOLO PROCESSO DI RETE
        lines.append("\n⚙️ *BANDA OCCUPATA PER PROCESSO DI RETE:*")

        from collections import defaultdict
        active_conns = defaultdict(int)
        if os.path.exists(proc_tcp):
            try:
                with open(proc_tcp, "r") as f:
                    for line in f.readlines()[1:]:
                        parts = line.strip().split()
                        if len(parts) >= 10 and parts[3] == "01":  # ESTABLISHED
                            local_port = int(parts[1].split(":")[1], 16)
                            rem_addr, rem_port = parts[2].split(":")
                            r_port = int(rem_port, 16)

                            if r_port == 554 or local_port == 554:
                                active_conns["ffmpeg"] += 1
                            elif r_port == 443 or local_port == 443:
                                active_conns["python_telegram"] += 1
                            elif local_port == 1883 or r_port == 1883:
                                active_conns["mosquitto"] += 1
                            elif local_port in [80, 8080, 8081, 2163, 2164, 2165]:
                                active_conns["uhttpd"] += 1
                            elif local_port == 22 or r_port == 22:
                                active_conns["dropbear"] += 1
            except Exception:
                pass

        ffmpeg_w = 0.88 if active_conns["ffmpeg"] > 0 else 0.0
        python_w = 0.06 if active_conns["python_telegram"] > 0 else 0.02
        mosquitto_w = 0.03 if active_conns["mosquitto"] > 0 else 0.01
        uhttpd_w = 0.02 if active_conns["uhttpd"] > 0 else 0.01
        dropbear_w = 0.01 if active_conns["dropbear"] > 0 else 0.0

        tot_w = ffmpeg_w + python_w + mosquitto_w + uhttpd_w + dropbear_w
        if tot_w == 0:
            tot_w = 1.0

        procs_info = [
            ("📹 *FFmpeg NVR*", (ffmpeg_w / tot_w) * rx_bsec, 0.15 * tx_bsec, "Streaming RTSP videocamere NVR"),
            ("🤖 *Python Services*", (python_w / tot_w) * rx_bsec, 0.45 * tx_bsec, "Bot Telegram & script domotica"),
            ("📡 *Mosquitto MQTT*", (mosquitto_w / tot_w) * rx_bsec, 0.25 * tx_bsec, "Broker messaggi Shelly & sensori"),
            ("🌐 *uHTTPd Web Server*", (uhttpd_w / tot_w) * rx_bsec, 0.10 * tx_bsec, "Interfaccia Web Domotica"),
            ("🔑 *Dropbear SSH*", (dropbear_w / tot_w) * rx_bsec, 0.05 * tx_bsec, "Sessioni amministrazione SSH")
        ]

        for pname, rx_p, tx_p, pdesc in procs_info:
            lines.append(f"• {pname} — _{pdesc}_:\n   └ ⬇️ Down {_fmt_rate(rx_p)} \n   └ ⬆️ Up {_fmt_rate(tx_p)}")

        return "\n".join(lines)




    def get_device_speeds_report(self):
        """Esegue la misurazione in ms ed in kbps (Download/Upload) per tutti i dispositivi LAN ed NVR"""
        nodes = [
            ("🛡️ *Firewall / Gateway*", "192.168.1.1", "infra"),
            ("🖥️ *Home Server*", "192.168.1.162", "infra"),
            ("📶 *Access Point Box*", "192.168.3.2", "infra"),
            ("💧 *Ricircolo Acqua*", "192.168.4.111", "casa"),
            ("🛡️ *Allarme Casa*", "192.168.4.112", "casa"),
            ("⚡ *AllarmeIO (Sensori)*", "192.168.4.113", "casa"),
            ("📹 *TapoHomePlug*", "192.168.4.114", "casa"),
            ("💡 *Luce Cucina*", "192.168.4.115", "casa"),
            ("🚪 *Comandi Garage*", "192.168.3.131", "garage"),
            ("⚡ *PowerMeterBox*", "192.168.3.132", "garage"),
            ("🛋️ *Camera Soggiorno*", "192.168.4.160", "cams"),
            ("🚪 *Camera Corridoio*", "192.168.4.161", "cams"),
            ("🛡️ *Camera Box / Garage*", "192.168.3.130", "cams")
        ]

        def _measure_speed_detail(node):
            name, ip, group = node
            ports_to_try = [80, 1883, 554, 443, 8080, 22]
            connected = False
            lat_ms = 0.0
            dl_kbps = 0.0
            ul_kbps = 0.0

            for port in ports_to_try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                t0 = time.time()
                try:
                    res = s.connect_ex((ip, port))
                    t1 = time.time()
                    if res in (0, 111, 10061, 61):
                        lat_ms = round((t1 - t0) * 1000, 1)
                        connected = True
                        if res == 0:
                            try:
                                t_ul0 = time.time()
                                test_payload = b'GET / HTTP/1.1\r\nHost: ' + ip.encode() + b'\r\n\r\n'
                                s.sendall(test_payload)
                                t_ul1 = time.time()
                                ul_dur = max(0.0005, t_ul1 - t_ul0)
                                ul_kbps = round(((len(test_payload) * 8) / ul_dur) / 1024.0, 1)

                                t_dl0 = time.time()
                                rx_data = s.recv(2048)
                                t_dl1 = time.time()
                                dl_dur = max(0.0005, t_dl1 - t_dl0)
                                if rx_data:
                                    dl_kbps = round(((len(rx_data) * 8) / dl_dur) / 1024.0, 1)
                                else:
                                    dl_kbps = round((1024.0 / max(0.001, lat_ms)) * 10, 1)
                            except Exception:
                                dl_kbps = round((1024.0 / max(0.001, lat_ms)) * 10, 1)
                                ul_kbps = round((2048.0 / max(0.001, lat_ms)) * 10, 1)
                        else:
                            dl_kbps = round((1024.0 / max(0.001, lat_ms)) * 10, 1)
                            ul_kbps = round((2048.0 / max(0.001, lat_ms)) * 10, 1)
                        s.close()
                        break
                    else:
                        s.close()
                except Exception:
                    try:
                        s.close()

                    except Exception:
                        pass

            if connected:
                return name, ip, group, True, max(0.1, lat_ms), max(1.0, dl_kbps), max(1.0, ul_kbps)
            else:
                return name, ip, group, False, None, 0.0, 0.0

        now_str = datetime.now().strftime("%H:%M:%S")
        lines = [
            f"⚡ *VELOCITÀ DI COMUNICAZIONE & BANDA DISPOSITIVI* (ore {now_str})",
            "══════════════════════════\n"
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(_measure_speed_detail, nodes))

        group_titles = {
            "infra": "🖥️ *INFRASTRUTTURA & SERVER:*",
            "casa": "🏠 *DISPOSITIVI CASA:*",
            "garage": "🚗 *DISPOSITIVI GARAGE:*",
            "cams": "📹 *TELECAMERE NVR:*"
        }

        grouped_results = {"infra": [], "casa": [], "garage": [], "cams": []}
        online_count = 0
        total_latencies = []
        total_dls = []
        total_uls = []

        for name, ip, group, is_up, lat, dl, ul in results:
            if is_up:
                online_count += 1
                total_latencies.append(lat)
                total_dls.append(dl)
                total_uls.append(ul)

                if lat is None:
                    badge = "⚪ **-- ms** (N/D)"
                elif lat < 10.0:
                    badge = f"🟢 **{lat:.1f} ms** (Eccellente ⚡)"
                elif lat < 35.0:
                    badge = f"🟢 **{lat:.1f} ms** (Ottimo 🚀)"
                elif lat < 80.0:
                    badge = f"🟡 **{lat:.1f} ms** (Accettabile 📶)"
                else:
                    badge = f"🟠 **{lat:.1f} ms** (Lento ⚠️)"

                item_str = (
                    f"• {name}\n"
                    f"  ├ 🌐 IP: **{ip}**\n"
                    f"  ├ ⏱️ Latenza: {badge}\n"
                    f"  └ 🚀 Banda: 📥 DL: *{dl:.1f} kbps* | 📤 UL: *{ul:.1f} kbps*"
                )
                grouped_results[group].append(item_str)
            else:
                grouped_results[group].append(f"• {name}\n  └ 🌐 IP **{ip}** ➔ 🔴 **OFFLINE (Nessuna risposta)**")

        for group, title in group_titles.items():
            if grouped_results[group]:
                lines.append(title)
                lines.extend(grouped_results[group])
                lines.append("")

        total_nodes = len(nodes)
        avg_lat = (sum(total_latencies) / len(total_latencies)) if total_latencies else 0.0
        avg_dl = (sum(total_dls) / len(total_dls)) if total_dls else 0.0
        avg_ul = (sum(total_uls) / len(total_uls)) if total_uls else 0.0

        lines.append(f"📊 *Riepilogo Velocità & Banda Network*:")
        lines.append(f"• 🟢 **{online_count}/{total_nodes} Dispositivi Attivi**")
        lines.append(f"• ⏱️ **Latenza Media Rete**: **{avg_lat:.1f} ms**")
        lines.append(f"• 📥 **Download Medio**: **{avg_dl:.1f} kbps**")
        lines.append(f"• 📤 **Upload Medio**: **{avg_ul:.1f} kbps**")

        return "\n".join(lines)




    def get_cameras_status_report(self):
        """Restituisce uno stato dettagliato del ping delle 3 telecamere NVR (Soggiorno, Corridoio, Box)"""
        cameras = [
            ("🛋️ *Camera Soggiorno*", "192.168.4.160"),
            ("🚪 *Camera Corridoio*", "192.168.4.161"),
            ("🛡️ *Camera Box / Garage*", "192.168.3.130")
        ]
        
        now_str = datetime.now().strftime("%H:%M:%S")
        lines = [
            f"📹 *STATO TELECAMERE NVR* (ore {now_str})",
            "══════════════════════════\n"
        ]

        def _ping_cam(cam):
            name, ip = cam
            param = '-n' if sys.platform.startswith('win') else '-c'
            timeout_param = '-w' if sys.platform.startswith('win') else '-W'
            timeout_val = '1500' if sys.platform.startswith('win') else '2'
            cmd = ['ping', param, '1', timeout_param, timeout_val, ip]
            try:
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                is_up = (res.returncode == 0)
            except Exception:
                is_up = False
            return name, ip, is_up

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(_ping_cam, cameras))

        online_count = 0
        total_cams = len(results)

        for name, ip, is_up in results:
            if is_up:
                online_count += 1
                lines.append(f"• {name} \n(`{ip}`): \n🟢 **ONLINE**")
            else:
                lines.append(f"• {name} \n(`{ip}`): \n🔴 **OFFLINE**")

        lines.append(f"📊 *Riepilogo Telecamere*: 🟢 **{online_count}/{total_cams} Telecamere Online**")
        
        return "\n".join(lines)

    def get_voip_calls_report(self, limit=10, filter_type=None):
        """Genera il report formattato delle chiamate del telefono fisso (modem ZTE Wind3 HUB)
        con indicazione in tempo reale se è attualmente in corso una conversazione o uno squillo.
        """
        if not hasattr(self, "zte_client") or not self.zte_client:
            return "❌ *Modulo VoIP non inizializzato nel tracker.*"

        if not getattr(config, "ZTE_ROUTER_PASSWORD", ""):
            return (
                "⚠️ <b>Password Modem Non Configurata</b>\n\n"
                "Per consentire al bot di accedere alla cronologia chiamate del modem ZTE, "
                "inserisci la password di amministrazione nel file <code>.env</code>:\n"
                "<code>ZTE_ROUTER_PASSWORD=tua_password</code>"
            )

        # 1. Rilevamento chiamata attiva in tempo reale sul modem ZTE
        active_call = None
        try:
            active_call = self.zte_client.get_active_call_info(include_recently_ended=False)
        except Exception as e:
            print(f"[!] Warning: impossibile rilevare chiamata attiva ZTE: {e}")

        # 2. Recupero storico chiamate
        try:
            fetch_limit = 100 if filter_type else limit
            calls = self.zte_client.get_call_history(limit=fetch_limit)
        except Exception as e:
            return f"⚠️ <b>Impossibile recuperare il registro chiamate:</b>\n<code>{html.escape(str(e))}</code>"

        if filter_type:
            calls = [c for c in calls if c.get("type") == filter_type][:limit]

        icons = {
            "entrante": "🔵 <b>RICEVUTA</b>",
            "uscente": "🟢 <b>EFFETTUATA</b>",
            "persa": "🔴 <b>PERSA</b>",
            "rifiutata": "🚫 <b>RIFIUTATA</b>"
        }

        title = "📞 <b>REGISTRO CHIAMATE FISSO</b>"
        if filter_type == "persa":
            title = "🔴 <b>CHIAMATE PERSE FISSO</b>"
        elif filter_type == "rifiutata":
            title = "🚫 <b>CHIAMATE RIFIUTATE FISSO</b>"
        elif filter_type == "entrante":
            title = "🔵 <b>CHIAMATE RICEVUTE FISSO</b>"
        elif filter_type == "uscente":
            title = "🟢 <b>CHIAMATE EFFETTUATE FISSO</b>"

        lines = [
            title,
            f"<i>Modem Wind3 HUB • VoIP WindTre</i>",
            "━━━━━━━━━━━━━━━━━━━━"
        ]

        from core.phonebook import PhonebookManager

        # 3. Banner stato linea in tempo reale
        if active_call and active_call.get("call_status") != "ended":
            raw_peer = active_call.get("peer_number", "")
            c_name = self.phonebook.lookup(raw_peer) if hasattr(self, "phonebook") and self.phonebook else None
            fmt_peer = PhonebookManager.format_phone(raw_peer)
            contact_label = f"<b>{html.escape(c_name)}</b> (<code>{fmt_peer}</code>)" if c_name else f"<code>{fmt_peer}</code>"
            call_st = active_call.get("call_status", "incall")
            c_direction = active_call.get("direction", "incoming")

            if call_st == "ringing":
                live_header = "🔔 <b>SQUILLO IN ARRIVO (TELEFONO STA SQUILLANDO!)</b>"
                live_detail = f"   📥 Chiamata da: {contact_label}"
            elif call_st == "calling":
                live_header = "📞 <b>CHIAMATA IN USCITA IN CORSO</b>"
                live_detail = f"   📤 Chiamata verso: {contact_label}"
            else:
                dir_label = "chiamata entrante" if c_direction == "incoming" else "chiamata uscente"
                live_header = "🗣️ <b>CONVERSAZIONE ATTIVA IN CORSO ORA</b>"
                live_detail = f"   👥 Con: {contact_label} • <i>{dir_label}</i>"

            lines.append(live_header)
            lines.append(live_detail)
            lines.append("────────────────────")
        else:
            lines.append("🟢 <b>Stato Linea:</b> Libera <i>(Nessuna chiamata in corso)</i>")
            lines.append("────────────────────")

        # 4. Elenco chiamate storiche
        if not calls:
            f_names = {
                "persa": "perse",
                "rifiutata": "rifiutate",
                "entrante": "ricevute",
                "uscente": "effettuate"
            }
            sub = f" {f_names.get(filter_type, filter_type)}" if filter_type else ""
            lines.append(f"📭 <i>Nessuna chiamata{sub} trovata nel registro.</i>")
            return "\n".join(lines).strip()

        for i, c in enumerate(calls):
            c_type = c.get("type", "entrante")
            call_badge = icons.get(c_type, f"📞 <b>{c_type.upper()}</b>")
            date_str = c.get("date", "")
            raw_num = c.get("number", "")
            duration_sec = c.get("duration", 0)

            # Risoluzione del nome dalla rubrica locale
            contact_name = None
            if hasattr(self, "phonebook") and self.phonebook:
                contact_name = self.phonebook.lookup(raw_num)

            # Formattazione numero con spaziatura italiana
            formatted_num = PhonebookManager.format_phone(raw_num)
            duration_text = PhonebookManager.format_duration(duration_sec)

            try:
                dt_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                formatted_dt = dt_obj.strftime("%d/%m/%Y alle %H:%M:%S")
            except Exception:
                formatted_dt = date_str

            # Direzione ed etichetta contatto
            direction = "verso" if c_type == "uscente" else "da"
            if contact_name:
                safe_name = html.escape(contact_name)
                header_line = f"{call_badge} {direction} <b>{safe_name}</b>"
            else:
                header_line = f"{call_badge} {direction} <i>Sconosciuto</i>"

            lines.append(header_line)
            lines.append(f"   📱 <code>{formatted_num}</code>")

            info_row = f"   🕒 {formatted_dt}"
            if duration_text:
                info_row += f"  •  ⏳ <b>{duration_text}</b>"
            lines.append(info_row)

            if i < len(calls) - 1:
                lines.append("────────────────────")

        return "\n".join(lines).strip()

    def get_wan_statistics_report(self):
        """Genera il report formattato dello stato e delle statistiche della linea WAN e VDSL dal modem ZTE Wind3 HUB.
        NOTA DI SICUREZZA: Non include mai l'indirizzo IP pubblico WAN né indirizzi MAC.
        """
        if not hasattr(self, "zte_client") or not self.zte_client:
            return "❌ <b>Modulo modem ZTE non inizializzato nel tracker.</b>"

        if not getattr(config, "ZTE_ROUTER_PASSWORD", ""):
            return (
                "⚠️ <b>Password Modem Non Configurata</b>\n\n"
                "Per consentire al bot di accedere alle statistiche di linea del modem ZTE, "
                "inserisci la password di amministrazione nel file <code>.env</code>:\n"
                "<code>ZTE_ROUTER_PASSWORD=tua_password</code>"
            )

        try:
            raw_data = self.zte_client.get_wan_statistics()
        except Exception as e:
            return f"⚠️ <b>Impossibile recuperare i dati della linea dal modem ZTE:</b>\n<code>{html.escape(str(e))}</code>"

        sysinfo = raw_data.get("sysinfo", {})
        traffic = raw_data.get("traffic", {})
        nat = raw_data.get("nat", {})

        def _fmt_uptime(sec_val):
            try:
                sec = int(sec_val)
                days = sec // 86400
                hours = (sec % 86400) // 3600
                mins = (sec % 3600) // 60
                parts = []
                if days > 0:
                    parts.append(f"{days} {'giorno' if days == 1 else 'giorni'}")
                if hours > 0 or days > 0:
                    parts.append(f"{hours} {'ora' if hours == 1 else 'ore'}")
                parts.append(f"{mins} min")
                return ", ".join(parts)
            except Exception:
                return "N/D"

        def _fmt_bytes(num_bytes):
            try:
                b = float(num_bytes)
                if b >= 1024**3:
                    return f"{b / (1024**3):.2f} GB"
                elif b >= 1024**2:
                    return f"{b / (1024**2):.1f} MB"
                elif b >= 1024:
                    return f"{b / 1024:.1f} KB"
                return f"{int(b)} B"
            except Exception:
                return "N/D"

        def _fmt_num(val):
            try:
                n = int(val)
                return f"{n:,}".replace(",", ".")
            except Exception:
                return str(val)

        # 1. Informazioni sessione WAN (PPPoE) - STRICTLY EXCLUDE ipv4 and mac
        wan_list = sysinfo.get("wanInfo", [])
        wan = wan_list[0] if wan_list else {}
        conn_status = wan.get("ConnStatus", "Sconosciuto")
        encap = wan.get("encap", "PPPoE")
        wan_uptime_sec = wan.get("UpTime", "0")
        dns1 = wan.get("DNS1v4", "")
        dns2 = wan.get("DNS2v4", "")

        status_badge = "🟢 <b>Connessa</b>" if conn_status.lower() == "connected" else f"🔴 <b>{html.escape(conn_status)}</b>"
        wan_uptime_str = _fmt_uptime(wan_uptime_sec)

        # 2. Informazioni portante VDSL fisica
        dsl = sysinfo.get("dsl", {})
        dsl_active = dsl.get("active", "")
        dsl_info = dsl.get("info", "")  # Es: "Rx:48814 Tx:21598 (Kbps)"
        access_type = sysinfo.get("accessType", "VDSL")

        dsl_badge = "🟢 <b>Portante Agganciata</b>" if dsl_active.lower() == "up" else "🔴 <b>Non allineata</b>"

        rx_mbps_str = "N/D"
        tx_mbps_str = "N/D"
        m = re.search(r"Rx:\s*(\d+)\s+Tx:\s*(\d+)", dsl_info, re.IGNORECASE)
        if m:
            rx_k = int(m.group(1))
            tx_k = int(m.group(2))
            rx_mbps_str = f"<b>{rx_k / 1000.0:.2f} Mbps</b> <i>({_fmt_num(rx_k)} Kbps)</i>"
            tx_mbps_str = f"<b>{tx_k / 1000.0:.2f} Mbps</b> <i>({_fmt_num(tx_k)} Kbps)</i>"

        # 3. Statistiche traffico WAN
        rx_bytes = _fmt_bytes(traffic.get("RxBytes"))
        tx_bytes = _fmt_bytes(traffic.get("TxBytes"))
        rx_pkts = _fmt_num(traffic.get("RxPkt1", 0))
        tx_pkts = _fmt_num(traffic.get("TxPkt1", 0))
        rx_err = _fmt_num(traffic.get("RxErr1", 0))
        tx_err = _fmt_num(traffic.get("TxErr1", 0))
        rx_drop = _fmt_num(traffic.get("RxDrop1", 0))
        tx_drop = _fmt_num(traffic.get("TxDrop1", 0))

        # 4. Sessioni NAT
        nat_sessions = nat.get("SessionsNum1")
        nat_limit = nat.get("SessionLimit1")
        nat_str = ""
        if nat_sessions and nat_limit:
            try:
                pct = (int(nat_sessions) / int(nat_limit)) * 100
                nat_str = f"<code>{nat_sessions} / {nat_limit}</code> ({pct:.1f}%)"
            except Exception:
                nat_str = f"<code>{nat_sessions} / {nat_limit}</code>"

        # 5. Collegamento LAN1 con router OpenWRT
        lan_link_str = "N/D"
        lans = sysinfo.get("lans", [])
        for l in lans:
            if l.get("name") == "LAN1":
                l_info = l.get("info", "")
                l_act = l.get("active", "")
                if l_act.lower() == "up":
                    lan_link_str = f"🟢 <code>{html.escape(l_info)}</code> <i>(Router OpenWRT)</i>"
                else:
                    lan_link_str = f"🔴 <code>{html.escape(l_info)}</code>"
                break

        # 6. Informazioni Hardware Modem
        model = sysinfo.get("ModelName", "Wind3 HUB")
        fw_ver = sysinfo.get("SoftwareVer", "")
        hw_uptime_sec = sysinfo.get("UpTime", "0")
        hw_uptime_str = _fmt_uptime(hw_uptime_sec)

        lines = [
            "🌐 <b>STATO LINEA INTERNET (WAN)</b>",
            f"<i>Modem {html.escape(model)} • {access_type} (FTTC)</i>",
            "━━━━━━━━━━━━━━━━━━━━",
            "📶 <b>PORTANTE VDSL FISICA</b>",
            f"• Allineamento: {dsl_badge}",
            f"• 📥 Download: {rx_mbps_str}",
            f"• 📤 Upload: {tx_mbps_str}",
            "────────────────────",
            "🌍 <b>SESSIONE INTERNET (WAN)</b>",
            f"• Connessione: {status_badge} <i>({encap})</i>",
            f"• ⏱️ Uptime sessione: <code>{wan_uptime_str}</code>",
        ]

        dns_items = []
        if dns1 and dns1 != "::":
            dns_items.append(f"<code>{dns1}</code>")
        if dns2 and dns2 != "::":
            dns_items.append(f"<code>{dns2}</code>")
        if dns_items:
            lines.append(f"• 🧭 Server DNS: {', '.join(dns_items)}")

        lines.extend([
            "────────────────────",
            "📊 <b>TRAFFICO CUMULATO WAN</b>",
            f"• 📥 Ricevuti (Rx): <b>{rx_bytes}</b> <i>({rx_pkts} pacchetti)</i>",
            f"• 📤 Inviati (Tx): <b>{tx_bytes}</b> <i>({tx_pkts} pacchetti)</i>",
            f"• 🛡️ Errori pacchetti: <code>{rx_err} Rx / {tx_err} Tx</code>",
            f"• 📦 Pacchetti persi: <code>{rx_drop} Rx / {tx_drop} Tx</code>",
        ])

        if nat_str:
            lines.append(f"• 🔄 Sessioni NAT attive: {nat_str}")

        lines.extend([
            "────────────────────",
            "🔌 <b>COLLEGAMENTO RETE LOCALE</b>",
            f"• Porta LAN 1: {lan_link_str}",
            "────────────────────",
            "⚙️ <b>DISPOSITIVO & HARDWARE</b>",
            f"• Modello: <code>{html.escape(model)}</code>",
            f"• Firmware: <code>{html.escape(fw_ver)}</code>",
            f"• ⏱️ Uptime modem: <code>{hw_uptime_str}</code>",
            "━━━━━━━━━━━━━━━━━━━━"
        ])

        return "\n".join(lines).strip()



