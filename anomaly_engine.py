"""Three-model LoRa anomaly gateway.

This daemon follows the edge_engine.py packet, database, notification, and
ACK flow, but uses the three per-sensor ONNX models trained in the notebook:
one model each for pH, turbidity, and temperature.

Two ways to run it:

    python anomaly_engine.py          on the Raspberry Pi, listening on the real LoRa radio
    python anomaly_engine.py --sim    on a laptop with no radio: it listens on a local UDP
                                      port instead, and simulate_data.py plays the part of
                                      the sensor node (Arduino + ESP32), sending the same
                                      packets the ESP32 would transmit

Only the physical layer differs. In --sim mode the packets still go through the same
parser, ML models, threshold rules, database writes and ACK reply as real ones.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import queue
import re
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime
from collections import deque
from dataclasses import dataclass

import numpy as np
import onnxruntime as ort

import dss_rules

# Use the patched pySX127x that ships with this repo (SX127x/SX127x: reset on GPIO 22, DIO0 on GPIO 4,
# BOARD.SpiDev(spi_bus, spi_cs), edge-detect failures fall back to polling) ahead of a pip-installed
# pyLoRa, whose BOARD uses other pins and has a SpiDev() that takes no arguments.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "SX127x"))

# The LoRa library only exists on the Pi. Importing it lazily-but-safely lets this file load on a
# laptop for --sim; running WITHOUT --sim on a machine that lacks it fails with a clear message
# (see init_radio) instead of an ImportError before anything has started.
try:
    from SX127x.LoRa import BW, CODING_RATE, LoRa, MODE
    from SX127x.board_config import BOARD
    _SX127X_IMPORT_ERROR = None
except ImportError as exc:        # not on a Pi (or the library isn't installed)
    LoRa = object                 # lets LoRaGateway below still be defined; never instantiated in --sim
    _SX127X_IMPORT_ERROR = exc


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Fall back to paths relative to this script's own location -- NOT a
# hardcoded Pi-only path. The real Pi deployment is unaffected (the
# systemd service sets WASTEWATER_DB/WASTEWATER_MODELS_DIR explicitly), but
# this means running `python anomaly_engine.py` directly for local testing
# now works regardless of where you've actually put this folder, matching
# the same pattern server.py already uses.
DB_PATH = os.environ.get("WASTEWATER_DB", os.path.join(BASE_DIR, "database.db"))
MODELS_DIR = os.environ.get(
    "WASTEWATER_MODELS_DIR", os.path.join(BASE_DIR, "models", "training", "notebook")
)
RADIO_FREQ_MHZ = 434.0
RH_BROADCAST_ADDRESS = 0xFF
GATEWAY_ADDRESS = 0x01
TX_TURNAROUND_S = 0.15
TX_DONE_TIMEOUT_S = 4.0        # an ACK takes ~1.8 s on air at SF12 / CR 4/8, preamble 12
SIM_HOST = "127.0.0.1"        # --sim: where the engine listens for the simulated node
SIM_PORT = 9434                # (434 MHz, for the memory); must match simulate_data.py

# SX127x registers
REG_PA_CONFIG = 0x09
REG_FIFO_ADDR_PTR = 0x0D
REG_FIFO_TX_BASE_ADDR = 0x0E
REG_MODEM_CONFIG_2 = 0x1E
REG_PAYLOAD_LENGTH = 0x22

DEFAULT_THRESHOLDS = dict(
    min_ph=7.0,
    max_ph=8.5,
    max_turbidity=50.0,
    min_temp=26.0,
    max_temp=30.0,
    max_level=90.0,
    min_level=5.0,
    max_flow=10.0,
)
DEFAULT_ML_CONTAMINATION = 0.02
TEMP_HISTORY_SECONDS = 24 * 60 * 60
TEMP_MIN_HISTORY = 30

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("anomaly_engine")

# Updated Regex to match the ESP32 payload exactly
DATA_RE = re.compile(
    r"DATA\|NODE:(?P<node>\d+)\|ID:(?P<pid>\d+)\|pH:(?P<ph>[\d.]+)\|"
    r"Turb:(?P<turb>[\d.]+)\|Temp:(?P<temp>[\d.]+)\|Lvl:(?P<lvl>[\d.]+)\|"
    r"Flow:(?P<flow>[\d.]+)\|Vol:(?P<vol>[\d.]+)"
    r"(?:\|Time:(?P<time>[^|]+))?"
)
SYNC_RE = re.compile(r"SYNC_REQ\|NODE:(?P<node>\d+)")
# A reading the node stored while the Pi was down or out of range, sent later between live packets.
# Age = seconds between taking the reading and sending this packet.
LOG_RE = re.compile(
    r"LOG\|NODE:(?P<node>\d+)\|ID:(?P<pid>\d+)\|pH:(?P<ph>[\d.]+)\|"
    r"Turb:(?P<turb>[\d.]+)\|Temp:(?P<temp>[\d.]+)\|Lvl:(?P<lvl>[\d.]+)\|"
    r"Flow:(?P<flow>[\d.]+)\|Vol:(?P<vol>[\d.]+)"
    r"(?:\|Time:[^|]+)?\|Age:(?P<age>\d+)$"
)
NUMERIC_FIELDS = ("ph", "turb", "temp", "lvl", "flow", "vol")
# A LOG reading with the same node + packet ID within this many seconds of an existing row is the
# same reading (stored here, but its ACK was lost, so the node kept it and sent it again)
LOG_DUPLICATE_WINDOW_S = 300


@dataclass
class CorruptPacket:
    """Queued instead of the text when the radio flags a packet as damaged (failed payload CRC)."""
    text: str
radio_lock = threading.Lock()


def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.commit()
    return conn


def init_db(conn):
    cur = conn.cursor()
    cur.execute(
        """
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
            threshold_flags TEXT,
            ml_flags TEXT,
            volume_total REAL
        )
        """
    )
    columns = {row[1] for row in cur.execute("PRAGMA table_info(telemetry_logs)")}
    for column in ("threshold_flags", "ml_flags"):
        if column not in columns:
            cur.execute(f"ALTER TABLE telemetry_logs ADD COLUMN {column} TEXT")
    # The node's running Vol exactly as sent, for the dashboard's volume card
    # (interval_volume holds the same value).
    if "volume_total" not in columns:
        cur.execute("ALTER TABLE telemetry_logs ADD COLUMN volume_total REAL")
    # The ML's working for each sensor of the reading, as JSON (DevStepModels.details)
    if "ml_details" not in columns:
        cur.execute("ALTER TABLE telemetry_logs ADD COLUMN ml_details TEXT")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_threshold INTEGER DEFAULT 0,
            is_anomaly INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            ph_snapshot REAL,
            turbidity_snapshot REAL,
            temp_snapshot REAL,
            is_acknowledged INTEGER DEFAULT 0,
            params TEXT
        )
        """
    )
    # Databases created before these notification_logs columns existed
    columns = {row[1] for row in cur.execute("PRAGMA table_info(notification_logs)")}
    for column in ("params", "severity", "dss"):     # severity + dss: DSS diagnoses (dss_rules.py)
        if column not in columns:
            cur.execute(f"ALTER TABLE notification_logs ADD COLUMN {column} TEXT")
    cur.execute(
        """
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
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS nodes (
            node_id INTEGER PRIMARY KEY,
            label TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ml_config (
            node_id INTEGER PRIMARY KEY,
            contamination REAL DEFAULT 0.02
        )
        """
    )
    # The dev/step models' current baseline per node and sensor -- the batch median each reading is
    # compared with -- rewritten on every packet, for the dashboard's expanded chart view.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ml_baselines (
            node_id INTEGER NOT NULL,
            sensor TEXT NOT NULL,
            median REAL,
            readings INTEGER,
            ready INTEGER,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (node_id, sensor)
        )
        """
    )
    conn.commit()


def ensure_node_registered(conn, node_id: int):
    cur = conn.cursor()
    if cur.execute("SELECT 1 FROM nodes WHERE node_id = ?", (node_id,)).fetchone():
        return

    cur.execute(
        "INSERT OR IGNORE INTO nodes (node_id, label) VALUES (?, ?)",
        (node_id, f"Node {node_id}"),
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO system_thresholds
        (node_id, min_ph, max_ph, max_turbidity, min_temp, max_temp, max_level, min_level, max_flow)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node_id,
            DEFAULT_THRESHOLDS["min_ph"],
            DEFAULT_THRESHOLDS["max_ph"],
            DEFAULT_THRESHOLDS["max_turbidity"],
            DEFAULT_THRESHOLDS["min_temp"],
            DEFAULT_THRESHOLDS["max_temp"],
            DEFAULT_THRESHOLDS["max_level"],
            DEFAULT_THRESHOLDS["min_level"],
            DEFAULT_THRESHOLDS["max_flow"],
        ),
    )
    cur.execute(
        "INSERT OR IGNORE INTO ml_config (node_id, contamination) VALUES (?, ?)",
        (node_id, DEFAULT_ML_CONTAMINATION),
    )
    conn.commit()
    log.info("[%s] New node detected and auto-registered: node_id=%s", ts(), node_id)


def get_thresholds(conn, node_id: int) -> dict:
    keys = (
        "min_ph",
        "max_ph",
        "max_turbidity",
        "min_temp",
        "max_temp",
        "max_level",
        "min_level",
        "max_flow",
    )
    row = conn.execute(
        f"SELECT {', '.join(keys)} FROM system_thresholds WHERE node_id = ?",
        (node_id,),
    ).fetchone()
    return dict(zip(keys, row)) if row else DEFAULT_THRESHOLDS


def evaluate_thresholds(ph, turbidity, temp, level, flow, limits):
    alerts = []
    if level > limits["max_level"]:
        alerts.append(("level", "Tank Overflow Risk Detected", f"Water level {level}% exceeds the {limits['max_level']}% maximum safe capacity."))
    if level < limits["min_level"]:
        alerts.append(("level", "Pump Dry-Run Hazard", f"Water level {level}% is below the {limits['min_level']}% low-water protection trigger."))
    if flow > limits["max_flow"]:
        alerts.append(("flow", "Pipe Over-Pressure / Burst Hazard", f"Flow rate {flow} L/min exceeds the {limits['max_flow']} L/min ceiling for this node."))
    if ph < limits["min_ph"]:
        alerts.append(("ph", "Acidic Contamination Spike", f"pH {ph} dropped below the {limits['min_ph']} baseline threshold."))
    if ph > limits["max_ph"]:
        alerts.append(("ph", "Alkaline Contamination Spike", f"pH {ph} rose above the {limits['max_ph']} baseline threshold."))
    if turbidity > limits["max_turbidity"]:
        alerts.append(("turbidity", "Elevated Turbidity", f"Turbidity {turbidity} NTU exceeds the {limits['max_turbidity']} baseline threshold."))
    if temp < limits["min_temp"]:
        alerts.append(("temperature", "Low Temperature Advisory", f"Temperature {temp} C dropped below the {limits['min_temp']} C baseline threshold."))
    if temp > limits["max_temp"]:
        alerts.append(("temperature", "Thermal Drift Advisory", f"Temperature {temp} C exceeded the {limits['max_temp']} C comfort band."))
    return alerts


@dataclass(frozen=True)
class AnomalyFinding:
    sensor: str
    message: str


class AnomalyModels:
    """The older per-sensor models (see best_params.json): pH and turbidity judge the value itself,
    temperature its dev/step. Used only when devstep_params.json isn't there (see load_models)."""

    def __init__(self, models_dir: str = MODELS_DIR):
        with open(os.path.join(models_dir, "best_params.json"), encoding="utf-8") as params_file:
            params = json.load(params_file)["models"]

        self.specs = {
            "ph": (params["ph"]["onnx_file"], float(params["ph"]["score_cutoff"])),
            "turbidity": (params["turbidity_fnu"]["onnx_file"], float(params["turbidity_fnu"]["score_cutoff"])),
            "temperature": ("iforest_temperature_degC.onnx", 0.0),
        }
        self.sessions = {
            sensor: ort.InferenceSession(
                os.path.join(models_dir, filename),
                providers=["CPUExecutionProvider"],
            )
            for sensor, (filename, _) in self.specs.items()
        }
        self.temperature_history = {}

    def _score(self, sensor: str, features: list[float]) -> float:
        session = self.sessions[sensor]
        outputs = dict(
            zip(
                (output.name for output in session.get_outputs()),
                session.run(
                    None,
                    {
                        session.get_inputs()[0].name: np.array([features], dtype=np.float32)
                    },
                ),
            )
        )
        return float(np.ravel(outputs["scores"])[0])

    def evaluate(self, node_id: int, ph: float, turbidity: float, temp: float):
        findings = []
        ph_score = self._score("ph", [ph])
        if ph_score <= self.specs["ph"][1]:
            findings.append(AnomalyFinding("ph", f"pH model flagged {ph:.2f} as unusual (score {ph_score:.3f})."))

        turbidity_score = self._score("turbidity", [math.log1p(max(turbidity, 0.0))])
        if turbidity_score <= self.specs["turbidity"][1]:
            findings.append(AnomalyFinding("turbidity", f"Turbidity model flagged {turbidity:.1f} NTU as unusual (score {turbidity_score:.3f})."))

        history = self.temperature_history.setdefault(node_id, deque())
        now = time.time()
        history.append((now, temp))
        while history and history[0][0] <= now - TEMP_HISTORY_SECONDS:
            history.popleft()
        temperatures = [value for _, value in history]
        if len(temperatures) >= TEMP_MIN_HISTORY:
            deviation = temp - float(np.median(temperatures))
            step = temp - temperatures[-2]
            temperature_score = self._score("temperature", [deviation, step])
            if temperature_score < 0:
                findings.append(AnomalyFinding("temperature", f"Temperature model flagged {temp:.2f} C (score {temperature_score:.3f})."))
        return findings


