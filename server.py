"""
Wastewater Hub — supervisory web server (Process 2)

Runs alongside edge_engine.py on the Raspberry Pi. This process never
touches the LoRa radio or runs the ONNX model directly — it only reads
database.db, and writes to it solely for operator actions (acknowledging
alerts, editing thresholds, renaming nodes). All sensor ingestion, ML
inference, and rule-based threshold evaluation happen in edge_engine.py.

Schema note: table/column names below MUST match edge_engine.py's init_db()
exactly, since it's the process that creates and writes telemetry_logs /
notification_logs / system_thresholds. See schema.sql for the shared
reference copy.

Run:
    uvicorn server:app --host 0.0.0.0 --port 3000
"""

import asyncio
import csv
import math
import os
import re
import sqlite3
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # headless -- no display on the Pi, must be set before importing pyplot
import matplotlib.pyplot as plt

import aiosqlite

import dss_rules
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WASTEWATER_DB", os.path.join(BASE_DIR, "database.db"))
REPORTS_DIR = os.environ.get("WASTEWATER_REPORTS", os.path.join(BASE_DIR, "reports"))
GATEWAY_HOST = os.environ.get("WASTEWATER_GATEWAY_IP", "192.168.4.1")
GATEWAY_PORT = int(os.environ.get("WASTEWATER_GATEWAY_PORT", "3000"))
# The Pi's Wi-Fi hotspot, for the Reports page's "join the hotspot" QR code (see SETUP_RASPI.md §6).
# Without a password (or WASTEWATER_HOTSPOT_OPEN=1 for an open hotspot) the page shows the name only.
HOTSPOT_SSID = os.environ.get("WASTEWATER_HOTSPOT_SSID", "WastewaterHub")
HOTSPOT_PASSWORD = os.environ.get("WASTEWATER_HOTSPOT_PASSWORD", "choose-a-real-password-here")
HOTSPOT_OPEN = os.environ.get("WASTEWATER_HOTSPOT_OPEN") == "1"
# Saved PDF reports and CSV exports are deleted this many days after they were written, so the SD
# card doesn't fill up. Checked at startup and then every hour.
REPORT_RETENTION_DAYS = float(os.environ.get("WASTEWATER_REPORT_RETENTION_DAYS", "7"))
FRONTEND_DIST = os.environ.get("WASTEWATER_FRONTEND_DIST", os.path.join(BASE_DIR, "frontend", "dist"))

os.makedirs(REPORTS_DIR, exist_ok=True)

TIMEFRAME_MINUTES = {"1h": 60, "6h": 360, "24h": 1440}

# Must match anomaly_engine.py's DEFAULT_THRESHOLDS / DEFAULT_ML_CONTAMINATION.
# Written explicitly because an existing table keeps the column defaults of
# whichever schema first created it.
DEFAULT_THRESHOLDS = dict(min_ph=7.0, max_ph=8.5, max_turbidity=50.0, min_temp=26.0, max_temp=30.0,
                          max_level=90.0, min_level=5.0, max_flow=10.0)
DEFAULT_ML_CONTAMINATION = 0.02


# --------------------------------------------------------------------------
# Startup: ensure schema exists (edge_engine.py owns it, this is a safety
# net if server.py happens to start first), launch the DB-watch broadcaster
# --------------------------------------------------------------------------
def init_schema():
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    with open(schema_path) as f:
        conn.executescript(f.read())
    # Databases created before the per-parameter flag columns existed
    columns = {row[1] for row in conn.execute("PRAGMA table_info(telemetry_logs)")}
    for column in ("threshold_flags", "ml_flags"):
        if column not in columns:
            conn.execute(f"ALTER TABLE telemetry_logs ADD COLUMN {column} TEXT")
    # The node's running Vol exactly as sent (anomaly_engine.py writes it)
    if "volume_total" not in columns:
        conn.execute("ALTER TABLE telemetry_logs ADD COLUMN volume_total REAL")
    # The ML's working per sensor, as JSON (anomaly_engine.py writes it)
    if "ml_details" not in columns:
        conn.execute("ALTER TABLE telemetry_logs ADD COLUMN ml_details TEXT")
    # notification_logs.params: which parameters an alert is about, same keys
    # as telemetry_logs.threshold_flags ("ph,turbidity"). anomaly_engine.py
    # writes it; tag_notification_params() fills rows from before that, or
    # from edge_engine.py, by parsing the message text.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(notification_logs)")}
    # severity + dss: the anomaly engine's DSS diagnoses (dss_rules.py), dss as JSON
    for column in ("params", "severity", "dss"):
        if column not in columns:
            conn.execute(f"ALTER TABLE notification_logs ADD COLUMN {column} TEXT")
    # The alert list: active rows, newest first
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notif_active_ts ON notification_logs(is_acknowledged, timestamp)"
    )
    # Keeps the tagging pass proportional to new rows, not the whole table
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notif_untagged ON notification_logs(id) WHERE params IS NULL"
    )
    tag_notification_params(conn)
    conn.commit()
    conn.close()


# Threshold titles as written by evaluate_thresholds() in the engines
THRESHOLD_TITLE_PARAMS = {
    "Tank Overflow Risk Detected": "level",
    "Pump Dry-Run Hazard": "level",
    "Pipe Over-Pressure / Burst Hazard": "flow",
    "Acidic Contamination Spike": "ph",
    "Alkaline Contamination Spike": "ph",
    "Elevated Turbidity": "turbidity",
    "Low Temperature Advisory": "temperature",
    "Thermal Drift Advisory": "temperature",
}
ML_SENSOR_PARAMS = {"ph": "ph", "turbidity": "turbidity", "temperature": "temperature"}
NOTIFICATION_PARAMS = ("ph", "turbidity", "temperature", "level", "flow")
PARAM_NAMES = {"ph": "pH", "turbidity": "Turbidity", "temperature": "Temperature", "level": "Water Level", "flow": "Flow"}


def alert_display_title(alert: dict) -> str:
    """Alerts saved before the engine named the sensors read "Multiple Anomaly Conditions Detected (3)";
    rebuild "Multiple Anomaly Conditions: pH, Turbidity, Water Level" from their params."""
    title = alert.get("title") or ""
    if title.startswith("Multiple Anomaly Conditions") and alert.get("params"):
        names = [PARAM_NAMES.get(p, p) for p in alert["params"].split(",") if p]
        if names:
            return f"Threshold breach: {', '.join(names)}"
    return title
# "[Threshold — Elevated Turbidity] ..." / "[ML Model - Turbidity] ..." (em dash or hyphen)
_THRESHOLD_TAG = re.compile(r"\[Threshold\W+([^\]]+)\]")
_ML_TAG = re.compile(r"\[ML Model\W+([^\]]+)\]")


def notification_params(message: str) -> str:
    found = [THRESHOLD_TITLE_PARAMS.get(t.strip()) for t in _THRESHOLD_TAG.findall(message)]
    found += [ML_SENSOR_PARAMS.get(s.strip().lower()) for s in _ML_TAG.findall(message)]
    # "" (not NULL) when nothing matched, e.g. edge_engine's untagged
    # "[ML Model] ..." text, so the row isn't rescanned every request
    return ",".join(dict.fromkeys(p for p in found if p))


def tag_notification_params(conn) -> None:
    """Fills params on rows written without it (older rows, edge_engine.py)."""
    rows = conn.execute("SELECT id, message FROM notification_logs WHERE params IS NULL").fetchall()
    if rows:
        conn.executemany(
            "UPDATE notification_logs SET params = ? WHERE id = ?",
            [(notification_params(message), row_id) for row_id, message in rows],
        )


class ConnectionManager:
    """Tracks live WebSocket clients for the telemetry broadcast."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def db_watch_loop():
    """
    Polls the database once a second and pushes what changed to every WebSocket client, so
    browsers never poll on their own:
      {"type": "telemetry", "data": row}      each new telemetry_logs row
      {"type": "notifications"}               new alert rows (acknowledgements are pushed by
                                              the acknowledge route itself)
      {"type": "node_status", "data": [...]}  a node crossing NODE_STALE_SECONDS either way;
                                              clients mark a node online themselves on its packets
    Polling (rather than a filesystem watch) is used because anomaly_engine.py commits via plain
    sqlite3, and a short poll interval is cheap and reliable on the Pi's local disk. WAL mode means
    this read never blocks anomaly_engine.py's writes, or vice versa.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COALESCE(MAX(id), 0) AS m FROM telemetry_logs")
        last_seen_id = (await cur.fetchone())["m"]
        cur = await db.execute("SELECT COALESCE(MAX(id), 0) AS m FROM notification_logs")
        last_alert_id = (await cur.fetchone())["m"]
        cur = await db.execute("SELECT node_id, MAX(timestamp) AS t FROM telemetry_logs GROUP BY node_id")
        node_last_seen = {r["node_id"]: r["t"] for r in await cur.fetchall()}
        node_online = {}

        while True:
            await asyncio.sleep(1.0)
            try:
                cur = await db.execute(
                    "SELECT * FROM telemetry_logs WHERE id > ? ORDER BY id ASC",
                    (last_seen_id,),
                )
                for r in await cur.fetchall():
                    last_seen_id = r["id"]
                    # max(): a late LOG reading carries its (older) measurement time
                    node_last_seen[r["node_id"]] = max(node_last_seen.get(r["node_id"]) or "", r["timestamp"])
                    await manager.broadcast({"type": "telemetry", "data": dict(r)})

                cur = await db.execute("SELECT COALESCE(MAX(id), 0) AS m FROM notification_logs")
                newest_alert = (await cur.fetchone())["m"]
                if newest_alert != last_alert_id:
                    last_alert_id = newest_alert
                    await manager.broadcast({"type": "notifications"})

                changed = []
                for node_id, last_seen in node_last_seen.items():
                    status = _node_status(last_seen)
                    if node_online.get(node_id) != status["is_online"]:
                        node_online[node_id] = status["is_online"]
                        changed.append({"node_id": node_id, **status})
                if changed:
                    await manager.broadcast({"type": "node_status", "data": changed})
            except sqlite3.Error:
                continue              # e.g. the database being replaced; try again next second


