"""
TEST-ONLY: anomaly-detection test data (remove with REVERT-REPLAY, see testing/README in memory notes).

Writes a simulated test run for node 1 into the database: stable readings well inside the limits,
with 30 single-reading anomalies where pH, temperature and turbidity jump at the same moment, 15-30
stable readings apart. Every reading goes through the REAL engine path (anomaly_engine.handle_data:
the ML models, threshold rules and DSS diagnoses), on a simulated 15 s packet clock, so the flags and
alerts are what the models actually produce. Only the timestamps are back-dated.

    python testing/generate_anomaly_test.py --dry-run     # in-memory copy, prints the results
    python testing/generate_anomaly_test.py --apply       # backs up, replaces Oct 8 (local), writes

--apply saves testing/anomaly_test_backup.db (the database before) and
testing/anomaly_test_manifest.json (what was deleted and inserted) so it can be undone.
"""
import argparse
import json
import logging
import math
import os
import random
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import anomaly_engine as ae            # noqa: E402

DB = os.environ.get("ARGUS_TEST_DB", os.path.join(ROOT, "database.db"))      # env override: rehearse on a copy
BACKUP = os.environ.get("ARGUS_TEST_BACKUP", os.path.join(ROOT, "testing", "anomaly_test_backup.db"))
MANIFEST = os.environ.get("ARGUS_TEST_MANIFEST", os.path.join(ROOT, "testing", "anomaly_test_manifest.json"))

NODE = 1
TZ = timedelta(hours=8)                                   # Asia/Manila
START_LOCAL = datetime(2026, 10, 8, 9, 0, 0)               # first reading, local time
REPLACE_FROM_UTC = "2026-10-07 16:00:00"                   # Oct 8 00:00 local
REPLACE_TO_UTC = "2026-10-08 16:00:00"                     # Oct 9 00:00 local
STEP_S = 15                                                # the ESP32's send interval
WARMUP = 45                                                # stable readings before the first anomaly
ANOMALIES = 30
GAP = (21, 30)    # stable readings between anomalies; over 20 (5 min) so each gets its own DSS alert
                  # (the same diagnosis is only raised again after ML_ALERT_QUIET_SECONDS)
SEED = 20261008

STABLE = {"ph": (7.62, 0.008), "temp": (28.1, 0.015), "turb": (11.8, 0.08), "lvl": (55.0, 0.2), "flow": (4.0, 0.02)}
# Realistic disturbance on the stable readings: a slow drift (sine, amplitude over a period in
# minutes) plus jitter (sd), tuned against the real models to stay below what they flag
# (stronger settings were trialled: 33-97 false flags). Ranges: pH 7.55-7.70, 27.9-28.3 °C, 10.8-12.8 NTU.
DISTURB = {"ph": (0.010, 0.05, 37), "temp": (0.025, 0.15, 53), "turb": (0.13, 0.6, 29)}
_reading = {"i": 0}


def stable(rng):
    _reading["i"] += 1
    t_min = _reading["i"] * STEP_S / 60
    r = {k: rng.gauss(mu, sd) for k, (mu, sd) in STABLE.items()}
    for k, (sd, amp, period) in DISTURB.items():
        r[k] = STABLE[k][0] + amp * math.sin(2 * math.pi * t_min / period + period) + rng.gauss(0, sd)
    return r


def anomaly(rng):
    """pH, temperature and turbidity jump together, past their limits (pH 7.0, temp 26 °C, 50 NTU)."""
    r = stable(rng)
    r["ph"] = rng.uniform(5.9, 6.4)
    r["temp"] = rng.uniform(23.8, 24.9)
    r["turb"] = rng.uniform(75, 120)
    return r


def schedule(rng):
    kinds = ["stable"] * WARMUP
    for i in range(ANOMALIES):
        kinds.append("anomaly")
        kinds += ["stable"] * rng.randint(*GAP)
    return kinds