DEVSTEP_PARAMS = "devstep_params.json"
# The ML baseline: the median of the current batch, at most this many hours back (one 4 h aeration +
# 2 h settling cycle). 0 = use the training setting from devstep_params.json (24 h).
ML_BASELINE_HOURS = float(os.environ.get("ARGUS_ML_BASELINE_HOURS", "6"))
# Deadband: an ML flag only counts when the reading moved at least this far (from its batch median or
# within step_minutes), in the sensor's own unit. The models flag sudden changes as small as ~0.08 pH,
# 0.3 °C or 30 % turbidity -- at or below what these sensors can resolve, so such flags can't be told
# apart from measurement noise. Basis:
#   pH 0.15        E-201-C electrode's listed +/-0.15 pH; ~5 ADC steps (5.832 pH/V x 4.89 mV = 0.03 pH)
#   temperature 0.5 °C   DS18B20 rated accuracy +/-0.5 °C (Maxim/Analog Devices datasheet)
#   turbidity 10 NTU     SEN0189 has no published accuracy; ~3 ADC steps (3000 NTU / 3.968 V x 4.89 mV = 3.7 NTU)
ML_DEADBAND = {
    "ph": float(os.environ.get("ARGUS_ML_DEADBAND_PH", "0.15")),
    "temperature": float(os.environ.get("ARGUS_ML_DEADBAND_TEMP", "0.5")),
    "turbidity": float(os.environ.get("ARGUS_ML_DEADBAND_TURB", "10")),
}
# devstep_params.json key -> (sensor name used in alerts and ml_flags, how the value is fed in,
# and back to the sensor's own unit -- for showing the median)
DEVSTEP_SENSORS = {
    "ph": ("ph", lambda v: v, lambda v: v),
    "turbidity_fnu": ("turbidity", lambda v: math.log1p(max(v, 0.0)), math.expm1),
    "temperature_degC": ("temperature", lambda v: v, lambda v: v),
}