def delete_old_reports() -> int:
    """Delete saved reports/exports (.pdf/.csv in REPORTS_DIR) older than REPORT_RETENTION_DAYS."""
    cutoff = datetime.now().timestamp() - REPORT_RETENTION_DAYS * 86400
    deleted = 0
    for entry in os.scandir(REPORTS_DIR):
        if (entry.is_file() and os.path.splitext(entry.name)[1].lower() in (".pdf", ".csv")
                and entry.stat().st_mtime < cutoff):
            try:
                os.remove(entry.path)
                deleted += 1
            except OSError:
                pass                  # e.g. removed meanwhile; the next pass tries again
    if os.path.isdir(PREVIEW_CACHE):             # page images of reports that no longer exist
        for entry in os.scandir(PREVIEW_CACHE):
            if not os.path.exists(os.path.join(REPORTS_DIR, entry.name.rsplit(".", 3)[0])):
                try:
                    os.remove(entry.path)
                except OSError:
                    pass
    if deleted:
        print(f"Deleted {deleted} saved report(s) older than {REPORT_RETENTION_DAYS:g} days", flush=True)
    return deleted


async def report_cleanup_loop():
    while True:
        await asyncio.to_thread(delete_old_reports)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()
    watcher = asyncio.create_task(db_watch_loop())
    cleaner = asyncio.create_task(report_cleanup_loop())
    yield
    watcher.cancel()
    cleaner.cancel()


app = FastAPI(title="Wastewater Hub API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local network only — kiosk + hotspot clients
    allow_methods=["*"],
    allow_headers=["*"],
)


async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


