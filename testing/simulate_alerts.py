"""
Simulate alert notifications, separate from the engine and the radio.

Writes alerts into notification_logs exactly as anomaly_engine.py would -- same threshold rules,
titles, messages and DSS diagnoses (dss_rules.py) -- so the running server pushes them to every
open dashboard within a second and the toast pop-ups appear. No readings are written and the
radio is not used.

ML alerts come from the REAL models (a private DevStepModels instance, not the running engine's):
a stable batch is fed through them first, then the anomaly reading, so the batch median, distance,
2-minute change and model score in the alert are genuine model output. Three strengths:

    min      the smallest sudden change the engine still flags: past the model's own decision boundary
             AND the sensor deadband (ML_DEADBAND) -- usually still inside the limits, so early warnings
    typical  a clear anomaly like the Oct 8 test run (pH ~6.1, ~24 °C, ~104 NTU): past the limits,
             so the threshold rules fire too and the DSS gives its plant-wide CRITICAL diagnosis
    max      an extreme but physically possible worst case (pH 3.0, 18 °C, 1000 NTU)

Every alert it writes is recorded in testing/simulated_alerts.json, so --clear removes exactly
those (and nothing else) from the Alerts page.

Run from the project folder:
    venv/bin/python3 testing/simulate_alerts.py threshold          # one threshold alert (turbidity)
    venv/bin/python3 testing/simulate_alerts.py multi              # several thresholds at once
    venv/bin/python3 testing/simulate_alerts.py ml                 # ML anomaly, typical strength
    venv/bin/python3 testing/simulate_alerts.py ml --level min     # ...or min / max
    venv/bin/python3 testing/simulate_alerts.py levels             # ML min, typical, max in turn
    venv/bin/python3 testing/simulate_alerts.py all                # threshold, multi, and the 3 ML levels
    venv/bin/python3 testing/simulate_alerts.py ml --node 2 --repeat 3 --every 5
    venv/bin/python3 testing/simulate_alerts.py --clear            # delete every simulated alert
"""
import argparse
import json
import logging
import math
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import anomaly_engine as ae                        # noqa: E402
import dss_rules                                   # noqa: E402

DB = os.environ.get("WASTEWATER_DB", os.path.join(ROOT, "database.db"))
# The record of simulated alert IDs belongs to the database they were written to, so a run against a
# test copy can never touch (or --clear) the record of the real one
LOG = (os.path.join(ROOT, "testing", "simulated_alerts.json") if os.path.abspath(DB) == os.path.join(ROOT, "database.db")
       else os.path.abspath(DB) + ".simulated_alerts.json")

# Threshold-only scenarios: (pH, turbidity NTU, temperature °C, level %, flow L/min)
SCENARIOS = {
    "threshold": {"ph": 7.6, "turbidity": 86.4, "temperature": 28.1, "level": 55.0, "flow": 4.0},
    "multi": {"ph": 5.97, "turbidity": 524.7, "temperature": 25.2, "level": 0.0, "flow": 0.0},
}

# ML scenarios. The batch is stable at these values (pH and temperature in the aeration basin,
# turbidity in the chlorine contact tank); the anomaly moves pH and temperature down and turbidity
# up at the same moment -- the "cold, acidic, turbid" pattern.
BATCH = {"ph": 7.62, "temperature": 28.1, "turbidity": 11.8}
DIRECTION = {"ph": -1, "temperature": -1, "turbidity": +1}
TYPICAL = {"ph": 6.12, "temperature": 24.3, "turbidity": 104.3}
MAXIMUM = {"ph": 3.0, "temperature": 18.0, "turbidity": 1000.0}
STABLE_READINGS = 45            # past the 30-reading warm-up, at the 15 s packet interval
STEP_S = 15


def limits_for(conn, node):
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM system_thresholds WHERE node_id = ?", (node,)).fetchone()
    return dict(row) if row else {"min_ph": 7.0, "max_ph": 8.5, "max_turbidity": 50.0, "min_temp": 26.0,
                                  "max_temp": 30.0, "max_level": 90.0, "min_level": 5.0, "max_flow": 10.0}


def fresh_models():
    """The real dev/step models on a simulated clock, warmed up on a stable batch."""
    models = ae.load_models()
    clock = {"t": 0.0}
    models._now = lambda node_id: clock["t"]
    for i in range(STABLE_READINGS):
        clock["t"] += STEP_S
        wobble = 0.004 * math.sin(i)             # tiny, steady variation like a settled probe
        models.evaluate(0, BATCH["ph"] + wobble, BATCH["turbidity"] + 10 * wobble, BATCH["temperature"] + wobble)
    clock["t"] += STEP_S
    return models


def minimum_values():
    """For each sensor, the smallest sudden move (in its anomaly direction) the engine flags: the model
    scores it below 0 and the change is at least the sensor's deadband."""
    models = fresh_models()
    values = {}
    for sensor, (session, transform) in models.sensors.items():
        base = transform(BATCH[sensor])
        value, step = BATCH[sensor], {"ph": 0.005, "temperature": 0.01, "turbidity": 0.05}[sensor]
        while True:
            value += DIRECTION[sensor] * step
            change = transform(value) - base         # a sudden jump: dev and step are equal
            in_unit = abs(value - BATCH[sensor])
            if models._score(session, change, change) < 0 and in_unit >= ae.ML_DEADBAND.get(sensor, 0.0):
                break
        values[sensor] = round(value + DIRECTION[sensor] * step, 2)   # one notch past the boundary
    return values


