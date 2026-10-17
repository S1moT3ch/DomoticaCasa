import os
import sqlite3
import threading
from datetime import datetime, timedelta
import requests

class EventLogger:
    def __init__(self, db_path=None):
        if not db_path:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            logs_dir = os.path.join(base_dir, "logs")
            os.makedirs(logs_dir, exist_ok=True)
            db_path = os.path.join(logs_dir, "domotica_events.db")
        
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _init_db(self):
        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS domotica_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        category TEXT NOT NULL,
                        device TEXT NOT NULL,
                        details TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON domotica_logs (timestamp)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_logs_category ON domotica_logs (category)
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS climate_telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        device TEXT NOT NULL,
                        temperature REAL,
                        humidity REAL,
                        target_temp REAL,
                        mode TEXT
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_climate_timestamp ON climate_telemetry (timestamp)
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS server_telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        cpu_load REAL,
                        ram_usage_mb REAL,
                        temp_c REAL
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_server_timestamp ON server_telemetry (timestamp)
                """)
                try:
                    cursor.execute("ALTER TABLE server_telemetry ADD COLUMN swap_usage_mb REAL")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE server_telemetry ADD COLUMN usb_used_pct REAL")
                except Exception:
                    pass
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS power_telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        device_name TEXT NOT NULL,
                        power_w REAL NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_power_timestamp ON power_telemetry (timestamp)
                """)
                try:
                    cursor.execute("ALTER TABLE power_telemetry ADD COLUMN voltage REAL")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE power_telemetry ADD COLUMN current REAL")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE power_telemetry ADD COLUMN energy_kwh REAL")
                except Exception:
                    pass
                conn.commit()

    def log_event(self, category, device, details, timestamp=None):
        """Registra un nuovo evento domotico nel database SQLite"""
        timestamp_str = timestamp if timestamp else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id FROM domotica_logs 
                        WHERE timestamp = ? AND category = ? AND device = ? AND details = ?
                    """, (timestamp_str, category, device, details))
                    if cursor.fetchone():
                        return
                    cursor.execute("""
                        INSERT INTO domotica_logs (timestamp, category, device, details)
                        VALUES (?, ?, ?, ?)
                    """, (timestamp_str, category, device, details))
                    conn.commit()
                print(f"[EVENT LOGGER] Evento registrato [{timestamp_str}] ({category}) {device}: {details}")
            except Exception as e:
                print(f"[!] Errore salvataggio log SQLite: {e}")

    def log_climate_telemetry(self, device, temperature, humidity, target_temp=None, mode=None):
        """Registra i dati di temperatura ed umidità per le analisi grafiche (con orari allineati ai 15 minuti)"""
        if temperature is None and humidity is None:
            return

        # Arrotonda il timestamp al multiplo di 15 minuti più vicino (es. 23:00, 23:15, 23:30, 23:45)
        dt_now = datetime.now()
        discard_min = dt_now.minute % 15
        discard = timedelta(minutes=discard_min, seconds=dt_now.second, microseconds=dt_now.microsecond)
        dt_rounded = dt_now - discard
        if discard >= timedelta(minutes=7, seconds=30):
            dt_rounded += timedelta(minutes=15)
        timestamp_str = dt_rounded.strftime("%Y-%m-%d %H:%M:00")

        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    # Evita duplicati ravvicinati nello stesso intervallo di 12 minuti (< 720 secondi)
                    cursor.execute("""
                        SELECT temperature, humidity, timestamp FROM climate_telemetry
                        WHERE device = ? ORDER BY id DESC LIMIT 1
                    """, (device,))
                    last = cursor.fetchone()
                    if last:
                        l_temp, l_hum, l_ts = last[0], last[1], last[2]
                        if l_temp == temperature and l_hum == humidity:
                            try:
                                dt_last = datetime.strptime(l_ts, "%Y-%m-%d %H:%M:%S")
                                if (datetime.now() - dt_last).total_seconds() < 720:
                                    return  # Mantiene il campionamento ogni 15 min
                            except Exception:
                                pass

                    cursor.execute("""
                        INSERT INTO climate_telemetry (timestamp, device, temperature, humidity, target_temp, mode)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (timestamp_str, device, temperature, humidity, target_temp, mode))
                    conn.commit()
            except Exception as e:
                print(f"[!] Errore salvataggio telemetria clima: {e}")

    def get_climate_history(self, hours=24, device="Termostato BTicino"):
        """Recupera la cronologia di temperatura ed umidità nelle ultime N ore"""
        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    query = """
                        SELECT timestamp, temperature, humidity 
                        FROM climate_telemetry 
                        WHERE datetime(timestamp) >= datetime('now', ? || ' hours')
                        ORDER BY id ASC
                    """
                    cursor.execute(query, (f"-{hours}",))
                    rows = cursor.fetchall()
                    return rows
            except Exception as e:
                print(f"[!] Errore lettura storico clima: {e}")
                return []

    def get_climate_stats(self, hours=24):
        """Calcola le metriche e le medie di temperatura ed umidità registrate nelle ultime N ore (es. 24h)"""
        history = self.get_climate_history(hours=hours)
        if not history:
            return None

        temps = [float(h[1]) for h in history if h[1] is not None]
        hums = [float(h[2]) for h in history if h[2] is not None]

        if not temps and not hums:
            return None

        return {
            "count": len(history),
            "latest_temp": temps[-1] if temps else 0.0,
            "latest_hum": hums[-1] if hums else 0.0,
            "avg_temp": sum(temps) / len(temps) if temps else 0.0,
            "avg_hum": sum(hums) / len(hums) if hums else 0.0,
            "min_temp": min(temps) if temps else 0.0,
            "max_temp": max(temps) if temps else 0.0,
            "min_hum": min(hums) if hums else 0.0,
            "max_hum": max(hums) if hums else 0.0
        }

    def log_server_telemetry(self, cpu_load, ram_usage_mb, temp_c, swap_usage_mb=None, usb_used_pct=None):
        """Registra i dati vitali del server ogni 15 minuti"""
        if cpu_load is None and ram_usage_mb is None and temp_c is None:
            return

        dt_now = datetime.now()
        discard_min = dt_now.minute % 15
        discard = timedelta(minutes=discard_min, seconds=dt_now.second, microseconds=dt_now.microsecond)
        dt_rounded = dt_now - discard
        if discard >= timedelta(minutes=7, seconds=30):
            dt_rounded += timedelta(minutes=15)
        timestamp_str = dt_rounded.strftime("%Y-%m-%d %H:%M:00")

        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT cpu_load, ram_usage_mb, temp_c, swap_usage_mb, usb_used_pct, timestamp FROM server_telemetry
                        ORDER BY id DESC LIMIT 1
                    """)
                    last = cursor.fetchone()
                    if last:
                        l_cpu, l_ram, l_temp, l_swap, l_usb, l_ts = last[0], last[1], last[2], last[3], last[4], last[5]
                        if l_cpu == cpu_load and l_ram == ram_usage_mb and l_temp == temp_c and l_swap == swap_usage_mb and l_usb == usb_used_pct:
                            try:
                                dt_last = datetime.strptime(l_ts, "%Y-%m-%d %H:%M:%S")
                                if (datetime.now() - dt_last).total_seconds() < 720:
                                    return
                            except Exception:
                                pass

                    cursor.execute("""
                        INSERT INTO server_telemetry (timestamp, cpu_load, ram_usage_mb, temp_c, swap_usage_mb, usb_used_pct)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (timestamp_str, cpu_load, ram_usage_mb, temp_c, swap_usage_mb, usb_used_pct))
                    conn.commit()
            except Exception as e:
                print(f"[!] Errore salvataggio telemetria server: {e}")

    def get_server_history(self, hours=24):
        """Recupera la cronologia vitale del server nelle ultime N ore"""
        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    query = """
                        SELECT timestamp, cpu_load, ram_usage_mb, temp_c, swap_usage_mb, usb_used_pct
                        FROM server_telemetry 
                        WHERE datetime(timestamp) >= datetime('now', ? || ' hours')
                        ORDER BY id ASC
                    """
                    cursor.execute(query, (f"-{hours}",))
                    return cursor.fetchall()
            except Exception as e:
                print(f"[!] Errore lettura storico server: {e}")
                return []

    def generate_server_chart(self, hours=24, output_path=None, current_metrics=None):
        """Genera un grafico (PNG) con le performance storiche del server"""
        if not output_path:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            output_path = os.path.join(base_dir, "charts", "server", "server_chart.png")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        history = self.get_server_history(hours=hours)

        if not history and current_metrics:
            c_cpu, c_ram, c_temp = current_metrics
            self.log_server_telemetry(c_cpu, c_ram, c_temp)
            history = self.get_server_history(hours=hours)

        if not history:
            print("[!] Nessun dato reale di sistema disponibile per generare il grafico.")
            return None

        def _format_time_15min(ts):
            try:
                parts = ts.split(" ")
                time_part = parts[1][:5] if len(parts) > 1 else ts[:5]
                h_str, m_str = time_part.split(":")
                m_int = (int(m_str) // 15) * 15
                return f"{h_str}:{m_int:02d}"
            except Exception:
                return ts

        times = [_format_time_15min(h[0]) for h in history]
        times = [_format_time_15min(h[0]) for h in history]
        cpus_raw = [float(h[1]) if h[1] is not None else None for h in history]
        cpus = [min(100.0, c * 50.0) if c is not None else None for c in cpus_raw]
        rams = [float(h[2]) if h[2] is not None else None for h in history]
        temps = [float(h[3]) if h[3] is not None else None for h in history]
        swaps = [float(h[4]) if len(h)>4 and h[4] is not None else None for h in history]

        valid_cpus = [v for v in cpus if v is not None]
        valid_rams = [v for v in rams if v is not None]
        valid_temps = [v for v in temps if v is not None]
        valid_swaps = [v for v in swaps if v is not None]

        latest_cpu = valid_cpus[-1] if valid_cpus else 0.0
        latest_ram = valid_rams[-1] if valid_rams else 0.0
        latest_temp = valid_temps[-1] if valid_temps else 0.0
        latest_swap = valid_swaps[-1] if valid_swaps else 0.0

        title_lines = [
            "Home Server NVR — Performance Ultime 24 Ore (Campionamento 15 min)",
            f"⚡ CPU: {latest_cpu:.0f}% (Max: {max(valid_cpus) if valid_cpus else 0:.0f}%)  •  🧠 RAM: {latest_ram:.0f} MB  •  ⚙️ Swap: {latest_swap:.0f} MB  •  🌡️ Temp: {latest_temp:.1f}°C"
        ]

        import math
        min_cpu = math.floor(min(valid_cpus) / 10) * 10 if valid_cpus else 0
        max_cpu = math.ceil(max(valid_cpus) / 10) * 10 if valid_cpus else 100
        if max_cpu - min_cpu < 10: max_cpu += 10

        min_ram = math.floor(min(valid_rams) / 100) * 100 if valid_rams else 0
        max_ram = math.ceil(max(valid_rams) / 100) * 100 if valid_rams else 1000
        if max_ram - min_ram < 100: max_ram += 100

        if not valid_temps:
            min_t = 30.0
            max_t = 80.0
            step_t = 10.0
        else:
            min_t_stat = min(valid_temps)
            max_t_stat = max(valid_temps)
            span_t = max_t_stat - min_t_stat
            if span_t < 1.0:
                min_t = math.floor(min_t_stat * 10) / 10.0 - 0.2
                max_t = math.ceil(max_t_stat * 10) / 10.0 + 0.2
                step_t = 0.1
            elif span_t < 2.5:
                min_t = math.floor(min_t_stat * 10) / 10.0 - 0.4
                max_t = math.ceil(max_t_stat * 10) / 10.0 + 0.4
                step_t = 0.2
            elif span_t <= 5.0:
                min_t = math.floor(min_t_stat) - 1.0
                max_t = math.ceil(max_t_stat) + 1.0
                step_t = 1.0
            else:
                min_t = math.floor(min_t_stat / 5) * 5.0
                max_t = math.ceil(max_t_stat / 5) * 5.0
                if max_t - min_t < 5: max_t += 5.0
                step_t = 2.0 if (max_t - min_t) <= 10 else 5.0

        custom_annotations = []
        
        # Linee CPU (Asse y - Giallo)
        curr_cpu = float(min_cpu)
        while curr_cpu <= max_cpu + 1:
            custom_annotations.append({
                "type": "line", "mode": "horizontal", "scaleID": "y", "value": curr_cpu,
                "borderColor": "rgba(241, 196, 15, 0.25)",
                "borderWidth": 1,
                "drawTime": "beforeDatasetsDraw"
            })
            curr_cpu += 10.0

        # Linee RAM (Asse y1 - Verde)
        curr_ram = float(min_ram)
        while curr_ram <= max_ram + 1:
            custom_annotations.append({
                "type": "line", "mode": "horizontal", "scaleID": "y1", "value": curr_ram,
                "borderColor": "rgba(80, 250, 123, 0.2)",
                "borderWidth": 1,
                "borderDash": [4, 4],
                "drawTime": "beforeDatasetsDraw"
            })
            curr_ram += (max_ram - min_ram) / 4 if (max_ram - min_ram) > 0 else 100

        # Linee Temp (Asse y2 - Rosso)
        curr_t = float(min_t)
        while curr_t <= max_t + 0.05:
            custom_annotations.append({
                "type": "line", "mode": "horizontal", "scaleID": "y2", "value": curr_t,
                "borderColor": "rgba(255, 85, 85, 0.25)",
                "borderWidth": 1,
                "borderDash": [2, 2],
                "drawTime": "beforeDatasetsDraw"
            })
            curr_t += step_t

        try:
            chart_config = {
                "type": "line",
                "data": {
                    "labels": times,
                    "datasets": [
                        {
                            "label": f"Carico CPU — Attuale: {latest_cpu:.0f}%",
                            "borderColor": "rgb(241, 196, 15)",
                            "backgroundColor": "rgba(241, 196, 15, 0.1)",
                            "fill": False,
                            "borderWidth": 2,
                            "pointRadius": 6 if len(history) < 5 else 3,
                            "data": cpus,
                            "yAxisID": "y"
                        },
                        {
                            "label": f"Temperatura (°C) — Attuale: {latest_temp:.1f}°C",
                            "borderColor": "rgb(255, 85, 85)",
                            "backgroundColor": "rgba(255, 85, 85, 0.1)",
                            "fill": False,
                            "borderWidth": 2,
                            "pointRadius": 6 if len(history) < 5 else 3,
                            "data": temps,
                            "yAxisID": "y2"
                        },
                        {
                            "label": f"RAM Usata (MB) — Attuale: {latest_ram:.0f} MB",
                            "borderColor": "rgb(80, 250, 123)",
                            "backgroundColor": "rgba(80, 250, 123, 0.1)",
                            "fill": True,
                            "borderWidth": 2,
                            "pointRadius": 6 if len(history) < 5 else 3,
                            "data": rams,
                            "yAxisID": "y1"
                        },
                        {
                            "label": f"Swap Usata (MB) — Attuale: {latest_swap:.0f} MB",
                            "borderColor": "rgb(255, 184, 108)",
                            "backgroundColor": "rgba(255, 184, 108, 0.2)",
                            "fill": True,
                            "borderWidth": 2,
                            "pointRadius": 6 if len(history) < 5 else 3,
                            "data": swaps,
                            "yAxisID": "y1"
                        }
                    ]
                },
                "options": {
                    "title": {
                        "display": True,
                        "text": title_lines,
                        "fontColor": "#ffffff",
                        "fontSize": 14
                    },
                    "legend": {"labels": {"fontColor": "#cccccc", "fontSize": 12}},
                    "annotation": {
                        "annotations": custom_annotations
                    },
                    "scales": {
                        "yAxes": [
                            {
                                "id": "y",
                                "type": "linear",
                                "position": "left",
                                "scaleLabel": {"display": True, "labelString": "CPU Load (%)", "fontColor": "#f1c40f"},
                                "ticks": {"fontColor": "#f1c40f", "min": min_cpu, "max": max_cpu},
                                "gridLines": {"color": "rgba(0,0,0,0)", "drawBorder": True}
                            },
                            {
                                "id": "y1",
                                "type": "linear",
                                "position": "right",
                                "scaleLabel": {"display": True, "labelString": "RAM (MB)", "fontColor": "#50fa7b"},
                                "ticks": {"fontColor": "#50fa7b", "min": min_ram, "max": max_ram},
                                "gridLines": {"color": "rgba(0,0,0,0)", "drawBorder": True}
                            },
                            {
                                "id": "y2",
                                "type": "linear",
                                "position": "right",
                                "scaleLabel": {"display": True, "labelString": "Temp °C", "fontColor": "#ff5555"},
                                "ticks": {"fontColor": "#ff5555", "min": min_t, "max": max_t},
                                "gridLines": {"color": "rgba(0,0,0,0)", "drawBorder": True}
                            }
                        ],
                        "xAxes": [{"ticks": {"fontColor": "#bbbbbb"}, "gridLines": {"color": "rgba(255, 255, 255, 0.05)"}}]
                    }
                }
            }

            resp = requests.post("https://quickchart.io/chart", json={"chart": chart_config, "width": 900, "height": 450, "backgroundColor": "#1a1a24"}, timeout=6)
            if resp.ok and len(resp.content) > 1000:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                print(f"[+] Grafico server QuickChart generato: {output_path}")
                return output_path
        except Exception as e_qc:
            print(f"[!] Errore QuickChart server: {e_qc}")

        return None

    def generate_usb_chart(self, hours=24, output_path=None):
        """Genera un grafico (PNG) con lo spazio usato su USB nelle ultime N ore"""
        if not output_path:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            output_path = os.path.join(base_dir, "charts", "usb", "usb_chart.png")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        history = self.get_server_history(hours=hours)
        if not history:
            return None

        def _format_time_15min(ts):
            try:
                parts = ts.split(" ")
                time_part = parts[1][:5] if len(parts) > 1 else ts[:5]
                h_str, m_str = time_part.split(":")
                m_int = (int(m_str) // 15) * 15
                return f"{h_str}:{m_int:02d}"
            except Exception:
                return ts

        times = [_format_time_15min(h[0]) for h in history]
        usb_pcts = [float(h[5]) if len(h)>5 and h[5] is not None else None for h in history]
        valid_usbs = [v for v in usb_pcts if v is not None]

        if not valid_usbs:
            return None

        latest_usb = valid_usbs[-1]
        title_lines = [
            "Home Server NVR — Spazio USB /dev/sda5 Ultime 24 Ore",
            f"💾 USB Usata: {latest_usb:.1f}%"
        ]

        chart_config = {
            "type": "line",
            "data": {
                "labels": times,
                "datasets": [
                    {
                        "label": f"Spazio Occupato (%) — Attuale: {latest_usb:.1f}%",
                        "borderColor": "rgb(189, 147, 249)",
                        "backgroundColor": "rgba(189, 147, 249, 0.3)",
                        "fill": True,
                        "borderWidth": 2,
                        "pointRadius": 6 if len(history) < 5 else 3,
                        "data": usb_pcts,
                        "yAxisID": "y"
                    }
                ]
            },
            "options": {
                "title": {
                    "display": True,
                    "text": title_lines,
                    "fontColor": "#ffffff",
                    "fontSize": 14
                },
                "legend": {"labels": {"fontColor": "#cccccc", "fontSize": 12}},
                "scales": {
                    "yAxes": [
                        {
                            "id": "y",
                            "type": "linear",
                            "position": "left",
                            "scaleLabel": {"display": True, "labelString": "Usato (%)", "fontColor": "#bd93f9"},
                            "ticks": {"fontColor": "#bd93f9", "min": 0, "max": 100},
                            "gridLines": {"color": "rgba(255,255,255,0.1)", "drawBorder": True}
                        }
                    ],
                    "xAxes": [{"ticks": {"fontColor": "#bbbbbb"}, "gridLines": {"color": "rgba(255, 255, 255, 0.05)"}}]
                }
            }
        }

        try:
            resp = requests.post("https://quickchart.io/chart", json={"chart": chart_config, "width": 900, "height": 450, "backgroundColor": "#1a1a24"}, timeout=6)
            if resp.ok and len(resp.content) > 1000:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                return output_path
        except Exception as e_qc:
            print(f"[!] Errore QuickChart usb: {e_qc}")

        return None

    def generate_climate_chart(self, hours=24, tracker=None, output_path=None):
        """Genera un'immagine grafico (PNG) con i dati REALI delle ultime 24h ed i relativi valori medi"""
        if not output_path:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            output_path = os.path.join(base_dir, "charts", "climate", "climate_chart.png")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        history = self.get_climate_history(hours=hours)

        # Se nel DB non c'è ancora uno storico, controlla se c'è un dato reale istantaneo in tracker.termostato_data
        if not history and tracker and getattr(tracker, "termostato_data", None):
            t_data = tracker.termostato_data
            if isinstance(t_data, dict):
                curr_t = t_data.get("current_temp")
                hum = t_data.get("humidity")
                target_t = t_data.get("target_temp")
                mode = t_data.get("mode")
                if curr_t is not None or hum is not None:
                    self.log_climate_telemetry("Termostato BTicino", curr_t, hum, target_t, mode)
                    history = self.get_climate_history(hours=hours)

        # Se non c'è NESSUN dato reale disponibile, non inventiamo MAI dati sintetici!
        if not history:
            print("[!] Nessun dato reale di temperatura/umidità disponibile per generare il grafico.")
            return None

        def _format_time_15min(ts):
            try:
                parts = ts.split(" ")
                time_part = parts[1][:5] if len(parts) > 1 else ts[:5]
                h_str, m_str = time_part.split(":")
                m_int = (int(m_str) // 15) * 15
                return f"{h_str}:{m_int:02d}"
            except Exception:
                return ts

        times = [_format_time_15min(h[0]) for h in history]
        temps = [float(h[1]) if h[1] is not None else None for h in history]
        hums = [float(h[2]) if h[2] is not None else None for h in history]

        valid_temps = [t for t in temps if t is not None]
        valid_hums = [h for h in hums if h is not None]

        latest_t = valid_temps[-1] if valid_temps else 0.0
        latest_h = valid_hums[-1] if valid_hums else 0.0
        avg_t = sum(valid_temps) / len(valid_temps) if valid_temps else 0.0
        avg_h = sum(valid_hums) / len(valid_hums) if valid_hums else 0.0
        min_t_stat = min(valid_temps) if valid_temps else 0.0
        max_t_stat = max(valid_temps) if valid_temps else 0.0
        min_h_stat = min(valid_hums) if valid_hums else 0.0
        max_h_stat = max(valid_hums) if valid_hums else 0.0

        import math
        min_y = math.floor(min_t_stat) if valid_temps else 15
        max_y = math.ceil(max_t_stat) if valid_temps else 30
        if max_y - min_y < 2:
            min_y -= 1
            max_y += 1
            
        step_decimi = 0.2 if (max_y - min_y) <= 4 else 0.5

        min_h = math.floor(min_h_stat / 10) * 10 if valid_hums else 30
        max_h = math.ceil(max_h_stat / 10) * 10 if valid_hums else 70
        if max_h - min_h < 10:
            min_h -= 5
            max_h += 5

        custom_annotations = []
        
        # Linee Temperatura (Asse y)
        curr_t = float(min_y)
        while curr_t <= max_y + 0.05:
            is_int = (abs(curr_t - round(curr_t)) < 0.01)
            custom_annotations.append({
                "type": "line", "mode": "horizontal", "scaleID": "y", "value": curr_t,
                "borderColor": "rgba(255, 85, 85, 0.35)" if is_int else "rgba(255, 85, 85, 0.1)",
                "borderWidth": 1,
                "borderDash": [] if is_int else [4, 4],
                "drawTime": "beforeDatasetsDraw"
            })
            curr_t += step_decimi

        # Linee Umidità (Asse y1)
        curr_h = float(min_h)
        while curr_h <= max_h + 0.1:
            is_main = (abs(curr_h % 10) < 0.1)
            custom_annotations.append({
                "type": "line", "mode": "horizontal", "scaleID": "y1", "value": curr_h,
                "borderColor": "rgba(139, 233, 253, 0.35)" if is_main else "rgba(139, 233, 253, 0.1)",
                "borderWidth": 1,
                "borderDash": [] if is_main else [4, 4],
                "drawTime": "beforeDatasetsDraw"
            })
            curr_h += 5.0

        title_lines = [
            "Termostato Netatmo BTicino — Clima Ultime 24 Ore (Campionamento 15 min)",
            f"🌡️ Temp 24h: Media {avg_t:.1f}°C (Min {min_t_stat:.1f}° | Max {max_t_stat:.1f}°)   •   💧 Umidità 24h: Media {avg_h:.1f}% (Min {min_h_stat:.0f}% | Max {max_h_stat:.0f}%)"
        ]

        # --- METODO 1: QuickChart API (Web Chart.js) ---
        try:
            chart_config = {
                "type": "line",
                "data": {
                    "labels": times,
                    "datasets": [
                        {
                            "label": f"Temperatura (°C) — Attuale: {latest_t:.1f}°C",
                            "borderColor": "rgb(255, 85, 85)",
                            "backgroundColor": "rgba(255, 85, 85, 0.12)",
                            "fill": (len(history) > 1),
                            "borderWidth": 3,
                            "pointRadius": 6 if len(history) < 5 else 3,
                            "pointHoverRadius": 7,
                            "data": temps,
                            "yAxisID": "y"
                        },
                        {
                            "label": f"Umidità (%) — Attuale: {latest_h:.0f}%",
                            "borderColor": "rgb(139, 233, 253)",
                            "backgroundColor": "rgba(139, 233, 253, 0.05)",
                            "fill": False,
                            "borderWidth": 2,
                            "borderDash": [4, 4] if len(history) > 1 else [],
                            "pointRadius": 6 if len(history) < 5 else 3,
                            "pointHoverRadius": 7,
                            "data": hums,
                            "yAxisID": "y1"
                        }
                    ]
                },
                "options": {
                    "title": {
                        "display": True,
                        "text": title_lines,
                        "fontColor": "#ffffff",
                        "fontSize": 14
                    },
                    "legend": {
                        "labels": {
                            "fontColor": "#cccccc",
                            "fontSize": 12
                        }
                    },
                    "annotation": {
                        "annotations": custom_annotations
                    },
                    "scales": {
                        "yAxes": [
                            {
                                "id": "y",
                                "type": "linear",
                                "position": "left",
                                "scaleLabel": {
                                    "display": True,
                                    "labelString": "Temperatura (°C)",
                                    "fontColor": "#ff5555"
                                },
                                "ticks": {
                                    "fontColor": "#ff5555",
                                    "min": min_y,
                                    "max": max_y,
                                    "stepSize": step_decimi
                                },
                                "gridLines": {
                                    "color": "rgba(0, 0, 0, 0)",
                                    "drawBorder": True
                                }
                            },
                            {
                                "id": "y1",
                                "type": "linear",
                                "position": "right",
                                "scaleLabel": {
                                    "display": True,
                                    "labelString": "Umidità (%)",
                                    "fontColor": "#8be9fd"
                                },
                                "ticks": {
                                    "fontColor": "#8be9fd",
                                    "min": min_h,
                                    "max": max_h,
                                    "stepSize": 5.0
                                },
                                "gridLines": {
                                    "color": "rgba(0, 0, 0, 0)",
                                    "drawBorder": True
                                }
                            }
                        ],
                        "xAxes": [
                            {
                                "ticks": {
                                    "fontColor": "#bbbbbb"
                                },
                                "gridLines": {
                                    "color": "rgba(255, 255, 255, 0.05)"
                                }
                            }
                        ]
                    }
                }
            }

            resp = requests.post(
                "https://quickchart.io/chart",
                json={
                    "chart": chart_config,
                    "width": 900,
                    "height": 450,
                    "backgroundColor": "#1a1a24"
                },
                timeout=6
            )
            if resp.ok and len(resp.content) > 1000:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                print(f"[+] Grafico clima QuickChart (dati reali: {len(history)}) generato con successo: {output_path}")
                return output_path
        except Exception as e_qc:
            print(f"[!] QuickChart API non raggiungibile, uso fallback locale PIL: {e_qc}")

        # --- METODO 2: Fallback locale Pillow (PIL) ---
        try:
            from PIL import Image, ImageDraw, ImageFont

            width, height = 900, 450
            img = Image.new("RGB", (width, height), color=(26, 26, 36))
            draw = ImageDraw.Draw(img)

            try:
                font_title = ImageFont.truetype("arial.ttf", 16)
                font_small = ImageFont.truetype("arial.ttf", 11)
            except Exception:
                font_title = font_small = ImageFont.load_default()

            draw.text((25, 12), title_lines[0], fill=(255, 255, 255), font=font_title)
            draw.text((25, 34), title_lines[1], fill=(139, 233, 253), font=font_small)

            padding_left = 60
            padding_right = 60
            padding_top = 70
            padding_bottom = 50

            chart_w = width - padding_left - padding_right
            chart_h = height - padding_top - padding_bottom

            min_t = min(valid_temps) - 1.0 if valid_temps else 18.0
            max_t = max(valid_temps) + 1.0 if valid_temps else 25.0
            min_h = min(valid_hums) - 5.0 if valid_hums else 40.0
            max_h = max(valid_hums) + 5.0 if valid_hums else 70.0
            if max_t == min_t: max_t += 1.0
            if max_h == min_h: max_h += 1.0

            grid_lines = 5
            for i in range(grid_lines + 1):
                y = padding_top + (chart_h / grid_lines) * i
                draw.line([(padding_left, y), (width - padding_right, y)], fill=(45, 45, 60), width=1)
                
                val_t = round(max_t - (max_t - min_t) * (i / grid_lines), 1)
                draw.text((15, y - 7), f"{val_t}°C", fill=(255, 85, 85), font=font_small)
                
                val_h = int(max_h - (max_h - min_h) * (i / grid_lines))
                draw.text((width - padding_right + 15, y - 7), f"{val_h}%", fill=(139, 233, 253), font=font_small)

            num_pts = len(history)
            step_x = chart_w / max(1, num_pts - 1) if num_pts > 1 else chart_w / 2

            pts_t = []
            pts_h = []
            for idx, (t_val, h_val) in enumerate(zip(temps, hums)):
                x = padding_left + (idx * step_x if num_pts > 1 else chart_w / 2)
                t_v = t_val if t_val is not None else min_t
                h_v = h_val if h_val is not None else min_h

                y_t = padding_top + chart_h * (1.0 - (t_v - min_t) / (max_t - min_t))
                y_h = padding_top + chart_h * (1.0 - (h_v - min_h) / (max_h - min_h))
                pts_t.append((x, y_t))
                pts_h.append((x, y_h))

                step_label = max(1, num_pts // 8)
                if num_pts == 1 or idx % step_label == 0 or idx == num_pts - 1:
                    draw.text((x - 15, height - padding_bottom + 10), times[idx], fill=(180, 180, 200), font=font_small)

            if len(pts_h) > 1:
                for i in range(len(pts_h) - 1):
                    draw.line([pts_h[i], pts_h[i+1]], fill=(139, 233, 253), width=2)
            for x, y in pts_h:
                draw.rectangle([x-4, y-4, x+4, y+4], fill=(139, 233, 253))

            if len(pts_t) > 1:
                for i in range(len(pts_t) - 1):
                    draw.line([pts_t[i], pts_t[i+1]], fill=(255, 85, 85), width=3)
            for x, y in pts_t:
                draw.ellipse([x-5, y-5, x+5, y+5], fill=(255, 85, 85))

            leg_x = width - 260
            leg_y = 22
            draw.rectangle([leg_x, leg_y, leg_x + 12, leg_y + 12], fill=(255, 85, 85))
            draw.text((leg_x + 20, leg_y - 2), "Temperatura (°C)", fill=(255, 255, 255), font=font_small)
            
            draw.rectangle([leg_x + 130, leg_y, leg_x + 142, leg_y + 12], fill=(139, 233, 253))
            draw.text((leg_x + 150, leg_y - 2), "Umidità (%)", fill=(255, 255, 255), font=font_small)

            img.save(output_path, "PNG")
            print(f"[+] Grafico clima PIL (dati reali: {len(history)}) generato con successo: {output_path}")
            return output_path

        except Exception as e_pil:
            print(f"[!] Errore generazione grafico PIL: {e_pil}")

            base_dir = os.path.dirname(os.path.abspath(__file__))
            output_path = os.path.join(base_dir, "climate_chart.png")

        history = self.get_climate_history(hours=hours)

        # Se nel DB non c'è ancora uno storico, controlla se c'è un dato reale istantaneo in tracker.termostato_data
        if not history and tracker and getattr(tracker, "termostato_data", None):
            t_data = tracker.termostato_data
            if isinstance(t_data, dict):
                curr_t = t_data.get("current_temp")
                hum = t_data.get("humidity")
                target_t = t_data.get("target_temp")
                mode = t_data.get("mode")
                if curr_t is not None or hum is not None:
                    self.log_climate_telemetry("Termostato BTicino", curr_t, hum, target_t, mode)
                    history = self.get_climate_history(hours=hours)

        # Se non c'è NESSUN dato reale disponibile, non inventiamo MAI dati sintetici!
        if not history:
            print("[!] Nessun dato reale di temperatura/umidità disponibile per generare il grafico.")
            return None

    def _build_logs_where(self, query_str):
        """
        Costruisce la clausola WHERE e i parametri per supportare sia le categorie
        predefinite (mappate) che la ricerca libera per parametri su dispositivo, dettagli e categoria.
        """
        cat_map = {
            "allarmi": ["alarm_house", "alarm_garage", "alarm"],
            "allarme": ["alarm_house", "alarm_garage", "alarm"],
            "alarm": ["alarm_house", "alarm_garage", "alarm"],
            "telecamere": ["camera", "camera_soggiorno", "camera_corridoio", "camera_garage", "tapo_camera"],
            "telecamera": ["camera", "camera_soggiorno", "camera_corridoio", "camera_garage", "tapo_camera"],
            "camera": ["camera", "camera_soggiorno", "camera_corridoio", "camera_garage", "tapo_camera"],
            "cams": ["camera", "camera_soggiorno", "camera_corridoio", "camera_garage", "tapo_camera"],
            "varchi": ["gate", "shutter"],
            "varco": ["gate", "shutter"],
            "serranda": ["shutter"],
            "serrande": ["shutter"],
            "shutter": ["shutter"],
            "cancello": ["gate"],
            "cancelli": ["gate"],
            "gate": ["gate"],
            "luci": ["light", "luce_cucina", "luce", "led_andrea"],
            "luce": ["light", "luce_cucina", "luce"],
            "led": ["led_andrea"],
            "andrea": ["led_andrea"],
            "light": ["light", "luce_cucina", "luce"],
            "cucina": ["luce_cucina"],
            "ricircolo": ["ricircolo"],
            "acqua": ["ricircolo"],
            "power": ["power", "outage_warning"],
            "blackout": ["power", "outage_warning"],
            "corrente": ["power", "outage_warning"],
            "consumi": ["power"],
            "watt": ["power"],
            "humidity": ["humidity"],
            "umidita": ["humidity"],
            "clima": ["daikin", "humidity"],
            "daikin": ["daikin"],
            "bosch": ["bosch"],
            "lavastoviglie": ["bosch"],
            "command": ["command"],
            "comandi": ["command"],
            "comando": ["command"],
            "cmd": ["command"],
            "azioni": ["command"],
            "azione": ["command"],
            "system": ["system"],
            "sistema": ["system"],
        }
        q = (query_str or "").strip().lower()
        if not q or q in ["all", "tutti", "tutto"]:
            return "", []

        words = q.split()
        where_parts = []
        params = []
        for w in words:
            cats = cat_map.get(w, [])
            if cats:
                cat_ph = ",".join(["?"] * len(cats))
                where_parts.append(f"(category IN ({cat_ph}) OR device LIKE ? OR details LIKE ?)")
                params.extend(cats)
                params.extend([f"%{w}%", f"%{w}%"])
            else:
                where_parts.append("(category LIKE ? OR device LIKE ? OR details LIKE ?)")
                params.extend([f"%{w}%", f"%{w}%", f"%{w}%"])

        where_sql = " WHERE " + " AND ".join(where_parts)
        return where_sql, params

    def get_recent_logs(self, limit=10, category=None, offset=0):
        """Recupera gli ultimi N eventi salvati, filtrati per categoria o ricerca testuale, paginati con offset"""
        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    where_sql, params = self._build_logs_where(category)
                    query = f"""
                        SELECT timestamp, category, device, details 
                        FROM domotica_logs 
                        {where_sql}
                        ORDER BY datetime(timestamp) DESC, id DESC 
                        LIMIT ? OFFSET ?
                    """
                    cursor.execute(query, params + [limit, offset])
                    return cursor.fetchall()
            except Exception as e:
                print(f"[!] Errore recupero log SQLite: {e}")
                return []

    def get_logs_count(self, category=None):
        """Recupera il conteggio totale degli eventi salvati nel DB SQLite per la paginazione con filtri"""
        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    where_sql, params = self._build_logs_where(category)
                    query = f"SELECT COUNT(*) FROM domotica_logs {where_sql}"
                    cursor.execute(query, params)
                    row = cursor.fetchone()
                    return row[0] if row else 0
            except Exception as e:
                print(f"[!] Errore conteggio log SQLite: {e}")
                return 0

    def get_overnight_events(self, hours=12):
        """Recupera gli eventi e le anomalie registrati nelle ultime N ore (es. durante la notte)"""
        return self.get_grouped_overnight_anomalies(hours=hours)

    def get_overnight_anomalies(self, hours=12):
        """Recupera le anomalie grezze senza raggruppamento"""
        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    query = """
                        SELECT timestamp, category, device, details 
                        FROM domotica_logs 
                        WHERE datetime(timestamp) >= datetime('now', ? || ' hours')
                          AND (
                            category IN ('camera', 'system', 'power', 'alarm_house', 'alarm_garage', 'alarm', 'humidity')
                            OR details LIKE '%OFFLINE%'
                            OR details LIKE '%ONLINE%'
                            OR details LIKE '%blackout%'
                            OR details LIKE '%disconness%'
                            OR details LIKE '%ripristino%'
                            OR details LIKE '%corrente%'
                          )
                          AND category != 'command'
                        ORDER BY id DESC 
                        LIMIT 15
                    """
                    cursor.execute(query, (f"-{hours}",))
                    rows = cursor.fetchall()
                    return rows
            except Exception as e:
                print(f"[!] Errore recupero anomalie notturne: {e}")
                return []

    def get_grouped_overnight_anomalies(self, hours=12):
        """Recupera le anomalie notturne raggruppando l'evento OFFLINE con il relativo evento di ripristino BACK ONLINE"""
        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    query = """
                        SELECT timestamp, category, device, details 
                        FROM domotica_logs 
                        WHERE datetime(timestamp) >= datetime('now', ? || ' hours')
                          AND (
                            category IN ('camera', 'system', 'power', 'alarm_house', 'alarm_garage', 'alarm', 'humidity')
                            OR details LIKE '%OFFLINE%'
                            OR details LIKE '%ONLINE%'
                            OR details LIKE '%blackout%'
                            OR details LIKE '%disconness%'
                            OR details LIKE '%ripristino%'
                            OR details LIKE '%corrente%'
                          )
                          AND category != 'command'
                        ORDER BY id ASC
                    """
                    cursor.execute(query, (f"-{hours}",))
                    rows = cursor.fetchall()
            except Exception as e:
                print(f"[!] Errore recupero anomalie notturne: {e}")
                return []

        if not rows:
            return []

        grouped = []
        used_indices = set()

        for i, row in enumerate(rows):
            if i in used_indices:
                continue

            ts, cat, dev, details = row
            det_upper = details.upper()
            is_offline = ("OFFLINE" in det_upper or "BLACKOUT" in det_upper or "DISCONNESS" in det_upper)
            is_online = ("ONLINE" in det_upper or "RIPRISTINO" in det_upper or "BACK ONLINE" in det_upper)

            if is_offline and not is_online:
                matching_online_idx = None
                for j in range(i + 1, len(rows)):
                    if j in used_indices:
                        continue
                    j_ts, j_cat, j_dev, j_details = rows[j]
                    j_det_upper = j_details.upper()
                    if j_dev == dev and ("ONLINE" in j_det_upper or "RIPRISTINO" in j_det_upper or "BACK ONLINE" in j_det_upper):
                        matching_online_idx = j
                        break

                if matching_online_idx is not None:
                    used_indices.add(matching_online_idx)
                    used_indices.add(i)
                    o_row = rows[matching_online_idx]
                    o_ts, _, _, o_details = o_row

                    t_start = ts.split(" ")[1] if " " in ts else ts
                    t_end = o_ts.split(" ")[1] if " " in o_ts else o_ts

                    duration_str = ""
                    try:
                        dt1 = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                        dt2 = datetime.strptime(o_ts, "%Y-%m-%d %H:%M:%S")
                        diff_sec = int((dt2 - dt1).total_seconds())
                        if diff_sec >= 60:
                            mins = diff_sec // 60
                            secs = diff_sec % 60
                            duration_str = f" ({mins}m {secs}s)" if secs > 0 else f" ({mins}m)"
                        else:
                            duration_str = f" ({diff_sec}s)"
                    except Exception:
                        pass

                    summary_line = {
                        "device": dev,
                        "category": cat,
                        "type": "paired_recovery",
                        "start_time": t_start,
                        "end_time": t_end,
                        "duration": duration_str,
                        "text": f"`{t_start}` ➔ `{t_end}`{duration_str}: 🔴 Disconnesso -> 🟢 Ripristinato ONLINE"
                    }
                    grouped.append(summary_line)
                else:
                    used_indices.add(i)
                    t_start = ts.split(" ")[1] if " " in ts else ts
                    summary_line = {
                        "device": dev,
                        "category": cat,
                        "type": "unrecovered_offline",
                        "start_time": t_start,
                        "text": f"`{t_start}`: 🔴 Disconnesso (⚠️ *Ancora non raggiungibile*)"
                    }
                    grouped.append(summary_line)

            elif is_online and not is_offline:
                used_indices.add(i)
                t_start = ts.split(" ")[1] if " " in ts else ts
                summary_line = {
                    "device": dev,
                    "category": cat,
                    "type": "standalone_online",
                    "start_time": t_start,
                    "text": f"`{t_start}`: 🟢 Tornato ONLINE"
                }
                grouped.append(summary_line)

            else:
                used_indices.add(i)
                t_start = ts.split(" ")[1] if " " in ts else ts
                summary_line = {
                    "device": dev,
                    "category": cat,
                    "type": "other",
                    "start_time": t_start,
                    "text": f"`{t_start}`: {details}"
                }
                grouped.append(summary_line)

        return list(reversed(grouped))

    def format_logs_for_telegram(self, limit=10, category=None, page=0, view_mode="table"):
        """Formatta i log per Telegram: in tabella monospazio compatta (ottimizzata mobile, max 30-31 car.) o elenco esteso"""
        import re
        page = max(0, int(page))
        offset = page * limit
        logs = self.get_recent_logs(limit=limit, category=category, offset=offset)
        total_count = self.get_logs_count(category=category)
        total_pages = max(1, (total_count + limit - 1) // limit)

        # Se la pagina richiesta supera il totale, riassesta sull'ultima pagina
        if page >= total_pages:
            page = max(0, total_pages - 1)
            offset = page * limit
            logs = self.get_recent_logs(limit=limit, category=category, offset=offset)

        q_clean = (category or "").strip()
        is_search = bool(q_clean and q_clean.lower() not in ["all", "tutti", "tutto"])
        
        if is_search:
            header_title = f"🔍 *LOG DOMOTICI — Ricerca:* `{q_clean}`\n"
        else:
            header_title = "📋 *REGISTRO EVENTI DOMOTICI*\n"

        if not logs:
            text = (
                f"{header_title}\n"
                f"ℹ️ _Nessun evento trovato nel database"
                + (f" per \"{q_clean}\"." if is_search else "._")
                + "\n💡 _Puoi digitare ad es. `/log serranda`, `/log allarme`, `/log luce`, `/log garage`._"
            )
            return text, total_pages, page

        today_str = datetime.now().strftime("%Y-%m-%d")

        def _get_dev_info(dev_name, cat_name):
            d_raw = (dev_name or "").strip()
            cat_l = (cat_name or "").lower()
            
            # Utente Telegram (per i comandi)
            if d_raw.startswith("@"):
                u_tag = d_raw[:11]
                return "👤", u_tag, u_tag
            if d_raw.startswith("Utente Chat ") or d_raw.startswith("Chat ") or d_raw.startswith("User ") or cat_l == "command":
                cid = d_raw.replace("Utente Chat ", "").replace("Chat ", "").replace("User ", "").strip()
                try:
                    auth_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "authorized_users.json")
                    if not os.path.exists(auth_path):
                        auth_path = "z:/www/domotica/bot/authorized_users.json"
                    if os.path.exists(auth_path):
                        with open(auth_path, "r", encoding="utf-8") as af:
                            u_data = json.load(af).get(cid, {})
                            tg_u = u_data.get("username")
                            if tg_u: return "👤", f"@{tg_u}"[:11], f"@{tg_u}"
                            name = u_data.get("name")
                            if name: return "👤", name[:11], name
                except Exception:
                    pass
                if cid == "283084390": return "👤", "Cantore", "Cantore"
                if cid == "905004771": return "👤", "Andrea C.", "Andrea C."
                if cid.isdigit(): return "👤", f"User {cid[:5]}", f"User {cid[:6]}"
                return "👤", d_raw[:11], d_raw
                
            name_map = {
                'Telecamera Casa': ('📹', 'Cam Casa', 'Telecamera Casa'),
                'Camera Corridoio': ('🚪', 'Cam Corr.', 'Cam Corridoio'),
                'Camera Soggiorno': ('🛋️', 'Cam Sogg.', 'Cam Soggiorno'),
                'Camera Garage': ('🎥', 'Cam Garage', 'Cam Garage'),
                'Telecamere Casa & Corridoio': ('📹', 'Cam C&C', 'Telecamere C&C'),
                'Serranda Garage': ('🚪', 'Serranda', 'Serranda Garage'),
                'Cancello Garage': ('🚗', 'Cancello', 'Cancello Garage'),
                'Allarme Garage': ('🚨', 'All. Garage', 'Allarme Garage'),
                'Allarme Casa': ('🛡️', 'All. Casa', 'Allarme Casa'),
                'Luce Cucina': ('💡', 'Luce Cucina', 'Luce Cucina'),
                'Led Andrea': ('💡', 'Led Andrea', 'Led Andrea'),
                'Ricircolo Acqua': ('💧', 'Ricircolo', 'Ricircolo Acqua'),
                'Comandi Garage': ('🚪', 'Com. Garage', 'Comandi Garage'),
                'Termostato BTicino': ('🌡️', 'Termostato', 'Termostato'),
                'Climatizzatore Daikin': ('❄️', 'Clima Daik.', 'Clima Daikin'),
                'Lavastoviglie Bosch': ('🍽️', 'Lavastov.', 'Lavastoviglie'),
                'Home Server': ('🖥️', 'Home Server', 'Home Server'),
                'Firewall': ('🛡️', 'Firewall', 'Firewall'),
                'Rete Elettrica': ('⚡', 'Rete Elett.', 'Rete Elettrica'),
                'ServerRemoto': ('☁️', 'Serv.Remoto', 'Server Remoto'),
                'Server Remoto': ('☁️', 'Serv.Remoto', 'Server Remoto'),
            }
            for k, (ic, short_n, full_n) in name_map.items():
                if k.lower() in d_raw.lower():
                    return ic, short_n, full_n
            return '🔹', d_raw[:11], d_raw[:16]

        def _clean_status(dev, det, cat="", for_table=False):
            d = (det or "").strip()
            cat_lower = (cat or "").lower()
            d_up = d.upper()
            
            # 0. Gestione Comandi Utente (Sintetici e chiari con emoji)
            if cat_lower == "command" or 'comando' in d.lower() or 'cmd:' in d.lower():
                if "LED" in d_up and "ANDREA" in d_up:
                    return ("Led ON 🟢" if ("ACC" in d_up or "ON" in d_up) else "Led OFF🔴") if for_table else ("💡 Led Andrea 🟢" if ("ACC" in d_up or "ON" in d_up) else "💡 Led Andrea 🔴")
                if "CUCINA" in d_up or "LUCE" in d_up:
                    return ("Luce ON 🟢" if ("ACC" in d_up or "ON" in d_up) else "Luce OFF🔴") if for_table else ("💡 Luce Cucina 🟢" if ("ACC" in d_up or "ON" in d_up) else "💡 Luce Cucina 🔴")
                if "RICIRCOLO" in d_up:
                    return ("Ric. ON 🟢" if ("ACC" in d_up or "ATT" in d_up or "ON" in d_up) else "Ric. OFF🔴") if for_table else ("💧 Ricircolo 🟢" if ("ACC" in d_up or "ATT" in d_up or "ON" in d_up) else "💧 Ricircolo 🔴")
                if "SOGGIORNO" in d_up or "TAPO" in d_up:
                    return ("Sogg ON 🟢" if ("ACC" in d_up or "ON" in d_up) else "Sogg OFF🔴") if for_table else ("🛋️ Cam Sogg. 🟢" if ("ACC" in d_up or "ON" in d_up) else "🛋️ Cam Sogg. 🔴")
                if "CORRIDOIO" in d_up or "ALLARMEGARAGE" in d_up or "ALLARMEIO" in d_up:
                    return ("Corr ON 🟢" if ("ACC" in d_up or "ON" in d_up) else "Corr OFF🔴") if for_table else ("🚪 Cam Corr. 🟢" if ("ACC" in d_up or "ON" in d_up) else "🚪 Cam Corr. 🔴")
                if "SERRANDA" in d_up:
                    return "Apri Serr🚪" if for_table else "🚪 Serranda APRI"
                if "CANCELLO" in d_up:
                    return "Apri Canc🚗" if for_table else "🚗 Cancello APRI"
                if "DAIKIN" in d_up or "CLIMA" in d_up:
                    if "COOL" in d_up or "RAFFRESCAMENTO" in d_up: return "Clima COOL" if for_table else "❄️ Daikin COOL"
                    if "DRY" in d_up or "DEUMIDIFICA" in d_up: return "Clima DRY" if for_table else "💧 Daikin DRY"
                    if "OFF" in d_up or "SPEGNI" in d_up: return "Clima OFF🔴" if for_table else "🔴 Daikin OFF"
                    return "Clima ON 🟢" if for_table else "🟢 Daikin ON"
                if "VACANZA" in d_up:
                    if "ATTIVAT" in d_up or "ON" in d_up: return "Vacanza ON🌴" if for_table else "🌴 Vacanza ON"
                    if "DISATTIVAT" in d_up or "OFF" in d_up: return "VacanzaOFF🏠" if for_table else "🏠 Vacanza OFF"
                    return "Vacanza 🌴"
                m_cmd = re.search(r'(?:Eseguito comando|CMD:)\s*(.+)', d, re.I)
                if m_cmd:
                    raw_cmd = m_cmd.group(1).replace("(", "").replace(")", "").strip()
                    return f"⚙️ {raw_cmd[:6]}" if for_table else f"⚙️ {raw_cmd[:14]}"
            
            # 1. Allarme (controllo DISINSERITO prima di INSERITO)
            m = re.search(r'Allarme.*?è stato\s+(DISINSERITO|INSERITO)', d, re.I)
            if m:
                st = m.group(1).upper()
                if for_table:
                    return 'DISINS. 🔓' if 'DIS' in st else 'INSERITO🚨'
                ic = '🔓' if 'DIS' in st else '🚨'
                return f"{ic} {st}"
                
            # 2. Serranda (con voltaggio)
            m = re.search(r'Serranda.*?risulta ora\s+(CHIUSA|APERTA).*?(\d+\.\d+V)?', d, re.I)
            if m:
                st = m.group(1).upper()
                if for_table:
                    return 'CHIUSA 🔒' if 'CHIUS' in st else 'APERTA 🔓'
                v = f" ({m.group(2)})" if m.group(2) else ""
                ic = '🔒' if 'CHIUS' in st else '🔓'
                return f"{ic} {st}{v}"
                
            # 3. Telecamera / Luce / Led / Ricircolo (ACCESO / SPENTO)
            m = re.search(r'impostat[oa]\s+su\s+(ACCES[OA]|SPENT[OA])', d, re.I)
            if m:
                state = "ACCESO" if "ACC" in m.group(1).upper() else "SPENTO"
                ic = '🟢' if state == "ACCESO" else '🔴'
                return f"{state} {ic}" if for_table else f"{ic} {state}"
            m_alt = re.search(r'\b(ACCES[OA]|SPENT[OA])\b', d, re.I)
            if m_alt and ('Dispositivo' in d or 'Camera' in d or 'Telecamera' in d or 'Luce' in d or 'Led' in d):
                state = "ACCESO" if "ACC" in m_alt.group(1).upper() else "SPENTO"
                ic = '🟢' if state == "ACCESO" else '🔴'
                return f"{state} {ic}" if for_table else f"{ic} {state}"

            # 4. Cancello
            m = re.search(r'cancello.*?risulta ora\s+(APERTO|CHIUSO)', d, re.I)
            if m:
                st = m.group(1).upper()
                if for_table:
                    return 'APERTO 🚗' if 'APERTO' in st else 'CHIUSO 🔒'
                ic = '🚗' if 'APERTO' in st else '🔒'
                return f"{ic} {st}"

            # 5. Outage / Down / Ripristino / Blackout / Offline / Online
            if 'umidità superata' in d.lower() or 'umidita' in d.lower():
                m_h = re.search(r'(\d+(?:\.\d+)?%)', d)
                if for_table:
                    return f"{m_h.group(1)} 💧" if m_h else "Umidità 💧"
                hum_str = f" ({m_h.group(1)})" if m_h else ""
                return f"💧 Umidità elevata{hum_str} ⚠️"
            if 'BLACKOUT' in d.upper() or 'ASSENZA CORRENTE' in d.upper():
                return 'BLACKOUT⚡' if for_table else '⚡ BLACKOUT'
            if 'DOWN' in d.upper():
                return 'DOWN 🔴'
            if 'RIPRISTINO' in d.upper() or 'RIPRISTINATO' in d.upper():
                return 'ONLINE 🟢' if for_table else '🟢 RIPRISTINATO'
            if 'OFFLINE' in d.upper():
                return 'OFFLINE ⚠️' if for_table else '⚠️ OFFLINE'
            if 'ONLINE' in d.upper():
                return 'ONLINE ✅' if for_table else '✅ ONLINE'
                
            # 6. Modalità Vacanza
            if 'Modalità Vacanza' in d or 'Vacanza' in d:
                if 'Attivata' in d: return 'VacanzaON🌴' if for_table else '🌴 VACANZA ON'
                if 'Disattivata' in d: return 'VacanzaOFF🏠' if for_table else '🏠 VACANZA OFF'
                if 'ACCESE' in d: return 'VacanzaON🌴' if for_table else '🌴 VACANZA ON'
                return 'Vacanza 🌴'

            # Fallback generico
            d_clean = d
            if dev.lower() in d_clean.lower():
                d_clean = re.sub(re.escape(dev), '', d_clean, flags=re.I).strip()
            return d_clean[:10] if for_table else d_clean[:18]

        # ----------------------------------------------------
        # MODALITÀ 1: TABELLA MONOSPAZIO (Ottimizzata Mobile, default)
        # Larghezza fissa <= 31 caratteri: NON va a capo sugli smartphone!
        # ----------------------------------------------------
        if view_mode not in ["list", "elenco", "extended"]:
            is_cmd_view = (category or "").lower() in ["command", "comandi", "comando", "cmd"]
            col2_hdr = "UTENTE     " if is_cmd_view else "DISPOSITIVO"
            col3_hdr = "COMANDO   " if is_cmd_view else "STATO     "

            table_rows = []
            table_rows.append(f"ORA   │ {col2_hdr} │ {col3_hdr}")
            table_rows.append("──────┼─────────────┼──────────")

            last_date = None
            for ts, cat, dev, details in logs:
                parts = ts.split(" ")
                if len(parts) == 2:
                    d_part, t_part = parts[0], parts[1]
                    # Se cambia il giorno o il primo giorno non è oggi, inserisci separatore data compatto (31 car.)
                    if last_date is not None and d_part != last_date:
                        try:
                            dt = datetime.strptime(d_part, "%Y-%m-%d")
                            badge = f"─── {dt.strftime('%d/%m')} "
                            table_rows.append(badge + "─" * max(0, 31 - len(badge)))
                        except Exception:
                            pass
                    elif last_date is None and d_part != today_str:
                        try:
                            dt = datetime.strptime(d_part, "%Y-%m-%d")
                            badge = f"─── {dt.strftime('%d/%m')} "
                            table_rows.append(badge + "─" * max(0, 31 - len(badge)))
                        except Exception:
                            pass
                    last_date = d_part
                    time_disp = t_part[:5]
                else:
                    time_disp = ts[:5]

                _, short_dev, _ = _get_dev_info(dev, cat)
                status_disp = _clean_status(dev, details, cat, for_table=True)
                table_rows.append(f"{time_disp:<5} │ {short_dev:<11} │ {status_disp}")

            table_text = "\n".join(table_rows)
            info_sub = f"*(Pagina {page + 1}/{total_pages} — Totale {total_count} eventi)*"
            full_msg = f"{header_title}{info_sub}\n\n```text\n{table_text}\n```"
            return full_msg, total_pages, page

        # ----------------------------------------------------
        # MODALITÀ 2: VISTA ELENCO ESTESO (Con icone e testo descrittivo)
        # ----------------------------------------------------
        list_rows = []
        for ts, cat, dev, details in logs:
            parts = ts.split(" ")
            if len(parts) == 2:
                d_part, t_part = parts[0], parts[1]
                time_disp = t_part if d_part == today_str else f"{d_part[5:].replace('-', '/')} {t_part[:5]}"
            else:
                time_disp = ts[:8]

            dev_icon, _, dev_disp = _get_dev_info(dev, cat)
            status_disp = _clean_status(dev, details, cat, for_table=False)
            list_rows.append(f"• `{time_disp}` {dev_icon} *{dev_disp}* ➔ {status_disp}")

        body_text = "\n".join(list_rows)
        info_sub = f"*(Pagina {page + 1}/{total_pages} — Totale {total_count} eventi)*"
        full_msg = f"{header_title}{info_sub}\n\n{body_text}"
        return full_msg, total_pages, page

    def log_power_telemetry(self, device, power_w, voltage=None, current=None, energy_kwh=None):
        """Registra i dati di consumo elettrico (W, V, A, kWh) nel database"""
        if power_w is None:
            return

        dt_now = datetime.now()
        timestamp_str = dt_now.strftime("%Y-%m-%d %H:%M:%S")

        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT power_w, voltage, current, energy_kwh, timestamp FROM power_telemetry
                        WHERE device_name = ? ORDER BY id DESC LIMIT 1
                    """, (device,))
                    last = cursor.fetchone()
                    if last:
                        l_power, l_v, l_a, l_e, l_ts = last[0], last[1], last[2], last[3], last[4]
                        try:
                            dt_last = datetime.strptime(l_ts, "%Y-%m-%d %H:%M:%S")
                            secs_since = (dt_now - dt_last).total_seconds()
                            is_sig_change = abs((l_power or 0) - power_w) >= 5.0 or \
                                            (voltage is not None and l_v is not None and abs(l_v - voltage) >= 1.0) or \
                                            (current is not None and l_a is not None and abs(l_a - current) >= 0.05)
                            if secs_since < 60 and not is_sig_change:
                                return
                        except Exception:
                            pass

                    cursor.execute("""
                        INSERT INTO power_telemetry (timestamp, device_name, power_w, voltage, current, energy_kwh)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (timestamp_str, device, power_w, voltage, current, energy_kwh))
                    conn.commit()
            except Exception as e:
                print(f"[!] Errore salvataggio telemetria potenza: {e}")

    def get_power_history(self, hours=24, device="PowerMeterBox.shelly", start_str=None, end_str=None):
        """Recupera la cronologia dei consumi nelle ultime N ore o in un intervallo specifico"""
        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    if start_str and end_str:
                        query = """
                            SELECT timestamp, power_w, voltage, current, energy_kwh
                            FROM power_telemetry 
                            WHERE device_name = ? AND timestamp >= ? AND timestamp <= ?
                            ORDER BY id ASC
                        """
                        cursor.execute(query, (device, start_str, end_str))
                    else:
                        query = """
                            SELECT timestamp, power_w, voltage, current, energy_kwh
                            FROM power_telemetry 
                            WHERE device_name = ? AND datetime(timestamp) >= datetime('now', ? || ' hours')
                            ORDER BY id ASC
                        """
                        cursor.execute(query, (device, f"-{hours}"))
                    return cursor.fetchall()
            except Exception as e:
                print(f"[!] Errore lettura storico potenza: {e}")
                return []

    def generate_power_chart(self, hours=24, device="PowerMeterBox.shelly", output_path=None, label="Consumi elettrici Garage", page=None):
        """Genera un grafico (PNG) con i consumi storici (W, V, A). Restituisce (path, total_pages, current_page)"""
        if not output_path:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            is_casa = "home" in device.lower() or "casa" in device.lower()
            sub = os.path.join("charts", "power", "casa" if is_casa else "garage")
            fname = "power_chart_casa.png" if is_casa else "power_chart_garage.png"
            output_path = os.path.join(base_dir, sub, fname)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        import math
        window_hours = 6

        # Calcola intervallo temporale totale disponibile nel database per il dispositivo
        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM power_telemetry WHERE device_name = ?", (device,))
                    row_span = cursor.fetchone()
                    if row_span and row_span[0] and row_span[1]:
                        dt_min = datetime.strptime(row_span[0], "%Y-%m-%d %H:%M:%S")
                        dt_max = datetime.strptime(row_span[1], "%Y-%m-%d %H:%M:%S")
                        total_span_hours = max(float(hours), (dt_max - dt_min).total_seconds() / 3600.0)
                        available_pages = math.ceil(total_span_hours / float(window_hours))
                        # 4 foto per coprire le 24 ore (o fino alle pagine disponibili se meno di 24h)
                        total_pages = min(4, max(1, available_pages))
                    else:
                        total_pages = 4
                        dt_max = datetime.now()
            except Exception:
                total_pages = 4
                dt_max = datetime.now()

        if page is None or page > total_pages:
            page = total_pages
        elif page < 1:
            page = 1

        # Ogni pagina visualizza un intervallo di 6h (Pagina total_pages = ultime 6h, Pagina 1 = da -24h a -18h)
        offset_from_end = (total_pages - page) * window_hours
        end_dt = dt_max - timedelta(hours=offset_from_end)
        start_dt = end_dt - timedelta(hours=window_hours)

        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

        history = self.get_power_history(hours=hours, device=device, start_str=start_str, end_str=end_str)

        if not history:
            # Fallback a qualsiasi dato recente disponibile
            history = self.get_power_history(hours=hours, device=device)

        if not history:
            print(f"[!] Nessun dato reale di potenza disponibile per {device} per generare il grafico.")
            return None, 0, 0

        # Campionamento a max 240 punti ben distribuiti lungo l'arco di 6 ore (limite free tier QuickChart)
        target_points = 240
        if len(history) > target_points:
            step = max(1, len(history) // target_points)
            sampled = history[::step]
            if history[-1] not in sampled:
                sampled.append(history[-1])
            sampled = sampled[-target_points:]
        else:
            sampled = history

        def _format_time_15min(ts):
            try:
                parts = ts.split(" ")
                time_part = parts[1][:5] if len(parts) > 1 else ts[:5]
                return time_part
            except Exception:
                return ts

        times = [_format_time_15min(h[0]) for h in sampled]
        powers = [float(h[1]) if h[1] is not None else 0.0 for h in sampled]
        voltages = [float(h[2]) if len(h)>2 and h[2] is not None else None for h in sampled]
        currents = [float(h[3]) if len(h)>3 and h[3] is not None else None for h in sampled]

        valid_v = [v for v in voltages if v is not None]
        valid_a = [a for a in currents if a is not None]

        latest_pow = powers[-1] if powers else 0.0
        max_pow = max(powers) if powers else 0.0
        latest_v = valid_v[-1] if valid_v else 0.0
        latest_a = valid_a[-1] if valid_a else 0.0

        if page == total_pages:
            time_desc = f"Ultime {window_hours} Ore"
        else:
            h_from = (total_pages - page + 1) * window_hours
            h_to = (total_pages - page) * window_hours
            time_desc = f"Da -{h_from}h a -{h_to}h"

        title_lines = [
            f"{label} — {time_desc} (Pag. {page}/{total_pages})",
            f"⚡ Potenza Max: {max_pow:.1f} W"
        ]

        min_p = 0
        max_p = math.ceil(max_pow / 100) * 100 if max_pow > 0 else 500
        if max_p - min_p < 100: max_p += 100

        min_v = math.floor(min(valid_v)) if valid_v else 210
        max_v = math.ceil(max(valid_v)) if valid_v else 250
        if max_v - min_v < 10:
            max_v += 5
            min_v -= 5

        min_a = 0
        max_a = math.ceil(max(valid_a)) if valid_a else 16
        if max_a - min_a < 2: max_a += 2

        datasets = [
            {
                "label": f"Potenza (W) — Attuale: {latest_pow:.1f} W",
                "borderColor": "rgb(80, 200, 250)",
                "backgroundColor": "rgba(80, 200, 250, 0.2)",
                "fill": True,
                "borderWidth": 2,
                "pointRadius": 0,
                "lineTension": 0,
                "spanGaps": True,
                "data": powers,
                "yAxisID": "y"
            }
        ]

        if valid_v:
            datasets.append({
                "label": f"Tensione (V) — Attuale: {latest_v:.1f} V",
                "borderColor": "rgb(255, 184, 108)",
                "backgroundColor": "rgba(255, 184, 108, 0.0)",
                "fill": False,
                "borderWidth": 2,
                "pointRadius": 0,
                "lineTension": 0,
                "spanGaps": True,
                "data": voltages,
                "yAxisID": "y_v"
            })

        if valid_a:
            datasets.append({
                "label": f"Corrente (A) — Attuale: {latest_a:.3f} A",
                "borderColor": "rgb(80, 250, 123)",
                "backgroundColor": "rgba(80, 250, 123, 0.1)",
                "fill": True,
                "borderWidth": 2,
                "pointRadius": 0,
                "lineTension": 0,
                "spanGaps": True,
                "data": currents,
                "yAxisID": "y_a"
            })

        step_p = max(10, int(max_p / 20))
        ticks_p = [min_p + i * step_p for i in range(int((max_p - min_p) / step_p) + 1)]
        colors_p = ["rgba(80, 200, 250, 0.2)" if str(int(t)).endswith('0') else "rgba(0,0,0,0)" for t in ticks_p]

        step_v = 2
        ticks_v = [min_v + i * step_v for i in range(int((max_v - min_v) / step_v) + 1)]
        colors_v = ["rgba(255, 184, 108, 0.2)" if str(int(t)).endswith('0') else "rgba(0,0,0,0)" for t in ticks_v]

        step_a = 0.5
        ticks_a = [min_a + i * step_a for i in range(int((max_a - min_a) / step_a) + 1)]
        colors_a = ["rgba(80, 250, 123, 0.2)" if str(float(t)).endswith('0') else "rgba(0,0,0,0)" for t in ticks_a]

        # ----------------------------------------------------
        # METODO 1: QuickChart API (Prioritario, layout originale 3 assi, fino a 240 punti)
        # ----------------------------------------------------
        try:
            chart_config = {
                "type": "line",
                "data": {
                    "labels": times,
                    "datasets": datasets
                },
                "options": {
                    "title": {
                        "display": True,
                        "text": title_lines,
                        "fontColor": "#ffffff",
                        "fontSize": 14
                    },
                    "legend": {"labels": {"fontColor": "#cccccc", "fontSize": 12}},
                    "scales": {
                        "yAxes": [
                            {
                                "id": "y",
                                "type": "linear",
                                "position": "left",
                                "scaleLabel": {"display": True, "labelString": "Watt (W)", "fontColor": "#50c8fa"},
                                "ticks": {"fontColor": "#50c8fa", "min": min_p, "max": max_p, "stepSize": step_p},
                                "gridLines": {"color": colors_p, "drawBorder": True}
                            },
                            {
                                "id": "y_v",
                                "type": "linear",
                                "position": "right",
                                "scaleLabel": {"display": True, "labelString": "Tensione (V)", "fontColor": "#ffb86c"},
                                "ticks": {"fontColor": "#ffb86c", "min": min_v, "max": max_v, "stepSize": step_v},
                                "gridLines": {"color": colors_v, "drawBorder": True}
                            },
                            {
                                "id": "y_a",
                                "type": "linear",
                                "position": "right",
                                "scaleLabel": {"display": True, "labelString": "Corrente (A)", "fontColor": "#50fa7b"},
                                "ticks": {"fontColor": "#50fa7b", "min": min_a, "max": max_a, "stepSize": step_a},
                                "gridLines": {"color": colors_a, "drawBorder": True}
                            }
                        ],
                        "xAxes": [
                            {
                                "ticks": {"fontColor": "#aaaaaa", "maxTicksLimit": 8, "maxRotation": 45},
                                "gridLines": {"color": "rgba(255,255,255,0.05)"}
                            }
                        ]
                    }
                }
            }

            url = "https://quickchart.io/chart"
            payload = {
                "chart": chart_config,
                "width": 800,
                "height": 400,
                "backgroundColor": "#1e1e1e",
                "format": "png",
                "version": "2.9.4"
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200 and len(resp.content) > 1000:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                print(f"[+] Grafico QuickChart generato: {output_path} ({len(sampled)} punti, {len(history)} totali)")
                return output_path, total_pages, page
            else:
                err_text = resp.text.encode('ascii', 'replace').decode('ascii')
                print(f"[!] Errore QuickChart (Potenza): {resp.status_code} - {err_text}, passo al fallback...")
        except Exception as e_qc:
            print(f"[!] Errore connessione QuickChart: {e_qc}, passo al fallback locale...")

        # ----------------------------------------------------
        # METODO 2: Fallback locale Matplotlib
        # ----------------------------------------------------
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            plt.style.use('dark_background')
            fig, ax1 = plt.subplots(figsize=(10, 5), dpi=100)
            fig.patch.set_facecolor('#1e1e1e')
            ax1.set_facecolor('#1e1e1e')

            n_pts = len(sampled)
            ax1.plot(range(n_pts), powers, color='#50c8fa', linewidth=1.8, label=f'Potenza: {latest_pow:.1f} W')
            ax1.fill_between(range(n_pts), powers, color='#50c8fa', alpha=0.18)
            ax1.set_ylabel('Watt (W)', color='#50c8fa', fontsize=11, fontweight='bold')
            ax1.tick_params(axis='y', labelcolor='#50c8fa')
            ax1.grid(True, color='#ffffff', alpha=0.08, linestyle='--', linewidth=0.7)

            lines_all = []
            labels_all = []
            l1, lab1 = ax1.get_legend_handles_labels()
            lines_all += l1
            labels_all += lab1

            if valid_v:
                ax2 = ax1.twinx()
                ax2.plot(range(n_pts), voltages, color='#ffb86c', linewidth=1.2, alpha=0.85, label=f'Tensione: {latest_v:.1f} V')
                ax2.set_ylabel('Tensione (V)', color='#ffb86c', fontsize=11)
                ax2.tick_params(axis='y', labelcolor='#ffb86c')
                l2, lab2 = ax2.get_legend_handles_labels()
                lines_all += l2
                labels_all += lab2

            ax1.legend(lines_all, labels_all, loc='upper left', facecolor='#222230', edgecolor='#444455', fontsize=9)

            step = max(1, n_pts // 8)
            tick_idx = list(range(0, n_pts, step))
            if tick_idx[-1] != n_pts - 1:
                tick_idx.append(n_pts - 1)
            ax1.set_xticks(tick_idx)
            ax1.set_xticklabels([times[i] for i in tick_idx], rotation=30, ha='right', color='#aaaaaa', fontsize=9)

            title_text = f"{label} — {time_desc} (Pag. {page}/{total_pages})\n⚡ Potenza Max: {max_pow:.1f} W"
            ax1.set_title(title_text, color='#ffffff', fontsize=12, fontweight='bold', pad=10)

            plt.tight_layout()
            plt.savefig(output_path, dpi=100, facecolor=fig.get_facecolor(), edgecolor='none')
            plt.close()
            print(f"[+] Grafico generato con fallback Matplotlib: {output_path}")
            return output_path, total_pages, page
        except Exception as e_mpl:
            print(f"[!] Matplotlib non disponibile o errore: {e_mpl}, uso fallback PIL...")

        # ----------------------------------------------------
        # METODO 3: Fallback locale Pillow (PIL)
        # ----------------------------------------------------
        try:
            from PIL import Image, ImageDraw, ImageFont

            w, h = 800, 400
            img = Image.new('RGB', (w, h), (30, 30, 30))
            draw = ImageDraw.Draw(img)

            try:
                font_title = ImageFont.truetype("arial.ttf", 15)
                font_small = ImageFont.truetype("arial.ttf", 11)
            except Exception:
                font_title = font_small = ImageFont.load_default()

            n_pts = len(sampled)
            draw.text((25, 12), f"{label} — {time_desc} (Pag. {page}/{total_pages})", fill=(255, 255, 255), font=font_title)
            draw.text((25, 32), f"⚡ Max: {max_pow:.1f} W   •   Attuale: {latest_pow:.1f} W", fill=(80, 200, 250), font=font_small)

            pad_l, pad_r, pad_t, pad_b = 65, 65, 65, 45
            cw = w - pad_l - pad_r
            ch = h - pad_t - pad_b

            pts = []
            for i, p in enumerate(powers):
                x = pad_l + (i * cw / max(1, n_pts - 1))
                y = pad_t + ch * (1.0 - (p - min_p) / max(1, max_p - min_p))
                pts.append((x, y))

            for i in range(len(pts) - 1):
                draw.line([pts[i], pts[i+1]], fill=(80, 200, 250), width=2)

            step = max(1, n_pts // 8)
            for i in range(0, n_pts, step):
                x = pad_l + (i * cw / max(1, n_pts - 1))
                draw.text((x - 12, h - pad_b + 8), times[i], fill=(160, 160, 180), font=font_small)

            img.save(output_path, "PNG")
            print(f"[+] Grafico generato con fallback PIL: {output_path}")
            return output_path, total_pages, page
        except Exception as e_pil:
            print(f"[!] Errore fallback PIL: {e_pil}")

        return None, total_pages, page

    def clear_old_logs(self, days=90):
        """Elimina dal database i log più vecchi di N giorni"""
        with self.lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        DELETE FROM domotica_logs 
                        WHERE datetime(timestamp) < datetime('now', ? || ' days')
                    """, (f"-{days}",))
                    conn.commit()
            except Exception as e:
                print(f"[!] Errore pulizia vecchi log: {e}")
