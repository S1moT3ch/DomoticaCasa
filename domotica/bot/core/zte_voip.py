import os
import sys
import time
from datetime import datetime
import json
import base64
import random
import hashlib
import subprocess
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar, Cookie

ROUTER_PUB_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAodPTerkUVCYmv28SOfRV
7UKHVujx/HjCUTAWy9l0L5H0JV0LfDudTdMNPEKloZsNam3YrtEnq6jqMLJV4ASb
1d6axmIgJ636wyTUS99gj4BKs6bQSTUSE8h/QkUYv4gEIt3saMS0pZpd90y6+B/9
hZxZE/RKU8e+zgRqp1/762TB7vcjtjOwXRDEL0w71Jk9i8VUQ59MR1Uj5E8X3WIc
fYSK5RWBkMhfaTRM6ozS9Bqhi40xlSOb3GBxCmliCifOJNLoO9kFoWgAIw5hkSIb
GH+4Csop9Uy8VvmmB+B3ubFLN35qIa5OG5+SDXn4L7FeAA5lRiGxRi8tsWrtew8w
nwIDAQAB
-----END PUBLIC KEY-----"""

class ZTEVoIPClient:
    """Client per modem-router ZTE Wind3 HUB (H389Q e compatibili).
    Gestisce l'autenticazione crittografica (RSA + AES-CBC) e l'interrogazione
    del registro chiamate VoIP in tempo reale.
    """

    def __init__(self, host="192.168.0.1", username="admin", password="", session_cache_file="/tmp/zte_session.json", last_missed_file="/tmp/zte_last_missed.json"):
        self.host = host
        self.username = username
        self.password = password
        self.session_cache_file = session_cache_file
        self.last_missed_file = last_missed_file

        self.cj = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.session_key = None
        self.xsrf_token = None

        # Prova a ripristinare la sessione salvata
        self._load_cached_session()

    def _random_num(self, length=16):
        return "".join(str(random.randint(0, 9)) for _ in range(length))

    def _rsa_encrypt(self, plaintext_bytes):
        """Cifra con RSA PKCS1v1.5 usando cryptography (se presente) o il binario openssl di sistema."""
        try:
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.primitives import serialization
            public_key = serialization.load_pem_public_key(ROUTER_PUB_KEY.encode("utf-8"))
            enc = public_key.encrypt(plaintext_bytes, padding.PKCS1v15())
            return base64.b64encode(enc).decode("utf-8")
        except ImportError:
            pass

        # Fallback universale: binario openssl nativo (OpenWrt / Linux)
        pub_tmp = "/tmp/zte_pubkey.pem"
        if not os.path.exists(pub_tmp):
            with open(pub_tmp, "w") as f:
                f.write(ROUTER_PUB_KEY)

        cmd = ["openssl", "rsautl", "-encrypt", "-pubin", "-inkey", pub_tmp]
        p = subprocess.run(cmd, input=plaintext_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode != 0:
            cmd = ["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", pub_tmp, "-pkeyopt", "rsa_padding_mode:pkcs1"]
            p = subprocess.run(cmd, input=plaintext_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if p.returncode != 0:
            raise RuntimeError(f"Errore crittografico RSA openssl: {p.stderr.decode('utf-8', errors='ignore')}")
        return base64.b64encode(p.stdout).decode("utf-8")

    def _aes_encrypt(self, text, key_str, iv_str):
        """Cifra con AES-256-CBC con zero-padding (esatto clone di CryptoJS nel modem)."""
        key_bytes = hashlib.sha256(key_str.encode("utf-8")).digest()
        iv_bytes = hashlib.sha256(iv_str.encode("utf-8")).digest()[:16]

        data_bytes = text.encode("utf-8")
        pad_len = 16 - (len(data_bytes) % 16)
        if pad_len != 16:
            data_bytes += b"\x00" * pad_len

        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes))
            encryptor = cipher.encryptor()
            ct = encryptor.update(data_bytes) + encryptor.finalize()
            return base64.b64encode(ct).decode("utf-8")
        except ImportError:
            pass

        # Fallback openssl CLI
        key_hex = key_bytes.hex()
        iv_hex = iv_bytes.hex()
        cmd = ["openssl", "enc", "-aes-256-cbc", "-K", key_hex, "-iv", iv_hex, "-nosalt", "-nopad"]
        p = subprocess.run(cmd, input=data_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode != 0:
            raise RuntimeError(f"Errore crittografico AES openssl: {p.stderr.decode('utf-8', errors='ignore')}")
        return base64.b64encode(p.stdout).decode("utf-8")

    def _get_cookie_val(self, name):
        for cookie in self.cj:
            if cookie.name == name:
                return cookie.value
        return ""

    def _load_cached_session(self):
        """Carica token di sessione salvati per evitare login inutili"""
        if os.path.exists(self.session_cache_file):
            try:
                with open(self.session_cache_file, "r") as f:
                    data = json.load(f)
                self.session_key = data.get("session_key")
                self.xsrf_token = data.get("xsrf_token")
                sid = data.get("sid")
                if sid:
                    c = Cookie(version=0, name="SID", value=sid, port=None, port_specified=False,
                               domain=self.host, domain_specified=False, domain_initial_dot=False,
                               path="/", path_specified=True, secure=False, expires=None,
                               discard=True, comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False)
                    self.cj.set_cookie(c)
                if self.xsrf_token:
                    c_xsrf = Cookie(version=0, name="XSRF-TOKEN", value=self.xsrf_token, port=None, port_specified=False,
                                    domain=self.host, domain_specified=False, domain_initial_dot=False,
                                    path="/", path_specified=True, secure=False, expires=None,
                                    discard=True, comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False)
                    self.cj.set_cookie(c_xsrf)
            except Exception:
                pass

    def _save_cached_session(self):
        """Salva session_key e cookie su file"""
        try:
            data = {
                "session_key": self.session_key,
                "xsrf_token": self.xsrf_token,
                "sid": self._get_cookie_val("SID")
            }
            with open(self.session_cache_file, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def login(self):
        """Esegue il login completo con handshake crittografico ZTE (challenge token + SHA-256 + AES/RSA)."""
        if not self.password:
            return False, "Password ZTE non configurata in .env (ZTE_ROUTER_PASSWORD)"

        try:
            # 1. Richiesta iniziale per ottenere SID e XSRF-TOKEN
            url_init = f"http://{self.host}/"
            req_init = urllib.request.Request(url_init, headers={"User-Agent": "HomeDomoticaBot/1.0"})
            self.opener.open(req_init, timeout=6)
            self.xsrf_token = self._get_cookie_val("XSRF-TOKEN")

            # 2. Richiesta challenge token (cmd=2)
            ts = int(time.time() * 1000)
            url_challenge = f"http://{self.host}/status.cgi?IF_ENCODE=&IF_ENCODE_FLAG=false&PAGE_ACTION=LIST&cmd=2&nvget=login_confirm&sessionKey=NULL&_={ts}"
            req_challenge = urllib.request.Request(url_challenge, headers={
                "Accept": "application/json",
                "Referer": f"http://{self.host}/",
                "X-XSRF-TOKEN": self.xsrf_token or "",
                "User-Agent": "HomeDomoticaBot/1.0"
            })
            res_challenge = self.opener.open(req_challenge, timeout=7)
            body_challenge = json.loads(res_challenge.read().decode("utf-8", errors="ignore"))
            login_conf = body_challenge.get("login_confirm", {})
            logintoken = str(login_conf.get("logintoken", ""))

            # Aggiorna il token XSRF aggiornato dopo la chiamata a status.cgi
            self.xsrf_token = self._get_cookie_val("XSRF-TOKEN")

            # 3. Hash della password con il logintoken dinamico
            hashed_pwd = hashlib.sha256((self.password + logintoken).encode("utf-8")).hexdigest()

            # 4. Cifratura credenziali con AES-CBC e chiave RSA
            key = self._random_num(16)
            iv = self._random_num(16)
            enc_key = self._rsa_encrypt(f"{key}+{iv}".encode("utf-8"))
            enc_pass = self._aes_encrypt(hashed_pwd, key, iv)

            # 5. Invio richiesta di autenticazione (cmd=3)
            payload = {
                "act": "nvset",
                "service": "login_confirm",
                "sessionKey": "NULL",
                "cmd": "3",
                "username": self.username,
                "password": enc_pass,
                "rememberPwd": "1",
                "IF_ENCODE": enc_key,
                "IF_ENCODE_FLAG": "false",
                "_": str(int(time.time() * 1000))
            }
            data = urllib.parse.urlencode(payload).encode("utf-8")
            url_login = f"http://{self.host}/status.cgi?service=login_confirm"
            req_login = urllib.request.Request(url_login, data=data, headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": f"http://{self.host}/",
                "X-XSRF-TOKEN": self.xsrf_token or "",
                "User-Agent": "HomeDomoticaBot/1.0"
            })
            res = self.opener.open(req_login, timeout=8)
            body = json.loads(res.read().decode("utf-8", errors="ignore"))

            login_confirm = body.get("login_confirm", {})
            err_type = login_confirm.get("loginErrType", "")

            # In ZTE "LOGIN_REFRESH_PAGE" indica successo del login
            if err_type == "LOGIN_REFRESH_PAGE" or "check_session" in login_confirm or "check_session" in body:
                self.session_key = login_confirm.get("check_session") or body.get("check_session")
                self.xsrf_token = self._get_cookie_val("XSRF-TOKEN")
                self._save_cached_session()
                return True, "Login effettuato con successo"

            if err_type == "LOGIN_LOCKED":
                lock_time = login_confirm.get("lockingTime", 60)
                return False, f"Modem temporaneamente bloccato per troppi tentativi (attendi {lock_time} secondi)"

            return False, f"Credenziali non valide o errore modem: {err_type or 'UNKNOWN'}"
        except Exception as e:
            return False, f"Errore di connessione con il modem ({self.host}): {e}"

    def get_call_history(self, limit=10):
        """Recupera la cronologia delle ultime chiamate dal modem ZTE.
        Restituisce una lista di dizionari con i dettagli di ciascuna chiamata.
        """
        if not self.session_key:
            ok, msg = self.login()
            if not ok:
                raise RuntimeError(msg)

        ts = int(time.time() * 1000)
        url = f"http://{self.host}/status.cgi?IF_ENCODE=&IF_ENCODE_FLAG=false&PAGE_ACTION=LIST&nvget=voip_call_history&sessionKey={self.session_key}&_={ts}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "Referer": f"http://{self.host}/",
            "X-XSRF-TOKEN": self._get_cookie_val("XSRF-TOKEN") or self.xsrf_token or "",
            "User-Agent": "HomeDomoticaBot/1.0"
        })

        try:
            res = self.opener.open(req, timeout=7)
            raw = res.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
        except Exception as e:
            raise RuntimeError(f"Errore recupero storico VoIP: {e}")

        # Se la sessione è scaduta o non autorizzata, riesegue il login e ritenta una volta sola
        if str(data.get("login_confirm", {}).get("login_status")) == "0":
            ok, msg = self.login()
            if not ok:
                raise RuntimeError(f"Sessione scaduta e login fallito: {msg}")
            # Ritenta con la nuova sessione
            ts = int(time.time() * 1000)
            url = f"http://{self.host}/status.cgi?IF_ENCODE=&IF_ENCODE_FLAG=false&PAGE_ACTION=LIST&nvget=voip_call_history&sessionKey={self.session_key}&_={ts}"
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "Referer": f"http://{self.host}/",
                "X-XSRF-TOKEN": self._get_cookie_val("XSRF-TOKEN") or self.xsrf_token or "",
                "User-Agent": "HomeDomoticaBot/1.0"
            })
            res = self.opener.open(req, timeout=7)
            data = json.loads(res.read().decode("utf-8", errors="ignore"))


        vh = data.get("voip_call_history", {})
        disp_num = int(vh.get("DispNum", 0))

        type_map = {
            "1": "persa",
            "2": "entrante",
            "3": "rifiutata",
            "4": "uscente"
        }

        calls = []
        for i in range(disp_num):
            dt = vh.get(f"callDate{i}")
            num = vh.get(f"number{i}")
            if not dt or not num:
                continue

            raw_type = str(vh.get(f"type{i}", ""))
            call_type = type_map.get(raw_type, "sconosciuto")
            sec = int(vh.get(f"duration{i}", 0))

            # Formatta la durata in hh:mm:ss
            hh = sec // 3600
            mm = (sec % 3600) // 60
            ss = sec % 60
            if hh > 0:
                duration_str = f"{hh:02d}:{mm:02d}:{ss:02d}"
            else:
                duration_str = f"{mm:02d}:{ss:02d}"

            calls.append({
                "index": i,
                "date": dt,
                "number": num,
                "type": call_type,
                "raw_type": raw_type,
                "duration": sec,
                "duration_str": duration_str,
                "phone_port": vh.get(f"numDisplay{i}", "phone1"),
                "out_num": vh.get(f"outNum{i}", "")
            })

            if len(calls) >= limit:
                break

        return calls

    def check_new_missed_calls(self):
        """Controlla se sono arrivate nuove chiamate perse rispetto all'ultimo controllo.
        Restituisce la lista delle sole nuove chiamate perse da notificare.
        """
        try:
            calls = self.get_call_history(limit=15)
        except Exception as e:
            return []

        missed = [c for c in calls if c["type"] == "persa"]
        if not missed:
            return []

        # Legge l'ultimo timestamp o identificativo salvato
        last_seen_date = ""
        if os.path.exists(self.last_missed_file):
            try:
                with open(self.last_missed_file, "r") as f:
                    saved = json.load(f)
                    last_seen_date = saved.get("last_date", "")
            except Exception:
                pass

        # Al primissimo avvio: memorizza la chiamata persa più recente senza inviare notifiche pregresse
        if not last_seen_date:
            try:
                with open(self.last_missed_file, "w") as f:
                    json.dump({"last_date": missed[0]["date"], "last_num": missed[0]["number"]}, f)
            except Exception:
                pass
            return []

        # Trova tutte le chiamate perse più recenti rispetto a last_seen_date
        new_missed = []
        for c in missed:
            if c["date"] > last_seen_date:
                new_missed.append(c)
            else:
                break

        if new_missed:
            # Aggiorna il file con la data della più recente
            try:
                with open(self.last_missed_file, "w") as f:
                    json.dump({"last_date": new_missed[0]["date"], "last_num": new_missed[0]["number"]}, f)
            except Exception:
                pass

        return new_missed

    def get_log_settings(self):
        """Recupera le impostazioni correnti di Syslog e diagnostica dal modem ZTE."""
        if not self.session_key:
            ok, msg = self.login()
            if not ok:
                raise RuntimeError(msg)

        ts = int(time.time() * 1000)
        url = f"http://{self.host}/status.cgi?IF_ENCODE=&IF_ENCODE_FLAG=false&PAGE_ACTION=LIST&nvget=log_settings&sessionKey={self.session_key}&_={ts}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "Referer": f"http://{self.host}/",
            "X-XSRF-TOKEN": self._get_cookie_val("XSRF-TOKEN") or self.xsrf_token or "",
            "User-Agent": "HomeDomoticaBot/1.0"
        })
        res = self.opener.open(req, timeout=7)
        data = json.loads(res.read().decode("utf-8", errors="ignore"))
        return data.get("log_settings", {})

    def set_log_settings(self, settings_dict):
        """Invia nuove impostazioni di Syslog al modem ZTE."""
        if not self.session_key:
            ok, msg = self.login()
            if not ok:
                raise RuntimeError(msg)

        payload = {
            "act": "nvset",
            "service": "log_settings",
            "sessionKey": self.session_key,
            "IF_ENCODE_FLAG": "false",
            "_": str(int(time.time() * 1000)),
            **settings_dict
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        url = f"http://{self.host}/status.cgi?service=log_settings"
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": f"http://{self.host}/",
            "X-XSRF-TOKEN": self._get_cookie_val("XSRF-TOKEN") or self.xsrf_token or "",
            "User-Agent": "HomeDomoticaBot/1.0"
        })
        res = self.opener.open(req, timeout=7)
        resp_data = json.loads(res.read().decode("utf-8", errors="ignore"))
        return resp_data.get("nvset") == "ok"

    def ensure_syslog_voice_enabled(self, target_ip="192.168.0.2", port="514"):
        """Verifica che il modem ZTE abbia il Syslog remoto abilitato verso target_ip con la categoria Voice attiva."""
        try:
            curr = self.get_log_settings()
            if not curr:
                return False

            if (str(curr.get("sysLogEnable")) == "1" and
                str(curr.get("sysLogVoice")) == "1" and
                str(curr.get("sysLogServer")) == str(target_ip) and
                str(curr.get("sysLogPort")) == str(port) and
                str(curr.get("sysLogXDSL")) == "0" and
                str(curr.get("selected")) in ["1", "2"]):
                return True

            new_settings = dict(curr)
            new_settings["sysLogEnable"] = "1"
            new_settings["selected"] = "2"
            new_settings["sysLogServer"] = str(target_ip)
            new_settings["sysLogPort"] = str(port)
            new_settings["sysLogVoice"] = "1"
            new_settings["sysLogXDSL"] = "0"
            new_settings["sysLogDHCPServer"] = "0"
            new_settings["sysLogPPPoE"] = "0"
            new_settings["sysLogWANDHCP"] = "0"
            return self.set_log_settings(new_settings)
        except Exception as e:
            print(f"[!] Errore configurazione Syslog su ZTE: {e}")
            return False

    def get_active_call_info(self, max_age_seconds=15, include_recently_ended=True):
        """Interroga lo stato della chiamata attiva sul modem ZTE in tempo reale.
        Distingue accuratamente:
        - "incoming": chiamata in arrivo dall'esterno (il telefono fisso sta squillando)
        - "outgoing": chiamata in uscita (la rete di casa sta effettuando una chiamata all'esterno)
        Restituisce None se nessuna chiamata è attiva o appena conclusa.
        """
        if not self.session_key:
            ok, _ = self.login()
            if not ok:
                return None

        ts = int(time.time() * 1000)

        # 1. Prova da systemMonitor_voipStatus con PAGE_TYPE=CALL (chiamata in corso/squillo)
        try:
            url_call = f"http://{self.host}/status.cgi?IF_ENCODE=&IF_ENCODE_FLAG=false&PAGE_ACTION=LIST&PAGE_TYPE=CALL&nvget=systemMonitor_voipStatus&sessionKey={self.session_key}&_={ts}"
            req_call = urllib.request.Request(url_call, headers={
                "Accept": "application/json",
                "Referer": f"http://{self.host}/",
                "X-XSRF-TOKEN": self._get_cookie_val("XSRF-TOKEN") or self.xsrf_token or "",
                "User-Agent": "HomeDomoticaBot/1.0"
            })
            res_call = self.opener.open(req_call, timeout=2.5)
            data_call = json.loads(res_call.read().decode("utf-8", errors="ignore"))
            st_call = data_call.get("systemMonitor_voipStatus", {})
            total = int(st_call.get("inst_totalNum", 0))
            if total > 0:
                for i in range(1, total + 1):
                    raw_type = str(st_call.get(f"callType{i}", "")).strip()
                    raw_status = str(st_call.get(f"callStatus{i}", "")).strip()
                    peer = st_call.get(f"peerNumber{i}") or st_call.get(f"peerNumber{i-1}") or st_call.get("peerNumber")

                    # Mappatura direzione dal firmware ZTE:
                    # 1, 2 = Incoming Call
                    # 3, 4 = Outgoing Call
                    if raw_type in ["3", "4"] or "out" in raw_type.lower():
                        direction = "outgoing"
                        call_type = "uscente"
                        call_status = "calling" if raw_status in ["3", "calling"] else "incall"
                    else:
                        direction = "incoming"
                        call_type = "entrante"
                        call_status = "ringing" if raw_status in ["1", "ringing"] else "incall"

                    peer_str = str(peer).strip() if peer else ""

                    # Se peerNumber non è ancora pronto nel record CALL, controlla in PAGE_TYPE=SIP
                    if not peer_str:
                        try:
                            url_sip = f"http://{self.host}/status.cgi?IF_ENCODE=&IF_ENCODE_FLAG=false&PAGE_ACTION=LIST&PAGE_TYPE=SIP&nvget=systemMonitor_voipStatus&sessionKey={self.session_key}&_={ts}"
                            req_sip = urllib.request.Request(url_sip, headers={
                                "Accept": "application/json",
                                "Referer": f"http://{self.host}/",
                                "X-XSRF-TOKEN": self._get_cookie_val("XSRF-TOKEN") or self.xsrf_token or "",
                                "User-Agent": "HomeDomoticaBot/1.0"
                            })
                            res_sip = self.opener.open(req_sip, timeout=2.0)
                            st_sip = json.loads(res_sip.read().decode("utf-8", errors="ignore")).get("systemMonitor_voipStatus", {})
                            if direction == "incoming":
                                peer_str = str(st_sip.get("lastLoc1", "")).strip()
                            else:
                                peer_str = str(st_sip.get("lastRmt1", "")).strip()
                        except Exception:
                            pass

                    peer_final = peer_str if peer_str else "Sconosciuto"
                    return {
                        "peer_number": peer_final,
                        "direction": direction,
                        "call_status": call_status,
                        "call_type": call_type,
                        "account": st_call.get(f"account{i}", "phone1")
                    }
        except Exception:
            pass

        # 2. Controllo ultima voce in voip_call_history (se richiesto e la chiamata si è conclusa da pochissimi secondi)
        if include_recently_ended and max_age_seconds > 0:
            try:
                recent_calls = self.get_call_history(limit=1)
                if recent_calls:
                    latest = recent_calls[0]
                    dt_str = latest.get("date", "")
                    if dt_str:
                        dt_call = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                        age = (datetime.now() - dt_call).total_seconds()
                        # Se il record è fresco (entro max_age_seconds)
                        if -2 <= age <= max_age_seconds:
                            c_type = latest.get("type", "entrante")
                            direction = "outgoing" if c_type == "uscente" else "incoming"
                            return {
                                "peer_number": latest.get("number"),
                                "direction": direction,
                                "call_status": "ended",
                                "call_type": c_type,
                                "account": latest.get("phone_port", "phone1")
                            }
            except Exception as e:
                print(f"[!] Errore controllo call_history in get_active_call_info: {e}")

        return None

    def get_wan_statistics(self):
        """Interroga il modem ZTE per ottenere lo stato e le statistiche complete della linea WAN e VDSL.
        Ritorna un dizionario con i dati grezzi parsati (DSL, WAN, traffico, link LAN, hardware).
        """
        if not self.session_key:
            ok, msg = self.login()
            if not ok:
                raise RuntimeError(msg)

        ts = int(time.time() * 1000)

        # 1. Recupero informazioni di sistema (DSL, Sessione WAN, LAN, Hardware)
        url_sys = f"http://{self.host}/status.cgi?IF_ENCODE=&IF_ENCODE_FLAG=false&PAGE_ACTION=GET&nvget=home_all_sysinfo&cmd=all&sessionKey={self.session_key}&_={ts}"
        req_sys = urllib.request.Request(url_sys, headers={
            "Accept": "application/json",
            "Referer": f"http://{self.host}/",
            "X-XSRF-TOKEN": self._get_cookie_val("XSRF-TOKEN") or self.xsrf_token or "",
            "User-Agent": "HomeDomoticaBot/1.0"
        })

        try:
            res_sys = self.opener.open(req_sys, timeout=7)
            data_sys = json.loads(res_sys.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            raise RuntimeError(f"Errore connessione modem per info di sistema: {e}")

        # Se la sessione è scaduta o non autorizzata, riesegue il login e ritenta una volta
        if str(data_sys.get("login_confirm", {}).get("login_status")) == "0":
            ok, msg = self.login()
            if not ok:
                raise RuntimeError(f"Sessione scaduta e login fallito: {msg}")
            ts = int(time.time() * 1000)
            url_sys = f"http://{self.host}/status.cgi?IF_ENCODE=&IF_ENCODE_FLAG=false&PAGE_ACTION=GET&nvget=home_all_sysinfo&cmd=all&sessionKey={self.session_key}&_={ts}"
            req_sys = urllib.request.Request(url_sys, headers={
                "Accept": "application/json",
                "Referer": f"http://{self.host}/",
                "X-XSRF-TOKEN": self._get_cookie_val("XSRF-TOKEN") or self.xsrf_token or "",
                "User-Agent": "HomeDomoticaBot/1.0"
            })
            res_sys = self.opener.open(req_sys, timeout=7)
            data_sys = json.loads(res_sys.read().decode("utf-8", errors="ignore"))

        home_info = data_sys.get("home_all_sysinfo", {})

        # 2. Recupero statistiche traffico WAN (byte, pacchetti, errori, drop)
        url_traf = f"http://{self.host}/status.cgi?IF_ENCODE=&IF_ENCODE_FLAG=false&PAGE_ACTION=LIST&nvget=tranffic_status_wan&sessionKey={self.session_key}&_={ts}"
        req_traf = urllib.request.Request(url_traf, headers={
            "Accept": "application/json",
            "Referer": f"http://{self.host}/",
            "X-XSRF-TOKEN": self._get_cookie_val("XSRF-TOKEN") or self.xsrf_token or "",
            "User-Agent": "HomeDomoticaBot/1.0"
        })
        traffic_info = {}
        try:
            res_traf = self.opener.open(req_traf, timeout=6)
            data_traf = json.loads(res_traf.read().decode("utf-8", errors="ignore"))
            traffic_info = data_traf.get("tranffic_status_wan", {})
        except Exception as e:
            print(f"[!] Warning: impossibile recuperare tranffic_status_wan: {e}")

        # 3. Recupero statistiche NAT (opzionale)
        url_nat = f"http://{self.host}/status.cgi?IF_ENCODE=&IF_ENCODE_FLAG=false&PAGE_ACTION=LIST&nvget=tranffic_status_nat&sessionKey={self.session_key}&_={ts}"
        req_nat = urllib.request.Request(url_nat, headers={
            "Accept": "application/json",
            "Referer": f"http://{self.host}/",
            "X-XSRF-TOKEN": self._get_cookie_val("XSRF-TOKEN") or self.xsrf_token or "",
            "User-Agent": "HomeDomoticaBot/1.0"
        })
        nat_info = {}
        try:
            res_nat = self.opener.open(req_nat, timeout=5)
            data_nat = json.loads(res_nat.read().decode("utf-8", errors="ignore"))
            nat_info = data_nat.get("tranffic_status_nat", {})
        except Exception:
            pass

        return {
            "sysinfo": home_info,
            "traffic": traffic_info,
            "nat": nat_info
        }



