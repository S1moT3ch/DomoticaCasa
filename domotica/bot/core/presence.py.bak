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
    
    Verifica sia le associazioni Wi-Fi dirette (iwinfo) sia le voci ARP attive (0x2).
    """

    def __init__(self, router_ip=None, ssh_key=None, poll_interval=15, aliases_file=None):
        self.router_ip = router_ip or getattr(config, "OPENWRT_ROUTER_IP", "192.168.1.1")
        self.ssh_key = ssh_key or getattr(config, "OPENWRT_SSH_KEY", "/root/.ssh/id_openwrt")
        self.poll_interval = int(poll_interval)
        self.aliases_file = aliases_file or os.path.join(os.path.dirname(os.path.dirname(__file__)), "aliases.json")

        self.lock = threading.Lock()
        self.active_macs = set()
        self.last_update = 0
        self.last_query_success = False
        self.running = False
        self._thread = None
        self._cached_aliases = {}
        self._load_aliases()

    def _load_aliases(self):
        """Carica la mappatura alias -> MAC da aliases.json se presente"""
        if os.path.exists(self.aliases_file):
            try:
                with open(self.aliases_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._cached_aliases = {
                        str(k).strip().lower(): str(v).strip().lower()
                        for k, v in data.items()
                    }
            except Exception:
                pass

    def start(self):
        """Avvia il monitor di presenza in background"""
        if self._thread and self._thread.is_alive():
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="PresenceManagerThread")
        self._thread.start()
        print(f"[+] PresenceManager avviato (Router OpenWRT: {self.router_ip}, intervallo: {self.poll_interval}s)")

    def stop(self):
        self.running = False

    def _query_router(self):
        """Interroga OpenWRT via SSH ed estrae tutti i MAC address attivi (Wi-Fi + ARP)."""
        sh_cmd = (
            "cat /proc/net/arp | grep '0x2' | awk '{print $4}'; "
            "for i in phy0-ap0 phy0-ap1 phy1-ap0 phy1-ap1 phy1-ap2 phy1-ap3; do "
            "iwinfo $i assoclist 2>/dev/null | grep -E '^[0-9A-Fa-f]{2}:' | awk '{print $1}'; "
            "done"
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
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=4).decode("utf-8", errors="ignore")
            found = set()
            for line in out.splitlines():
                line = line.strip().lower()
                if len(line) == 17 and line.count(":") == 5:
                    found.add(line)
            return found, True
        except Exception as e:
            # In caso di errore temporaneo di connessione SSH al router
            return None, False

    def _loop(self):
        """Loop di aggiornamento periodico della cache dei MAC attivi"""
        while self.running:
            try:
                macs, ok = self._query_router()
                with self.lock:
                    if ok and macs is not None:
                        self.active_macs = macs
                        self.last_update = time.time()
                        self.last_query_success = True
                    else:
                        self.last_query_success = False
            except Exception as e:
                print(f"[!] Errore ciclo PresenceManager: {e}")
            time.sleep(self.poll_interval)

    def get_active_macs(self, max_age=30):
        """Restituisce il set dei MAC attivi. Se la cache è scaduta, esegue un refresh rapido."""
        now = time.time()
        with self.lock:
            if self.active_macs and (now - self.last_update <= max_age):
                return set(self.active_macs)

        # Cache scaduta o vuota: prova refresh sincrono
        macs, ok = self._query_router()
        with self.lock:
            if ok and macs is not None:
                self.active_macs = macs
                self.last_update = time.time()
                self.last_query_success = True
            return set(self.active_macs)

    def resolve_to_mac(self, identifier):
        """Risolve un identificatore (MAC, alias come 'Gamma' o 'iPhone') in un MAC normalizzato."""
        if not identifier:
            return None
        raw = str(identifier).strip().lower()
        # Se è un alias conosciuto
        if raw in self._cached_aliases:
            return self._cached_aliases[raw]
        # Se ha formato MAC
        if len(raw) == 17 and raw.count(":") == 5:
            return raw
        # Ricarica aliases al volo per eventuali novità
        self._load_aliases()
        return self._cached_aliases.get(raw, raw)

    def is_user_home(self, user_devices):
        """Verifica se almeno uno dei dispositivi associati all'utente è connesso alla rete di casa.
        
        Ritorna True se:
        - Nessun dispositivo è associato (fail-open, non blocca l'utente senza dispositivi configurati)
        - Almeno uno dei MAC o alias è trovato tra i dispositivi attivi
        - Il router OpenWRT non risponde (fail-safe per non perdere chiamate in caso di blocco di rete)
        """
        if not user_devices:
            return True

        with self.lock:
            # Se la query a OpenWRT non ha mai avuto successo, fail-open per sicurezza
            if not self.last_query_success and not self.active_macs:
                return True

        active = self.get_active_macs(max_age=25)
        
        # Se anche dopo il refresh non abbiamo MAC per un errore di rete del router, fail-open
        if not active:
            return True

        for dev in user_devices:
            mac = self.resolve_to_mac(dev)
            if mac and mac.lower() in active:
                return True

        return False
