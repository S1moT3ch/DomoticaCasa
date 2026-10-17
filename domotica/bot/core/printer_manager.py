# -*- coding: utf-8 -*-
import urllib.request
import re
import html
import sys

class PrinterManager:
    def __init__(self, ip="192.168.1.8", community="public"):
        self.ip = ip
        self.community = community
        self.is_printing_active = False
        self._watchdog_running = False

    def get_printer_status(self):
        """Interroga la stampante HP LaserJet Professional P1102w tramite il Web Server integrato (EWS).
        Fornisce livello toner %, modello cartuccia, pagine totali, pagine su cartuccia e stato di salute.
        """
        model = "HP LaserJet Professional P1102w"
        toner_pct = "N/D"
        cartridge_model = "CE285A"
        supply_state = "Normale"
        total_pages = "N/D"
        cartridge_pages = "N/D"
        is_online, status_str = self.check_engine_online()

        # 1. Recupero stato materiali di consumo (Toner) via EWS HTTP
        try:
            req_supp = urllib.request.Request(f"http://{self.ip}/SSI/supply_status.htm", headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            })
            with urllib.request.urlopen(req_supp, timeout=4) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
                
                # Percentuale toner: cerca nel badge o nello stile WIDTH:xx%
                m_pct = re.search(r'class="supplyName width10">\s*(\d+)%', content)
                if not m_pct:
                    m_pct = re.search(r'WIDTH:(\d+)%;.*?BACKGROUND-COLOR:\s*#000000', content, re.IGNORECASE)
                if m_pct:
                    toner_pct = f"{m_pct.group(1)}%"

                # Modello cartuccia (es. CE285A)
                m_cart = re.search(r'(?:l[\'’]ordine|Order Number).*?(?:&nbsp;|\s)+([A-Z0-9]+)', content, re.IGNORECASE)
                if m_cart:
                    cartridge_model = m_cart.group(1).strip()
                elif "CE285" in content:
                    cartridge_model = "CE285A"

                # Stato materiale (es. Normale)
                m_st = re.search(r'Livello materiale di consumo:\s*</td>\s*<td[^>]*>([^<]+)</td>', content, re.IGNORECASE)
                if m_st:
                    supply_state = m_st.group(1).strip()

                # Pagine stampate con questa cartuccia
                m_cp = re.search(r'Pag\.\s*stampate con questo mat\. consumo\*?:\s*</td>\s*<td[^>]*>(\d+)</td>', content, re.IGNORECASE)
                if m_cp:
                    cartridge_pages = m_cp.group(1).strip()

        except Exception as e:
            # Se la porta HTTP 80 non risponde affatto, la stampante è davvero spenta o offline
            return (
                "❌ *Errore di Connessione*\n"
                f"La stampante `{self.ip}` non risponde sulla rete locale.\n"
                "Verifica che sia accesa e collegata al Wi-Fi."
            )

        # 2. Recupero configurazione periferica (Pagine totali e modello esatto)
        try:
            req_conf = urllib.request.Request(f"http://{self.ip}/SSI/info_configuration.htm", headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            })
            with urllib.request.urlopen(req_conf, timeout=4) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
                
                # Modello stampante
                m_title = re.search(r'<title>(.*?)(?:&nbsp;|\s{2,})', content, re.IGNORECASE)
                if m_title and m_title.group(1).strip():
                    model = m_title.group(1).strip()

                # Totale pagine stampate nella vita della stampante
                m_tp = re.search(r'Totale pagine stampate:\s*</td>\s*<td[^>]*>(\d+)</td>', content, re.IGNORECASE)
                if m_tp:
                    total_pages = m_tp.group(1).strip()

        except Exception:
            pass

        def _fmt(val):
            try:
                return f"{int(val):,}".replace(",", ".")
            except Exception:
                return str(val)

        tp_str = _fmt(total_pages) if total_pages != "N/D" else "N/D"
        cp_str = _fmt(cartridge_pages) if cartridge_pages != "N/D" else "N/D"

        # Barra grafica visiva del toner (su 10 quadratini)
        bar = ""
        try:
            pct_num = int(toner_pct.replace("%", ""))
            filled = max(0, min(10, round(pct_num / 10)))
            bar = "🟩" * filled + "⬜" * (10 - filled)
        except Exception:
            bar = ""

        msg = (
            "🖨️ *STATO STAMPANTE HP*\n\n"
            f"⚡ *Stato:* {status_str}\n"
            f"🏷️ *Modello:* `{model}`\n"
            f"⬛ *Livello Toner:* *{toner_pct}* {bar}\n"
            f"📦 *Cartuccia:* `HP {cartridge_model}` _({supply_state})_\n"
            f"📄 *Pagine Totali Stampate:* *{tp_str}*\n"
            f"📑 *Pagine con questo Toner:* *{cp_str}*\n"
            f"🌐 *Indirizzo IP:* `{self.ip}`"
        )
        return msg

    def convert_via_vercel(self, doc_bytes=None, filename="document.pdf", options=None, file_url=None):
        """Invia il documento o l'URL Telegram a Vercel per la rasterizzazione in PCLm, con supporto opzioni avanzate."""
        import config, json
        vercel_url = getattr(config, "REMOTE_SERVER_URL", "https://powermonitor-one.vercel.app/api/heartbeat")
        base_url = vercel_url.split("/api/")[0]
        convert_url = f"{base_url}/api/print_convert"
        secret = getattr(config, "HEARTBEAT_SECRET", "SecretHeartbeatKey2026")

        opts = options or {}
        is_img = any(filename.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"])

        if file_url:
            # Invio ultra-leggero via JSON con file_url (< 1 KB): Vercel scarica direttamente da Telegram.
            # Questo azzera totalmente il limite 413 di upload di Vercel (4.5 MB), per qualsiasi dimensione del file!
            payload = json.dumps({
                "file_url": file_url,
                "filename": filename,
                "pages": str(opts.get("pages", "all")),
                "orientation": str(opts.get("orientation", "portrait")),
                "fit": str(opts.get("fit", "fit")),
                "pos": str(opts.get("pos", "center")),
            }).encode("utf-8")
            headers = {
                "X-Heartbeat-Secret": secret,
                "Content-Type": "application/json",
                "User-Agent": "HomeDomoticaBot/1.0"
            }
            req = urllib.request.Request(convert_url, data=payload, headers=headers)
        else:
            c_type = "image/jpeg" if is_img else "application/pdf"
            headers = {
                "X-Heartbeat-Secret": secret,
                "Content-Type": c_type,
                "User-Agent": "HomeDomoticaBot/1.0",
                "X-Print-Pages": str(opts.get("pages", "all")),
                "X-Print-Orientation": str(opts.get("orientation", "portrait")),
                "X-Print-Fit": str(opts.get("fit", "fit")),
                "X-Print-Pos": str(opts.get("pos", "center")),
            }
            req = urllib.request.Request(convert_url, data=doc_bytes, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                if resp.status == 200:
                    raw_data = resp.read()
                    c_type_resp = resp.headers.get("Content-Type", "").lower()
                    if "json" in c_type_resp or raw_data.strip().startswith(b"{"):
                        import base64
                        p_info = json.loads(raw_data.decode("utf-8"))
                        pages = p_info.get("pages", [])
                        return [base64.b64decode(p) for p in pages]
                    else:
                        return [raw_data]
                raise RuntimeError(f"Errore server Vercel HTTP {resp.status}")
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Errore Vercel ({e.code}): {err_msg}")
        except Exception as e:
            raise RuntimeError(f"Impossibile contattare Vercel: {e}")

    def get_remote_page_count(self, file_url=None, filename="document.pdf"):
        """Interroga Vercel per ottenere il numero totale esatto di pagine di un documento tramite PyMuPDF"""
        import config, json
        vercel_url = getattr(config, "REMOTE_SERVER_URL", "https://powermonitor-one.vercel.app/api/heartbeat")
        base_url = vercel_url.split("/api/")[0]
        convert_url = f"{base_url}/api/print_convert"
        secret = getattr(config, "HEARTBEAT_SECRET", "SecretHeartbeatKey2026")

        try:
            if file_url:
                payload = json.dumps({"action": "get_info", "file_url": file_url, "filename": filename}).encode("utf-8")
                headers = {"X-Heartbeat-Secret": secret, "Content-Type": "application/json", "User-Agent": "HomeDomoticaBot/1.0"}
                req = urllib.request.Request(convert_url, data=payload, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        return data.get("total_pages")
        except Exception:
            pass
        return None

    def get_print_preview_jpeg(self, doc_bytes=None, filename="document.pdf", options=None, file_url=None, preview_page=1):
        """Genera un'immagine JPEG ad alta fedeltà che mostra l'anteprima esatta del foglio stampato su A4/Letter"""
        import config, json
        vercel_url = getattr(config, "REMOTE_SERVER_URL", "https://powermonitor-one.vercel.app/api/heartbeat")
        base_url = vercel_url.split("/api/")[0]
        convert_url = f"{base_url}/api/print_convert"
        secret = getattr(config, "HEARTBEAT_SECRET", "SecretHeartbeatKey2026")

        opts = options or {}
        headers = {
            "X-Heartbeat-Secret": secret,
            "User-Agent": "HomeDomoticaBot/1.0",
            "X-Preview-Page": str(preview_page)
        }

        try:
            if file_url:
                payload = {
                    "action": "preview",
                    "file_url": file_url,
                    "filename": filename,
                    "preview_page": int(preview_page),
                    "pages": opts.get("pages", "all"),
                    "orientation": opts.get("orientation", "portrait"),
                    "fit": opts.get("fit", "fit"),
                    "pos": opts.get("pos", "center")
                }
                data = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"
            else:
                headers["Content-Type"] = "application/octet-stream"
                headers["x-action"] = "preview"
                headers["x-print-pages"] = str(opts.get("pages", "all"))
                headers["x-print-orientation"] = str(opts.get("orientation", "portrait"))
                headers["x-print-fit"] = str(opts.get("fit", "fit"))
                headers["x-print-pos"] = str(opts.get("pos", "center"))
                data = doc_bytes or b""

            req = urllib.request.Request(convert_url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as resp:
                if resp.status == 200:
                    return resp.read()
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Errore Vercel ({e.code}): {err_msg}")
        except Exception as e:
            raise RuntimeError(f"Impossibile generare anteprima: {e}")
        return None

    def get_pdf_page_count(self, doc_bytes):
        """Determina in modo affidabile il numero di pagine di un file PDF scansionandone la struttura interna"""
        if not doc_bytes or not doc_bytes.startswith(b"%PDF"):
            return 1
        try:
            # Metodo 1: conteggio /Type /Page
            page_objs = re.findall(rb'/Type\s*/Page\b', doc_bytes)
            if page_objs:
                return len(page_objs)
            # Metodo 2: tag /Count <N> nel catalogo Pages
            counts = re.findall(rb'/Count\s+(\d+)', doc_bytes)
            if counts:
                return max(int(c) for c in counts)
        except Exception:
            pass
        return 1

    def validate_pages_spec(self, pages_spec, total_pages=1):
        """Valida una specifica pagine verificando i limiti inferiori (>=1) e superiori (<= total_pages).
        Restituisce (is_valid: bool, clean_spec: str, error_message: str)
        """
        raw_t = str(pages_spec or "").strip().lower()
        if not raw_t:
            return False, "", "⚠️ Non hai specificato alcuna pagina."

        if raw_t in ["all", "tutte", "*"]:
            return True, "all", ""

        # Verifica formato caratteri ammessi
        if not re.match(r'^[0-9\s,\-]+$', raw_t):
            return False, "", (
                "⚠️ *Formato pagine non valido.*\n\n"
                "Puoi digitare ad esempio:\n"
                f"• `1` _(solo pagina 1)_\n"
                f"• `1-{total_pages}` _(intervallo di pagine)_\n"
                "• `all` o `tutte` _(tutte le pagine)_"
            )

        clean_spec = re.sub(r'\s+', '', raw_t)
        tokens = [t for t in clean_spec.split(",") if t]
        if not tokens:
            return False, "", "⚠️ Specifica delle pagine non valida."

        resolved_pages = []
        for token in tokens:
            if "-" in token:
                parts = token.split("-")
                if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                    return False, "", f"⚠️ Intervallo `{token}` non valido. Usa il formato `inizio-fine` (es. `1-3`)."
                start_p = int(parts[0])
                end_p = int(parts[1])

                if start_p < 1:
                    return False, "", f"❌ *Valore inferiore a pagina 1:* `{start_p}` non è valido (la numerazione parte da pagina 1)."
                if end_p < 1:
                    return False, "", f"❌ *Valore inferiore a pagina 1:* `{end_p}` non è valido."
                if start_p > end_p:
                    return False, "", f"⚠️ Intervallo non valido: `inizio ({start_p})` non può essere superiore a `fine ({end_p})`."
                if total_pages and end_p > total_pages:
                    return False, "", (
                        f"❌ *Pagina oltre il limite:* `{end_p}` supera il totale delle pagine del documento!\n\n"
                        f"📁 Questo documento contiene **{total_pages}** {'pagina' if total_pages == 1 else 'pagine'}.\n"
                        f"👉 Il valore massimo consentito è `{total_pages}`."
                    )
                for p in range(start_p, end_p + 1):
                    if p not in resolved_pages:
                        resolved_pages.append(p)

            elif token.isdigit():
                p = int(token)
                if p < 1:
                    return False, "", f"❌ *Valore inferiore a pagina 1:* `{p}` non è valido (la numerazione parte da pagina 1)."
                if total_pages and p > total_pages:
                    return False, "", (
                        f"❌ *Pagina oltre il limite:* La pagina `{p}` non esiste!\n\n"
                        f"📁 Questo documento contiene **{total_pages}** {'pagina' if total_pages == 1 else 'pagine'}.\n"
                        f"👉 Puoi indicare valori da `1` a `{total_pages}`."
                    )
                if p not in resolved_pages:
                    resolved_pages.append(p)
            else:
                return False, "", f"⚠️ Valore `{token}` non riconosciuto."

        if not resolved_pages:
            return False, "", "⚠️ Nessuna pagina valida selezionata."

        return True, clean_spec, ""

    def resolve_pages_list(self, pages_spec, total_pages):
        """Risolve una specifica di pagine (es. 'all', '1', '1-3', '1,3,5') in una lista ordinata di indici 1-based"""
        if not pages_spec or str(pages_spec).lower() in ["all", "tutte", "*"]:
            return list(range(1, total_pages + 1))

        resolved = []
        spec_str = str(pages_spec).lower().replace(" ", "")
        for part in spec_str.split(","):
            if "-" in part:
                bounds = part.split("-")
                if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
                    s_p = int(bounds[0])
                    e_p = int(bounds[1])
                    for p in range(s_p, e_p + 1):
                        if 1 <= p <= total_pages and p not in resolved:
                            resolved.append(p)
            elif part.isdigit():
                p = int(part)
                if 1 <= p <= total_pages and p not in resolved:
                    resolved.append(p)
        return resolved or list(range(1, total_pages + 1))

    def check_engine_online(self):
        """Verifica lo stato hardware reale del motore di stampa (PJL su porta 9100 / EWS su porta 80)."""
        import socket, time
        # 1. Interrogazione PJL su porta 9100 (risposta hardware in pochi millisecondi)
        try:
            s = socket.socket()
            s.settimeout(1.5)
            s.connect((self.ip, 9100))
            s.sendall(b'\x1b%-12345X@PJL\r\n@PJL INFO STATUS\r\n\x1b%-12345X')
            time.sleep(0.2)
            resp = s.recv(512).decode("latin1", "ignore")
            s.close()
            if "ONLINE=FALSE" in resp:
                if "30017" in resp:
                    return False, "Fuori linea 🔴 (Errore memoria/complessità: premere tasto Riprendi)"
                return False, "Fuori linea 🔴 (Carta esaurita, inceppamento o sportello aperto)"
            if "ONLINE=TRUE" in resp:
                return True, "Pronta 🟢"
        except Exception:
            pass

        # 2. Fallback su Embedded Web Server (/SSI/index.htm)
        try:
            req = urllib.request.Request(f"http://{self.ip}/SSI/index.htm", headers={
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, timeout=2) as resp:
                html_txt = resp.read().decode("utf-8", errors="ignore")
                if "Fuori linea" in html_txt:
                    return False, "Fuori linea 🔴 (Verificare carta/sportello/tasto)"
                elif "In linea" in html_txt or "Pronta" in html_txt:
                    return True, "Pronta 🟢"
        except Exception:
            pass

        return True, "Pronta 🟢"

    def clear_stuck_jobs(self):
        """Cancella eventuali lavori bloccati nel buffer di memoria della stampante"""
        import struct
        def make_attr(tag, name, val):
            n = name.encode('utf-8')
            v = val.encode('utf-8') if isinstance(val, str) else val
            return struct.pack('>B H', tag, len(n)) + n + struct.pack('>H', len(v)) + v

        for jid in range(650, 750):
            req = bytearray()
            req += struct.pack('>BB H I', 2, 0, 0x0008, 1) # Cancel-Job
            req += b'\x01'
            req += make_attr(0x47, 'attributes-charset', 'utf-8')
            req += make_attr(0x48, 'attributes-natural-language', 'it-it')
            req += make_attr(0x45, 'printer-uri', f'ipp://{self.ip}:631/printers/Laserjet')
            req += make_attr(0x45, 'job-uri', f'ipp://{self.ip}:631/printers/Laserjet/job-{jid:04d}')
            req += make_attr(0x42, 'requesting-user-name', 'telegram-bot')
            req += b'\x03'
            try:
                with urllib.request.urlopen(urllib.request.Request(f'http://{self.ip}:631/printers/Laserjet', data=bytes(req), headers={'Content-Type': 'application/ipp'}), timeout=1) as r:
                    pass
            except Exception:
                pass

    def print_document_via_ipp(self, pclm_bytes, job_name="Telegram Print", is_image=False, skip_check=False):
        """Invia lo stream PCLm alla stampante HP tramite protocollo IPP 2.0 nativo sulla porta 631."""
        import struct

        # Verifica preventiva dello stato fisico della stampante (solo sul primo foglio per non bloccare la pipeline)
        if not skip_check:
            is_online, engine_desc = self.check_engine_online()
            if not is_online:
                return False, (
                f"⚠️ *Impossibile stampare:* la stampante è attualmente *{engine_desc}*!\n\n"
                "👉 *Azioni richieste:*\n"
                "• Controlla che ci sia carta nel vassoio di alimentazione.\n"
                "• Assicurati che lo sportello del toner sia ben chiuso.\n"
                "• Se la spia arancione 'Attenzione' lampeggia o è fissa, premi il pulsante fisico **Riprendi/Go** (o spegni e riaccendi la stampante)."
            )

        # Risoluzione: 600 DPI nativa hardware per la LaserJet P1102w
        dpi = 600

        def make_attr(tag, name, val):
            n = name.encode('utf-8')
            v = val.encode('utf-8') if isinstance(val, str) else val
            return struct.pack('>B H', tag, len(n)) + n + struct.pack('>H', len(v)) + v

        # Costruisce la richiesta IPP 2.0 Print-Job (0x0002) identica a Windows 11
        req = bytearray()
        req += struct.pack('>BB H I', 2, 0, 0x0002, 1) # version 2.0, Print-Job, req_id=1

        # Operation Attributes (tag 0x01)
        req += b'\x01'
        req += make_attr(0x47, "attributes-charset", "utf-8")
        req += make_attr(0x48, "attributes-natural-language", "it-it")
        req += make_attr(0x45, "printer-uri", f"ipp://{self.ip}:631/printers/Laserjet")
        clean_name = re.sub(r'[^a-zA-Z0-9_\-\. ]', '_', job_name)[:30] or "Telegram Document"
        req += make_attr(0x42, "job-name", clean_name)
        req += make_attr(0x42, "requesting-user-name", "telegram-bot")
        req += make_attr(0x49, "document-format", "application/PCLm")

        # Job Attributes (tag 0x02) - Essenziali per l'engine laser HP!
        req += b'\x02'
        req += make_attr(0x44, "media", "na_letter_8.5x11in")
        req += make_attr(0x44, "output-bin", "face-down")
        req += make_attr(0x44, "print-color-mode", "monochrome")
        req += make_attr(0x23, "print-quality", struct.pack('>I', 4))
        req += make_attr(0x44, "sides", "one-sided")
        # Risoluzione: deve corrispondere al DPI del contenuto PCLm
        req += make_attr(0x32, "printer-resolution", struct.pack('>I I B', dpi, dpi, 3))

        # End of Attributes (tag 0x03)
        req += b'\x03'

        payload = bytes(req) + pclm_bytes
        http_req = urllib.request.Request(f"http://{self.ip}:631/printers/Laserjet", data=payload, headers={
            "Content-Type": "application/ipp",
            "User-Agent": "wPrintWindowsDoc"
        })
        try:
            with urllib.request.urlopen(http_req, timeout=35) as resp:
                res_bytes = resp.read()
                status_code = struct.unpack('>H', res_bytes[2:4])[0]
                if status_code == 0:
                    return True, "Stampa inviata con successo alla stampante! 🖨️📄"
                elif status_code == 0x0504: # spool-area-full
                    self.clear_stuck_jobs()
                    return False, "Buffer memoria stampante saturo. Coda pulita automaticamente, riprova a inviare il documento."
                return False, f"La stampante ha restituito errore IPP: 0x{status_code:04x}"
        except Exception as e:
            return False, f"Errore di connessione verso la stampante ({self.ip}:631): {e}"

    def get_pjl_status(self):
        """Interroga la stampante per lo stato PJL in tempo reale (CODE, DISPLAY, ONLINE)."""
        import socket
        try:
            s = socket.socket()
            s.settimeout(2.0)
            s.connect((self.ip, 9100))
            s.sendall(b'\x1b%-12345X@PJL\r\n@PJL INFO STATUS\r\n\x1b%-12345X')
            resp = s.recv(1024).decode('latin1', 'ignore')
            s.close()
            code_m = re.search(r'CODE=(\d+)', resp)
            online_m = re.search(r'ONLINE=(TRUE|FALSE)', resp, re.IGNORECASE)
            display_m = re.search(r'DISPLAY="([^"]*)"', resp)
            return {
                "code": int(code_m.group(1)) if code_m else 0,
                "online": online_m.group(1).upper() == "TRUE" if online_m else True,
                "display": display_m.group(1) if display_m else "",
                "raw": resp.strip()
            }
        except Exception as e:
            return {"code": -1, "online": False, "display": str(e), "raw": ""}

    def get_pjl_error_log(self):
        """Legge il registro errori hardware interno della stampante HP tramite PJL INFO LOG."""
        import socket
        try:
            s = socket.socket()
            s.settimeout(3.0)
            s.connect((self.ip, 9100))
            s.sendall(b'\x1b%-12345X@PJL\r\n@PJL INFO LOG\r\n\x1b%-12345X')
            raw = b''
            while True:
                try:
                    chunk = s.recv(1024)
                    if not chunk:
                        break
                    raw += chunk
                except Exception:
                    break
            s.close()
            txt = raw.decode('latin1', 'ignore')
            codes = dict(re.findall(r'ErrCode(\d+)\s*=\s*([0-9.]+)', txt))
            pages = dict(re.findall(r'PageCount(\d+)\s*=\s*(\d+)', txt))

            known_descriptions = {
                "21.0000": "Print Overrun / Pagina troppo complessa (buffer memoria raster esaurito: foglio espulso bianco e LED rosso lampeggiante)",
                "13.0000": "Inceppamento carta nel percorso rulli di trascinamento",
                "10.3000": "Errore memoria chip cartuccia toner (o toner non originale/esaurito)",
                "11.0000": "Vassoio carta vuoto",
                "41.0000": "Errore temporaneo del motore di stampa o discrepanza formato carta",
                "50.0000": "Errore gruppo fusore (temperatura)"
            }

            entries = []
            for k in sorted(codes.keys(), key=int):
                code = codes[k]
                p_cnt = pages.get(k, "N/D")
                desc = known_descriptions.get(code, f"Errore hardware HP codice {code}")
                entries.append({
                    "index": int(k),
                    "code": code,
                    "page_count": p_cnt,
                    "description": desc
                })
            return entries
        except Exception:
            return []

    def get_total_page_count(self):
        """Recupera il contatore delle pagine totali stampate dalla stampante (EWS)"""
        try:
            req = urllib.request.Request(f"http://{self.ip}/SSI/info_configuration.htm", headers={
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
                m = re.search(r'Totale pagine stampate:\s*</td>\s*<td[^>]*>(\d+)</td>', content, re.IGNORECASE)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
        return None

    def diagnose_printer_issue(self, failure_reason=None):
        """Diagnostica dettagliata e accurata dello stato di errore attuale della stampante HP."""
        st = self.get_pjl_status()
        code = st.get("code", 0)
        disp = str(st.get("display", "")).upper()
        is_online = st.get("online", True)

        total_pages = self.get_total_page_count()

        # Legge il log solo per verificare se c'è un errore recente (della pagina attuale o precedente)
        errors = self.get_pjl_error_log()
        recent_err = None
        if errors and total_pages is not None:
            first = errors[0]
            try:
                err_pg = int(first.get("page_count", 0))
                # Considera l'errore del log valido solo se risale alle ultime 2 pagine
                if abs(total_pages - err_pg) <= 2:
                    recent_err = first
            except Exception:
                pass

        err_code = recent_err.get("code", "") if recent_err else ""
        err_desc = recent_err.get("description", "") if recent_err else ""
        page_c = str(total_pages) if total_pages is not None else (recent_err.get("page_count", "N/D") if recent_err else "N/D")

        fail_lower = (failure_reason or "").lower()

        # Priorità di classificazione:
        # A. Errori espliciti di rete o buffer IPP
        if any(w in fail_lower for w in ["connessione", "timeout", "timed out", "refused", "unreachable"]):
            title = "🔌 Errore di Connessione alla Stampante"
            detail = failure_reason or f"Impossibile comunicare con la stampante via rete ({self.ip})."
            action = "1. Verifica che la stampante sia accesa e con la spia Wi-Fi blu accesa.\n2. Controlla il collegamento alla rete locale."
            diag_code = "NET_TIMEOUT"

        elif any(w in fail_lower for w in ["saturo", "spool"]):
            title = "⏳ Buffer Memoria Stampante Saturo"
            detail = "La coda di ricezione interna della stampante è momentaneamente piena."
            action = "1. Attendi 10-15 secondi che la stampante elabori i dati in coda.\n2. Premi Riprendi Stampa."
            diag_code = "IPP_SPOOL_FULL"

        # B. Rilevamento Real-time PJL STATUS e display hardware
        elif any(w in disp for w in ["DOOR", "SPORTELLO", "COVER", "COPERCHIO"]) or code in [40021, 40022]:
            title = "🚪 Sportello Stampante Aperto"
            detail = "Lo sportello superiore del vano cartuccia toner risulta aperto."
            action = "1. Chiudi saldamente lo sportello della stampante.\n2. La spia arancione si spegnerà."
            diag_code = "40.0022"

        elif (
            "13.0000" in str(err_code) or
            code in [11018, 30016, 40010] or
            any(w in disp for w in ["JAM", "INCEPP", "BLOCC"])
        ):
            title = "⚠️ Inceppamento Carta nei Rulli"
            detail = "Un foglio si è inceppato lungo il percorso dei rulli di trascinamento o nel gruppo fusore."
            action = "1. Solleva lo sportello di accesso alla cartuccia toner ed estrai delicatamente il foglio bloccato.\n2. Richiudi lo sportello e premi il pulsante **Riprendi/Go**."
            diag_code = "13.0000"

        elif (
            "11.0000" in str(err_code) or
            code in [11000, 10002, 10003, 10004, 11002, 11003, 11004, 11013, 11014] or
            any(w in disp for w in ["OUT OF PAPER", "PAPER OUT", "LOAD PAPER", "NO PAPER", "TRAY EMPTY", "CARTA ESAURITA", "CARICARE CARTA", "VASSOIO VUOTO", "PAPER", "CARTA", "LOAD", "TRAY", "VASSOIO", "ALIMENTA"])
        ):
            title = "📭 Carta Esaurita nel Vassoio"
            detail = "Il vassoio di alimentazione carta è vuoto o il foglio non è stato prelevato correttamente."
            action = "1. Inserisci i fogli di carta nel vassoio di alimentazione.\n2. Premi il pulsante **Riprendi/Go** sulla stampante (la spia arancione si spegnerà)."
            diag_code = "11.0000"

        elif "21.0000" in str(err_code) or code == 30017 or any(w in disp for w in ["OVERRUN", "MEMORIA", "MEMORY"]):
            title = "🔴 Buffer Memoria Esaurito (Print Overrun)"
            detail = "La stampante ha esaurito la memoria prima di completare il raster della pagina (foglio espulso bianco)."
            action = "1. Premi il pulsante fisico **Riprendi/Go** sulla stampante per resettare l'errore."
            diag_code = "21.0000"

        elif "10.3000" in str(err_code) or any(w in disp for w in ["TONER", "CARTUCCIA", "CARTRIDGE", "SUPPLY"]):
            title = "🖨️ Problema Cartuccia Toner"
            detail = "Cartuccia toner esaurita, assente o chip non riconosciuto."
            action = "1. Verifica che la cartuccia CE285A sia inserita correttamente."
            diag_code = "10.3000"

        elif not is_online or code == -1:
            title = "🔴 Stampante Fuori Linea (Offline)"
            detail = failure_reason or err_desc or "La stampante non risponde al motore di stampa o è in pausa."
            action = "1. Controlla che la stampante sia accesa e con la spia verde pronta.\n2. Premi il pulsante **Riprendi/Go**."
            diag_code = "OFFLINE"

        elif failure_reason:
            title = "⚠️ Errore durante l'Invio alla Stampante"
            detail = failure_reason
            action = "1. Controlla lo stato della stampante.\n2. Premi Riprendi per riprovare."
            diag_code = str(code) if code else "ERR_IPP"

        else:
            title = "⚠️ Errore Hardware Stampante"
            detail = err_desc or f"Stato stampante non pronto (Codice PJL: {code}, Display: '{st.get('display', '')}')"
            action = "Verifica lo stato della stampante e premi il pulsante Riprendi/Go."
            diag_code = str(code) if code else "HW_ERR"

        return {
            "title": title,
            "detail": detail,
            "action": action,
            "code": diag_code,
            "page_count": page_c,
            "pjl_code": code,
            "display": st.get("display", "")
        }

    def monitor_print_progress(self, max_wait=20, event_logger=None):
        """Monitora l'esecuzione fisica della stampa sul motore hardware HP P1102w.
        Funziona come pipeline reattiva ad alte prestazioni:
        - Rileva l'avvio della fase di stampa (CODE=10023).
        - Non appena il foglio viene espulso e la stampante torna pronta (CODE=10001),
          ritorna immediatamente liberando la pipeline per la pagina successiva.
        - Applica un filtro antirumore (debounce di errori consecutivi) per evitare falsi allarmi
          dovuti a brevi timeout di socket mentre il microcontrollore HP rasterizza la pagina.
        """
        import time
        start_t = time.time()
        saw_printing = False
        consecutive_errors = 0

        while time.time() - start_t < max_wait:
            st = self.get_pjl_status()
            code = st.get("code", 0)
            disp = str(st.get("display", "")).upper()
            is_online = st.get("online", True)

            # Verifica codici hardware critici espliciti (30017 overrun, carta esaurita, inceppamento, sportello)
            is_hw_error = (
                code == 30017 or
                code in [11000, 10002, 10003, 10004, 11002, 11003, 11004, 11013, 11018, 30016, 40010, 40021, 40022] or
                any(w in disp for w in ["PAPER", "CARTA", "JAM", "INCEPP", "DOOR", "SPORTELLO", "COVER"])
            )

            # Se la stampante è offline o non risponde al socket (code == -1), controlliamo se persiste
            is_offline_or_timeout = (not is_online or code == -1) and not is_hw_error

            if is_hw_error:
                consecutive_errors += 1
                if consecutive_errors >= 2: # Confermato su 2 letture successive (~0.8s)
                    diag = self.diagnose_printer_issue()
                    if event_logger and hasattr(event_logger, "log_event"):
                        try:
                            event_logger.log_event(
                                "printer",
                                "HP LaserJet P1102w",
                                f"Errore hardware stampa: {diag['title']} ({diag['code']}) - Pagina {diag['page_count']}"
                            )
                        except Exception:
                            pass
                    return False, diag
            elif is_offline_or_timeout:
                # Solo se dura più di 4 cicli consecutivi (~2s) e non avevamo già visto la stampa concludersi
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    diag = self.diagnose_printer_issue(failure_reason="Stampante non raggiungibile o fuori linea")
                    return False, diag
            else:
                consecutive_errors = 0

            # 10023 = Printing / In elaborazione sul motore laser
            if code == 10023:
                saw_printing = True

            # Se ha iniziato l'elaborazione fisica ed è tornata pronta (10001 = Ready),
            # significa che il foglio è uscito e il buffer raster è 100% libero!
            if saw_printing and code == 10001:
                elapsed = round(time.time() - start_t, 1)
                return True, {"code": "OK", "duration": elapsed}

            time.sleep(0.4)

        elapsed = round(time.time() - start_t, 1)
        return True, {"code": "OK", "duration": elapsed}

    def format_error_log_message(self):
        """Formatta il registro degli errori recenti della stampante per la visualizzazione su Telegram."""
        errors = self.get_pjl_error_log()
        if not errors:
            return "📋 *Registro Errori Stampante HP*\n\nNessun errore registrato o stampante non raggiungibile."

        lines = ["📋 *REGISTRO ERRORI HARDWARE HP P1102w*\n"]
        for e in errors[:6]:
            icon = "🔴" if e["code"] == "21.0000" else ("⚠️" if e["code"] == "13.0000" else "🔸")
            lines.append(
                f"{icon} *Codice:* `{e['code']}` _(Pag. {e['page_count']})_\n"
                f"   └ {e['description']}\n"
            )
        lines.append("💡 *Nota:* Il codice `21.0000` corrisponde a *Print Overrun* (foglio bianco espulso + LED rosso lampeggiante). Premi il tasto **Riprendi** sulla stampante per resettarlo.")
        return "\n".join(lines)

    def start_printer_watchdog(self, notify_callback=None, interval=12, event_logger=None):
        """Avvia il monitoraggio proattivo in background della stampante HP per rilevare errori
        generati da qualsiasi fonte (PC Windows, smartphone, stampe di rete o bot)."""
        if getattr(self, "_watchdog_running", False):
            return
        self._watchdog_running = True

        def _loop():
            import time
            print("[PRINTER WATCHDOG] Avviato monitoraggio proattivo errori stampante HP...")
            consecutive_errors = 0
            current_alert_active = False

            while True:
                try:
                    time.sleep(interval)

                    # Se il bot sta eseguendo una stampa attiva, la gestione errori è già demandata al flusso del job
                    if getattr(self, "is_printing_active", False):
                        continue

                    st = self.get_pjl_status()
                    code = st.get("code", 0)
                    disp = str(st.get("display", "")).upper()
                    is_online = st.get("online", True)

                    # Rilevamento codici hardware di errore (carta esaurita, inceppamento, sportello aperto, overrun)
                    is_hw_error = (
                        code == 30017 or
                        code in [11000, 10002, 10003, 10004, 11002, 11003, 11004, 11013, 11018, 30016, 40010, 40021, 40022] or
                        any(w in disp for w in ["PAPER", "CARTA", "JAM", "INCEPP", "DOOR", "SPORTELLO", "COVER", "OVERRUN", "MEMORIA", "TONER"])
                    )

                    if is_hw_error:
                        consecutive_errors += 1
                        # Richiede 2 rilevamenti consecutivi (~24s) per filtrare oscillazioni transitorie
                        if consecutive_errors >= 2 and not current_alert_active:
                            diag = self.diagnose_printer_issue()
                            err_type = diag.get("code", "HW_ERR")
                            current_alert_active = True

                            title = diag.get("title", "⚠️ Errore Hardware Stampante")
                            desc = diag.get("detail", "La stampante ha interrotto il lavoro.")
                            action = diag.get("action", "Verifica la stampante e premi Riprendi/Go.")
                            pg_info = f" • *Pagina:* `{diag.get('page_count')}`\n" if diag.get("page_count") != "N/D" else "\n"

                            msg = (
                                f"🚨 *ALLARME STAMPANTE HP* 🖨️\n\n"
                                f"*{title}*\n\n"
                                f"🏷️ *Periferica:* `HP LaserJet Professional P1102w`\n"
                                f"🌐 *IP:* `{self.ip}`{pg_info}"
                                f"📝 *Dettagli:* {desc}\n\n"
                                f"👉 *Cosa fare adesso:*\n"
                                f"{action}\n\n"
                                "💡 _Rilevato automaticamente da un lavoro di stampa (PC, Smartphone, Rete o Bot). "
                                "Riceverai una notifica non appena il problema verrà risolto!_"
                            )
                            kb = {
                                "inline_keyboard": [
                                    [{"text": "🔄 Controlla Stato Adesso", "callback_data": "check_printer_status"}],
                                    [{"text": "📜 Registro Errori Hardware", "callback_data": "printer_err_log"}]
                                ]
                            }

                            if notify_callback:
                                try:
                                    notify_callback("printer", msg, reply_markup=kb)
                                except Exception as ne:
                                    print(f"[!] Errore notify_callback printer watchdog: {ne}")

                            if event_logger and hasattr(event_logger, "log_event"):
                                try:
                                    event_logger.log_event("printer", "HP LaserJet P1102w", f"Allarme hardware attivo: {title} ({err_type})")
                                except Exception:
                                    pass

                    elif not is_hw_error and code in [10001, 10023] and is_online:
                        consecutive_errors = 0
                        # Se c'era un allarme attivo e la stampante è tornata pronta
                        if current_alert_active:
                            current_alert_active = False
                            resolved_msg = (
                                "✅ *STAMPANTE HP RIPRISTINATA (PRONTA)* 🖨️🟢\n\n"
                                "🏷️ *Periferica:* `HP LaserJet Professional P1102w`\n"
                                f"🌐 *IP:* `{self.ip}`\n\n"
                                "✨ _Il problema hardware precedente è stato risolto con successo! La stampante è tornata in linea e pronta all'uso._"
                            )
                            kb = {
                                "inline_keyboard": [
                                    [{"text": "🖨️ Visualizza Dettagli", "callback_data": "check_printer_status"}]
                                ]
                            }
                            if notify_callback:
                                try:
                                    notify_callback("printer", resolved_msg, reply_markup=kb)
                                except Exception as ne:
                                    print(f"[!] Errore notify_callback printer resolution: {ne}")

                            if event_logger and hasattr(event_logger, "log_event"):
                                try:
                                    event_logger.log_event("printer", "HP LaserJet P1102w", "Problema hardware risolto: stampante pronta (10001)")
                                except Exception:
                                    pass

                except Exception:
                    pass

        import threading
        t = threading.Thread(target=_loop, daemon=True, name="PrinterWatchdogThread")
        t.start()