# --------------------------------------------------------------------------
# WebSocket: live telemetry stream
# --------------------------------------------------------------------------
@app.websocket("/ws/telemetry/live")
async def ws_telemetry_live(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Clients don't need to send anything; keep the socket alive.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# --------------------------------------------------------------------------
# Telemetry REST routes
# --------------------------------------------------------------------------
@app.get("/api/telemetry/latest")
async def telemetry_latest(node_id: int):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM telemetry_logs WHERE node_id = ? ORDER BY timestamp DESC LIMIT 1",
            (node_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(404, f"No telemetry for node {node_id}")
        return dict(row)
    finally:
        await db.close()


HISTORY_MAX_POINTS = 2000


@app.get("/api/telemetry/history")
async def telemetry_history(node_id: int, timeframe: str = "1h", start: Optional[str] = None,
                            end: Optional[str] = None, max_points: Optional[int] = None):
    """Chart series: the last `timeframe` (1h / 6h / 24h), or start..end (UTC "YYYY-MM-DD HH:MM:SS",
    the expanded chart's date range). With max_points, a longer series is thinned to about that
    many readings -- evenly spaced, but every flagged reading is kept so no marker is lost."""
    if start or end:
        clause, params = "AND timestamp >= ? AND timestamp <= ?", [start or "0000", end or "9999"]
    elif timeframe in TIMEFRAME_MINUTES:
        since = datetime.utcnow() - timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
        clause, params = "AND timestamp >= ?", [since.isoformat(sep=" ", timespec="seconds")]
    else:
        raise HTTPException(400, "timeframe must be one of 1h, 6h, 24h (or give start/end)")
    db = await get_db()
    try:
        cur = await db.execute(
            f"SELECT * FROM telemetry_logs WHERE node_id = ? {clause} ORDER BY timestamp ASC",
            (node_id, *params),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    limit = min(max_points or len(rows), HISTORY_MAX_POINTS * 5)
    if len(rows) > limit > 0:
        every = len(rows) / limit
        keep = {int(i * every) for i in range(limit)} | {len(rows) - 1}
        rows = [r for i, r in enumerate(rows) if i in keep or r.get("is_anomaly")]
    return rows


@app.get("/api/telemetry/baseline")
async def telemetry_baseline(node_id: int):
    """The ML models' current baseline per sensor: the batch median (at most 6 h) each new reading is compared
    with (written by anomaly_engine.py on every packet), how many readings it covers, and whether
    the models judge that sensor yet (they wait for 30 readings). {} until the engine has one."""
    db = await get_db()
    try:
        try:
            cur = await db.execute(
                "SELECT sensor, median, readings, ready, updated_at FROM ml_baselines WHERE node_id = ?",
                (node_id,),
            )
        except sqlite3.OperationalError:          # no such table: the engine hasn't written one yet
            return {}
        return {r["sensor"]: {"median": r["median"], "readings": r["readings"], "ready": bool(r["ready"]),
                              "updated_at": r["updated_at"]} for r in await cur.fetchall()}
    finally:
        await db.close()


# Historical Logs readings filter. threshold / ml include readings flagged by both; "both" is no
# longer offered on the page but still accepted.
TELEMETRY_FLAG_FILTERS = {
    "all": None,
    "none": "is_anomaly = 0",
    "flagged": "is_anomaly = 1",
    "threshold": "threshold_flags IS NOT NULL",
    "ml": "ml_flags IS NOT NULL",
    "both": "threshold_flags IS NOT NULL AND ml_flags IS NOT NULL",
}


def _telemetry_filter(node_id: Optional[int], start: Optional[str], end: Optional[str], flag: str):
    """WHERE clause + params shared by the Historical Logs table and its CSV export."""
    if flag not in TELEMETRY_FLAG_FILTERS:
        raise HTTPException(400, f"flag must be one of {', '.join(TELEMETRY_FLAG_FILTERS)}")
    clauses, params = [], []
    if TELEMETRY_FLAG_FILTERS[flag]:
        clauses.append(TELEMETRY_FLAG_FILTERS[flag])
    if node_id is not None:
        clauses.append("node_id = ?")
        params.append(node_id)
    if start:
        clauses.append("timestamp >= ?")
        params.append(start)
    if end:
        clauses.append("timestamp <= ?")
        params.append(end)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


@app.get("/api/telemetry/rows")
async def telemetry_rows(
    node_id: Optional[int] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    flag: str = "all",
    page: int = 1,
    page_size: int = 50,
):
    """Powers the Historical SQL Logs (Database Inspector) view: filter + pagination.

    flag: all | flagged (any alert) | threshold (rules only) | ml (model only) | both.
    Rows written before threshold_flags/ml_flags existed only match all/flagged.
    """
    where, params = _telemetry_filter(node_id, start, end, flag)

    db = await get_db()
    try:
        count_cur = await db.execute(f"SELECT COUNT(*) AS c FROM telemetry_logs {where}", params)
        total = (await count_cur.fetchone())["c"]
        offset = (page - 1) * page_size
        cur = await db.execute(
            f"SELECT * FROM telemetry_logs {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        )
        rows = await cur.fetchall()
        return {"total": total, "page": page, "page_size": page_size, "rows": [dict(r) for r in rows]}
    finally:
        await db.close()


# --------------------------------------------------------------------------
# Notifications REST routes
# --------------------------------------------------------------------------
@app.get("/api/notifications/active")
async def notifications_active():
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM notification_logs WHERE is_acknowledged = 0 ORDER BY timestamp DESC"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


# Alerts page type filter: threshold / ml include an alert flagged by both (older alerts could be;
# new threshold and ML alerts are separate rows). "both" is no longer offered but still accepted.
NOTIFICATION_TYPE_CLAUSES = {
    "all": "",
    "threshold": " AND is_threshold = 1",
    "ml": " AND is_anomaly = 1",
    "both": " AND is_threshold = 1 AND is_anomaly = 1",
}


@app.get("/api/notifications")
async def notifications_list(
    node_id: Optional[int] = None,
    alert_type: str = "all",
    param: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    since_id: Optional[int] = None,
    focus_id: Optional[int] = None,
):
    """
    One page of active alerts plus the DSS header counts. The counts follow
    the node and parameter filters; the list additionally follows alert_type.
    A page past the end is clamped to the last page, so acknowledging the
    only alert on the last page doesn't leave the client on an empty one.
    since_id adds new_count: active alerts (unfiltered) with id > since_id,
    which the dashboard's new-alert toasts use.
    focus_id overrides page with the page holding that alert under these
    filters (focus_found says whether it is still active and matches).
    """
    if alert_type not in NOTIFICATION_TYPE_CLAUSES:
        raise HTTPException(400, f"alert_type must be one of {', '.join(NOTIFICATION_TYPE_CLAUSES)}")
    if param is not None and param not in NOTIFICATION_PARAMS:
        raise HTTPException(400, f"param must be one of {', '.join(NOTIFICATION_PARAMS)}")
    page_size = max(1, min(page_size, 100))

    where = "is_acknowledged = 0"
    params: list = []
    if node_id is not None:
        where += " AND node_id = ?"
        params.append(node_id)
    if param:
        # Exact match inside the comma list ("ph" must not match "phx")
        where += " AND (',' || params || ',') LIKE ?"
        params.append(f"%,{param},%")

    db = await get_db()
    try:
        # Tag rows written without params (older rows, edge_engine.py)
        cur = await db.execute("SELECT id, message FROM notification_logs WHERE params IS NULL")
        untagged = await cur.fetchall()
        if untagged:
            await db.executemany(
                "UPDATE notification_logs SET params = ? WHERE id = ?",
                [(notification_params(r["message"]), r["id"]) for r in untagged],
            )
            await db.commit()

        cur = await db.execute(
            f"""SELECT COUNT(*) AS active,
                       COALESCE(SUM(is_threshold = 1), 0) AS threshold,
                       COALESCE(SUM(is_anomaly = 1), 0) AS ml,
                       COALESCE(SUM(is_threshold = 1 AND is_anomaly = 1), 0) AS both_,
                       MAX(timestamp) AS latest
                FROM notification_logs WHERE {where}""",
            params,
        )
        c = await cur.fetchone()
        counts = {"active": c["active"], "threshold": c["threshold"], "ml": c["ml"],
                  "both": c["both_"], "latest_timestamp": c["latest"]}
        # Each type filter's total is one of these counts, so no second COUNT(*) is needed
        total = {
            "all": counts["active"],
            "threshold": counts["threshold"],
            "ml": counts["ml"],
            "both": counts["both"],
        }[alert_type]

        type_clause = NOTIFICATION_TYPE_CLAUSES[alert_type]
        focus_found = None
        if focus_id is not None:
            cur = await db.execute(
                f"SELECT timestamp FROM notification_logs WHERE id = ? AND {where}{type_clause}",
                (focus_id, *params),
            )
            target = await cur.fetchone()
            focus_found = target is not None
            if target:
                # Rows ahead of it in ORDER BY timestamp DESC, id DESC
                cur = await db.execute(
                    f"""SELECT COUNT(*) AS c FROM notification_logs WHERE {where}{type_clause}
                        AND (timestamp > ? OR (timestamp = ? AND id > ?))""",
                    (*params, target["timestamp"], target["timestamp"], focus_id),
                )
                page = (await cur.fetchone())["c"] // page_size + 1

        pages = max(1, math.ceil(total / page_size))
        page = max(1, min(page, pages))
        cur = await db.execute(
            f"""SELECT * FROM notification_logs WHERE {where}{type_clause}
                ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?""",
            (*params, page_size, (page - 1) * page_size),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        result = {"total": total, "page": page, "pages": pages, "page_size": page_size,
                  "counts": counts, "rows": rows}
        if focus_found is not None:
            result["focus_found"] = focus_found
        if since_id is not None:
            cur = await db.execute(
                "SELECT COUNT(*) AS c FROM notification_logs WHERE is_acknowledged = 0 AND id > ?",
                (since_id,),
            )
            result["new_count"] = (await cur.fetchone())["c"]
        return result
    finally:
        await db.close()


# Matches anomaly_engine.py: an ML event raises one notification, and repeats within this long of
# the previous flag belong to the same event
ML_ALERT_QUIET_SECONDS = 5 * 60


@app.get("/api/notifications/at")
async def notifications_at(node_id: int, timestamp: str):
    """The alerts behind one reading, acknowledged or not, shown when a Historical Logs row is opened
    on the dashboard: those saved within 2 s of the reading's timestamp (UTC "YYYY-MM-DD HH:MM:SS"),
    plus -- for each sensor the ML flagged without a notification of its own -- the notification that
    started that ongoing event (marked "ongoing": the sensor stayed flagged since, with no gap longer
    than ML_ALERT_QUIET_SECONDS)."""
    near = "node_id = ? AND ABS(julianday(timestamp) - julianday(?)) * 86400 <= 2"
    db = await get_db()
    try:
        cur = await db.execute(f"SELECT * FROM notification_logs WHERE {near} ORDER BY id", (node_id, timestamp))
        rows = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(f"SELECT ml_flags FROM telemetry_logs WHERE {near} AND ml_flags IS NOT NULL",
                               (node_id, timestamp))
        flagged = {s for r in await cur.fetchall() for s in r["ml_flags"].split(",") if s}
        covered = {s for r in rows if r["is_anomaly"] for s in (r["params"] or "").split(",")}
        for sensor in sorted(flagged - covered):
            like = f"%,{sensor},%"
            # Walk back through this sensor's flagged readings to where the event began
            cur = await db.execute(
                """SELECT timestamp FROM telemetry_logs
                   WHERE node_id = ? AND timestamp <= datetime(?, '+2 seconds') AND (',' || ml_flags || ',') LIKE ?
                   ORDER BY timestamp DESC""",
                (node_id, timestamp, like),
            )
            start, prev = timestamp, None
            for r in await cur.fetchall():
                t = datetime.fromisoformat(r["timestamp"])
                if prev is not None and (prev - t).total_seconds() > ML_ALERT_QUIET_SECONDS:
                    break
                start, prev = r["timestamp"], t
            cur = await db.execute(
                """SELECT * FROM notification_logs
                   WHERE node_id = ? AND is_anomaly = 1 AND (',' || params || ',') LIKE ?
                     AND timestamp >= datetime(?, '-2 seconds') AND timestamp <= datetime(?, '+2 seconds')
                   ORDER BY timestamp DESC, id DESC LIMIT 1""",
                (node_id, like, start, timestamp),
            )
            event = await cur.fetchone()
            if event and all(r["id"] != event["id"] for r in rows):
                rows.append({**dict(event), "ongoing": True})
        return rows
    finally:
        await db.close()


@app.get("/api/notifications/summary")
async def notifications_summary():
    """
    Backs the DSS header: active count plus independent threshold/anomaly
    counts. These can overlap -- a packet flagged by both counts toward
    both totals, so threshold_count + anomaly_count may exceed active_count.
    """
    db = await get_db()
    try:
        cur = await db.execute("SELECT COUNT(*) AS c FROM notification_logs WHERE is_acknowledged = 0")
        active_count = (await cur.fetchone())["c"]
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM notification_logs WHERE is_acknowledged = 0 AND is_threshold = 1"
        )
        threshold_count = (await cur.fetchone())["c"]
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM notification_logs WHERE is_acknowledged = 0 AND is_anomaly = 1"
        )
        anomaly_count = (await cur.fetchone())["c"]
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM notification_logs WHERE is_acknowledged = 0 AND is_threshold = 1 AND is_anomaly = 1"
        )
        both_count = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT timestamp FROM notification_logs ORDER BY timestamp DESC LIMIT 1")
        latest_row = await cur.fetchone()
        return {
            "active_count": active_count,
            "threshold_count": threshold_count,
            "anomaly_count": anomaly_count,
            "both_count": both_count,
            "latest_timestamp": latest_row["timestamp"] if latest_row else None,
        }
    finally:
        await db.close()


@app.put("/api/notifications/acknowledge/{log_id}")
async def notifications_acknowledge(log_id: int):
    db = await get_db()
    try:
        cur = await db.execute(
            "UPDATE notification_logs SET is_acknowledged = 1 WHERE id = ?", (log_id,)
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"No notification with id {log_id}")
        await manager.broadcast({"type": "notifications"})   # other open dashboards drop it too
        return {"id": log_id, "is_acknowledged": True}
    finally:
        await db.close()


# --------------------------------------------------------------------------
# Report generation + QR-addressable download
# --------------------------------------------------------------------------
class ReportRequest(BaseModel):
    node_id: int
    start: str                       # UTC "YYYY-MM-DD HH:MM:SS", as the database stores timestamps
    end: str
    report_type: str = "Full Audit"  # "Full Audit" | "Anomaly Log Only"
    tz_offset: int = 0               # the browser's Date.getTimezoneOffset(), for local times in the PDF


@app.post("/api/reports/generate")
async def reports_generate(req: ReportRequest, request: Request):
    db = await get_db()
    try:
        query = "SELECT * FROM telemetry_logs WHERE node_id = ? AND timestamp BETWEEN ? AND ?"
        params = [req.node_id, req.start, req.end]
        if req.report_type == "Anomaly Log Only":
            query += " AND is_anomaly = 1"
        query += " ORDER BY timestamp ASC"
        cur = await db.execute(query, params)
        rows = [dict(r) for r in await cur.fetchall()]

        # Node's current acceptable ranges, so the PDF charts can draw the
        # same dashed limit lines the live dashboard shows -- not required
        # for the report to work, so a missing row just means charts render
        # without them rather than failing the report.
        cur = await db.execute("SELECT * FROM system_thresholds WHERE node_id = ?", (req.node_id,))
        limits_row = await cur.fetchone()
        limits = dict(limits_row) if limits_row else {}

        # Every alert raised for this node in the window, acknowledged or not
        cur = await db.execute(
            "SELECT * FROM notification_logs WHERE node_id = ? AND timestamp BETWEEN ? AND ? "
            "ORDER BY timestamp ASC, id ASC",
            (req.node_id, req.start, req.end),
        )
        alerts = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute("SELECT label FROM nodes WHERE node_id = ?", (req.node_id,))
        node_row = await cur.fetchone()
        node_label = node_row["label"] if node_row else f"Node {req.node_id}"
    finally:
        await db.close()

    if not rows:
        raise HTTPException(404, "No telemetry rows in the requested range")

    summary = _summarize(rows)
    kind = "AnomalyOnly" if req.report_type == "Anomaly Log Only" else "FullAudit"
    filename = _report_filename(f"Node{req.node_id}_{kind}", req.start, req.end, req.tz_offset, "pdf")
    filepath = os.path.join(REPORTS_DIR, filename)
    # A full audit can be thousands of rows: render off the event loop so live updates keep flowing
    await asyncio.to_thread(_render_pdf, filepath, req, node_label, summary, rows, alerts, limits)

    download_url = f"{_public_base_url(request)}/api/reports/download/{filename}"
    return {"filename": filename, "download_url": download_url, "summary": summary}


# Addresses that only mean something on this machine: loopback (the kiosk's localhost:3000) and
# the "all interfaces" bind addresses (a browser opened at http://0.0.0.0:3000)
LOCAL_ONLY_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}


def _lan_ip() -> Optional[str]:
    """This machine's address on its local network, or None. connect() on a UDP socket sends
    nothing; it only asks the OS which interface it would use to reach a hotspot client. On the
    Pi that is its hotspot address (192.168.4.1 as set up in SETUP_RASPI.md, or NetworkManager's
    default 10.42.0.1); on a laptop, its Wi-Fi/LAN address."""
    import socket
    for probe in ("192.168.4.2", "10.42.0.2"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))
                ip = s.getsockname()[0]
        except OSError:
            continue                  # no route that way (e.g. an offline Pi without that subnet)
        if not ip.startswith("127.") and ip != "0.0.0.0":
            return ip
    return None


