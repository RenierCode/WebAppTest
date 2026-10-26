-- Wastewater Hub — shared database schema
-- Authoritative source of truth: anomaly_engine.py creates/owns these tables on
-- boot (see init_db() there). This file exists so server.py and
-- simulate_data.py can also ensure the schema is present if they start
-- before anomaly_engine.py, and so a human can inspect the schema without
-- reading the daemon source.
-- Column names/types/defaults here MUST stay in sync with anomaly_engine.py's
-- init_db() and DEFAULT_THRESHOLDS.

CREATE TABLE IF NOT EXISTS telemetry_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL,
    packet_id INTEGER NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    ph REAL NOT NULL,
    turbidity REAL NOT NULL,
    temperature REAL NOT NULL,
    water_level REAL NOT NULL,
    flowrate REAL NOT NULL,
    interval_volume REAL NOT NULL,
    is_anomaly INTEGER DEFAULT 0,
    threshold_flags TEXT,   -- parameters the if/else rules flagged, e.g. "ph,turbidity" (NULL = none)
    ml_flags TEXT,          -- parameters the ML model flagged, same format (NULL = none)
    volume_total REAL,      -- the node's own running Vol exactly as sent (resets after 60 s with no flow); NULL on older rows
    ml_details TEXT         -- JSON, per sensor: the ML's median, dev, step, score, sd, z and why it was (not) flagged
);
CREATE INDEX IF NOT EXISTS idx_telemetry_node_ts ON telemetry_logs(node_id, timestamp);

CREATE TABLE IF NOT EXISTS notification_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_threshold INTEGER DEFAULT 0,   -- 1 if the if/else rule check fired
    is_anomaly INTEGER DEFAULT 0,     -- 1 if the ML model fired
                                       -- both can be 1 on the same row
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    ph_snapshot REAL,
    turbidity_snapshot REAL,
    temp_snapshot REAL,
    is_acknowledged INTEGER DEFAULT 0,
    params TEXT,                      -- "ph,turbidity": parameters the alert is about, written by
                                       -- anomaly_engine.py (server.py backfills older rows)
    severity TEXT,                    -- DSS diagnoses only: CRITICAL / WARNING
    dss TEXT                          -- DSS diagnoses only: JSON from dss_rules.py (most common
                                       -- reasons, impact, actions, references)
);

CREATE TABLE IF NOT EXISTS system_thresholds (
    node_id INTEGER PRIMARY KEY,
    min_ph REAL DEFAULT 7.0,
    max_ph REAL DEFAULT 8.5,
    max_turbidity REAL DEFAULT 50.0,
    min_temp REAL DEFAULT 26.0,
    max_temp REAL DEFAULT 30.0,
    max_level REAL DEFAULT 90.0,
    min_level REAL DEFAULT 5.0,
    max_flow REAL DEFAULT 10.0
);

-- NOT part of edge_engine.py's schema — see server.py comment. The ONNX
-- pipeline bundles its own trained contamination rate, so this table is
-- informational/UI-only until the model export or edge_engine.py is changed
-- to read it at inference time.
CREATE TABLE IF NOT EXISTS ml_config (
    node_id INTEGER PRIMARY KEY,
    contamination REAL DEFAULT 0.02
);

-- Node label registry — used by the Admin "Node Management" view and the
-- sidebar's node dropdown. edge_engine.py identifies nodes by integer ID
-- only; this table just attaches a human-readable label to that ID.
CREATE TABLE IF NOT EXISTS nodes (
    node_id INTEGER PRIMARY KEY,
    label   TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- NOTE: intentionally no pre-seeded rows here. A node only exists once
-- edge_engine.py actually receives a DATA packet from it and calls
-- ensure_node_registered() -- see edge_engine.py for that logic. Nothing
-- in this schema assumes any specific node IDs exist ahead of time.
