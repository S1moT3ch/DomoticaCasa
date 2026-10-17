#!/usr/bin/env python3
# -*- coding: utf-8 -*-
print("Content-Type: application/json")
print("Access-Control-Allow-Origin: *")
print("Cache-Control: no-cache")
print()
import sys, os, json, sqlite3
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "..", "bot", "logs", "domotica_events.db")

def get_params():
    params = {}
    for part in os.environ.get("QUERY_STRING","").split("&"):
        if "=" in part:
            k,v = part.split("=",1)
            params[k.strip()] = v.strip()
    return params

def err(msg):
    print(json.dumps({"error": msg}))
    sys.exit(0)

params = get_params()
device = params.get("device", "PowerMeterHome.shelly")
mode   = params.get("mode", "history")
rng    = params.get("range", "")
try:
    hours = max(1, min(int(params.get("hours","24")), 8760))
except Exception:
    hours = 24

now = datetime.now()
if rng == "today":
    start_dt = now.replace(hour=0,minute=0,second=0,microsecond=0); end_dt = now
elif rng == "yesterday":
    start_dt = (now-timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    end_dt = start_dt + timedelta(hours=23,minutes=59,seconds=59)
elif rng == "week":
    start_dt = now - timedelta(days=7); end_dt = now
elif rng == "month":
    start_dt = now - timedelta(days=30); end_dt = now
else:
    start_dt = now - timedelta(hours=hours); end_dt = now

ss = start_dt.strftime("%Y-%m-%d %H:%M:%S")
es = end_dt.strftime("%Y-%m-%d %H:%M:%S")

if not os.path.exists(DB_PATH):
    err("DB non trovato: " + DB_PATH)

try:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if mode == "live":
        c.execute(
            "SELECT timestamp,power_w,voltage,current,energy_kwh "
            "FROM power_telemetry WHERE device_name=? ORDER BY id DESC LIMIT 1",
            (device,))
        r = c.fetchone()
        if not r:
            err("Nessuna lettura")
        print(json.dumps({
            "timestamp":  r["timestamp"],
            "power_w":    round(float(r["power_w"]    or 0), 2),
            "voltage":    round(float(r["voltage"]    or 0), 2),
            "current":    round(float(r["current"]    or 0), 3),
            "energy_kwh": round(float(r["energy_kwh"] or 0), 4)
        }))

    elif mode == "stats":
        c.execute(
            "SELECT COUNT(*) as n,"
            "MIN(power_w) as min_w, MAX(power_w) as max_w, AVG(power_w) as avg_w,"
            "MIN(voltage) as min_v, MAX(voltage) as max_v, AVG(voltage) as avg_v,"
            "MIN(current) as min_a, MAX(current) as max_a, AVG(current) as avg_a,"
            "MAX(energy_kwh)-MIN(energy_kwh) as delta_kwh,"
            "MIN(timestamp) as first_ts, MAX(timestamp) as last_ts "
            "FROM power_telemetry WHERE device_name=? AND timestamp>=? AND timestamp<=?",
            (device, ss, es))
        r = c.fetchone()
        avg_w  = float(r["avg_w"] or 0)
        span_h = (end_dt - start_dt).total_seconds() / 3600
        est    = round(avg_w * span_h / 1000, 3)
        dkwh   = round(float(r["delta_kwh"] or 0), 4) if r["delta_kwh"] else est
        print(json.dumps({
            "count": int(r["n"] or 0),
            "period_hours": round(span_h, 1),
            "power":   {"min": round(float(r["min_w"] or 0),1), "max": round(float(r["max_w"] or 0),1), "avg": round(avg_w,1)},
            "voltage": {"min": round(float(r["min_v"] or 0),1), "max": round(float(r["max_v"] or 0),1), "avg": round(float(r["avg_v"] or 0),1)},
            "current": {"min": round(float(r["min_a"] or 0),3), "max": round(float(r["max_a"] or 0),3), "avg": round(float(r["avg_a"] or 0),3)},
            "energy_kwh": dkwh,
            "estimated_cost_eur": round(dkwh * 0.25, 3),
            "first_ts": r["first_ts"],
            "last_ts":  r["last_ts"]
        }))

    elif mode == "hourly":
        c.execute(
            "SELECT strftime('%Y-%m-%d %H:00', timestamp) as hr,"
            "AVG(power_w) as avg_w, MAX(power_w) as max_w,"
            "AVG(voltage) as avg_v, AVG(current) as avg_a, COUNT(*) as n "
            "FROM power_telemetry "
            "WHERE device_name=? AND timestamp>=? AND timestamp<=? "
            "GROUP BY hr ORDER BY hr ASC",
            (device, ss, es))
        rows = c.fetchall()
        print(json.dumps({"mode": "hourly", "count": len(rows), "data": [
            {"ts": r["hr"], "avg_w": round(float(r["avg_w"] or 0),1),
             "max_w": round(float(r["max_w"] or 0),1),
             "avg_v": round(float(r["avg_v"] or 0),1),
             "avg_a": round(float(r["avg_a"] or 0),3),
             "n": int(r["n"] or 0)} for r in rows]}))

    elif mode == "daily":
        c.execute(
            "SELECT strftime('%Y-%m-%d', timestamp) as dy,"
            "AVG(power_w) as avg_w, MAX(power_w) as max_w,"
            "AVG(voltage) as avg_v, AVG(current) as avg_a, COUNT(*) as n "
            "FROM power_telemetry "
            "WHERE device_name=? AND timestamp>=? AND timestamp<=? "
            "GROUP BY dy ORDER BY dy ASC",
            (device, ss, es))
        rows = c.fetchall()
        result = []
        for r in rows:
            aw = float(r["avg_w"] or 0)
            ek = round(aw * 24 / 1000, 3)
            result.append({"ts": r["dy"], "avg_w": round(aw,1),
                           "max_w": round(float(r["max_w"] or 0),1),
                           "avg_v": round(float(r["avg_v"] or 0),1),
                           "avg_a": round(float(r["avg_a"] or 0),3),
                           "est_kwh": ek, "est_cost": round(ek*0.25,3),
                           "n": int(r["n"] or 0)})
        print(json.dumps({"mode": "daily", "count": len(result), "data": result}))

    else:  # history
        c.execute(
            "SELECT COUNT(*) as n FROM power_telemetry "
            "WHERE device_name=? AND timestamp>=? AND timestamp<=?",
            (device, ss, es))
        total = c.fetchone()["n"]
        mp    = 800
        step  = max(1, total // mp)
        if total > mp:
            c.execute(
                "SELECT timestamp,power_w,voltage,current,energy_kwh FROM ("
                "SELECT timestamp,power_w,voltage,current,energy_kwh,"
                "ROW_NUMBER() OVER (ORDER BY id) as rn "
                "FROM power_telemetry "
                "WHERE device_name=? AND timestamp>=? AND timestamp<=?) "
                f"WHERE rn%{step}=0 OR rn=1 OR rn={total} ORDER BY rn ASC",
                (device, ss, es))
        else:
            c.execute(
                "SELECT timestamp,power_w,voltage,current,energy_kwh "
                "FROM power_telemetry "
                "WHERE device_name=? AND timestamp>=? AND timestamp<=? "
                "ORDER BY id ASC",
                (device, ss, es))
        rows = c.fetchall()
        print(json.dumps({
            "mode": "history", "device": device,
            "start": ss, "end": es,
            "total_rows": total, "returned": len(rows),
            "timestamps":  [r["timestamp"] for r in rows],
            "power_w":    [round(float(r["power_w"]    or 0),2) if r["power_w"]    is not None else None for r in rows],
            "voltage":    [round(float(r["voltage"]    or 0),2) if r["voltage"]    is not None else None for r in rows],
            "current":    [round(float(r["current"]    or 0),3) if r["current"]    is not None else None for r in rows],
            "energy_kwh": [round(float(r["energy_kwh"] or 0),4) if r["energy_kwh"] is not None else None for r in rows]
        }))

    conn.close()
except Exception as e:
    err(str(e))