def _public_base_url(request: Request) -> str:
    """Base URL a phone scanning the QR code can reach. If the dashboard was
    opened through a LAN address, that address works for the phone too. A
    local-only address (localhost, 0.0.0.0) does not, so use WASTEWATER_GATEWAY_IP
    if it was set, else this machine's LAN address, else the hotspot IP.
    The port must be included: uvicorn listens on 3000, not 80."""
    host = request.url.hostname or ""
    if host and host.strip("[]") not in LOCAL_ONLY_HOSTS:
        return str(request.base_url).rstrip("/")
    if "WASTEWATER_GATEWAY_IP" in os.environ:
        ip = GATEWAY_HOST
    else:
        ip = _lan_ip() or GATEWAY_HOST
    return f"http://{ip}:{GATEWAY_PORT}"


def _wifi_qr_escape(text: str) -> str:
    """Backslash-escape the characters the WIFI: QR format reserves."""
    return re.sub(r'([\\;,:"])', r"\\\1", text)


@app.get("/api/hotspot")
async def hotspot():
    """The hotspot a phone must join before the report QR's link works. `qr` is the standard
    WIFI: payload phone cameras turn into "join this network", or null when the server doesn't
    know the password (WASTEWATER_HOTSPOT_PASSWORD, or WASTEWATER_HOTSPOT_OPEN=1)."""
    if HOTSPOT_OPEN:
        qr = f"WIFI:T:nopass;S:{_wifi_qr_escape(HOTSPOT_SSID)};;"
    elif HOTSPOT_PASSWORD:
        qr = f"WIFI:T:WPA;S:{_wifi_qr_escape(HOTSPOT_SSID)};P:{_wifi_qr_escape(HOTSPOT_PASSWORD)};;"
    else:
        qr = None
    return {"ssid": HOTSPOT_SSID, "qr": qr}


class CsvExportRequest(BaseModel):
    node_id: Optional[int] = None
    start: Optional[str] = None
    end: Optional[str] = None
    flag: str = "all"
    tz_offset: int = 0  # the browser's Date.getTimezoneOffset(): minutes UTC is ahead of local time


CSV_FLAG_NAMES = {"all": "DataLog", "none": "DataLog-NoAlerts", "flagged": "DataLog-WithAlerts",
                  "threshold": "DataLog-ThresholdAlerts", "ml": "DataLog-MLAnomaly", "both": "DataLog-ThresholdAndML"}

CSV_HEADER = ["Log ID", "Node", "Packet", "Timestamp (local)", "pH", "Turbidity", "Temp", "Level", "Flow",
              "Interval Vol", "Anomaly", "Threshold Flags", "ML Flags"]


@app.post("/api/telemetry/export-csv")
async def telemetry_export_csv(req: CsvExportRequest, request: Request):
    """Every row matching the Historical Logs filters (not just the page on screen), written to
    REPORTS_DIR so a phone can fetch it through the same QR download link as a PDF report."""
    where, params = _telemetry_filter(req.node_id, req.start, req.end, req.flag)
    db = await get_db()
    try:
        cur = await db.execute(f"SELECT * FROM telemetry_logs {where} ORDER BY timestamp DESC", params)
        rows = [dict(r) for r in await cur.fetchall()]
        if not rows:
            raise HTTPException(404, "No telemetry rows match these filters")
        # The filter's dates, or the data's own first/last reading without them
        start, end = req.start or rows[-1]["timestamp"], req.end or rows[0]["timestamp"]
        # For the summary block, as in the PDF: every alert raised in the window, and the node's limits
        node_clause = "AND node_id = ?" if req.node_id is not None else ""
        node_param = [req.node_id] if req.node_id is not None else []
        cur = await db.execute(
            f"SELECT is_threshold, is_anomaly FROM notification_logs WHERE timestamp BETWEEN ? AND ? {node_clause}",
            (start, end, *node_param),
        )
        alerts = [dict(r) for r in await cur.fetchall()]
        limits, node_label = {}, "All nodes"
        if req.node_id is not None:
            cur = await db.execute("SELECT * FROM system_thresholds WHERE node_id = ?", (req.node_id,))
            limits = dict(await cur.fetchone() or {})
            cur = await db.execute("SELECT label FROM nodes WHERE node_id = ?", (req.node_id,))
            node_row = await cur.fetchone()
            node_label = f"{node_row['label'] if node_row else f'Node {req.node_id}'} (node {req.node_id})"
    finally:
        await db.close()

    shift = timedelta(minutes=-req.tz_offset)
    scope = f"Node{req.node_id}" if req.node_id is not None else "AllNodes"
    kind = CSV_FLAG_NAMES[req.flag]
    filename = _report_filename(f"{scope}_{kind}", start, end, req.tz_offset, "csv")
    with open(os.path.join(REPORTS_DIR, filename), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        # Summary block above the data, like the PDF's first page; the data starts at the CSV_HEADER row
        writer.writerow(["ARGUS — Data Log"])
        writer.writerow(["Node", node_label])
        writer.writerow(["Readings", REPORT_KIND_LABELS.get(kind, kind)])
        writer.writerow(["Window (local time)", f"{_local(start, req.tz_offset)} to {_local(end, req.tz_offset)}"])
        writer.writerow(["Generated (local time)", _local(datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), req.tz_offset)])
        writer.writerow([])
        writer.writerow(["Summary"])
        writer.writerow(["Measure", "Value"])
        writer.writerows(_summary_measures(_summarize(rows), alerts))
        if limits:
            writer.writerow(["Configured limits", _limits_text(limits)])
        writer.writerow([])
        writer.writerow(CSV_HEADER)
        for r in rows:
            ts = r["timestamp"]
            try:
                ts = (datetime.fromisoformat(ts) + shift).strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                pass                  # keep whatever the database holds rather than drop the row
            writer.writerow([r["id"], r["node_id"], r["packet_id"], ts, r["ph"], r["turbidity"],
                             r["temperature"], r["water_level"], r["flowrate"], r["interval_volume"],
                             r["is_anomaly"], r.get("threshold_flags") or "", r.get("ml_flags") or ""])

    download_url = f"{_public_base_url(request)}/api/reports/download/{filename}"
    return {"filename": filename, "download_url": download_url, "rows": len(rows)}


DOWNLOAD_TYPES = {".pdf": "application/pdf", ".csv": "text/csv"}

