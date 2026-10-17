import os
import re
import time
import socket
import threading

class ZTESyslogListener:
    """Listener UDP Syslog in ascolto sulla porta 514 per intercettare in tempo reale
    gli eventi VoIP del modem-router ZTE Wind3 HUB (ZXHN H389Q).
    Rileva le chiamate in entrata mentre il telefono sta squillando (prima della risposta o dell'aggancio).
    """

    def __init__(self, port=514, on_incoming_call=None, log_file="/tmp/zte_voip_syslog.log"):
        self.port = int(port)
        self.on_incoming_call = on_incoming_call
        self.log_file = log_file
        self.running = False
        self.thread = None
        self.sock = None
        self.last_notified_time = 0.0
        self.last_notified_num = ""

    def start(self):
        """Avvia il thread demone del listener UDP"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._listen_loop, daemon=True, name="ZTESyslogListenerThread")
        self.thread.start()

    def stop(self):
        """Ferma il listener"""
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def _prune_old_logs(self, max_age_hours=12, max_lines=150):
        """Pulisce il file di log rimuovendo le righe più vecchie di max_age_hours o eccedenti max_lines"""
        if not os.path.exists(self.log_file):
            return
        try:
            now = time.time()
            max_age_sec = max_age_hours * 3600
            retained_lines = []

            with open(self.log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            for line in lines:
                m = re.match(r"^\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]", line)
                if m:
                    try:
                        ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                        if (now - ts) <= max_age_sec:
                            retained_lines.append(line)
                        continue
                    except Exception:
                        pass
                # Se non ha data ma è nelle ultime righe, conservala
                retained_lines.append(line)

            # Limita al massimo consentito
            if len(retained_lines) > max_lines:
                retained_lines = retained_lines[-max_lines:]

            with open(self.log_file, "w", encoding="utf-8", errors="ignore") as f:
                f.writelines(retained_lines)
        except Exception:
            pass

    def _append_log(self, text):
        """Scrive l'evento sul file di log filtrando il rumore e mantenendo il file snello"""
        try:
            t_low = text.lower()
            # Ignora telemetria non VoIP (es. WWAN polling modem ogni 30s) che riempie il disco
            if "wwan_get" in t_low or "dwobjid" in t_low:
                return

            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{now_str}] {text}\n"
            with open(self.log_file, "a", encoding="utf-8", errors="ignore") as f:
                f.write(line)

            if not hasattr(self, "_write_count"):
                self._write_count = 0
            self._write_count += 1

            # Manutenzione periodica ogni 20 eventi o se il file supera i 30 KB
            if self._write_count % 20 == 0 or (os.path.exists(self.log_file) and os.path.getsize(self.log_file) > 30000):
                self._prune_old_logs(max_age_hours=12, max_lines=150)
        except Exception:
            pass

    def _extract_caller_number(self, raw_text):
        """Estrae il numero del chiamante dal messaggio SIP / Syslog ZTE"""
        # 1. Intestazione standard SIP From: From: "Name" <sip:3391234567@...
        m = re.search(r'[fF]rom:\s*(?:"?[^"<@\r\n]*"?\s*)?<sip:(\+?\d{4,16})@', raw_text)
        if m:
            return m.group(1)

        # 2. SIP URI diretto sip:123456@
        m = re.search(r'sip:(\+?\d{6,16})@', raw_text)
        if m:
            return m.group(1)

        # 3. Formati testuali tipici di log driver / Asterisk / voice: caller=..., from=...
        m = re.search(r'(?:caller|from|calling|da|num|number)[=:\s]+["\']?(\+?\d{6,16})["\']?', raw_text, re.IGNORECASE)
        if m:
            return m.group(1)

        # 4. Sequenza numerica telefonica nel payload
        m = re.search(r'\b(3\d{8,9}|0\d{6,10}|\+39\d{6,11})\b', raw_text)
        if m:
            return m.group(1)

        return "Sconosciuto"

    def _is_inbound_ring(self, raw_text):
        """Determina se il pacchetto Syslog corrisponde a una chiamata in entrata in squillo"""
        t = raw_text.lower()

        # 1. Evento nativo Broadcom / ZTE Wind3 HUB H389Q di allocazione canale voce / RTP per squillo in arrivo
        if "rtpmanageralloc" in t or "cspvoiprtpmanageralloc" in t:
            return True

        # Esclude pacchetti di routine SIP che non indicano uno squillo
        if any(ignore in t for ignore in ["register", "options", "subscribe", "notify", "publish", "rtpmanagerfree"]):
            return False

        # Verifica se è un messaggio VoIP / Voce
        is_voip = any(k in t for k in ["voip", "voice", "sip", "dsp", "fxs", "phone"])

        # 2. Metodo SIP INVITE (lo standard per chiamata entrante)
        if "invite" in t and ("sip:" in t or is_voip):
            if not any(k in t for k in ["bye", "cancel", "outbound", "chiamata uscente"]):
                return True

        # 3. Log espliciti di stato squillo / incoming
        if is_voip or "call" in t or "chiamata" in t:
            if any(k in t for k in ["incoming", "inbound", "ringing", "squillo", "ring start", "incoming call", "new call", "call from"]):
                if not any(k in t for k in ["stop ring", "ring stop", "hangup", "onhook", "outbound"]):
                    return True

        return False


    def _listen_loop(self):
        """Loop principale di ricezione pacchetti UDP"""
        print(f"[+] ZTESyslogListener in avvio su 0.0.0.0:{self.port}...")

        while self.running:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except Exception:
                    pass

                self.sock.bind(("0.0.0.0", self.port))
                self.sock.settimeout(2.0)
                print(f"[+] ZTESyslogListener in ascolto su UDP 0.0.0.0:{self.port} (pronto per squilli in real-time)")
                break
            except Exception as e:
                print(f"[!] Impossibile avviare socket Syslog su porta {self.port}: {e}. Riprovo tra 5s...")
                time.sleep(5)

        # Pulizia iniziale all'avvio del demone
        self._prune_old_logs(max_age_hours=12, max_lines=150)
        last_prune_time = time.time()

        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                raw_text = data.decode("utf-8", errors="ignore").strip()
                if not raw_text:
                    continue

                # Registra su log per monitoraggio e debug trasparente
                self._append_log(f"[{addr[0]}] {raw_text}")

                # Analisi chiamata entrante
                if self._is_inbound_ring(raw_text):
                    caller_num = self._extract_caller_number(raw_text)
                    now = time.time()

                    # Antirimbalzo / Cooldown: evita notifiche multiple durante i vari pacchetti della stessa chiamata
                    if (now - self.last_notified_time < 12):
                        continue

                    self.last_notified_time = now
                    self.last_notified_num = caller_num

                    print(f"[VoIP Real-Time] ⚡ Evento chiamata VoIP rilevato (da {addr[0]})")

                    if self.on_incoming_call:
                        try:
                            self.on_incoming_call(caller_num, raw_text)
                        except Exception as cb_err:
                            print(f"[!] Errore nel callback evento VoIP: {cb_err}")

            except socket.timeout:
                # Esegue la rotazione/potatura ogni 30 minuti per prevenire accumuli e overflow su /tmp
                if time.time() - last_prune_time > 1800:
                    self._prune_old_logs(max_age_hours=12, max_lines=150)
                    last_prune_time = time.time()
                continue
            except Exception as e:
                if self.running:
                    print(f"[!] Errore ricezione socket Syslog: {e}")
                    time.sleep(1)

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        print("[-] ZTESyslogListener terminato.")
