import os
import re
import time
import json
import threading
import subprocess
import config


class PresenceManager:
    """Gestisce il rilevamento in tempo reale dei dispositivi connessi alla rete domestica
    interrogando il router principale OpenWRT (192.168.1.1) tramite SSH.
    
    Supporta il rilevamento ibrido (MAC address + Hostname DHCP + Associazioni Wi-Fi iwinfo + Tabella ARP)
    per gestire la rotazione dei MAC casuali (Android 14+ e iOS 18+) senza falsi positivi.
    """

    def __init__(self, router_ip=None, ssh_key=None, poll_interval=15, aliases_file=None):
        self.router_ip = router_ip or getattr(config, "OPENWRT_ROUTER_IP", "192.168.1.1")
        self.ssh_key = ssh_key or getattr(config, "OPENWRT_SSH_KEY", "/root/.ssh/id_openwrt")
        self.poll_interval = int(poll_interval)
        self.aliases_file = aliases_file or os.path.join(os.path.dirname(os.path.dirname(__file__)), "aliases.json")

        self.lock = threading.Lock()
        self.active_macs = set()
        self.wifi_macs = set()
        self.arp_macs = set()
        self.dhcp_leases = []
        self.last_update = 0
        self.last_query_success = False
        self.running = False
        self._thread = None

        self.aliases_config = {}
        self._mac_to_alias = {}
        self._hostname_to_alias = {}
        self._cached_aliases = {}
        self._load_aliases()

    def _load_aliases(self):
        """Carica e normalizza la mappatura alias da aliases.json."""
        if not os.path.exists(self.aliases_file):
            return

        try:
            with open(self.aliases_file, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            normalized = {}
            mac_map = {}
            host_map = {}
            legacy_map = {}

            for alias_name, conf in raw_data.items():
                alias_clean = str(alias_name).strip()
                macs = set()
                hostnames = set()

                if isinstance(conf, str):
                    val = conf.strip().lower()
                    if len(val) == 17 and val.count(":") == 5:
                        macs.add(val)
                    else:
                        hostnames.add(val)
                elif isinstance(conf, dict):
                    raw_mac = conf.get("mac") or conf.get("macs") or []
                    if isinstance(raw_mac, str):
                        raw_mac = [raw_mac]
                    for m in raw_mac:
                        m_clean = str(m).strip().lower()
                        if len(m_clean) == 17 and m_clean.count(":") == 5:
                            macs.add(m_clean)

                    raw_host = conf.get("hostname") or conf.get("hostnames") or []
                    if isinstance(raw_host, str):
                        raw_host = [raw_host]
                    for h in raw_host:
                        h_clean = str(h).strip().lower()
                        if h_clean:
                            hostnames.add(h_clean)
                elif isinstance(conf, list):
                    for item in conf:
                        i_clean = str(item).strip().lower()
                        if len(i_clean) == 17 and i_clean.count(":") == 5:
                            macs.add(i_clean)
                        elif i_clean:
                            hostnames.add(i_clean)

                normalized[alias_clean] = {
                    "macs": sorted(list(macs)),
                    "hostnames": sorted(list(hostnames))
                }

                for m in macs:
                    mac_map[m] = alias_clean
                for h in hostnames:
                    host_map[h] = alias_clean

                if macs:
                    legacy_map[alias_clean.lower()] = sorted(list(macs))[0]

            self.aliases_config = normalized
            self._mac_to_alias = mac_map
            self._hostname_to_alias = host_map
            self._cached_aliases = legacy_map
        except Exception as e:
            print(f"[!] Errore lettura aliases.json: {e}")

    def start(self):
        """Avvia il monitor di presenza in background."""
        if self._thread and self._thread.is_alive():
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="PresenceManagerThread")
        self._thread.start()
        print(f"[+] PresenceManager avviato (Router OpenWRT: {self.router_ip}, intervallo: {self.poll_interval}s)")

    def stop(self):
        self.running = False

    def _query_router(self):
        """Interroga OpenWRT via SSH ed estrae ARP, associazioni Wi-Fi e lease DHCP in una sola esecuzione."""
        sh_cmd = (
            "echo '===ARP==='; cat /proc/net/arp; "
            "echo '===WIFI==='; for i in phy0-ap0 phy0-ap1 phy1-ap0 phy1-ap1 phy1-ap2 phy1-ap3; do iwinfo \"$i\" assoclist 2>/dev/null; done; "
            "echo '===LEASES==='; cat /tmp/dhcp.leases 2>/dev/null || cat /var/dhcp.leases 2>/dev/null"
        )
        cmd = [
            "ssh",
            "-i", self.ssh_key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            f"root@{self.router_ip}",
            sh_cmd
        ]

        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5).decode("utf-8", errors="ignore")
            
            sections = out.split("===")
            arp_text = ""
            wifi_text = ""
            leases_text = ""

            for i in range(1, len(sections), 2):
                sec_name = sections[i].strip()
                sec_content = sections[i + 1] if i + 1 < len(sections) else ""
                if sec_name == "ARP":
                    arp_text = sec_content
                elif sec_name == "WIFI":
                    wifi_text = sec_content
                elif sec_name == "LEASES":
                    leases_text = sec_content

            wifi_set = set()
            arp_set = set()
            leases_list = []

            # 1. Parsing ARP (solo flag 0x2 = attivo e MAC non nullo)
            for line in arp_text.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    flags, mac = parts[2], parts[3].strip().lower()
                    if flags == "0x2" and mac != "00:00:00:00:00:00" and len(mac) == 17:
                        arp_set.add(mac)

            # 2. Parsing Wi-Fi assoclist
            mac_regex = re.compile(r"^([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
            for line in wifi_text.splitlines():
                m = mac_regex.match(line.strip())
                if m:
                    wifi_set.add(m.group(1).lower())

            # 3. Parsing Lease DHCP: timestamp mac ip hostname client_id
            for line in leases_text.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    ts, mac, ip, host = parts[0], parts[1].strip().lower(), parts[2], parts[3].strip()
                    if host != "*" and len(mac) == 17:
                        leases_list.append({
                            "timestamp": int(ts) if ts.isdigit() else 0,
                            "mac": mac,
                            "ip": ip,
                            "hostname": host
                        })

            # Ordina i lease dal più recente al più vecchio
            leases_list.sort(key=lambda x: x["timestamp"], reverse=True)

            active_combined = wifi_set | arp_set
            return wifi_set, arp_set, active_combined, leases_list, True
        except Exception:
            return None, None, None, None, False

    def _loop(self):
        """Loop di aggiornamento periodico della cache dei MAC attivi e lease DHCP."""
        while self.running:
            try:
                self._load_aliases()
                w_set, a_set, combined, leases, ok = self._query_router()
                with self.lock:
                    if ok and combined is not None:
                        self.wifi_macs = w_set
                        self.arp_macs = a_set
                        self.active_macs = combined
                        self.dhcp_leases = leases
                        self.last_update = time.time()
                        self.last_query_success = True
                    else:
                        self.last_query_success = False
            except Exception as e:
                print(f"[!] Errore ciclo PresenceManager: {e}")
            time.sleep(self.poll_interval)

    def refresh(self, max_age=15):
        """Aggiorna lo stato se la cache è più vecchia di max_age secondi."""
        now = time.time()
        with self.lock:
            if self.active_macs and (now - self.last_update <= max_age):
                return True

        w_set, a_set, combined, leases, ok = self._query_router()
        with self.lock:
            if ok and combined is not None:
                self.wifi_macs = w_set
                self.arp_macs = a_set
                self.active_macs = combined
                self.dhcp_leases = leases
                self.last_update = time.time()
                self.last_query_success = True
                return True
            else:
                self.last_query_success = False
                return False

    def get_active_macs(self, max_age=30):
        """Restituisce il set dei MAC attivi. Se la cache è scaduta, esegue un refresh rapido."""
        self.refresh(max_age=max_age)
        with self.lock:
            return set(self.active_macs)

    def is_device_online(self, identifier, max_age=15):
        """Verifica se un dispositivo (specificato per alias, hostname, MAC o dict)
        è attualmente connesso e attivo sulla rete (Wi-Fi o ARP).
        
        Ritorna: (is_online: bool, matched_mac: str or None, reason: str)
        """
        if not identifier:
            return False, None, "empty_identifier"

        self.refresh(max_age=max_age)

        with self.lock:
            active_macs = set(self.active_macs)
            wifi_macs = set(self.wifi_macs)
            leases = list(self.dhcp_leases)
            aliases_conf = dict(self.aliases_config)
            mac_to_alias = dict(self._mac_to_alias)
            host_to_alias = dict(self._hostname_to_alias)

        cand_macs = set()
        cand_hosts = set()

        raw_id = str(identifier).strip()
        raw_lower = raw_id.lower()

        # Verifica se corrisponde al nome di un alias
        matched_alias = None
        for a_name, conf in aliases_conf.items():
            if a_name.lower() == raw_lower:
                matched_alias = a_name
                cand_macs.update(conf["macs"])
                cand_hosts.update(conf["hostnames"])
                break

        if not matched_alias:
            # Se ha formato MAC
            if len(raw_lower) == 17 and raw_lower.count(":") == 5:
                cand_macs.add(raw_lower)
                if raw_lower in mac_to_alias:
                    a_name = mac_to_alias[raw_lower]
                    cand_hosts.update(aliases_conf.get(a_name, {}).get("hostnames", []))
            else:
                cand_hosts.add(raw_lower)
                if raw_lower in host_to_alias:
                    a_name = host_to_alias[raw_lower]
                    cand_macs.update(aliases_conf.get(a_name, {}).get("macs", []))

        # 1. Verifica corrispondenza diretta sui MAC candidati
        for m in cand_macs:
            if m in active_macs:
                ctype = "Wi-Fi" if m in wifi_macs else "ARP"
                return True, m, f"mac_active ({ctype})"

        # 2. Verifica corrispondenza Hostname via Lease DHCP attivi
        for h in cand_hosts:
            for l in leases:
                l_host = l["hostname"].lower()
                if l_host == h or (len(h) >= 4 and h in l_host):
                    l_mac = l["mac"]
                    if l_mac in active_macs:
                        ctype = "Wi-Fi" if l_mac in wifi_macs else "ARP"
                        return True, l_mac, f"dhcp_hostname:{l['hostname']} ({ctype})"

        return False, None, "offline"

    def is_user_home(self, user_devices, max_age=25):
        """Verifica se almeno uno dei dispositivi associati all'utente è connesso alla rete di casa.
        
        Ritorna True se:
        - Nessun dispositivo è associato (fail-open)
        - Almeno uno dei dispositivi è trovato attivo (via MAC o DHCP Hostname)
        - Il router OpenWRT non risponde (fail-safe)
        """
        if not user_devices:
            return True

        with self.lock:
            if not self.last_query_success and not self.active_macs:
                return True

        self.refresh(max_age=max_age)

        for dev in user_devices:
            online, _, _ = self.is_device_online(dev, max_age=max_age)
            if online:
                return True

        return False

    def resolve_to_mac(self, identifier):
        """Risolve un identificatore (MAC, alias come 'Gamma' o 'iPhone') nel MAC normalizzato più recente."""
        if not identifier:
            return None
        raw_lower = str(identifier).strip().lower()
        if len(raw_lower) == 17 and raw_lower.count(":") == 5:
            return raw_lower

        online, mac, _ = self.is_device_online(identifier)
        if mac:
            return mac

        with self.lock:
            aliases_conf = dict(self.aliases_config)
            leases = list(self.dhcp_leases)

        for a_name, conf in aliases_conf.items():
            if a_name.lower() == raw_lower:
                for h in conf["hostnames"]:
                    for l in leases:
                        if l["hostname"].lower() == h:
                            return l["mac"]
                if conf["macs"]:
                    return conf["macs"][0]

        for l in leases:
            if l["hostname"].lower() == raw_lower:
                return l["mac"]

        return self._cached_aliases.get(raw_lower, raw_lower)

    def get_all_aliases_status(self, max_age=15):
        """Restituisce lo stato online/offline di tutti gli alias configurati in aliases.json."""
        self._load_aliases()
        self.refresh(max_age=max_age)

        results = {}
        with self.lock:
            alias_names = list(self.aliases_config.keys())

        for name in alias_names:
            online, mac, reason = self.is_device_online(name, max_age=max_age)
            results[name] = {
                "online": online,
                "mac": mac,
                "reason": reason
            }
        return results
