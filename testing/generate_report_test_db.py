"""
TEST-ONLY: builds a separate database for trying out report generation -- 20 days of normal
operation for node 1 (Oct 6 00:00 to Oct 26 00:00, Asia/Manila), with occasional anomaly events.

Every reading goes through the REAL engine path (anomaly_engine.handle_data: the ML models, threshold
rules and DSS diagnoses) on a simulated clock, so the flags and alerts are what the system actually
produces; only the timestamps are back-dated. One reading every 2 minutes (the spacing the ML models
were trained on) rather than the node's 15 s, so a 20-day Full Audit PDF stays a manageable size.

    python testing/generate_report_test_db.py                  # writes database_report_test.db
    python testing/generate_report_test_db.py --out other.db   # somewhere else
    python testing/generate_report_test_db.py --dry-run        # in memory, prints the summary only

Point the server at it (PowerShell):
    $env:WASTEWATER_DB = "database_report_test.db"; uvicorn server:app --port 3000
"""
import argparse
import logging
import math
import os
import random
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import anomaly_engine as ae            # noqa: E402

NODE = 1
TZ = timezone(timedelta(hours=8))                         # Asia/Manila
START = datetime(2026, 10, 6, 0, 0, 0, tzinfo=TZ)
END = datetime(2026, 10, 26, 0, 0, 0, tzinfo=TZ)
STEP_S = 120
BATCH_H = 6                       # treatment cycle: the node's volume total restarts every 6 h
EVENTS_PER_DAY = (1, 3)
SEED = 20261006

# Anomaly events: (name, readings it lasts, weight). Each changes the normal reading while it lasts.
EVENTS = [
    ("ph_dip", (3, 8), 3),             # acidic discharge: pH below 7.0
    ("ph_shift", (2, 5), 3),           # sudden in-range pH change (ML only)
    ("turbidity_surge", (3, 10), 3),   # solids carry-over: above 50 NTU
    ("turbidity_bump", (2, 4), 2),     # in-range turbidity jump (ML only)
    ("temp_high", (4, 10), 2),         # above 30 °C
    ("temp_low", (4, 10), 1),          # below 26 °C
    ("level_high", (3, 8), 2),         # tank near overflow: above 90 %
    ("level_low", (3, 6), 1),          # tank nearly empty: below 5 %
    ("plant_upset", (3, 6), 1),        # pH down, turbidity up, temperature down together
]


def normal(t: datetime, i: int, drift: dict, rng: random.Random) -> dict:
    """Normal operation: daily cycles plus slow drift and sensor noise, well inside the limits."""
    hour = t.hour + t.minute / 60
    day = 2 * math.pi * hour / 24
    batch = 2 * math.pi * ((hour % BATCH_H) / BATCH_H)
    for k, sd in (("ph", 0.004), ("temp", 0.01), ("turb", 0.08)):
        drift[k] = 0.995 * drift[k] + rng.gauss(0, sd)       # slow mean-reverting wander
    return {
        "ph": 7.65 + 0.06 * math.sin(day - 2.0) + drift["ph"] + rng.gauss(0, 0.008),
        "temp": 28.0 + 0.6 * math.sin(day - 2.4) + drift["temp"] + rng.gauss(0, 0.02),
        "turb": max(2.0, 14.0 + 3.0 * math.sin(day + 0.5) + drift["turb"] + rng.gauss(0, 0.25)),
        "lvl": 50.0 - 20.0 * math.cos(batch) + rng.gauss(0, 0.3),
        "flow": min(5.0, max(1.0, 3.0 + 0.9 * math.sin(batch) + rng.gauss(0, 0.08))),
    }


def apply_event(r: dict, name: str, k: int, n: int, rng: random.Random) -> dict:
    """Reading k of an n-reading event: ramps in, holds, ramps out."""
    f = min(1.0, (k + 1) / 2, (n - k) / 2)           # 0.5 / 1 / ... / 1 / 0.5
    if name == "ph_dip":
        r["ph"] -= f * rng.uniform(0.9, 1.3)
    elif name == "ph_shift":
        r["ph"] -= f * rng.uniform(0.3, 0.45)
    elif name == "turbidity_surge":
        r["turb"] += f * rng.uniform(55, 130)
    elif name == "turbidity_bump":
        r["turb"] += f * rng.uniform(15, 25)
    elif name == "temp_high":
        r["temp"] += f * rng.uniform(2.6, 3.4)
    elif name == "temp_low":
        r["temp"] -= f * rng.uniform(2.6, 3.2)
    elif name == "level_high":
        r["lvl"] = max(r["lvl"], 91 + f * rng.uniform(0, 4))
    elif name == "level_low":
        r["lvl"] = min(r["lvl"], 4.5 - f * rng.uniform(0, 2.5))
    elif name == "plant_upset":
        r["ph"] -= f * rng.uniform(0.8, 1.1)
        r["turb"] += f * rng.uniform(60, 110)
        r["temp"] -= f * rng.uniform(2.5, 3.0)
    return r


