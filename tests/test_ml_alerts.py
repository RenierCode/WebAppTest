"""Regression test: one ML event gives one notification, while every flagged reading stays in ml_flags.

Run from anywhere:   python tests/test_ml_alerts.py

The real anomaly_engine.handle_data runs against an in-memory SQLite database, with scripted
model findings and a controlled clock. Only the LoRa library and onnxruntime are stubbed.
"""
import logging
import os
import sys, sqlite3
from unittest.mock import MagicMock

for name in ("SX127x", "SX127x.LoRa", "SX127x.board_config", "onnxruntime"):
    sys.modules[name] = MagicMock()
sys.modules["SX127x.LoRa"].LoRa = type("LoRa", (), {"__init__": lambda self, *a, **k: None})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anomaly_engine as ae
ae.log.setLevel(logging.CRITICAL)
ae.radio_send = lambda radio, text: None

class ScriptedModels:                   # flags exactly the sensors named in `next`
    next = ()
    def evaluate(self, *a):
        return [ae.AnomalyFinding(s, f"{s} flagged") for s in ScriptedModels.next]

clock = [0.0]
ae.time.monotonic = lambda: clock[0]

def fresh():
    ae._last_ml_flag.clear()
    clock[0] = 1000.0
    conn = sqlite3.connect(":memory:")
    ae.init_db(conn)
    return conn

pid = [0]
def packet(conn, flags=(), node=1, ph=7.8, every=30):
    """One packet `every` seconds after the previous one; `flags` = sensors the models flag."""
    clock[0] += every
    pid[0] += 1
    ScriptedModels.next = flags
    ae.handle_data(conn, ScriptedModels(), None, dict(
        node=str(node), pid=str(pid[0]), ph=str(ph), turb="20.0", temp="27.0",
        lvl="50.0", flow="4.0", vol="1.00", time="12:00:00"))

def notifications(conn):
    return conn.execute("SELECT node_id, is_threshold, is_anomaly, params FROM notification_logs ORDER BY id").fetchall()

def marked(conn):
    return conn.execute("SELECT COUNT(*) FROM telemetry_logs WHERE ml_flags IS NOT NULL").fetchone()[0]

print("A. pH flagged on 30 packets in a row (15 min at 30 s): one notification, 30 chart markers")
conn = fresh()
for _ in range(30): packet(conn, ("ph",))
assert notifications(conn) == [(1, 0, 1, "ph")], notifications(conn)
assert marked(conn) == 30
print("  PASS ", notifications(conn), "| markers:", marked(conn))

print("B. quiet for more than 5 min, then flagged again: a new notification")
for _ in range(11): packet(conn)                   # 5.5 min without a pH flag
packet(conn, ("ph",))
assert len(notifications(conn)) == 2
print("  PASS  notifications:", len(notifications(conn)))

print("C. a gap shorter than 5 min is the same event")
conn = fresh()
packet(conn, ("ph",)); packet(conn); packet(conn); packet(conn, ("ph",))
assert len(notifications(conn)) == 1
print("  PASS  notifications:", len(notifications(conn)))

print("D. another sensor flagged during a pH event gets its own notification")
conn = fresh()
packet(conn, ("ph",)); packet(conn, ("ph",)); packet(conn, ("ph", "temperature")); packet(conn, ("ph", "temperature"))
assert [n[3] for n in notifications(conn)] == ["ph", "temperature"], notifications(conn)
print("  PASS ", notifications(conn))

print("E. nodes are independent")
conn = fresh()
packet(conn, ("ph",), node=1); packet(conn, ("ph",), node=2); packet(conn, ("ph",), node=1)
assert [n[0] for n in notifications(conn)] == [1, 2]
print("  PASS ", notifications(conn))

