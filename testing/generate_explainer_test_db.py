"""
TEST-ONLY: builds a separate database for checking the ML's reasoning -- 3.5 days of stable readings
for node 1 (Oct 23 00:00 to Oct 26 12:00, Asia/Manila, one every 15 s like the real node), with
single-reading anomalies 30-45 min apart after a 3-hour stable start, so a dashboard chart shows only
1-2 at a time.

Every reading goes through the REAL engine path (anomaly_engine.handle_data), so each one gets the
ML's working saved in telemetry_logs.ml_details (median, dev, step, score, sd, z, and why it was or
wasn't flagged). Only the timestamps are back-dated. The truth about every reading is saved in a
sim_labels table, for testing/evaluate_detection.py:
    anomaly      a reading we made unusual on purpose (past a limit, or a sudden in-range jump)
    normal       everything else, including small bumps smaller than the sensors can resolve

    python testing/generate_explainer_test_db.py               # writes database_explainer_test.db
    python testing/generate_explainer_test_db.py --dry-run     # in memory, prints the summary only
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
START = datetime(2026, 10, 23, 0, 0, 0, tzinfo=TZ)
HOURS = 84                                                # to Oct 26 12:00
STEP_S = 15                                               # the ESP32's send interval
STABLE_START = 3 * 3600 // STEP_S                         # 3 h of stable readings before the first event
GAP = (30 * 60 // STEP_S, 45 * 60 // STEP_S)              # 30-45 min between events
BATCH_H = 6                                               # the node's volume total restarts every 6 h
SEED = 20261026

# Stable readings, as in generate_anomaly_test.py (tuned there against the real models to stay below
# what they flag): mean and jitter, plus a slow drift (jitter sd, amplitude, period in minutes)
STABLE = {"ph": (7.62, 0.008), "temp": (28.1, 0.015), "turb": (11.8, 0.08), "lvl": (55.0, 0.2), "flow": (4.0, 0.02)}
DISTURB = {"ph": (0.010, 0.05, 37), "temp": (0.025, 0.15, 53), "turb": (0.13, 0.6, 29)}

# Single-reading events: (kind, label, weight). One sensor each, picked at random.
EVENTS = [
    ("past_limit", "anomaly", 3),      # past its limit: threshold breach and an ML flag
    ("sudden_jump", "anomaly", 3),     # a sudden jump that stays inside the limits: ML only
    ("small_bump", "normal", 3),       # smaller than the sensor can resolve: should NOT be flagged
]
CHANGES = {   # kind -> sensor -> how the reading changes
    "past_limit": {"ph": lambda r, g: g.uniform(6.2, 6.6), "turb": lambda r, g: g.uniform(70, 110),
                   "temp": lambda r, g: g.uniform(30.8, 31.6)},
    "sudden_jump": {"ph": lambda r, g: r - g.uniform(0.4, 0.5), "turb": lambda r, g: r + g.uniform(22, 30),
                    "temp": lambda r, g: r + g.uniform(1.3, 1.6)},
    # Well under the sensors' resolution (ML_DEADBAND: 0.15 pH, 10 NTU, 0.5 °C) even with the drift and
    # the rounding to the packet's decimals added
    "small_bump": {"ph": lambda r, g: r - g.uniform(0.05, 0.07), "turb": lambda r, g: r + g.uniform(3, 5),
                   "temp": lambda r, g: r + g.uniform(0.1, 0.15)},
}


def stable(i: int, rng: random.Random) -> dict:
    t_min = i * STEP_S / 60
    r = {k: rng.gauss(mu, sd) for k, (mu, sd) in STABLE.items()}
    for k, (sd, amp, period) in DISTURB.items():
        r[k] = STABLE[k][0] + amp * math.sin(2 * math.pi * t_min / period + period) + rng.gauss(0, sd)
    return r


def schedule(total: int, rng: random.Random) -> dict:
    """reading index -> (kind, label, sensor). The last event lands a few minutes before the end, so the
    dashboard's live chart (its last 50 readings) opens with one in view."""
    kinds, labels, weights = zip(*EVENTS)
    last = total - 24                                      # 6 min before the end
    events, i = {}, STABLE_START
    while i < last - GAP[0]:
        k = rng.choices(range(len(kinds)), weights)[0]
        events[i] = (kinds[k], labels[k], rng.choice(["ph", "turb", "temp"]))
        i += rng.randint(*GAP)
    events[last] = ("past_limit", "anomaly", rng.choice(["ph", "turb", "temp"]))
    return events


def run(conn) -> list:
    rng = random.Random(SEED)
    ae.log.setLevel(logging.WARNING)
    ae.radio_send = lambda radio, text: None
    ae._last_ml_flag.clear()
    ae.init_db(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS sim_labels (telemetry_id INTEGER PRIMARY KEY, label TEXT NOT NULL, "
                 "kind TEXT, sensor TEXT)")
    models = ae.load_models()
    sim = {"t": START.timestamp()}
    models._now = lambda node_id: sim["t"]                 # the models' clock
    ae.time.monotonic = lambda: sim["t"]                   # the alert quiet-period clock

    total = HOURS * 3600 // STEP_S
    events = schedule(total, rng)
    vol, batch, results = 0.0, None, []
    for i in range(total):
        t = datetime.fromtimestamp(sim["t"], TZ)
        r = stable(i, rng)
        kind, label, sensor = events.get(i, ("stable", "normal", None))
        if sensor:
            r[sensor] = CHANGES[kind][sensor](r[sensor], rng)
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
        sensor_name = {"turb": "turbidity", "temp": "temperature"}.get(sensor, sensor)
        conn.execute("INSERT INTO sim_labels VALUES (?, ?, ?, ?)", (row[0], label, kind, sensor_name))
        results.append({"kind": kind, "label": label, "ml": row[1], "threshold": row[2], "local": t})
        sim["t"] += STEP_S
    conn.commit()
    return results


def summary(results):
    end = START + timedelta(hours=HOURS)
    print(f"{len(results)} readings, {START:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} (UTC+8), every {STEP_S} s")
    print("events:", dict(Counter(r["kind"] for r in results if r["kind"] != "stable")))
    print(f"stable readings flagged: ML {sum(1 for r in results if r['kind'] == 'stable' and r['ml'])}")
    for r in results:
        if r["kind"] != "stable":
            print(f"  {r['local']:%m-%d %H:%M:%S}  {r['kind']:12} ML: {r['ml'] or '-':12} threshold: {r['threshold'] or '-'}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=os.path.join(ROOT, "database_explainer_test.db"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if not args.dry_run and os.path.exists(args.out):
        sys.exit(f"{args.out} already exists; delete it or pick another --out")

    conn = sqlite3.connect(":memory:")
    results = run(conn)
    summary(results)
    if not args.dry_run:
        out = sqlite3.connect(args.out)
        conn.backup(out)
        out.close()
        print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
