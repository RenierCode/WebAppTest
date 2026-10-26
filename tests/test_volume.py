"""Regression test for anomaly_engine's volume handling.

The node reports Vol as a running total (resets after 60 s with no flow). The gateway stores it
exactly as sent, in both interval_volume and volume_total (the Dashboard's volume card).

Run from anywhere:   python tests/test_volume.py

The real anomaly_engine module is imported and its real handle_data runs
against an in-memory SQLite database. Only the Raspberry-Pi-only LoRa library
and onnxruntime are stubbed, so this needs no hardware and no model files.
"""
import logging
import os
import sys, sqlite3
from unittest.mock import MagicMock

# --- stub ONLY the hardware / ML imports; everything else is the real module ---
for name in ("SX127x", "SX127x.LoRa", "SX127x.board_config", "onnxruntime"):
    sys.modules[name] = MagicMock()
sys.modules["SX127x.LoRa"].LoRa = type("LoRa", (), {"__init__": lambda self, *a, **k: None})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anomaly_engine as ae
ae.log.setLevel(logging.CRITICAL)   # keep the output readable

class FakeModels:                       # no ML findings -> isolates the volume logic
    def evaluate(self, *a):
        return []

acks = []
ae.radio_send = lambda radio, text: acks.append(text)     # capture instead of transmitting

def fresh():
    acks.clear()
    conn = sqlite3.connect(":memory:")
    ae.init_db(conn)
    return conn

pid = [0]
def packet(conn, node, vol):
    pid[0] += 1
    f = dict(node=str(node), pid=str(pid[0]), ph="7.4", turb="12.0", temp="27.0",
             lvl="50.0", flow="4.0", vol=f"{vol:.2f}", time="12:00:00")
    ae.handle_data(conn, FakeModels(), None, f)

print("A. Vol is stored exactly as the node sent it, in both columns")
conn = fresh()
sent = [0.0, 10.0, 20.0, 30.0, 30.0, 0.0, 1.5]
for v in sent: packet(conn, 1, v)
rows = conn.execute("SELECT interval_volume, volume_total FROM telemetry_logs ORDER BY id").fetchall()
assert [r[0] for r in rows] == sent, rows
assert [r[1] for r in rows] == sent, rows
print("  PASS  interval_volume and volume_total:", sent)

print("B. two nodes interleaved keep their own values")
conn = fresh()
for a, b in ((1.0, 10.0), (2.0, 10.5)): packet(conn, 1, a); packet(conn, 2, b)
by_node = lambda n: [r[0] for r in conn.execute(
    "SELECT interval_volume FROM telemetry_logs WHERE node_id=? ORDER BY id", (n,))]
assert by_node(1) == [1.0, 2.0] and by_node(2) == [10.0, 10.5]
print("  PASS  node 1", by_node(1), "| node 2", by_node(2))

print("C. every DATA packet is stored and ACKed")
conn = fresh()
for v in (0.0, 0.5, 1.0): packet(conn, 1, v)
assert len(acks) == 3 and all(a.startswith("ACK,") for a in acks)
assert conn.execute("SELECT COUNT(*) FROM telemetry_logs").fetchone()[0] == 3
print("  PASS  3 rows stored, 3 ACK replies dispatched")

print("D. a database created BEFORE volume_total existed upgrades itself (gateway)")
old = sqlite3.connect(":memory:")
old.execute("""CREATE TABLE telemetry_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL,
    packet_id INTEGER NOT NULL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, ph REAL NOT NULL,
    turbidity REAL NOT NULL, temperature REAL NOT NULL, water_level REAL NOT NULL, flowrate REAL NOT NULL,
    interval_volume REAL NOT NULL, is_anomaly INTEGER DEFAULT 0, threshold_flags TEXT, ml_flags TEXT)""")
old.execute("INSERT INTO telemetry_logs (node_id,packet_id,ph,turbidity,temperature,water_level,flowrate,interval_volume)"
            " VALUES (1,1,7,10,27,50,3,2.5)")
ae.init_db(old)
cols = {r[1] for r in old.execute("PRAGMA table_info(telemetry_logs)")}
assert "volume_total" in cols
assert old.execute("SELECT volume_total FROM telemetry_logs").fetchone()[0] is None
packet(old, 1, 4.0)
assert old.execute("SELECT volume_total FROM telemetry_logs ORDER BY id DESC").fetchone()[0] == 4.0
print("  PASS  column added, the old row reads NULL (the card shows an em dash), new rows work")
ae.init_db(old)                                    # running it twice must be harmless
print("  PASS  init_db is safe to run again")

print("E. ...and the same for server.py's own startup migration")
import tempfile
for name in ("fastapi", "fastapi.middleware", "fastapi.middleware.cors", "fastapi.responses",
             "fastapi.staticfiles", "aiosqlite", "pydantic"):
    sys.modules[name] = MagicMock()
dbfile = os.path.join(tempfile.mkdtemp(), "old.db")
c = sqlite3.connect(dbfile)
c.execute("""CREATE TABLE telemetry_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL,
    packet_id INTEGER NOT NULL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, ph REAL NOT NULL,
    turbidity REAL NOT NULL, temperature REAL NOT NULL, water_level REAL NOT NULL, flowrate REAL NOT NULL,
    interval_volume REAL NOT NULL, is_anomaly INTEGER DEFAULT 0, threshold_flags TEXT, ml_flags TEXT)""")
c.commit(); c.close()
os.environ["WASTEWATER_DB"] = dbfile
import server
server.init_schema()
cols = {r[1] for r in sqlite3.connect(dbfile).execute("PRAGMA table_info(telemetry_logs)")}
assert "volume_total" in cols, cols
server.init_schema()                               # idempotent
print("  PASS  server.py adds the column to an existing database too, and is re-runnable")

print("F. a brand-new database (schema.sql) has the column from the start")
fresh_db = os.path.join(tempfile.mkdtemp(), "new.db")
os.environ["WASTEWATER_DB"] = fresh_db; server.DB_PATH = fresh_db
server.init_schema()
assert "volume_total" in {r[1] for r in sqlite3.connect(fresh_db).execute("PRAGMA table_info(telemetry_logs)")}
print("  PASS  schema.sql includes volume_total")

print("\nALL SCENARIOS PASSED")