# ARGUS_<Node1|AllNodes>_<FullAudit|AnomalyOnly|DataLog[-filter]>_<start>_to_<end>[_n].<pdf|csv>,
# start/end as local "YYYY-MM-DD_HHMM"
REPORT_NAME_RE = re.compile(
    r"^ARGUS_(?P<scope>Node\d+|AllNodes)_(?P<kind>[A-Za-z-]+)_(?P<start>\d{4}-\d{2}-\d{2}_\d{4})"
    r"_to_(?P<end>\d{4}-\d{2}-\d{2}_\d{4})(?:_\d+)?\.(?:pdf|csv)$")
REPORT_KIND_LABELS = {
    "FullAudit": "Full Audit", "AnomalyOnly": "Anomaly Log Only", "DataLog": "Data log (all readings)",
    "DataLog-NoAlerts": "Data log (no alerts)", "DataLog-WithAlerts": "Data log (with alerts only)",
    "DataLog-ThresholdAlerts": "Data log (threshold alerts)", "DataLog-MLAnomaly": "Data log (ML anomaly)",
    # names of files saved before the filter labels changed
    "DataLog-Flagged": "Data log (any alert)", "DataLog-ThresholdOnly": "Data log (threshold only)",
    "DataLog-MLOnly": "Data log (ML only)", "DataLog-ThresholdAndML": "Data log (threshold + ML)",
}


def _report_filename(prefix: str, start_utc: str, end_utc: str, tz_offset: int, ext: str) -> str:
    """A name saying what the file holds, e.g. ARGUS_Node1_FullAudit_2026-10-01_0000_to_2026-10-08_2359.pdf
    (local times). A second file with the same name gets _2, _3, ... instead of replacing the first."""
    def stamp(ts):
        local = _local(ts, tz_offset)
        return local[:10] + "_" + local[11:13] + local[14:16] if len(local) >= 16 else "unknown"
    base = f"ARGUS_{prefix}_{stamp(start_utc)}_to_{stamp(end_utc)}"
    name, n = f"{base}.{ext}", 1
    while os.path.exists(os.path.join(REPORTS_DIR, name)):
        n += 1
        name = f"{base}_{n}.{ext}"
    return name


def _report_path(filename: str) -> str:
    """The saved report's path, or 404 for anything that isn't a PDF/CSV in REPORTS_DIR."""
    filename = os.path.basename(filename)
    path = os.path.join(REPORTS_DIR, filename)
    if os.path.splitext(filename)[1].lower() not in DOWNLOAD_TYPES or not os.path.isfile(path):
        raise HTTPException(404, "Report not found")
    return path


@app.get("/api/reports/files")
async def reports_files(request: Request):
    """Every saved report and CSV export, newest first, with what its name says about it.
    Files from before the descriptive names show only their name, type, size and date."""
    base = _public_base_url(request)
    files = []
    for entry in os.scandir(REPORTS_DIR):
        ext = os.path.splitext(entry.name)[1].lower()
        if not entry.is_file() or ext not in DOWNLOAD_TYPES:
            continue
        info = entry.stat()
        meta = REPORT_NAME_RE.match(entry.name)
        files.append({
            "filename": entry.name,
            "format": ext[1:].upper(),
            "size": info.st_size,
            "created": datetime.utcfromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "expires": datetime.utcfromtimestamp(info.st_mtime + REPORT_RETENTION_DAYS * 86400)
                       .strftime("%Y-%m-%d %H:%M:%S"),
            "scope": meta["scope"].replace("Node", "Node ") if meta and meta["scope"] != "AllNodes"
                     else ("All nodes" if meta else None),
            "report_type": REPORT_KIND_LABELS.get(meta["kind"], meta["kind"]) if meta else None,
            # local times, as written in the name
            "start": f"{meta['start'][:10]} {meta['start'][11:13]}:{meta['start'][13:]}" if meta else None,
            "end": f"{meta['end'][:10]} {meta['end'][11:13]}:{meta['end'][13:]}" if meta else None,
            "download_url": f"{base}/api/reports/download/{entry.name}",
        })
    files.sort(key=lambda f: f["created"], reverse=True)
    return files


# --------------------------------------------------------------------------
# Clock sync from a phone: the Pi has no RTC, so after a reboot without internet its clock resumes
# from the last time it saved (often an hour or more behind). Every dashboard that opens sends its
# own clock here; phones keep accurate time from the mobile network. The Pi's clock is set from it
# only when the Pi hasn't had internet (NTP) time since boot and is more than
# CLOCK_SYNC_MIN_OFFSET_S off -- so a kiosk on the Pi itself (same clock) or a phone a few seconds
# off never changes anything, and internet time always wins. The ESP32 picks the corrected time up
# from its next ACK. Needs "sudo date" without a password (scripts/install_autostart.sh sets a rule).
# --------------------------------------------------------------------------
CLOCK_SYNC_MIN_OFFSET_S = 60
_last_phone_sync: Optional[dict] = None


def _ntp_synchronized() -> bool:
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