def schedule(total: int, rng: random.Random) -> dict:
    """reading index -> (event name, position, length), a few events a day at random times,
    none in the first 2 hours (the ML models' warm-up) and never overlapping."""
    per_day = 24 * 3600 // STEP_S
    events = {}
    names, weights = zip(*[(name, w) for name, _, w in EVENTS])
    lengths = {name: span for name, span, _ in EVENTS}
    for day_start in range(0, total, per_day):
        for _ in range(rng.randint(*EVENTS_PER_DAY)):
            name = rng.choices(names, weights)[0]
            n = rng.randint(*lengths[name])
            for _attempt in range(20):
                start = rng.randrange(day_start, min(day_start + per_day, total) - n)
                if start < 60 or any(i in events for i in range(start - 15, start + n + 15)):
                    continue
                for k in range(n):
                    events[start + k] = (name, k, n)
                break
    return events


def run(conn) -> list:
    rng = random.Random(SEED)
    ae.log.setLevel(logging.WARNING)
    ae.radio_send = lambda radio, text: None
    ae._last_ml_flag.clear()
    ae.init_db(conn)
    models = ae.load_models()
    sim = {"t": START.timestamp()}
    models._now = lambda node_id: sim["t"]                 # the models' clock
    ae.time.monotonic = lambda: sim["t"]                   # the alert quiet-period clock

    total = int((END - START).total_seconds() // STEP_S)
    events = schedule(total, rng)
    drift = {"ph": 0.0, "temp": 0.0, "turb": 0.0}
    vol, batch, results = 0.0, None, []
    for i in range(total):
        t = datetime.fromtimestamp(sim["t"], TZ)
        r = normal(t, i, drift, rng)
        event = events.get(i)
        if event:
            r = apply_event(r, event[0], event[1], event[2], rng)
        this_batch = (t.date(), t.hour // BATCH_H)
        if this_batch != batch:
            vol, batch = 0.0, this_batch
        vol += r["flow"] * STEP_S / 60
        stamp = datetime.fromtimestamp(sim["t"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        last_alert = conn.execute("SELECT COALESCE(MAX(id), 0) FROM notification_logs").fetchone()[0]
        ae.handle_data(conn, models, None, {
            "node": str(NODE), "pid": str(i + 1), "ph": f"{r['ph']:.2f}", "turb": f"{r['turb']:.1f}",
            "temp": f"{r['temp']:.1f}", "lvl": f"{r['lvl']:.1f}", "flow": f"{r['flow']:.2f}", "vol": f"{vol:.2f}",
        })
        row = conn.execute("SELECT id, ml_flags, threshold_flags FROM telemetry_logs ORDER BY id DESC LIMIT 1").fetchone()
        conn.execute("UPDATE telemetry_logs SET timestamp = ? WHERE id = ?", (stamp, row[0]))
        conn.execute("UPDATE notification_logs SET timestamp = ? WHERE id > ?", (stamp, last_alert))
        results.append({"event": event, "ml": row[1], "threshold": row[2]})
        sim["t"] += STEP_S
    conn.commit()
    return results


def summary(conn, results):
    events = Counter(r["event"][0] for r in results if r["event"] and r["event"][1] == 0)
    quiet = [r for r in results if not r["event"]]
    print(f"{len(results)} readings, {START:%Y-%m-%d %H:%M} to {END:%Y-%m-%d %H:%M} (UTC+8), every {STEP_S // 60} min")
    print(f"anomaly events: {sum(events.values())}  {dict(events)}")
    print(f"normal readings flagged: ML {sum(1 for r in quiet if r['ml'])}, threshold {sum(1 for r in quiet if r['threshold'])}")
    print("readings flagged:", conn.execute("SELECT COUNT(*) FROM telemetry_logs WHERE is_anomaly = 1").fetchone()[0])
    print("notifications:", conn.execute(
        "SELECT SUM(is_threshold), SUM(is_anomaly), COUNT(*) FROM notification_logs").fetchone(), "(threshold, ML, total)")
    for col in ("ph", "turbidity", "temperature", "water_level", "flowrate", "volume_total"):
        print(f"  {col}: {conn.execute(f'SELECT ROUND(MIN({col}), 2), ROUND(AVG({col}), 2), ROUND(MAX({col}), 2) FROM telemetry_logs').fetchone()}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=os.path.join(ROOT, "database_report_test.db"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if not args.dry_run and os.path.exists(args.out):
        sys.exit(f"{args.out} already exists; delete it or pick another --out")

    conn = sqlite3.connect(":memory:")           # far faster than committing every row to a file
    results = run(conn)
    summary(conn, results)
    if not args.dry_run:
        out = sqlite3.connect(args.out)
        conn.backup(out)
        out.close()
        print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