print("F. threshold alerts still notify on every packet; a repeated ML flag only adds its marker")
conn = fresh()
packet(conn, ("ph",), ph=9.5); packet(conn, ("ph",), ph=9.5); packet(conn, ("ph",), ph=9.5)
n = notifications(conn)
# threshold and ML diagnosis are separate rows: (threshold), (ML), then threshold only
assert [(t, a) for _, t, a, _ in n] == [(1, 0), (0, 1), (1, 0), (1, 0)], n
assert marked(conn) == 3
print("  PASS ", n, "| markers:", marked(conn))

import json
def diagnoses(conn):
    return [(r[0], r[1], json.loads(r[2])["rule"]) for r in conn.execute(
        "SELECT severity, params, dss FROM notification_logs WHERE dss IS NOT NULL AND is_anomaly = 1 ORDER BY id")]

def reading(conn, flags, ph=7.8, turb="20.0", temp="27.0"):
    clock[0] += 30
    pid[0] += 1
    ScriptedModels.next = flags
    ae.handle_data(conn, ScriptedModels(), None, dict(
        node="1", pid=str(pid[0]), ph=str(ph), turb=turb, temp=temp,
        lvl="50.0", flow="4.0", vol="1.00", time="12:00:00"))

print("G. DSS: pH low + temperature low + turbidity high, all flagged -> one CRITICAL plant-wide diagnosis")
conn = fresh()
reading(conn, ("ph", "temperature", "turbidity"), ph=6.2, temp="24.0", turb="80.0")
assert diagnoses(conn) == [("CRITICAL", "ph,temperature,turbidity", "cold_acid_turbidity")], diagnoses(conn)
print("  PASS ", diagnoses(conn))

print("H. DSS: pH low + turbidity high, temperature flagged but in range -> compound + temperature single")
conn = fresh()
reading(conn, ("ph", "temperature", "turbidity"), ph=6.2, temp="27.0", turb="80.0")
assert diagnoses(conn) == [("CRITICAL", "ph,turbidity", "acid_turbidity"),
                           ("WARNING", "temperature", "single_temperature")], diagnoses(conn)
print("  PASS ", diagnoses(conn))

print("I. DSS: past the limit but not flagged by the ML -> no diagnosis (threshold alert only)")
conn = fresh()
reading(conn, (), ph=6.2, turb="80.0")
assert diagnoses(conn) == [], diagnoses(conn)
print("  PASS  no diagnosis")

print("K. threshold alerts carry guidance for exactly the limits crossed")
conn = fresh()
reading(conn, (), ph=6.2, turb="80.0")
g = json.loads(conn.execute("SELECT dss FROM notification_logs WHERE is_threshold = 1").fetchone()[0])
assert g["rule"] == "threshold:ph_low,turbidity_high", g["rule"]
assert all(c["text"].startswith(("Low pH:", "High turbidity:")) for c in g["causes"]), g["causes"]
print("  PASS ", g["rule"])

print("J. DSS: every cited reference id exists and has a URL")
import dss_rules
for rule in list(dss_rules.RULES.values()) + list(dss_rules.SINGLE_RULES.values()) + list(dss_rules.THRESHOLD_GUIDANCE.values()):
    for section in ("causes", "impact", "actions"):
        for item in rule[section]:
            assert item["refs"], ("uncited", item["text"])
            for ref in item["refs"]:
                assert dss_rules.REFERENCES[ref]["url"].startswith("https://")
    for ref in rule["incidents"]:
        assert dss_rules.REFERENCES[ref]["kind"] == "incident"
for rule in list(dss_rules.RULES.values()) + list(dss_rules.SINGLE_RULES.values()):
    refs = {r for section in ("causes", "impact", "actions") for item in rule[section] for r in item["refs"]}
    assert any(dss_rules.REFERENCES[r]["scope"] == "national" for r in refs), rule["title"]
assert all(r["scope"] in ("national", "international") for r in dss_rules.REFERENCES.values())
print("  PASS  every cause, impact and action cites a reference; every rule cites a national one")

print("\nALL SCENARIOS PASSED")