def _describe_offset(seconds: float) -> str:
    s = abs(round(seconds))
    h, m = divmod(s // 60, 60)
    text = f"{h} h {m} min" if h else f"{m} min {s % 60} s" if m else f"{s} s"
    return f"{text} {'behind' if seconds > 0 else 'ahead'}"


class ClientTime(BaseModel):
    client_ms: float          # the browser's Date.now() when it sent this


@app.get("/api/system/time")
async def system_time_status():
    return {"ntp_synchronized": await asyncio.to_thread(_ntp_synchronized),
            "server_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "last_phone_sync": _last_phone_sync}


@app.post("/api/system/time")
async def system_time_sync(body: ClientTime, request: Request):
    """A dashboard's clock. Sets the Pi's clock from it when the Pi has no internet time and is
    more than CLOCK_SYNC_MIN_OFFSET_S off; otherwise only reports the difference."""
    global _last_phone_sync
    offset = body.client_ms / 1000 - time.time()         # > 0: the Pi is behind
    result = {"offset_s": round(offset, 1)}
    if await asyncio.to_thread(_ntp_synchronized):
        return {**result, "action": "none", "reason": "The Pi already has internet time."}
    if abs(offset) < CLOCK_SYNC_MIN_OFFSET_S:
        return {**result, "action": "none", "reason": "The Pi's clock is close enough."}

    target = time.time() + offset                         # recomputed here, so processing time doesn't count
    proc = await asyncio.to_thread(
        subprocess.run, ["sudo", "-n", "/usr/bin/date", "-u", "-s", f"@{target:.3f}"],
        capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        print(f"Clock sync from {request.client.host} failed: {proc.stderr.strip()}", flush=True)
        return {**result, "action": "failed",
                "reason": "The server isn't allowed to set the clock (see scripts/install_autostart.sh)."}

    _last_phone_sync = {
        "at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "offset_s": round(offset, 1),
        "description": _describe_offset(offset),
        "client": request.client.host if request.client else None,
    }
    print(f"Clock set from {_last_phone_sync['client']}: the Pi was {_last_phone_sync['description']}", flush=True)
    await manager.broadcast({"type": "clock", "data": _last_phone_sync})
    return {**result, "action": "set", "sync": _last_phone_sync}


# --------------------------------------------------------------------------
# Safe shutdown from the dashboard. Only the Pi's own screen (the kiosk, at localhost) may do it
# unless WASTEWATER_ALLOW_REMOTE_SHUTDOWN=1 -- anyone who joins the hotspot can open the dashboard.
# systemd then stops the engine and server cleanly (the database is committed as each reading
# arrives) before the Pi powers off. Needs "sudo systemctl poweroff" without a password
# (scripts/install_autostart.sh sets a rule).
# --------------------------------------------------------------------------
ALLOW_REMOTE_SHUTDOWN = os.environ.get("WASTEWATER_ALLOW_REMOTE_SHUTDOWN") == "1"
SHUTDOWN_DELAY_S = 3            # time for the dashboard to show "shutting down" first


def _from_this_pi(request: Request) -> bool:
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1", "localhost")


@app.get("/api/system/shutdown")
async def system_shutdown_allowed(request: Request):
    """Whether this browser may shut the Pi down, so the dashboard only shows the button where it works."""
    return {"allowed": ALLOW_REMOTE_SHUTDOWN or _from_this_pi(request)}


@app.post("/api/system/shutdown")
async def system_shutdown(request: Request):
    if not (ALLOW_REMOTE_SHUTDOWN or _from_this_pi(request)):
        raise HTTPException(403, "Shut down only from the Pi's own screen")
    check = await asyncio.to_thread(
        subprocess.run, ["sudo", "-n", "-l", "/usr/bin/systemctl", "poweroff"], capture_output=True, text=True, timeout=10)
    if check.returncode != 0:
        raise HTTPException(500, "The server isn't allowed to shut the Pi down (see scripts/install_autostart.sh)")
    print(f"Shutdown requested from the dashboard ({request.client.host}); powering off in {SHUTDOWN_DELAY_S} s", flush=True)
    await manager.broadcast({"type": "shutdown"})

    async def power_off():
        await asyncio.sleep(SHUTDOWN_DELAY_S)
        subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "poweroff"])
    asyncio.create_task(power_off())
    return {"shutting_down": True, "in_seconds": SHUTDOWN_DELAY_S}


@app.get("/api/dss/guide")
async def dss_guide():
    """Every condition the system can alert on, with its most common reasons, impact, actions and references
    (the Alerts page's DSS Guide), straight from dss_rules.py."""
    return dss_rules.guide_entries()


class ReportDeleteRequest(BaseModel):
    filenames: list[str]


@app.post("/api/reports/delete")
async def reports_delete(req: ReportDeleteRequest):
    """Delete saved reports/exports chosen on the Reports page. Only PDF/CSV files directly in
    REPORTS_DIR can be deleted; names that aren't there are reported back, not errors."""
    deleted, missing = [], []
    for name in req.filenames:
        try:
            path = _report_path(name)
        except HTTPException:
            missing.append(name)
            continue
        try:
            os.remove(path)
            deleted.append(os.path.basename(path))
        except OSError:
            missing.append(name)
    if deleted:
        print(f"Deleted {len(deleted)} saved report(s) from the Reports page: {', '.join(deleted)}", flush=True)
    return {"deleted": deleted, "missing": missing}


@app.get("/api/reports/download/{filename}")
async def reports_download(filename: str):
    path = _report_path(filename)
    return FileResponse(path, media_type=DOWNLOAD_TYPES[os.path.splitext(path)[1].lower()],
                        filename=os.path.basename(path))


@app.get("/api/reports/view/{filename}")
async def reports_view(filename: str):
    """The file shown in the browser instead of downloaded (the PDF preview)."""
    path = _report_path(filename)
    return FileResponse(path, media_type=DOWNLOAD_TYPES[os.path.splitext(path)[1].lower()],
                        filename=os.path.basename(path), content_disposition_type="inline")


# PDF preview as page images, so it works in browsers with no PDF viewer of their own (VS Code's
# Simple Browser, many phone browsers), which would only offer to save the file. Rendered on demand
# with poppler-utils' pdftoppm and cached until the PDF changes or the Pi reboots (/tmp).
PREVIEW_CACHE = os.path.join("/tmp", "argus-report-previews")
PREVIEW_DPI = 110


def _pdf_page_count(path: str) -> int:
    import subprocess
    out = subprocess.run(["pdfinfo", path], capture_output=True, text=True, timeout=30).stdout
    match = re.search(r"^Pages:\s+(\d+)", out, re.M)
    if not match:
        raise HTTPException(500, "Couldn't read the PDF (is poppler-utils installed?)")
    return int(match.group(1))


def _pdf_page_png(path: str, page: int) -> str:
    import subprocess
    os.makedirs(PREVIEW_CACHE, exist_ok=True)
    stamp = int(os.path.getmtime(path))
    out = os.path.join(PREVIEW_CACHE, f"{os.path.basename(path)}.{stamp}.p{page}")
    if not os.path.exists(out + ".png"):
        subprocess.run(["pdftoppm", "-r", str(PREVIEW_DPI), "-f", str(page), "-l", str(page),
                        "-png", "-singlefile", path, out], check=True, timeout=60)
    return out + ".png"


@app.get("/api/reports/pages/{filename}")
async def reports_pages(filename: str):
    """How many pages a saved PDF has, for the page-image preview."""
    path = _report_path(filename)
    if not path.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDFs have pages")
    return {"pages": await asyncio.to_thread(_pdf_page_count, path)}


@app.get("/api/reports/page/{filename}/{page}")
async def reports_page(filename: str, page: int):
    """One page of a saved PDF as a PNG image."""
    path = _report_path(filename)
    if not path.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDFs have pages")
    if page < 1 or page > await asyncio.to_thread(_pdf_page_count, path):
        raise HTTPException(404, "No such page")
    return FileResponse(await asyncio.to_thread(_pdf_page_png, path, page), media_type="image/png")


@app.get("/api/reports/preview/{filename}")
async def reports_preview(filename: str, limit: int = 500):
    """A CSV's header and first `limit` rows as JSON, for the preview table. Exports with a summary
    block above the data (everything before the CSV_HEADER row) return it as "summary" rows."""
    path = _report_path(filename)
    if not path.lower().endswith(".csv"):
        raise HTTPException(400, "Only CSV files have a table preview; PDFs open in /api/reports/view")
    limit = max(1, min(limit, 5000))
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        summary = []
        if header and header != CSV_HEADER and header[0].startswith("ARGUS"):
            summary.append(header)
            for row in reader:
                if row == CSV_HEADER:
                    break
                if row:
                    summary.append(row)
            header = CSV_HEADER
        rows, total = [], 0
        for row in reader:
            if total < limit:
                rows.append(row)
            total += 1
    return {"header": header, "rows": rows, "total": total, "shown": len(rows), "summary": summary}


def _summarize(rows: list[dict]) -> dict:
    n = len(rows)
    ph = [r["ph"] for r in rows if r["ph"] is not None]
    turb = [r["turbidity"] for r in rows if r["turbidity"] is not None]
    temp = [r["temperature"] for r in rows if r["temperature"] is not None]
    anomalies = sum(1 for r in rows if r.get("is_anomaly"))
    total_vol = _discharged_volume(rows)
    return {
        "row_count": n,
        "mean_ph": round(sum(ph) / len(ph), 2) if ph else None,
        "max_turbidity": max(turb) if turb else None,
        "mean_temp": round(sum(temp) / len(temp), 2) if temp else None,
        "anomaly_count": anomalies,
        "anomaly_pct": round(anomalies / n * 100, 1) if n else 0,
        "total_volume_l": round(total_vol, 1),
    }


def _discharged_volume(rows: list[dict]) -> float:
    """Litres discharged over these readings. Each row holds the node's RUNNING total (volume_total;
    interval_volume holds the same number, and is the only one on older rows), so summing the rows
    would count every litre again on each later reading. Instead add how much the total grew from
    one reading to the next, per node; a drop means the node restarted its total for a new batch."""
    total, last = 0.0, {}
    for r in sorted(rows, key=lambda r: (r["node_id"], r["timestamp"], r["id"])):
        v = r.get("volume_total")
        if v is None:
            v = r.get("interval_volume")
        if v is None:
            continue
        prev = last.get(r["node_id"])
        if prev is not None:
            total += v - prev if v >= prev else v
        last[r["node_id"]] = v
    return total


def _summary_measures(summary: dict, alerts: list[dict]) -> list[tuple[str, str]]:
    """The report summary as (measure, value) pairs -- the PDF's summary table and the CSV's header block."""
    alert_threshold = sum(1 for a in alerts if a.get("is_threshold"))
    alert_ml = sum(1 for a in alerts if a.get("is_anomaly"))
    return [(k, "—" if v is None else str(v)) for k, v in [
        ("Readings analyzed", summary["row_count"]),
        ("Mean pH", summary["mean_ph"]),
        ("Peak turbidity (NTU)", summary["max_turbidity"]),
        ("Mean temperature (°C)", summary["mean_temp"]),
        ("Flagged readings", summary["anomaly_count"]),
        ("Flagged readings (%)", summary["anomaly_pct"]),
        ("Total discharge volume (L)", summary["total_volume_l"]),
        ("Alerts raised", f"{len(alerts)} ({alert_threshold} threshold breach, {alert_ml} anomaly)"),
    ]]


def _limits_text(limits: dict) -> str:
    return (f"pH {limits.get('min_ph')}–{limits.get('max_ph')}, turbidity ≤ {limits.get('max_turbidity')} NTU, "
            f"temperature {limits.get('min_temp')}–{limits.get('max_temp')} °C, level {limits.get('min_level')}–{limits.get('max_level')} %, "
            f"flow ≤ {limits.get('max_flow')} L/min")


_CHART_FIELD_CFG = {
    "ph": {"label": "pH", "unit": "", "lo_key": "min_ph", "hi_key": "max_ph"},
    "turbidity": {"label": "Turbidity", "unit": " NTU", "lo_key": None, "hi_key": "max_turbidity"},
    "temperature": {"label": "Temperature", "unit": " °C", "lo_key": "min_temp", "hi_key": "max_temp"},
}


def _render_chart_image(rows: list[dict], field: str, limits: dict, tz_offset: int = 0) -> "BytesIO":
    """One field's trend over the report window, matching the live
    dashboard's visual language: dashed limit lines, red dots for real threshold_flags hits, purple dots for
    real ml_flags hits -- not re-derived or simulated, the actual stored
    columns anomaly_engine.py wrote at ingest time."""
    from io import BytesIO
    from datetime import datetime as dt

    cfg = _CHART_FIELD_CFG[field]
    # Local time like the rest of the report (tz_offset: the browser's Date.getTimezoneOffset())
    times = [dt.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S") - timedelta(minutes=tz_offset) for r in rows]
    values = [r[field] for r in rows]

    fig, ax = plt.subplots(figsize=(6.4, 2.1), dpi=150)

    lo = limits.get(cfg["lo_key"]) if cfg["lo_key"] else None
    hi = limits.get(cfg["hi_key"])
    if lo is not None or hi is not None:
        if lo is not None:
            ax.axhline(lo, color="#94A3B8", linestyle="--", linewidth=0.8)
        if hi is not None:
            ax.axhline(hi, color="#94A3B8", linestyle="--", linewidth=0.8)

    ax.plot(times, values, color="#0284C7", linewidth=1.3)

    threshold_pts = [(t, v) for t, v, r in zip(times, values, rows)
                      if field in (r.get("threshold_flags") or "").split(",")]
    ml_pts = [(t, v) for t, v, r in zip(times, values, rows)
              if field in (r.get("ml_flags") or "").split(",")]
    if threshold_pts:
        tx, ty = zip(*threshold_pts)
        ax.scatter(tx, ty, color="#D03B3B", s=14, zorder=5, label="Threshold")
    if ml_pts:
        mx, my = zip(*ml_pts)
        ax.scatter(mx, my, color="#4A3AA7", s=14, zorder=5, label="ML model")
    if threshold_pts or ml_pts:
        ax.legend(loc="upper right", fontsize=7, frameon=False)

    ax.set_title(f"{cfg['label']}{cfg['unit']}", fontsize=10, loc="left", color="#0F172A")
    ax.tick_params(labelsize=7, colors="#334155")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def _local(ts: Optional[str], tz_offset: int) -> str:
    """A database UTC timestamp as the viewer's local time (tz_offset: Date.getTimezoneOffset())."""
    if not ts:
        return ""
    try:
        return (datetime.fromisoformat(ts) - timedelta(minutes=tz_offset)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts


def _render_pdf(filepath: str, req: ReportRequest, node_label: str, summary: dict,
                rows: list[dict], alerts: list[dict], limits: dict):
    """The offline compliance PDF (reportlab, no internet needed), in this order:
    summary, graphs, alerts, tabled data (Full Audit only; same columns as the CSV export),
    references (the sources cited by the alerts' DSS diagnoses). Portrait pages, except the
    data table, which gets landscape pages so all 13 CSV columns fit."""
    import json
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (BaseDocTemplate, Frame, Image, LongTable, NextPageTemplate,
                                    PageBreak, PageTemplate, Paragraph, Spacer, TableStyle)

    tz = req.tz_offset
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#1E3A8A"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=13, textColor=colors.HexColor("#1E3A8A"),
                        spaceBefore=6, spaceAfter=6)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=12.5)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9.5)
    cell = ParagraphStyle("cell", parent=body, fontSize=6.5, leading=8)
    muted = colors.HexColor("#334155")
    grid = colors.HexColor("#E2E8F0")
    header_bg = colors.HexColor("#F1F5F9")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(doc.leftMargin, 0.45 * inch, f"ARGUS compliance report · {node_label} · {req.report_type}")
        canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 0.45 * inch, f"Page {doc.page}")
        canvas.restoreState()

    margin = 0.6 * inch
    portrait, wide = LETTER, landscape(LETTER)
    doc = BaseDocTemplate(filepath, pagesize=portrait, leftMargin=margin, rightMargin=margin,
                          topMargin=margin, bottomMargin=0.75 * inch, title="ARGUS Compliance Report")
    doc.addPageTemplates([
        PageTemplate("portrait", pagesize=portrait, onPage=footer,
                     frames=[Frame(margin, 0.75 * inch, portrait[0] - 2 * margin, portrait[1] - margin - 0.75 * inch)]),
        PageTemplate("landscape", pagesize=wide, onPage=footer,
                     frames=[Frame(margin, 0.75 * inch, wide[0] - 2 * margin, wide[1] - margin - 0.75 * inch)]),
    ])
    story = []

    def table(data, widths, repeat=1, zebra=True):
        t = LongTable(data, colWidths=widths, repeatRows=repeat)
        style = [("GRID", (0, 0), (-1, -1), 0.4, grid),
                 ("BACKGROUND", (0, 0), (-1, 0), header_bg),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
        if zebra:
            style += [("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")])]
        t.setStyle(TableStyle(style))
        return t

    # ---- 1. Summary -------------------------------------------------------------------------
    story.append(Paragraph("ARGUS — Compliance Report", h1))
    story.append(Paragraph(
        f"<b>Node:</b> {escape(node_label)} (node {req.node_id}) &nbsp; · &nbsp; <b>Report type:</b> {escape(req.report_type)}<br/>"
        f"<b>Window:</b> {_local(req.start, tz)} to {_local(req.end, tz)} (local time)<br/>"
        f"<b>Generated:</b> {_local(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), tz)}", body))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Summary", h2))
    summary_rows = [["Measure", "Value"]] + [list(m) for m in _summary_measures(summary, alerts)]
    story.append(table(summary_rows, [2.6 * inch, 2.4 * inch]))
    if limits:
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"Configured limits: {_limits_text(limits)}.", small))

    # ---- 2. Graphs --------------------------------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Graphs", h2))
    story.append(Paragraph("Times in local time. Dashed lines: acceptable range. Red dots: threshold "
                           "breaches. Purple dots: anomalies (unusual changes).", small))
    story.append(Spacer(1, 4))
    for field in ("ph", "turbidity", "temperature"):
        story.append(Image(_render_chart_image(rows, field, limits, tz), width=7.2 * inch, height=2.36 * inch))
        story.append(Spacer(1, 6))

    # ---- 3. Alerts --------------------------------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph(f"Alerts ({len(alerts)})", h2))

    def parsed_dss(alert):
        try:
            return json.loads(alert["dss"]) if alert.get("dss") else None
        except ValueError:
            return None

    # Number every cited reference up front: national (Philippine) sources first, then international,
    # each in order of first citation. Alerts saved before references had a scope count as international.
    cited = {}
    for a in alerts:
        dss = parsed_dss(a)
        if not dss:
            continue
        refs = dss.get("references", {})
        ids = [r for section in ("causes", "impact", "actions") for item in dss.get(section, []) for r in item["refs"]]
        for ref_id in ids + list(dss.get("incidents", [])):
            if ref_id in refs and ref_id not in cited:
                cited[ref_id] = refs[ref_id]
    ordered = ([i for i, r in cited.items() if r.get("scope") == "national"]
               + [i for i, r in cited.items() if r.get("scope") != "national"])
    ref_number = {ref_id: n for n, ref_id in enumerate(ordered, 1)}

    def cite(ref_id, ref):
        return f"[{ref_number[ref_id]}]"

    if not alerts:
        story.append(Paragraph("No alerts were raised for this node in the report window.", body))
    else:
        story.append(Paragraph("Each alert as raised, acknowledged or not. ML alerts carry the DSS diagnosis "
                               "(most common reasons, not a confirmed cause); numbers in brackets refer to the "
                               "References section.", small))
        story.append(Spacer(1, 4))
        alert_rows = [[Paragraph(f"<b>{h}</b>", cell) for h in ("Time (local)", "Type", "Alert", "Details")]]
        for a in alerts:
            kind = []
            if a.get("is_threshold"):
                kind.append("Threshold breach")
            if a.get("is_anomaly"):
                kind.append("Anomaly")
            if a.get("severity"):
                kind.append(a["severity"])
            if a.get("is_acknowledged"):
                kind.append("Acknowledged")
            # One condition per line, as on the dashboard
            parts = re.split(r"\s+(?=\[(?:Threshold|ML Model) - )|\s+(?=Delivered late:)", a.get("message") or "")
            lines = []
            for part in parts:
                tag = re.match(r"^\[(?:Threshold|ML Model) - ([^\]]+)\]\s*", part)
                lines.append(f"<b>{escape(tag.group(1))}:</b> {escape(part[tag.end():])}" if tag else escape(part))
            dss = parsed_dss(a)
            if dss:
                refs = dss.get("references", {})
                def items(section):
                    return "<br/>".join(
                        f"• {escape(item['text'])} " + "".join(cite(r, refs[r]) for r in item["refs"] if r in refs)
                        for item in dss.get(section, []))
                lines.append(f"<i>Where:</i> {escape(dss.get('stage', ''))}")
                lines.append(f"<b>Most common reasons:</b><br/>{items('causes')}")
                lines.append(f"<b>Recommended actions:</b><br/>{items('actions')}")
                if dss.get("incidents"):
                    lines.append("<b>Similar situations:</b> " + " ".join(
                        cite(i, refs[i]) for i in dss["incidents"] if i in refs))
            alert_rows.append([Paragraph(_local(a["timestamp"], tz), cell), Paragraph(" · ".join(kind), cell),
                               Paragraph(escape(alert_display_title(a)), cell), Paragraph("<br/>".join(lines), cell)])
        story.append(table(alert_rows, [0.95 * inch, 0.8 * inch, 1.55 * inch, 3.9 * inch]))

    # ---- 4. Tabled data (Full Audit) -----------------------------------------------------------
    if req.report_type == "Full Audit":
        story.append(NextPageTemplate("landscape"))
        story.append(PageBreak())
        story.append(Paragraph(f"Tabled data ({len(rows)} readings)", h2))
        story.append(Paragraph("Every reading in the window, with the same columns as the Historical Logs CSV export.", small))
        story.append(Spacer(1, 4))
        data = [[Paragraph(f"<b>{h}</b>", cell) for h in (
            "Log ID", "Node", "Packet", "Timestamp (local)", "pH", "Turbidity", "Temp", "Level", "Flow",
            "Interval Vol", "Anomaly", "Threshold Flags", "ML Flags")]]
        for r in rows:
            data.append([r["id"], r["node_id"], r["packet_id"], _local(r["timestamp"], tz), r["ph"], r["turbidity"],
                         r["temperature"], r["water_level"], r["flowrate"], r["interval_volume"], r["is_anomaly"],
                         r.get("threshold_flags") or "", r.get("ml_flags") or ""])
        widths = [0.55, 0.4, 0.55, 1.25, 0.55, 0.7, 0.55, 0.55, 0.55, 0.75, 0.6, 1.4, 1.4]
        t = LongTable(data, colWidths=[w * inch for w in widths], repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 1), (-1, -1), "Helvetica", 6.5),
            ("GRID", (0, 0), (-1, -1), 0.3, grid),
            ("BACKGROUND", (0, 0), (-1, 0), header_bg),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ]))
        story.append(t)
        story.append(NextPageTemplate("portrait"))

    # ---- 5. References ------------------------------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("References", h2))
    if ordered:
        story.append(Paragraph("Sources cited by the alerts' DSS diagnoses above.", small))
        h3 = ParagraphStyle("h3", parent=h2, fontSize=11, spaceBefore=8, spaceAfter=4)
        for heading, ids in (("National references (Philippines)", [i for i in ordered if cited[i].get("scope") == "national"]),
                             ("International references", [i for i in ordered if cited[i].get("scope") != "national"])):
            if not ids:
                continue
            story.append(Paragraph(heading, h3))
            for ref_id in ids:
                ref = cited[ref_id]
                story.append(Paragraph(
                    f"[{ref_number[ref_id]}] <b>{escape(ref['title'])}</b>. {escape(ref['publisher'])}.<br/>"
                    f"<font color='#0284C7'>{escape(ref['url'])}</font>", body))
                story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("No sources were cited: none of the alerts in this window carried a DSS diagnosis.", body))

    doc.build(story)


