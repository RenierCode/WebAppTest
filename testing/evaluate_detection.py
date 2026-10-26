"""
TEST-ONLY: how accurately each detector finds the anomalies in a test database whose truth is known
(the sim_labels table written by generate_explainer_test_db.py).

Detectors compared, reading by reading:
    ML model          the engine's flags (telemetry_logs.ml_flags)
    Empirical rule    any sensor more than K standard deviations from its batch mean (|z| > K, from
                      telemetry_logs.ml_details; default K = 3, the 99.7 % band)
    Threshold rules   the configured limits (telemetry_logs.threshold_flags)
    System            either of the engine's layers (telemetry_logs.is_anomaly)

It also checks the empirical rule's own assumption: on the normal readings, about 68 / 95 / 99.7 %
should fall within 1 / 2 / 3 standard deviations if they are roughly normally distributed.

    python testing/evaluate_detection.py                         # database_explainer_test.db
    python testing/evaluate_detection.py other.db --k 2
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def metrics(pairs):
    """pairs: (truth, predicted) booleans -> confusion counts and the usual scores."""
    tp = sum(1 for t, p in pairs if t and p)
    fp = sum(1 for t, p in pairs if not t and p)
    fn = sum(1 for t, p in pairs if t and not p)
    tn = sum(1 for t, p in pairs if not t and not p)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    return {"TP": tp, "FP": fp, "FN": fn, "TN": tn, "precision": precision, "recall": recall, "F1": f1,
            "accuracy": (tp + tn) / len(pairs), "false alarm rate": fp / (fp + tn) if fp + tn else None}


def fmt(v):
    return "-" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db", nargs="?", default=os.path.join(ROOT, "database_explainer_test.db"))
    p.add_argument("--k", type=float, default=3.0, help="empirical rule: flag |z| above this (default 3)")
    args = p.parse_args()
    c = sqlite3.connect(args.db)
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'sim_labels'").fetchone():
        sys.exit("no sim_labels table: build the database with testing/generate_explainer_test_db.py")
    rows = c.execute("""SELECT t.ml_flags, t.threshold_flags, t.is_anomaly, t.ml_details, l.label, l.kind
                        FROM telemetry_logs t JOIN sim_labels l ON l.telemetry_id = t.id ORDER BY t.id""").fetchall()

    readings = []
    for ml, threshold, any_flag, details, label, kind in rows:
        details = json.loads(details) if details else {}
        zs = {s: d["z"] for s, d in details.items() if d.get("z") is not None}
        if not zs:
            continue                  # the model's warm-up: nothing judged yet, by any statistical method
        readings.append({"truth": label == "anomaly", "kind": kind, "zs": zs, "ml": bool(ml),
                         "rule": any(abs(z) > args.k for z in zs.values()), "threshold": bool(threshold),
                         "system": bool(any_flag)})
    anomalies = sum(r["truth"] for r in readings)
    print(f"{args.db}\n{len(readings)} judged readings: {anomalies} anomalies, {len(readings) - anomalies} normal\n")

    detectors = [("ML model", "ml"), (f"Empirical rule |z|>{args.k:g}", "rule"),
                 ("Threshold rules", "threshold"), ("System (ML or threshold)", "system")]
    cols = ["TP", "FP", "FN", "TN", "precision", "recall", "F1", "accuracy", "false alarm rate"]
    print(f"{'detector':26}" + "".join(f"{c:>7}" for c in cols[:4]) + "".join(f"{c:>18}" for c in cols[4:]))
    for name, key in detectors:
        m = metrics([(r["truth"], r[key]) for r in readings])
        print(f"{name:26}" + "".join(f"{fmt(m[c]):>7}" for c in cols[:4]) + "".join(f"{fmt(m[c]):>18}" for c in cols[4:]))

    print("\nCaught, by kind of event (anomaly kinds should be caught; small_bump should not):")
    by_kind = defaultdict(list)
    for r in readings:
        if r["kind"] != "stable":
            by_kind[r["kind"]].append(r)
    for kind, rs in sorted(by_kind.items()):
        print(f"  {kind:12} {len(rs):3} events   " + "   ".join(
            f"{name.split(' (')[0]}: {sum(r[key] for r in rs)}" for name, key in detectors[:3]))

    print("\nEmpirical rule check on the NORMAL readings (expect about 68 / 95 / 99.7 %):")
    for sensor in ("ph", "turbidity", "temperature"):
        zs = [abs(r["zs"][sensor]) for r in readings if not r["truth"] and sensor in r["zs"]]
        if zs:
            within = [100 * sum(z <= k for z in zs) / len(zs) for k in (1, 2, 3)]
            print(f"  {sensor:12} within 1 sd: {within[0]:5.1f} %   2 sd: {within[1]:5.1f} %   3 sd: {within[2]:5.1f} %")


if __name__ == "__main__":
    main()