def _duration_seconds(text: str) -> float:
    """'24h' / '90min' / '30s' -> seconds (the notebook's BASELINE setting)."""
    match = re.fullmatch(r"\s*([\d.]+)\s*(h|min|s)\s*", text)
    if not match:
        raise ValueError(f"unsupported baseline {text!r} in {DEVSTEP_PARAMS}")
    return float(match.group(1)) * {"h": 3600, "min": 60, "s": 1}[match.group(2)]


class DevStepModels:
    """The dev/step models from train_dev_step_models.ipynb (see devstep_params.json).

    They judge how a reading BEHAVES, not its value (the threshold rules do that):
      dev  = reading - median of this node's readings in the current batch, at most the last
             ML_BASELINE_HOURS (6 h; current one included)
      step = reading - this node's reading from step_minutes (2 min) earlier
    built exactly as in training, where readings were 2 minutes apart. With a node sending every
    30 s, step therefore looks 4 packets back, not at the previous packet; a step over 30 s would be
    much smaller than anything the models learned to flag. A sensor is judged once it has min_history
    (30) readings in its window, however fast they arrive -- at 1.5 s per packet that is ~45 s. Until
    the history spans step_minutes, step is taken from the oldest reading instead, so it covers less
    time than in training. The history lives in memory, so after a restart the models stay quiet for
    those first 30 readings.

    packet_minutes: None = time packets by the clock (the real radio). In --sim, simulate_data.py
    sends one 2-minute dataset row per packet, however fast, so each packet counts as that long.
    """

    def __init__(self, models_dir: str = MODELS_DIR, packet_minutes: float | None = None):
        with open(os.path.join(models_dir, DEVSTEP_PARAMS), encoding="utf-8") as params_file:
            params = json.load(params_file)
        if params["features"] != ["dev", "step"] or params.get("flag_if", "score < 0") != "score < 0":
            raise ValueError(f"{DEVSTEP_PARAMS}: expected features [dev, step] flagged on score < 0")
        # The baseline window: one 6 h treatment cycle (ML_BASELINE_HOURS), not the 24 h the models were
        # trained with -- the history also restarts each batch (batch_discharged), so this only caps how
        # far back it reaches if a discharge goes undetected
        self.baseline_s = ML_BASELINE_HOURS * 3600 if ML_BASELINE_HOURS else _duration_seconds(params["baseline"])
        self.step_s = float(params["step_minutes"]) * 60
        self.min_history = int(params["min_history"])
        self.packet_s = None if packet_minutes is None else packet_minutes * 60
        self.sensors = {}             # sensor name -> (ONNX session, value transform)
        self.to_unit = {}             # sensor name -> model input value back to the sensor's unit
        for key, model in params["models"].items():
            name, transform, to_unit = DEVSTEP_SENSORS[key]
            session = ort.InferenceSession(os.path.join(models_dir, model["onnx_file"]),
                                           providers=["CPUExecutionProvider"])
            self.sensors[name] = (session, transform)
            self.to_unit[name] = to_unit
        self.history = {}             # (node_id, sensor) -> deque of (seconds, model input value)
        self.baselines = {}           # (node_id, sensor) -> (median as model input, readings, judged yet)
        # node_id -> {sensor: the latest reading's working (median, dev, step, score, z, why)}, which
        # handle_data saves with the reading as telemetry_logs.ml_details
        self.details = {}
        self.packet_clock = {}        # node_id -> seconds, when packet_minutes is set

    def _now(self, node_id: int) -> float:
        if self.packet_s is None:
            return time.time()
        self.packet_clock[node_id] = self.packet_clock.get(node_id, 0.0) + self.packet_s
        return self.packet_clock[node_id]

    def _features(self, node_id: int, sensor: str, value: float, now: float):
        """Record the reading; return (dev, step, the dev of the reading step compares with), or
        None while the history is too short."""
        history = self.history.setdefault((node_id, sensor), deque())
        history.append((now, value))
        while history[0][0] <= now - self.baseline_s:
            history.popleft()
        median = float(np.median([v for _, v in history]))
        ready = len(history) >= self.min_history
        self.baselines[(node_id, sensor)] = (median, len(history), ready)
        if not ready:
            return None
        # the newest earlier reading at least step_minutes old (5% slack for packet timing jitter),
        # or the oldest one while the history is still shorter than that
        cutoff = now - 0.95 * self.step_s
        previous = next((v for t, v in reversed(history) if t <= cutoff), history[0][1])
        return value - median, value - previous, previous - median

    def reset(self, node_id: int):
        """Forget a node's history: the next readings are a new batch of water, learned from scratch."""
        for key in [k for k in self.history if k[0] == node_id]:
            del self.history[key]
        for key in [k for k in self.baselines if k[0] == node_id]:
            del self.baselines[key]

    def baseline_rows(self, node_id: int):
        """(sensor, batch median in the sensor's unit, readings in the window, judged yet) per sensor."""
        return [(sensor, self.to_unit[sensor](median), count, int(ready))
                for (node, sensor), (median, count, ready) in self.baselines.items() if node == node_id]

    def _change_in_unit(self, node_id: int, sensor: str, value: float, dev: float, step: float) -> float:
        """The larger of the reading's distance from its batch median and its change over step_minutes,
        in the sensor's own unit (turbidity: NTU, converted back from the model's log scale)."""
        if sensor != "turbidity":
            return max(abs(dev), abs(step))
        median = self.to_unit[sensor](self.baselines[(node_id, sensor)][0])
        previous = math.expm1(math.log1p(max(value, 0.0)) - step)
        return max(abs(value - median), abs(value - previous))

    @staticmethod
    def _score(session, dev: float, step: float) -> float:
        return float(np.ravel(session.run(
            ["scores"], {session.get_inputs()[0].name: np.array([[dev, step]], dtype=np.float32)})[0])[0])

    def evaluate(self, node_id: int, ph: float, turbidity: float, temp: float):
        now = self._now(node_id)
        readings = {"ph": ph, "turbidity": turbidity, "temperature": temp}
        findings, details = [], {}
        for sensor, (session, transform) in self.sensors.items():
            value = readings[sensor]
            features = self._features(node_id, sensor, transform(value), now)
            median_in, count, _ready = self.baselines[(node_id, sensor)]
            detail = details[sensor] = {"value": value, "median": round(self.to_unit[sensor](median_in), 4),
                                        "readings": count, "flagged": False}
            if features is None:
                detail["why"] = (f"Not judged yet: the model waits for {self.min_history} readings in the batch "
                                 f"({count} so far).")
                continue
            dev, step, previous_dev = features
            score = raw_score = self._score(session, dev, step)
            # Recovery, not a new anomaly: step compares with the reading step_minutes earlier, so a
            # reading back to normal right after an anomaly shows a large step only because that
            # earlier reading was the anomaly. A reading closer to the median than that earlier one
            # was, and normal with its step left out, isn't flagged. A move away from the median
            # still is.
            recovering = score < 0 and abs(dev) < abs(previous_dev) and self._score(session, dev, 0.0) >= 0
            if recovering:
                score = 0.0
            # Deadband: a change smaller than the sensor can resolve isn't evidence of anything
            change = self._change_in_unit(node_id, sensor, value, dev, step)
            deadband = ML_DEADBAND.get(sensor, 0.0)
            below_deadband = score < 0 and change < deadband
            if below_deadband:
                score = 0.0
            # For people, all in the sensor's unit (turbidity: NTU, not the model's log scale): dev, step,
            # and the empirical rule's z = (reading - mean) / standard deviation of this batch window --
            # for checking the model against; the model itself doesn't use it
            to_unit = self.to_unit[sensor]
            median = to_unit(median_in)
            previous = math.expm1(math.log1p(max(value, 0.0)) - step) if sensor == "turbidity" else value - step
            window = [to_unit(v) for _, v in self.history[(node_id, sensor)]]
            mean, sd = float(np.mean(window)), float(np.std(window))
            detail.update(dev=round(value - median, 4), step=round(value - previous, 4), score=round(raw_score, 4),
                          mean=round(mean, 4), sd=round(sd, 4), z=round((value - mean) / sd, 2) if sd > 0 else None,
                          change=round(change, 4), deadband=deadband, flagged=score < 0)
            if score < 0:
                detail["why"] = (f"Anomaly: model score {raw_score:.3f} is below 0, and the change of {change:.2f} is "
                                 f"at least the sensor's resolution of {deadband:g}.")
                findings.append(AnomalyFinding(sensor, self._describe(node_id, sensor, value, dev, step, raw_score)))
            elif recovering:
                detail["why"] = (f"Not an anomaly: the score ({raw_score:.3f}) is below 0 only because of the jump from "
                                 f"the unusual reading {self.step_s / 60:g} min earlier; this reading is moving back "
                                 f"toward the median, and scores normal without that step.")
            elif below_deadband:
                detail["why"] = (f"Not an anomaly: the score ({raw_score:.3f}) is below 0, but the change of {change:.2f} "
                                 f"is smaller than the sensor's resolution of {deadband:g}, so it can't be told apart "
                                 f"from measurement noise.")
            else:
                detail["why"] = (f"Normal: model score {raw_score:.3f} is not below 0 -- this distance from the median "
                                 f"and change over {self.step_s / 60:g} min are within what the model learned as normal.")
        self.details[node_id] = details
        return findings

    def _describe(self, node_id: int, sensor: str, value: float, dev: float, step: float, score: float) -> str:
        """The evidence for a flag: the reading against its batch median, its change over step_minutes,
        and the model's score (an Isolation Forest flags scores below 0; the lower, the more unusual)."""
        span = f"{self.step_s / 60:g} minutes"
        median = self.to_unit[sensor](self.baselines[(node_id, sensor)][0])
        side = "above" if dev > 0 else "below"
        move = "rose" if step > 0 else "fell"
        evidence = f"Model score {score:.3f} (below 0 = anomaly)."
        if sensor == "turbidity":       # the model works on log1p(NTU); report in NTU like the others
            previous = math.expm1(math.log1p(max(value, 0.0)) - step)      # the reading step_minutes earlier
            return (f"{value:.1f} NTU is {abs(value - median):.1f} NTU {side} its batch median of {median:.1f} NTU, "
                    f"and {move} {abs(value - previous):.1f} NTU within {span}. {evidence}")
        unit = "" if sensor == "ph" else " °C"
        return (f"{value:.2f}{unit} is {abs(dev):.2f}{unit} {side} its batch median of {median:.2f}{unit}, "
                f"and {move} {abs(step):.2f}{unit} within {span}. {evidence}")