# --------------------------------------------------------------------------
# Admin: hardware thresholds, node management, ML sensitivity
# --------------------------------------------------------------------------
@app.get("/api/admin/thresholds")
async def admin_thresholds_get():
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM system_thresholds")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


class ThresholdUpdate(BaseModel):
    node_id: int
    min_ph: Optional[float] = None
    max_ph: Optional[float] = None
    max_turbidity: Optional[float] = None
    min_temp: Optional[float] = None
    max_temp: Optional[float] = None
    max_level: Optional[float] = None
    min_level: Optional[float] = None
    max_flow: Optional[float] = None


THRESHOLD_FIELDS = (
    "min_ph", "max_ph", "max_turbidity", "min_temp", "max_temp",
    "max_level", "min_level", "max_flow",
)


@app.put("/api/admin/thresholds")
async def admin_thresholds_put(update: ThresholdUpdate):
    if update.min_ph is not None and update.max_ph is not None and update.min_ph >= update.max_ph:
        raise HTTPException(400, "min_ph must be less than max_ph")
    if update.min_temp is not None and update.max_temp is not None and update.min_temp >= update.max_temp:
        raise HTTPException(400, "min_temp must be less than max_temp")
    if update.min_level is not None and update.max_level is not None and update.min_level >= update.max_level:
        raise HTTPException(400, "min_level must be less than max_level")

    fields, params = [], []
    for field in THRESHOLD_FIELDS:
        val = getattr(update, field)
        if val is not None:
            fields.append(f"{field} = ?")
            params.append(val)
    if not fields:
        raise HTTPException(400, "No fields provided")
    params.append(update.node_id)

    db = await get_db()
    try:
        await db.execute(
            f"UPDATE system_thresholds SET {', '.join(fields)} WHERE node_id = ?", params
        )
        await db.commit()
        return {"node_id": update.node_id, "updated": fields}
    finally:
        await db.close()