def insert_threshold(conn, node, r, limits, required=True):
    alerts = ae.evaluate_thresholds(r["ph"], r["turbidity"], r["temperature"], r.get("level", 55.0),
                                    r.get("flow", 4.0), limits)
    if not alerts:
        if required:
            sys.exit("these readings are inside the node's limits; nothing to alert on")
        return []
    message = " ".join(f"[Threshold - {title}] {text}" for _, title, text in alerts)
    cur = conn.execute(
        """INSERT INTO notification_logs (node_id, is_threshold, is_anomaly, title, message,
           ph_snapshot, turbidity_snapshot, temp_snapshot, params, dss) VALUES (?, 1, 0, ?, ?, ?, ?, ?, ?, ?)""",
        (node, ae.alert_title(alerts), message, r["ph"], r["turbidity"], r["temperature"],
         ",".join(dict.fromkeys(key for key, _, _ in alerts)),
         ae.threshold_dss(r["ph"], r["temperature"], r["turbidity"], r.get("level", 55.0), r.get("flow", 4.0), limits, alerts)))
    return [cur.lastrowid]


def insert_ml(conn, node, level, limits):
    """One ML anomaly through the real models: its threshold alert (if past a limit, as the engine
    would raise) and its DSS diagnosis alerts with the models' real evidence."""
    r = {"min": minimum_values, "typical": lambda: dict(TYPICAL), "max": lambda: dict(MAXIMUM)}[level]()
    models = fresh_models()
    findings = models.evaluate(0, r["ph"], r["turbidity"], r["temperature"])
    flagged = {f.sensor: f.message for f in findings}
    if not flagged:
        sys.exit(f"the models did not flag the {level} readings {r}; nothing to send")
    ids = insert_threshold(conn, node, r, limits, required=False)
    for _, title, sensors, dss in dss_rules.diagnose(r, flagged, limits):
        cur = conn.execute(
            """INSERT INTO notification_logs (node_id, is_threshold, is_anomaly, title, message,
               ph_snapshot, turbidity_snapshot, temp_snapshot, params, severity, dss)
               VALUES (?, 0, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (node, title, dss["summary"], r["ph"], r["turbidity"], r["temperature"], ",".join(sensors),
             dss["severity"], json.dumps(dss)))
        ids.append(cur.lastrowid)
    print(f"  {level} readings: pH {r['ph']}, {r['temperature']} °C, {r['turbidity']} NTU "
          f"(batch: pH {BATCH['ph']}, {BATCH['temperature']} °C, {BATCH['turbidity']} NTU)")
    for f in findings:
        print(f"    {f.sensor}: {f.message}")
    return ids


def record(ids):
    done = json.load(open(LOG)) if os.path.exists(LOG) else []
    with open(LOG, "w") as f:
        json.dump(done + ids, f)


def clear(conn):
    ids = json.load(open(LOG)) if os.path.exists(LOG) else []
    if ids:
        conn.execute(f"DELETE FROM notification_logs WHERE id IN ({','.join(str(int(i)) for i in ids)})")
        conn.commit()
    if os.path.exists(LOG):
        os.remove(LOG)
    print(f"Deleted {len(ids)} simulated alert(s).")


def main():
    p = argparse.ArgumentParser(description="Simulate alert notifications (see the top of this file).")
    p.add_argument("kind", nargs="?", choices=["threshold", "multi", "ml", "levels", "all"], help="which alert to send")
    p.add_argument("--level", choices=["min", "typical", "max"], default="typical",
                   help="ML anomaly strength for 'ml' (default typical)")
    p.add_argument("--node", type=int, default=1, help="node the alert is for (default 1)")
    p.add_argument("--repeat", type=int, default=1, help="send it this many times (default 1)")
    p.add_argument("--every", type=float, default=4.0, help="seconds between alerts (default 4)")
    p.add_argument("--clear", action="store_true", help="delete every alert this script has written")
    args = p.parse_args()

    conn = sqlite3.connect(DB, timeout=10)
    if args.clear:
        clear(conn)
        return
    if not args.kind:
        p.error("give a kind (threshold, multi, ml, levels, all) or --clear")

    ae.log.setLevel(logging.WARNING)
    limits = limits_for(conn, args.node)
    levels = [("ml", lv) for lv in ("min", "typical", "max")]
    jobs = {"threshold": [("threshold", None)], "multi": [("multi", None)], "ml": [("ml", args.level)],
            "levels": levels, "all": [("threshold", None), ("multi", None)] + levels}[args.kind]
    sent = 0
    for _ in range(args.repeat):
        for kind, level in jobs:
            if sent:
                time.sleep(args.every)          # so each one pops up as its own toast
            if kind == "ml":
                ids = insert_ml(conn, args.node, level, limits)
            else:
                ids = insert_threshold(conn, args.node, SCENARIOS[kind], limits)
            conn.commit()
            record(ids)
            titles = [row[0] for row in conn.execute(
                f"SELECT title FROM notification_logs WHERE id IN ({','.join(map(str, ids))})")]
            print(f"node {args.node} · {kind}{' ' + level if level else ''}: {' | '.join(titles)}")
            sent += 1
    print("Open dashboards show these within a second. Remove them later with: --clear")


if __name__ == "__main__":
    main()