def load_models(models_dir: str = MODELS_DIR, sim: bool = False):
    """The dev/step models when devstep_params.json is there, else the older best_params.json ones."""
    path = os.path.join(models_dir, DEVSTEP_PARAMS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as params_file:
            step_minutes = float(json.load(params_file)["step_minutes"])
        models = DevStepModels(models_dir, packet_minutes=step_minutes if sim else None)
        log.info("[%s] ML: dev/step models from %s (step over %g min%s)", ts(), DEVSTEP_PARAMS, step_minutes,
                 ", one packet = one simulated reading" if sim else "")
        return models
    log.info("[%s] ML: %s not found, using the best_params.json models", ts(), DEVSTEP_PARAMS)
    return AnomalyModels(models_dir)


class LoRaGateway(LoRa):
    def __init__(self, rx_queue, verbose=False):
        super().__init__(verbose)
        self.rx_queue = rx_queue
        self._tx_id = 0
        self.set_mode(MODE.SLEEP)
        self.set_dio_mapping([0] * 6)

    def on_rx_done(self):
        crc_error = self.get_irq_flags().get("crc_error")
        self.clear_irq_flags(RxDone=1, PayloadCrcError=1)
        payload = self.read_payload(nocheck=True)
        decoded = bytes(payload[4:]).decode("utf-8", "ignore").strip() if payload else ""
        # A failed CRC only shows up if the node sends one (RadioHead: setPayloadCRC(true));
        # without it, damage is only caught when the text no longer parses (worker_loop).
        if crc_error:
            self.rx_queue.put(CorruptPacket(decoded))
        else:
            self.rx_queue.put(decoded)      # an empty payload is reported as corrupted too
        self.set_mode(MODE.RXCONT)

    def transmit(self, text: str):
        self._tx_id = (self._tx_id + 1) % 256
        payload = [RH_BROADCAST_ADDRESS, GATEWAY_ADDRESS, self._tx_id, 0]
        payload.extend(text.encode("utf-8"))
        self.set_mode(MODE.STDBY)
        time.sleep(0.01)
        # DIO0 -> TxDone
        self.set_dio_mapping([1, 0, 0, 0, 0, 0])
        # Point the FIFO at the TX base address and set the payload length explicitly
        self.set_register(REG_FIFO_ADDR_PTR, self.get_register(REG_FIFO_TX_BASE_ADDR))
        self.set_register(REG_PAYLOAD_LENGTH, len(payload))
        self.write_payload(payload)
        self.set_mode(MODE.TX)
        sent = False
        deadline = time.time() + TX_DONE_TIMEOUT_S
        while time.time() < deadline:
            if self.get_irq_flags().get("tx_done"):
                sent = True
                break
            time.sleep(0.005)
        self.clear_irq_flags(TxDone=1)
        # Guard time so the final CRC symbols leave the antenna before switching modes
        time.sleep(0.05)
        # DIO0 -> RxDone
        self.set_dio_mapping([0] * 6)
        self.reset_ptr_rx()
        self.set_mode(MODE.RXCONT)
        return sent

    def run(self, stop_event):
        self.reset_ptr_rx()
        self.set_mode(MODE.RXCONT)
        while not stop_event.is_set():
            time.sleep(0.1)
            with radio_lock:
                if self.get_irq_flags().get("rx_done"):
                    self.on_rx_done()

    def close(self):
        self.set_mode(MODE.SLEEP)
        BOARD.teardown()


class SimRadio:
    """Stands in for the LoRa radio when there isn't one (laptop testing, --sim).

    The ESP32 is simulate_data.py, talking over UDP on localhost. Packets are the very
    same text the ESP32 would transmit (DATA|... and SYNC_REQ|...), so everything
    after this class -- queue, parser, models, thresholds, database, ACK -- is the real code.

    Like LoRa, the medium is BROADCAST: a reply is heard by every node that has talked
    to us, and each node accepts any ACK, exactly as the ESP32 firmware does.
    """

    def __init__(self, rx_queue, host=SIM_HOST, port=SIM_PORT):
        self.rx_queue = rx_queue
        self.address = (host, port)
        self._closed = False
        self._listeners = set()
        self._lock = threading.Lock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # No SO_REUSEADDR on purpose: a second engine must fail to start, not silently share the port.
        self.sock.bind(self.address)
        self.sock.settimeout(0.2)

    def run(self, stop_event):
        while not stop_event.is_set() and not self._closed:
            try:
                data, addr = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue          # Windows: an earlier reply was sent to a node that has since exited
            except OSError:
                if self._closed:
                    break
                time.sleep(0.1)
                continue
            text = data.decode("utf-8", "ignore").strip()
            if text:
                with self._lock:
                    self._listeners.add(addr)
                self.rx_queue.put(text)

    def transmit(self, text: str):
        data = text.encode("utf-8")
        with self._lock:
            listeners = list(self._listeners)
        sent = False
        for addr in listeners:
            try:
                self.sock.sendto(data, addr)
                sent = True
            except OSError:
                with self._lock:
                    self._listeners.discard(addr)
        return sent

    def close(self):
        self._closed = True
        self.sock.close()


def init_sim_radio(rx_queue, host=SIM_HOST, port=SIM_PORT):
    try:
        return SimRadio(rx_queue, host, port)
    except OSError as exc:
        sys.exit(f"Can't listen on udp://{host}:{port} ({exc}). "
                 f"Is another anomaly_engine.py already running? Use --sim-port to pick another port.")


def require_radio_library():
    """Exit with a clear message when there's no LoRa library and --sim wasn't asked for."""
    if _SX127X_IMPORT_ERROR is not None:
        sys.exit(f"The LoRa radio library isn't available here ({_SX127X_IMPORT_ERROR}).\n"
                 f"This is normal on a laptop: run  python anomaly_engine.py --sim  together with simulate_data.py.\n"
                 f"On the Raspberry Pi, install the radio libraries (see SETUP_RASPI.md).")


def init_radio(rx_queue):
    require_radio_library()
    import RPi.GPIO as GPIO
    
    # 1. Manually reset the LoRa module (Fixes the AttributeError)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(22, GPIO.OUT)
    GPIO.output(22, GPIO.LOW)
    time.sleep(0.05)
    GPIO.output(22, GPIO.HIGH)
    time.sleep(0.05)

    # 2. Standard Board Setup
    BOARD.setup()
    BOARD.DIO0 = 4
    try:
        BOARD.SpiDev(spi_bus=0, spi_cs=0)
    except Exception:
        log.exception("BOARD.SpiDev() error")
        
    radio = LoRaGateway(rx_queue, verbose=False)
    radio.set_freq(434.0)
    # Must match the ESP32 exactly. SF12 needs LowDataRateOptimize: its symbols (32.8 ms at 125 kHz)
    # exceed the 16 ms limit, and the ESP32 sets the same bit.
    radio.set_spreading_factor(12)
    radio.set_bw(BW.BW125)
    radio.set_coding_rate(CODING_RATE.CR4_8)
    radio.set_low_data_rate_optim(True)
    radio.set_sync_word(0x12)
    radio.set_preamble(12)
    radio.set_rx_crc(True)
    
    # Enable PA_BOOST and TX CRC
    radio.set_register(0x09, 0x8F)
    modem_config_2 = radio.get_register(0x1E)
    radio.set_register(0x1E, modem_config_2 | 0x04)
    
    return radio


def radio_send(radio, text: str):
    # Give the ESP32 time to switch from TX back to RX before we reply
    time.sleep(TX_TURNAROUND_S)
    with radio_lock:
        return radio.transmit(text)


# One ML event, one notification: a model keeps flagging for as long as a reading stays unusual
# (its dev feature stays far from the batch median for hours after a lasting shift). ML flags are
# reported as DSS diagnoses (dss_rules.py), and a diagnosis only raises a notification if the same
# one wasn't raised on this node within the last ML_ALERT_QUIET_SECONDS; every repeat restarts that
# window. Every flagged reading is still stored in telemetry_logs.ml_flags, so the chart shows them
# all. Threshold alerts are not affected.
ML_ALERT_QUIET_SECONDS = 5 * 60

# A new batch starts once the treated water has been discharged. Each batch is different water,
# so the ML relearns its baseline from the new water instead of comparing it with the last batch.
# The flow sensor measures the outflow; the node's Vol is the running total of the current
# discharge (reset by the Arduino after 60 s without flow). When the flow stops after at least
# BATCH_DISCHARGE_LITRES went out, the batch has left: the ML forgets that node's history and
# warms up on the next water (min_history readings; the threshold rules stay active throughout).
BATCH_DISCHARGE_LITRES = float(os.environ.get("ARGUS_BATCH_DISCHARGE_LITRES", "10.0"))
_discharged: dict[int, float] = {}    # node_id -> litres out so far in the current discharge


def batch_discharged(node_id: int, flow: float, vol: float) -> float | None:
    """The litres discharged if this reading ends a discharge of at least BATCH_DISCHARGE_LITRES."""
    if flow > 0:
        _discharged[node_id] = max(_discharged.get(node_id, 0.0), vol)
        return None
    litres = _discharged.pop(node_id, 0.0)
    return litres if litres >= BATCH_DISCHARGE_LITRES else None
_last_ml_flag: dict[tuple[int, str], float] = {}     # (node_id, diagnosis key) -> time.monotonic() of the last one


def diagnoses_to_notify(node_id: int, diagnoses, now: float):
    """The diagnoses that start a new event; records every one as that diagnosis's latest."""
    new = []
    for diagnosis in diagnoses:
        key = diagnosis[0]
        last = _last_ml_flag.get((node_id, key))
        if last is None or now - last > ML_ALERT_QUIET_SECONDS:
            new.append(diagnosis)
        _last_ml_flag[(node_id, key)] = now
    return new


PARAM_NAMES = {"ph": "pH", "turbidity": "Turbidity", "temperature": "Temperature", "level": "Water Level", "flow": "Flow"}


def threshold_dss(ph, temp, turbidity, level, flow, limits, rule_alerts):
    """The DSS guidance (most common reasons, actions, references) for exactly the limits crossed, as JSON."""
    guidance = dss_rules.threshold_guidance(
        {"ph": ph, "temperature": temp, "turbidity": turbidity, "level": level, "flow": flow},
        limits, [key for key, _, _ in rule_alerts])
    return json.dumps(guidance) if guidance else None


def alert_title(rule_alerts) -> str:
    """One threshold alert keeps its own title; several name their sensors, e.g.
    "Threshold breach: pH, Turbidity, Water Level"."""
    if len(rule_alerts) == 1:
        return rule_alerts[0][1]
    names = [PARAM_NAMES.get(key, key) for key in dict.fromkeys(key for key, _, _ in rule_alerts)]
    return f"Threshold breach: {', '.join(names)}"


def handle_data(conn, models, radio, fields):
    node_id = int(fields["node"])
    packet_id = int(fields["pid"])
    ph = float(fields["ph"])
    turbidity = float(fields["turb"])
    temp = float(fields["temp"])
    level = float(fields["lvl"])
    flow = float(fields["flow"])
    vol = float(fields["vol"])        # the node's running total, stored exactly as sent

    ensure_node_registered(conn, node_id)
    discharged = batch_discharged(node_id, flow, vol)
    if discharged is not None and hasattr(models, "reset"):
        models.reset(node_id)
        log.info("[%s][NODE %s] %.1f L discharged: new batch, ML relearning the baseline from the new water",
                 ts(), node_id, discharged)
    ml_findings = models.evaluate(node_id, ph, turbidity, temp)
    limits = get_thresholds(conn, node_id)
    rule_alerts = evaluate_thresholds(ph, turbidity, temp, level, flow, limits)
    is_anomaly = bool(ml_findings or rule_alerts)
    threshold_flags = ",".join(dict.fromkeys(key for key, _, _ in rule_alerts)) or None
    ml_flags = ",".join(finding.sensor for finding in ml_findings) or None
    # The ML flags, read together with the node's limits as "most common reasons" diagnoses
    diagnoses = dss_rules.diagnose(
        {"ph": ph, "temperature": temp, "turbidity": turbidity},
        {finding.sensor: finding.message for finding in ml_findings},
        limits,
    )
    dss_alerts = diagnoses_to_notify(node_id, diagnoses, time.monotonic())
    ml_details = getattr(models, "details", {}).get(node_id)      # the older models keep none

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO telemetry_logs
        (node_id, packet_id, ph, turbidity, temperature, water_level, flowrate,
         interval_volume, is_anomaly, threshold_flags, ml_flags, volume_total, ml_details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (node_id, packet_id, ph, turbidity, temp, level, flow,
         vol, int(is_anomaly), threshold_flags, ml_flags, vol, json.dumps(ml_details) if ml_details else None),
    )
    if hasattr(models, "baseline_rows"):             # the older models keep no medians
        cur.executemany(
            """INSERT OR REPLACE INTO ml_baselines (node_id, sensor, median, readings, ready, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            [(node_id, *row) for row in models.baseline_rows(node_id)],
        )

    if rule_alerts:
        titles = [title for _, title, _ in rule_alerts]
        messages = [f"[Threshold - {title}] {message}" for _, title, message in rule_alerts]
        title = alert_title(rule_alerts)
        cur.execute(
            """INSERT INTO notification_logs
            (node_id, is_threshold, is_anomaly, title, message,
             ph_snapshot, turbidity_snapshot, temp_snapshot, params, dss)
            VALUES (?, 1, 0, ?, ?, ?, ?, ?, ?, ?)""",
            (node_id, title, " ".join(messages), ph, turbidity, temp,
             ",".join(dict.fromkeys(key for key, _, _ in rule_alerts)),
             threshold_dss(ph, temp, turbidity, level, flow, limits, rule_alerts)),
        )
    for _, title, sensors, dss in dss_alerts:
        cur.execute(
            """INSERT INTO notification_logs
            (node_id, is_threshold, is_anomaly, title, message,
             ph_snapshot, turbidity_snapshot, temp_snapshot, params, severity, dss)
            VALUES (?, 0, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (node_id, title, dss["summary"], ph, turbidity, temp, ",".join(sensors),
             dss["severity"], json.dumps(dss)),
        )
    conn.commit()

    # Respond in the exact format the ESP32 expects for time synchronization
    now_str = datetime.now().strftime("%H:%M:%S")
    radio_send(radio, f"ACK,{now_str}")
    log.info("[%s][NODE %s][PACKET %s] Rx Success | Anomaly: %s | ACK Dispatched.", ts(), node_id, packet_id, int(is_anomaly))


def handle_log(conn, radio, fields):
    """A stored reading sent late (LOG|...): saved at the time it was measured -- arrival minus
    Age -- so charts, logs and reports put it where it belongs. Only the threshold rules judge it:
    the ML models compare each reading with the ones just before it in real time, and a late one
    would skew their batch median and 2-minute step. An alert it raises carries the measurement
    time and says how late it arrived."""
    node_id = int(fields["node"])
    packet_id = int(fields["pid"])
    age = int(fields["age"])
    ph, turbidity, temp = float(fields["ph"]), float(fields["turb"]), float(fields["temp"])
    level, flow, vol = float(fields["lvl"]), float(fields["flow"]), float(fields["vol"])
    measured_at = f"-{age} seconds"   # SQLite datetime('now', ...) modifier: the same UTC clock as live rows

    ensure_node_registered(conn, node_id)
    cur = conn.cursor()
    duplicate = cur.execute(
        """SELECT 1 FROM telemetry_logs WHERE node_id = ? AND packet_id = ?
        AND ABS(strftime('%s', timestamp) - strftime('%s', datetime('now', ?))) <= ?""",
        (node_id, packet_id, measured_at, LOG_DUPLICATE_WINDOW_S),
    ).fetchone()
    if duplicate:
        radio_send(radio, f"ACK,{datetime.now().strftime('%H:%M:%S')}")   # so the node stops resending it
        log.info("[%s][NODE %s][LOG %s] Already stored -> ACK (not stored again).", ts(), node_id, packet_id)
        return

    limits = get_thresholds(conn, node_id)
    rule_alerts = evaluate_thresholds(ph, turbidity, temp, level, flow, limits)
    threshold_flags = ",".join(dict.fromkeys(key for key, _, _ in rule_alerts)) or None
    cur.execute(
        """INSERT INTO telemetry_logs
        (node_id, packet_id, timestamp, ph, turbidity, temperature, water_level, flowrate,
         interval_volume, is_anomaly, threshold_flags, ml_flags, volume_total)
        VALUES (?, ?, datetime('now', ?), ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
        (node_id, packet_id, measured_at, ph, turbidity, temp, level, flow,
         vol, int(bool(rule_alerts)), threshold_flags, vol),
    )
    if rule_alerts:
        late = f"Delivered late: measured {age // 60} min {age % 60} s before it reached the gateway."
        titles = [title for _, title, _ in rule_alerts]
        title = alert_title(rule_alerts)
        messages = [f"[Threshold - {title}] {message}" for _, title, message in rule_alerts]
        cur.execute(
            """INSERT INTO notification_logs
            (node_id, timestamp, is_threshold, is_anomaly, title, message,
             ph_snapshot, turbidity_snapshot, temp_snapshot, params, dss)
            VALUES (?, datetime('now', ?), 1, 0, ?, ?, ?, ?, ?, ?, ?)""",
            (node_id, measured_at, title, " ".join(messages + [late]), ph, turbidity, temp,
             ",".join(dict.fromkeys(key for key, _, _ in rule_alerts)),
             threshold_dss(ph, temp, turbidity, level, flow, limits, rule_alerts)),
        )
    conn.commit()

    radio_send(radio, f"ACK,{datetime.now().strftime('%H:%M:%S')}")
    log.info("[%s][NODE %s][LOG %s] Stored reading from %ss ago | Threshold flags: %s | ACK Dispatched.",
             ts(), node_id, packet_id, age, threshold_flags or "none")


def handle_corrupt(radio, reason: str, payload: str):
    """Tell the node its packet arrived damaged, so it can resend at once instead of waiting out
    its ACK timeout. Doesn't start with "ACK," so the ESP32 never takes it as a delivery (nor
    sets its clock from it). reason: CRC (radio checksum failed), FORMAT (not a DATA/LOG/SYNC_REQ
    packet), VALUE (a DATA/LOG field that isn't a number)."""
    radio_send(radio, f"NACK,{reason}")
    log.warning("[%s] Corrupted packet (%s) -> NACK,%s: %r", ts(), reason, reason, payload)


def handle_sync(radio, node_id: int):
    now_str = datetime.now().strftime("%H:%M:%S")
    radio_send(radio, f"ACK,{now_str}")
    log.info("[%s][NODE %s] SYNC_REQ -> ACK,%s", ts(), node_id, now_str)


def worker_loop(conn, models, radio, rx_queue, stop_event):
    while not stop_event.is_set():
        try:
            payload = rx_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            if isinstance(payload, CorruptPacket):
                handle_corrupt(radio, "CRC", payload.text)
                continue
            data_match = DATA_RE.match(payload)
            sync_match = SYNC_RE.match(payload)
            log_match = LOG_RE.match(payload)
            if data_match or log_match:
                fields = (data_match or log_match).groupdict()
                try:
                    for key in NUMERIC_FIELDS:      # e.g. "7..3": matches [\d.]+ but isn't a number
                        float(fields[key])
                except ValueError:
                    handle_corrupt(radio, "VALUE", payload)
                    continue
                if log_match:
                    handle_log(conn, radio, fields)
                else:
                    handle_data(conn, models, radio, fields)
            elif sync_match:
                handle_sync(radio, int(sync_match.group("node")))
            else:
                handle_corrupt(radio, "FORMAT", payload)
        except Exception:
            log.exception("[%s] Error processing packet %r", ts(), payload)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="ARGUS gateway: LoRa ingestion + anomaly detection -> database.db")
    p.add_argument("--sim", action="store_true", default=os.environ.get("ARGUS_SIM") == "1",
                   help="no radio: listen on a local UDP port for simulate_data.py instead (also ARGUS_SIM=1)")
    p.add_argument("--sim-host", default=os.environ.get("ARGUS_SIM_HOST", SIM_HOST),
                   help=f"--sim: address to listen on (default {SIM_HOST})")
    p.add_argument("--sim-port", type=int, default=int(os.environ.get("ARGUS_SIM_PORT", SIM_PORT)),
                   help=f"--sim: UDP port to listen on (default {SIM_PORT}; simulate_data.py must use the same)")
    return p.parse_args(argv)


def main():
    args = parse_args()
    if not args.sim:
        require_radio_library()       # fail now, before loading the models
    conn = get_connection()
    init_db(conn)
    models = load_models(sim=args.sim)
    rx_queue = queue.Queue()
    radio = init_sim_radio(rx_queue, args.sim_host, args.sim_port) if args.sim else init_radio(rx_queue)
    stop_event = threading.Event()
    rx_thread = threading.Thread(target=radio.run, args=(stop_event,), daemon=True)
    worker_thread = threading.Thread(target=worker_loop, args=(conn, models, radio, rx_queue, stop_event), daemon=True)
    rx_thread.start()
    worker_thread.start()
    if args.sim:
        log.info("[%s] anomaly_engine.py running in SIMULATION mode (no radio): listening for simulate_data.py "
                 "on udp://%s:%d", ts(), args.sim_host, args.sim_port)
    else:
        log.info("[%s] anomaly_engine.py running on %.1f MHz", ts(), RADIO_FREQ_MHZ)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down anomaly_engine.py ...")
        stop_event.set()
        rx_thread.join(timeout=3)
        worker_thread.join(timeout=3)
        radio.close()
        conn.close()


if __name__ == "__main__":
    main()