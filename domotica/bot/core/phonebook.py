import os
import re
import json
import time
import quopri

class PhonebookManager:
    """Gestore Rubrica Contatti per il Bot Domotico e Integrazione VoIP.
    Consente l'importazione di contatti da file .vcf (Android / Google Contacts / Apple),
    il salvataggio persistente in rubrica.json e la risoluzione dei nomi dei chiamanti.
    """

    def __init__(self, storage_path=None):
        if storage_path is None:
            # Default: stessa cartella del bot
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            storage_path = os.path.join(base_dir, "rubrica.json")

        self.storage_path = storage_path
        self.contacts = {}  # { normalized_number: contact_name }
        self.raw_contacts = {} # { contact_name: [list_of_numbers] }
        self._load()

    @staticmethod
    def clean_name(name):
        """Pulisce un nome da asterischi residui (es. usati come preferiti o separatori) e doppi spazi"""
        if not name:
            return ""
        cleaned = re.sub(r'[*_~`]+', ' ', str(name))
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned or str(name).strip()

    @staticmethod
    def format_phone(num_str):
        """Formatta un numero telefonico italiano per renderlo visivamente leggibile ed elegante"""
        if not num_str:
            return "Sconosciuto"
        clean = re.sub(r'[\s\-\(\)\./]', '', str(num_str).strip())
        # Mobile standard italiano (10 cifre che iniziano per 3) -> 339 534 3851
        if len(clean) == 10 and clean.startswith("3"):
            return f"{clean[:3]} {clean[3:6]} {clean[6:]}"
        # Mobile con prefisso +39 o 0039
        if clean.startswith("+39") or clean.startswith("0039"):
            prefix = "+39 "
            sub = clean[3:] if clean.startswith("+39") else clean[4:]
            if len(sub) == 10 and sub.startswith("3"):
                return f"{prefix}{sub[:3]} {sub[3:6]} {sub[6:]}"
            elif len(sub) >= 9:
                return f"{prefix}{sub[:3]} {sub[3:]}"
        # Fisso italiano 2 cifre: Roma (06) e Milano (02) -> 06 1234 5678
        if (clean.startswith("06") or clean.startswith("02")) and len(clean) >= 8:
            return f"{clean[:2]} {clean[2:6]} {clean[6:]}"
        # Fisso italiano 3 cifre: Bari (080), Napoli (081), Torino (011), ecc. -> 080 343 3687
        if clean.startswith("0") and len(clean) >= 9:
            return f"{clean[:3]} {clean[3:6]} {clean[6:]}"
        return clean

    @staticmethod
    def format_duration(seconds):
        """Formatta la durata di una chiamata in formato leggibile (es: 38s, 16m 20s, 1h 12m)"""
        if not seconds or seconds <= 0:
            return ""
        try:
            sec = int(seconds)
        except (ValueError, TypeError):
            return ""
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}h {m}m" if m > 0 else f"{h}h"
        if m > 0:
            return f"{m}m {s}s" if s > 0 else f"{m} min"
        return f"{s}s"

    def _normalize_number(self, num_str):
        """Normalizza un numero telefonico rimuovendo caratteri non numerici e prefissi internazionali"""
        if not num_str:
            return ""

        # Rimuove spazi, trattini, parentesi, punti, slash
        cleaned = re.sub(r'[\s\-\(\)\./]', '', str(num_str).strip())

        # Rimuove prefisso internazionale italiano (+39 o 0039)
        if cleaned.startswith("+39"):
            cleaned = cleaned[3:]
        elif cleaned.startswith("0039"):
            cleaned = cleaned[4:]
        elif cleaned.startswith("+"):
            cleaned = cleaned[1:]

        return cleaned

    def _load(self):
        """Carica i contatti dal file JSON e normalizza i nomi"""
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    raw_nums = data.get("numbers", {})
                    self.contacts = {k: self.clean_name(v) for k, v in raw_nums.items()}
                    raw_names = data.get("names", {})
                    self.raw_contacts = {}
                    for k, v in raw_names.items():
                        c_k = self.clean_name(k)
                        if c_k not in self.raw_contacts:
                            self.raw_contacts[c_k] = []
                        for num in v:
                            if num not in self.raw_contacts[c_k]:
                                self.raw_contacts[c_k].append(num)
            except Exception as e:
                print(f"[!] Errore caricamento rubrica.json: {e}")
                self.contacts = {}
                self.raw_contacts = {}

    def _save(self):
        """Salva i contatti su file JSON"""
        try:
            data = {
                "metadata": {
                    "total_names": len(self.raw_contacts),
                    "total_numbers": len(self.contacts),
                    "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
                },
                "numbers": self.contacts,
                "names": self.raw_contacts
            }
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"[!] Errore salvataggio rubrica.json: {e}")
            return False

    def lookup(self, number_str):
        """Cerca il nome associato a un numero telefonico.
        Restituisce il nome pulito del contatto se trovato, altrimenti None.
        """
        if not number_str:
            return None

        clean = self._normalize_number(number_str)
        if not clean:
            return None

        # 1. Corrispondenza esatta
        if clean in self.contacts:
            return self.clean_name(self.contacts[clean])

        # 2. Corrispondenza con prefisso +39 se memorizzato
        if f"+39{clean}" in self.contacts:
            return self.clean_name(self.contacts[f"+39{clean}"])

        # 3. Corrispondenza sulle ultime 9 o 8 cifre (per numeri con/senza prefisso internazionale)
        if len(clean) >= 8:
            suffix = clean[-9:] if len(clean) >= 9 else clean[-8:]
            for stored_num, name in self.contacts.items():
                stored_clean = self._normalize_number(stored_num)
                if stored_clean.endswith(suffix) or clean.endswith(stored_clean[-8:]):
                    return self.clean_name(name)

        return None

    def add_contact(self, name, number):
        """Aggiunge o aggiorna manualmente un contatto"""
        name = name.strip()
        clean = self._normalize_number(number)
        if not clean or not name:
            return False

        self.contacts[clean] = name

        if name not in self.raw_contacts:
            self.raw_contacts[name] = []
        if clean not in self.raw_contacts[name]:
            self.raw_contacts[name].append(clean)

        return self._save()

    def remove_contact(self, query):
        """Rimuove un contatto per nome o per numero"""
        clean = self._normalize_number(query)
        removed = 0

        # Rimuovi per numero
        if clean in self.contacts:
            name = self.contacts.pop(clean)
            if name in self.raw_contacts and clean in self.raw_contacts[name]:
                self.raw_contacts[name].remove(clean)
                if not self.raw_contacts[name]:
                    del self.raw_contacts[name]
            removed += 1

        # Rimuovi per nome esatto
        query_lower = query.strip().lower()
        to_del_names = [n for n in self.raw_contacts.keys() if n.lower() == query_lower]
        for n in to_del_names:
            nums = self.raw_contacts.pop(n, [])
            for num in nums:
                self.contacts.pop(num, None)
                removed += 1

        if removed > 0:
            self._save()
            return True
        return False

    def search(self, query):
        """Cerca contatti per nome o per numero"""
        q = str(query).strip().lower()
        results = []

        # Cerca nei nomi
        for name, nums in self.raw_contacts.items():
            if q in name.lower():
                results.append((name, nums))

        # Cerca nei numeri
        q_num = self._normalize_number(q)
        if q_num:
            for num, name in self.contacts.items():
                if q_num in num and not any(r[0] == name for r in results):
                    results.append((name, self.raw_contacts.get(name, [num])))

        return results

    def import_vcf_content(self, vcf_text):
        """Importa contatti da un testo vCard (.vcf) esportato da Android / Google Contatti.
        Supporta file vCard multi-contatto (2.1, 3.0, 4.0) e codifiche UTF-8 / Quoted-Printable.
        Restituisce una tupla: (num_contatti_importati, num_telefoni_importati).
        """
        imported_contacts = 0
        imported_numbers = 0

        # Separa i blocchi VCARD
        cards = re.findall(r'BEGIN:VCARD(.*?)END:VCARD', vcf_text, re.DOTALL | re.IGNORECASE)
        if not cards:
            return 0, 0

        for card in cards:
            name = None
            numbers = []

            for line in card.splitlines():
                line = line.strip()
                if not line:
                    continue

                # Gestione Quoted-Printable (es. caratteri accentati =C3=A0)
                if "ENCODING=QUOTED-PRINTABLE" in line.upper():
                    try:
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            decoded_bytes = quopri.decodestring(parts[1].encode('ascii'))
                            line = f"{parts[0]}:{decoded_bytes.decode('utf-8', errors='ignore')}"
                    except Exception:
                        pass

                # Estrazione Nome Completo (FN)
                if line.upper().startswith("FN:") or line.upper().startswith("FN;"):
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[1].strip():
                        name = parts[1].strip()

                # Fallback sul campo strutturato N (Cognome;Nome;...)
                elif not name and (line.upper().startswith("N:") or line.upper().startswith("N;")):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        n_parts = [p.strip() for p in parts[1].split(";") if p.strip()]
                        if len(n_parts) >= 2:
                            name = f"{n_parts[1]} {n_parts[0]}"
                        elif len(n_parts) == 1:
                            name = n_parts[0]

                # Estrazione Numeri di Telefono (TEL)
                elif line.upper().startswith("TEL:") or line.upper().startswith("TEL;"):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        raw_num = parts[1].strip()
                        clean_num = self._normalize_number(raw_num)
                        if clean_num and len(clean_num) >= 5:
                            numbers.append(clean_num)

            if name and numbers:
                # Sanitizza nome rimuovendo caratteri non standard ed asterischi
                name = self.clean_name(name.replace("\r", "").replace("\n", "").strip())
                if name:
                    imported_contacts += 1
                    if name not in self.raw_contacts:
                        self.raw_contacts[name] = []

                    for num in numbers:
                        self.contacts[num] = name
                        if num not in self.raw_contacts[name]:
                            self.raw_contacts[name].append(num)
                        imported_numbers += 1

        if imported_contacts > 0:
            self._save()

        return imported_contacts, imported_numbers

    def get_stats(self):
        """Restituisce le statistiche della rubrica"""
        return {
            "total_names": len(self.raw_contacts),
            "total_numbers": len(self.contacts)
        }