def run(conn):
    rng = random.Random(SEED)
    _reading["i"] = 0
    ae.log.setLevel(logging.WARNING)
    ae.radio_send = lambda radio, text: None
    ae._last_ml_flag.clear()
    models = ae.load_models()
    sim = {"t": START_LOCAL.replace(tzinfo=timezone(TZ)).timestamp()}
    models._now = lambda node_id: sim["t"]                 # the models' clock: 15 s per packet
    ae.time.monotonic = lambda: sim["t"]                   # the alert quiet-period clock

    vol, results = 0.0, []
    for i, kind in enumerate(schedule(rng)):
        r = anomaly(rng) if kind == "anomaly" else stable(rng)
        vol += r["flow"] * STEP_S / 60
        stamp = datetime.fromtimestamp(sim["t"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        last_alert = conn.execute("SELECT COALESCE(MAX(id), 0) FROM notification_logs").fetchone()[0]
        ae.handle_data(conn, models, None, {
            "node": str(NODE), "pid": str(i + 1), "ph": f"{r['ph']:.2f}", "turb": f"{r['turb']:.1f}",
            "temp": f"{r['temp']:.1f}", "lvl": f"{r['lvl']:.1f}", "flow": f"{r['flow']:.2f}", "vol": f"{vol:.2f}",
        })
        row = conn.execute("SELECT id, ml_flags, threshold_flags FROM telemetry_logs ORDER BY id DESC LIMIT 1").fetchone()
        conn.execute("UPDATE telemetry_logs SET timestamp = ? WHERE id = ?", (stamp, row[0]))
        alerts = conn.execute("SELECT id, title, severity FROM notification_logs WHERE id > ?", (last_alert,)).fetchall()
        conn.execute("UPDATE notification_logs SET timestamp = ? WHERE id > ?", (stamp, last_alert))
        results.append({"kind": kind, "id": row[0], "utc": stamp, "packet": i + 1, "ml": row[1], "threshold": row[2],
                        "alerts": [{"id": a[0], "title": a[1], "severity": a[2]} for a in alerts],
                        "values": {k: round(v, 2) for k, v in r.items()}})
        sim["t"] += STEP_S
    conn.commit()
    return results


def report(results):
    local = lambda utc: (datetime.strptime(utc, "%Y-%m-%d %H:%M:%S") + TZ).strftime("%Y-%m-%d %H:%M:%S")
    anomalies = [r for r in results if r["kind"] == "anomaly"]
    stable_flagged = [r for r in results if r["kind"] == "stable" and r["ml"]]
    print(f"{len(results)} readings, {local(results[0]['utc'])} to {local(results[-1]['utc'])} local")
    print(f"anomalies: {len(anomalies)}; ML flagged all three sensors on "
          f"{sum(1 for r in anomalies if r['ml'] and set(r['ml'].split(',')) >= {'ph', 'temperature', 'turbidity'})}")
    print(f"stable readings the ML flagged: {len(stable_flagged)}")
    for n, r in enumerate(anomalies, 1):
        v = r["values"]
        print(f"  #{n:2}  {local(r['utc'])}  packet {r['packet']:4}  pH {v['ph']:.2f}  temp {v['temp']:.1f}  "
              f"turb {v['turb']:.1f}  ML: {r['ml']}  alerts: {[a['title'][:40] for a in r['alerts']]}")
    return anomalies, stable_flagged


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    if args.dry_run == args.apply:
        sys.exit("use exactly one of --dry-run / --apply")

    if args.dry_run:
        conn = sqlite3.connect(":memory:")
        sqlite3.connect(DB).backup(conn)
        ae.init_db(conn)
        conn.execute("DELETE FROM telemetry_logs WHERE node_id = ? AND timestamp >= ? AND timestamp < ?",
                     (NODE, REPLACE_FROM_UTC, REPLACE_TO_UTC))
        report(run(conn))
        return

    if os.path.exists(MANIFEST):
        sys.exit("a test run is already applied (testing/anomaly_test_manifest.json); revert it first")
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(BACKUP)
    src.backup(dst)
    dst.close()
    conn = src
    ae.init_db(conn)
    removed_t = [r[0] for r in conn.execute(
        "SELECT id FROM telemetry_logs WHERE node_id = ? AND timestamp >= ? AND timestamp < ?",
        (NODE, REPLACE_FROM_UTC, REPLACE_TO_UTC))]
    removed_n = [r[0] for r in conn.execute(
        "SELECT id FROM notification_logs WHERE node_id = ? AND timestamp >= ? AND timestamp < ?",
        (NODE, REPLACE_FROM_UTC, REPLACE_TO_UTC))]
    conn.execute("DELETE FROM telemetry_logs WHERE node_id = ? AND timestamp >= ? AND timestamp < ?",
                 (NODE, REPLACE_FROM_UTC, REPLACE_TO_UTC))
    conn.execute("DELETE FROM notification_logs WHERE node_id = ? AND timestamp >= ? AND timestamp < ?",
                 (NODE, REPLACE_FROM_UTC, REPLACE_TO_UTC))
    results = run(conn)
    anomalies, _ = report(results)
    with open(MANIFEST, "w") as f:
        json.dump({
            "created": datetime.now().isoformat(timespec="seconds"),
            "backup": BACKUP,
            "replaced_window_utc": [REPLACE_FROM_UTC, REPLACE_TO_UTC],
            "removed_telemetry_ids": removed_t, "removed_notification_ids": removed_n,
            "inserted_telemetry_ids": [r["id"] for r in results],
            "inserted_notification_ids": [a["id"] for r in results for a in r["alerts"]],
            "anomalies": [{"n": i, "utc": r["utc"], "packet": r["packet"], "values": r["values"], "ml": r["ml"]}
                          for i, r in enumerate(anomalies, 1)],
        }, f, indent=1)
    print(f"\nbackup: {BACKUP}\nmanifest: {MANIFEST}")


if __name__ == "__main__":
    main()