class MlConfigUpdate(BaseModel):
    node_id: int
    contamination: float


@app.get("/api/admin/ml-config")
async def admin_ml_config_get():
    """
    NOTE: the ONNX pipeline bundles its own trained contamination rate, so
    this value is informational/UI-only until edge_engine.py (or the model
    export step) is updated to actually read it at inference time.
    """
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM ml_config")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@app.put("/api/admin/ml-config")
async def admin_ml_config_put(update: MlConfigUpdate):
    if not (0.01 <= update.contamination <= 0.10):
        raise HTTPException(400, "contamination must be between 0.01 and 0.10")
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO ml_config (node_id, contamination) VALUES (?, ?) "
            "ON CONFLICT(node_id) DO UPDATE SET contamination = excluded.contamination",
            (update.node_id, update.contamination),
        )
        await db.commit()
        return {"node_id": update.node_id, "contamination": update.contamination}
    finally:
        await db.close()


@app.get("/api/admin/nodes")
async def admin_nodes_get():
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM nodes ORDER BY node_id")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


class NodeUpsert(BaseModel):
    node_id: int
    label: str


@app.put("/api/admin/nodes")
async def admin_nodes_put(node: NodeUpsert):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO nodes (node_id, label) VALUES (?, ?) "
            "ON CONFLICT(node_id) DO UPDATE SET label = excluded.label",
            (node.node_id, node.label),
        )
        await db.execute(
            f"INSERT OR IGNORE INTO system_thresholds (node_id, {', '.join(DEFAULT_THRESHOLDS)}) "
            f"VALUES (?{', ?' * len(DEFAULT_THRESHOLDS)})",
            (node.node_id, *DEFAULT_THRESHOLDS.values()),
        )
        await db.execute("INSERT OR IGNORE INTO ml_config (node_id, contamination) VALUES (?, ?)",
                         (node.node_id, DEFAULT_ML_CONTAMINATION))
        await db.commit()
        return {"node_id": node.node_id, "label": node.label}
    finally:
        await db.close()


# A node is "online" if we've heard from it more recently than this. The node sends every 15 s, so
# 60 s is ~4 missed readings in a row -- enough to ride out a lost or resent LoRa packet without the
# status flickering, while a node that has really stopped shows offline within a minute.
NODE_STALE_SECONDS = 60


@app.get("/api/nodes/status")
async def nodes_status():
    """
    Connectivity is inferred, not queried -- the gateway never pings a node,
    it only listens. So "online" here means "we received a packet from this
    node more recently than NODE_STALE_SECONDS ago," derived from the most
    recent telemetry_logs row per node. A node that's still physically
    powered on but hasn't transmitted in a while will correctly show as
    offline until its next packet arrives.
    """
    db = await get_db()
    try:
        cur = await db.execute("SELECT node_id, label FROM nodes ORDER BY node_id")
        nodes = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT node_id, MAX(timestamp) AS last_seen FROM telemetry_logs GROUP BY node_id"
        )
        last_seen_map = {r["node_id"]: r["last_seen"] for r in await cur.fetchall()}
    finally:
        await db.close()

    return [{**n, **_node_status(last_seen_map.get(n["node_id"]))} for n in nodes]


def _node_status(last_seen: Optional[str]) -> dict:
    """Online/offline from a node's newest telemetry timestamp (UTC, as the database stores it)."""
    if last_seen is None:
        return {"last_seen": None, "seconds_since_last_reading": None, "is_online": False}
    seconds_ago = (datetime.utcnow() - datetime.fromisoformat(last_seen.replace("Z", ""))).total_seconds()
    return {
        "last_seen": last_seen,
        "seconds_since_last_reading": round(seconds_ago),
        "is_online": seconds_ago < NODE_STALE_SECONDS,
    }


@app.get("/api/admin/diagnostics")
async def admin_diagnostics():
    db_size_mb = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2) if os.path.exists(DB_PATH) else 0
    db = await get_db()
    try:
        cur = await db.execute("SELECT COUNT(*) AS c FROM telemetry_logs")
        row_count = (await cur.fetchone())["c"]
    finally:
        await db.close()
    return {
        "db_size_mb": db_size_mb,
        "row_count": row_count,
        "websocket_clients": len(manager.active),
        "connection_status": "Connected" if os.path.exists(DB_PATH) else "Missing database.db",
    }


# --------------------------------------------------------------------------
# Serve the built React SPA (frontend/dist) on this same origin/port, so the
# kiosk's `chromium-browser --kiosk http://localhost:3000` and phones on the
# hotspot both hit one process for API + UI. Mounted last so it never
# shadows the /api and /ws routes above. Falls back gracefully with a plain
# message if `npm run build` hasn't been run yet.
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    @app.get("/")
    async def frontend_not_built():
        return {
            "detail": f"Frontend build not found at {FRONTEND_DIST}. "
                      f"Run `npm install && npm run build` inside frontend/, then restart server.py."
        }
