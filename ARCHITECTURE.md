# ARGUS — Architecture

Offline, edge-native wastewater/water-quality monitoring and anomaly
detection system. Real-time sensor telemetry over LoRa, dual-layer anomaly
detection (deterministic thresholds + ML), a live web dashboard served
entirely on a local network with no internet dependency.

---

## 1. System Diagram

```
                          [ PHYSICAL LAYER ]
    Arduino Uno/Nano (sensors only)           Raspberry Pi 4B (gateway)
    ┌─────────────────────────────┐
    │ pH, Turbidity   (analog)    │
    │ DS18B20 Temp    (1-Wire)    │
    │ JSN-SR04T Level (ultrasonic)│
    │ YF-S201 Flow    (interrupt) │
    └──────────────┬──────────────┘
                    │ hardware Serial, 115200 baud, one line / 1.5 s
                    │ 5V→3.3V divider on Arduino TX
                    ▼
    ┌─────────────────────────────┐
    │ ESP32 (LoRa node + display) │
    │ - SH1106 OLED: readings,    │
    │   LoRa status, clock        │
    │ - 3 pilot-light relays      │
    │   (green / orange / red)    │
    │ - Wraps readings into DATA| │
    │   packets, waits for ACK    │
    └──────────────┬──────────────┘
                    │ LoRa 434.0 MHz, SF12 / BW125 / CR4-8, sync word 0x12
                    ▼
                          [ GATEWAY DAEMON — Raspberry Pi ]
                     Process 1: anomaly_engine.py (systemd)
        ┌─────────────────────────────────────────────────────┐
        │ RX thread: SPI radio listener (pySX127x) → queue      │
        │ Worker thread, per packet:                            │
        │   1. Auto-register node if never seen before           │
        │   2. Three per-sensor ONNX Isolation Forests run FIRST │
        │   3. If/else threshold check runs SECOND, independent  │
        │   4. Merge into ONE notification: is_threshold +       │
        │      is_anomaly (both can be 1 on the same row)        │
        │   5. Store Vol exactly as the node sent it             │
        │   6. Reply ACK,HH:MM:SS (also answers SYNC_REQ)         │
        └─────────────────────────────────────────────────────┘
                                    │
                                    ▼
                        [ PERSISTENCE LAYER ]
                   SQLite database.db (WAL mode)
     telemetry_logs · notification_logs · system_thresholds
                    · nodes · ml_config
                                    │
                                    ▼
                       [ APPLICATION LAYER ]
                 Process 2: server.py — FastAPI (systemd)
        ┌─────────────────────────────────────────────────────┐
        │ Polls DB → broadcasts new rows over WebSocket          │
        │   (/ws/telemetry/live)                                 │
        │ REST: telemetry, notifications, reports (PDF+QR),      │
        │       admin (thresholds/ml-config/nodes),               │
        │       /api/nodes/status (real last-seen connectivity)   │
        │ Serves built React SPA on the same port (3000)          │
        │ Never touches the radio or runs ML directly             │
        └─────────────────────────────────────────────────────┘
                                    │
                                    ▼
                         [ PRESENTATION LAYER ]
              React (Vite + Tailwind) SPA — 5 views + retractable nav
                          │                         │
                          ▼                         ▼
              Pi's local LCD (Chromium        Phones on the Pi's
              kiosk, localhost:3000)          Wi-Fi hotspot (192.168.4.1:3000)


                    [ OFFLINE TRAINING PIPELINE — run separately ]
     train_anomaly_model.py (real telemetry_logs OR domain-grounded
     generated data) → anomaly_pipeline.joblib → convert_to_onnx.py →
     anomaly_pipeline.onnx (copied to models/ on the Pi)
     test_anomaly_model.py — manual CLI verification before deploying
```

---

## 2. Hardware Layer

### 2.1 Arduino (sensors only) — `arduino_sensor_node.ino` (kept outside this repo)

| Signal | Pin | Notes |
|---|---|---|
| pH (analog) | A2 | Direct 5V ADC read, 60-sample average |
| Turbidity (analog) | A3 | Gravity module, "D↔A" switch on **A**. 60 samples, sorted, outer 20% trimmed each side, no EMA |
| Ultrasonic Trigger | 5 | JSN-SR04T. The pin is released (`INPUT`) right after each pulse so it never sits LOW while the DS18B20 is being read |
| Ultrasonic Echo | 6 | Direct, no level shifting (Arduino is native 5V) |
| DS18B20 Temp (1-Wire) | 4 | Raw 1-Wire protocol (presence check, CRC-8, rejects the 85.0 °C power-on value). `OneWire` library only — no `DallasTemperature` |
| Flow pulse (interrupt) | 2 | **Must** be pin 2 or 3 on Uno/Nano. 2.5 ms debounce |
| Link to ESP32 | 0 / 1 (hardware Serial) | Pin 1 (TX) → ESP32 GPIO16 **through a voltage divider**; pin 0 (RX) ← ESP32 GPIO17 directly. Same UART the USB programmer uses |

### 2.2 ESP32 (LoRa node + display) — `esp32_lora_node.ino` (kept outside this repo)

| Signal | Pin | Notes |
|---|---|---|
| LoRa NSS (CS) | 5 | |
| LoRa RST | 14 | |
| LoRa DIO0 (interrupt) | 2 | Boot-strapping pin — safe in practice since the radio reset happens in software after the ESP32's own boot completes |
| LoRa SCK / MISO / MOSI | 18 / 19 / 23 | Default VSPI (the sketch doesn't override them) |
| OLED SDA / SCL | 21 / 22 (default I2C), address `0x3C` | **SH1106** 1.3" via `Adafruit_SH110X` — *not* SSD1306, the drivers aren't interchangeable |
| UART2 RX / TX (from Arduino) | 16 / 17 | 115200 baud. The sketch never writes to TX |
| Green pilot light (relay) | 25 | LoRa OK (`TX OK` / `SYNCED`) |
| Orange pilot light (relay) | 32 | Sensor warning, computed **locally on the ESP32** (see §9) |
| Red pilot light (relay) | 33 | LoRa failure / init failure |

Relays are **active-LOW** (`LOW` = on, `HIGH` = off).

Two wiring risks, both consequences of choices already made rather than code bugs:
- **GPIO16/17 are the PSRAM pins on ESP32-WROVER modules.** Fine on a plain WROOM; confirm which chip your board has.
- **Arduino pin 0 is also driven by the USB-serial chip.** With the ESP32 wired to it, disconnect that wire before uploading to the Arduino or using its Serial Monitor, or expect upload failures / garbled output.

### 2.3 Power

12V DC input via baseboard barrel jack → shield jumper bridges to 5V rail
powering all Arduino-side sensor `V` pins → ESP32 and Arduino each have
their own onboard 3.3V/5V regulation. Common ground required across
Arduino, ESP32, and all sensors — a frequently-missed step that silently
breaks UART communication if skipped.

### 2.4 Sensor calibration status

Values below are read from the current Arduino sketch; calibration records
(buffers used, dates, procedure) are not stored in this repo.

| Sensor | Value in firmware | Notes |
|---|---|---|
| pH | slope `-5.832`, intercept `21.970` | Measured, no longer placeholders |
| Turbidity | `clearWaterVoltage = 3.968` V | Measured on the Arduino's 5V/10-bit ADC. `MAX_NTU = 3000` is **still an assumed ceiling**, not measured |
| Water level | tank height `21.2` cm (sensor → bottom); 100 % = `18.2` cm (21.2 − 3.0 cm blind zone) | Geometry, not sensor calibration |
| Temperature | none applied | DS18B20 factory calibration, ±0.5 °C typical |
| Flow | `FLOW_CAL_FACTOR = 7.5` | Datasheet value for YF-S201, not measured on this unit. Readings under 5 pulses/s (~0.67 L/min) are forced to 0; the running volume resets after 60 s without flow |
| Warm-up | 60 s standby on the ESP32 after boot | Clock sync + stabilisation, then the UART is flushed. An earlier measurement (on the previous ESP32-ADC path, with probable handling disturbances) suggested several minutes for turbidity to settle — if post-boot turbidity looks off, check this first |

---

## 3. Communication Protocols

### 3.1 Arduino → ESP32 (hardware UART, 115200 8N1, one direction in practice)

One line every 1.5 s, no warm-up gate on the Arduino side:
```
PH:<v>,PH_V:<volts>,NTU:<v>,NTU_V:<volts>,TEMP:<v>,FLOW:<L/min>,LVL:<percent>,LVL_CM:<cm>,VOL:<litres>\n
```
- The ESP32 parses by **key**, so field order doesn't matter, and it ignores `PH_V`/`NTU_V` (debug voltages).
- `LVL` is **percent** (what the gateway thresholds expect); `LVL_CM` is shown on the OLED only.
- `VOL` is a **running total** that resets after 60 s without flow. The gateway stores it
  **exactly as the node sent it**, in both `telemetry_logs.interval_volume` and
  `telemetry_logs.volume_total`. The Dashboard's "Volume (from node)" card reads the latest
  row's `volume_total`, so it matches the ESP32's OLED (30 L, then 0 L after the node's idle reset).
  `volume_total` is `NULL` on rows from before that column existed.
- **Known issue:** the PDF report's total volume (`server.py`) **sums** `interval_volume`. Because each
  row holds the running total, the same water is counted again on every packet, so that total is
  inflated. A correct total needs the per-packet increases of the running total (see §9).

### 3.2 ESP32 ↔ Raspberry Pi (LoRa, 434.0 MHz, RadioHead framing)

| Message | Direction | Format |
|---|---|---|
| Telemetry | ESP32 → Pi | `DATA\|NODE:<id>\|ID:<n>\|pH:<v>\|Turb:<v>\|Temp:<v>\|Lvl:<percent>\|Flow:<v>\|Vol:<L>\|Time:HH:MM:SS` |
| Clock sync request | ESP32 → Pi | `SYNC_REQ\|NODE:<id>` (every 5 s during the 60 s boot standby, until ACKed) |
| Reply to **both** of the above | Pi → ESP32 | `ACK,HH:MM:SS` (the Pi's local time of day) |

- In the gateway's `DATA_RE`, `Vol:` is **mandatory** and `Time:` is optional. A packet missing `Vol` is silently dropped. `Time:` is accepted but **not stored** — rows are stamped with the database's own clock.
- Numeric fields match `[\d.]+`, so a **negative value drops the whole packet**.
- Delivery is "confirmed" only if an `ACK,` reply arrives within 5 s of the packet leaving; otherwise the OLED shows `NO ACK` and the red light comes on. There is no retry — an unacknowledged reading is not re-sent.
- The ESP32 has no RTC: it sets its system clock's time of day from the ACK (display and `Time:` field only; no date).
- At SF12 / CR 4/8 with a 12-symbol preamble a DATA packet is ~6.0 s on air and the ACK ~1.8 s; the ESP32 sends one reading every 15 s.
- Radio parameters are set explicitly to match the Pi: SF12, BW125 kHz, CR 4/8, LowDataRateOptimize on (required at SF12/125 kHz), preamble 12, sync word `0x12` (written with `spiWrite(0x39, 0x12)`), 18 dBm, promiscuous mode (accepts any header address), **payload CRC off**.
- The Pi frames its replies with RadioHead's 4-byte header (`[TO=0xFF, FROM, ID, FLAGS]`), since it uses register-level `pySX127x`.

---

## 4. Software Stack

| Layer | Technology |
|---|---|
| Arduino firmware | C++ (Arduino IDE sketch), `OneWire` |
| ESP32 firmware | C++ (Arduino IDE sketch), `RadioHead` (`RH_RF95`), `Adafruit_GFX`, `Adafruit_SH110X` |
| Edge gateway (`anomaly_engine.py`) | Python 3, `pySX127x`/`pyLoRa`, `RPi.GPIO`, `spidev`, `sqlite3`, `onnxruntime`, `numpy` |
| Backend API (`server.py`) | Python 3, FastAPI, `uvicorn`, `aiosqlite`, `reportlab`, WebSockets |
| Database | SQLite (WAL mode) — no separate DB server |
| Frontend | React (Vite), Tailwind CSS, `recharts`, `qrcode.react`, `lucide-react` |
| Prototype | Standalone HTML/vanilla JS (Chart.js, QRCode.js via CDN) — no build step, kept in sync with the real app's data model |
| ML training (offline) | `scikit-learn` (three per-sensor `IsolationForest` models, trained in `models/training/notebook/`), `pandas`, `skl2onnx` |
| Deployment | `systemd` (2 services), `NetworkManager` (Wi-Fi hotspot), Chromium kiosk |

---

## 5. Database Schema

| Table | Owner | Key columns |
|---|---|---|
| `telemetry_logs` | `anomaly_engine.py` | `node_id, packet_id, timestamp, ph, turbidity, temperature, water_level, flowrate, interval_volume, is_anomaly, threshold_flags, ml_flags, volume_total` |
| `notification_logs` | `anomaly_engine.py` | `node_id, timestamp, is_threshold, is_anomaly, title, message, ph_snapshot, turbidity_snapshot, temp_snapshot, is_acknowledged, params` |
| `system_thresholds` | `anomaly_engine.py` | `node_id, min_ph, max_ph, max_turbidity, min_temp, max_temp, max_level, min_level, max_flow` — genuinely live, queried fresh every packet |
| `nodes` | `anomaly_engine.py` (auto-registers on first packet), `server.py` (renaming) | `node_id, label, created_at` |
| `ml_config` | `anomaly_engine.py` (default row per new node), `server.py` (Admin slider) | `node_id, contamination` — **UI-only**, not read by `anomaly_engine.py` at inference time (the models use the score cut-offs in `best_params.json`) |

`interval_volume` and `volume_total` both hold the node's running `Vol` exactly as sent (§3.1), so
summing either one over-counts (see §9). Existing databases gain the `volume_total` column
automatically at startup (the gateway and `server.py` both migrate).

No node IDs are pre-seeded. A node exists in the system only once it has
actually transmitted — `ensure_node_registered()` inserts default rows into
`nodes`/`system_thresholds`/`ml_config` the first time an unfamiliar
`node_id` shows up in a `DATA` packet.

---

## 6. Backend API (`server.py`)

| Route | Purpose |
|---|---|
| `GET /api/telemetry/latest?node_id=` | Newest row for a node |
| `GET /api/telemetry/history?node_id=&timeframe=1h\|6h\|24h` | Chart series |
| `GET /api/telemetry/rows?...` | Paginated Historical Logs browser |
| `GET /api/notifications/active` / `/summary` | Active alerts, threshold/anomaly/both counts |
| `PUT /api/notifications/acknowledge/{id}` | Acknowledge an alert |
| `POST /api/reports/generate` | Builds PDF, returns QR-ready download URL |
| `GET /api/reports/download/{filename}` | Serves the generated PDF |
| `GET`/`PUT /api/admin/thresholds` | Read/update per-node limits |
| `GET`/`PUT /api/admin/ml-config` | Read/update contamination value (display-only, see §5) |
| `GET`/`PUT /api/admin/nodes` | Node label registry |
| `GET /api/nodes/status` | Real connectivity — staleness-based (`now - last_seen < 150s`), not "any message ever received" |
| `GET /api/admin/diagnostics` | DB size, row count, live WebSocket client count |
| `WS /ws/telemetry/live` | Live broadcast of new telemetry rows |

---

## 7. Frontend — 5 Pages

1. **Dashboard** — live gauges (tank level, flow-rate scaled to the actual
   configured threshold, not an arbitrary ceiling), pH/turbidity/temperature
   charts with per-parameter anomaly highlighting (a level/flow breach never
   bleeds into the chemistry charts; an ML flag only highlights the specific
   parameter it's statistically attributed to), node picker, "All Nodes"
   grid view. Live via WebSocket.
2. **Notifications & Alerts** — DSS feed, independent `THRESHOLD`/`ANOMALY`
   badges per card (both can show on one notification), acknowledge action.
   Drives a blocking banner (threshold breaches) and auto-dismissing toast
   (pure ML anomalies) visible on every page.
3. **Historical Logs** — paginated, filterable `telemetry_logs` table, CSV export.
4. **Reports & QR Export** — PDF compliance report + QR code encoding the
   local download URL, for phone access with no internet.
5. **Admin** — live threshold editing, node management, read-only ML
   sensitivity display (previously an interactive slider that looked
   functional but did nothing — fixed to be honestly non-interactive).

---

## 8. Anomaly Detection — Two Independent Layers

1. **If/else threshold check** (`anomaly_engine.py`) — deterministic, zero
   false-positive cost by construction, but only catches what a fixed rule
   was written for. Runs on pH, turbidity, temperature, level, flow.
2. **ML models — current: `dev`/`step` models** (`DevStepModels`, loaded when
   `models/training/notebook/devstep_params.json` exists; trained in
   `train_dev_step_models.ipynb`). One `IsolationForest` per sensor that only sees how a reading
   behaves: `dev` = reading − this node's batch median (at most 6 h, `ARGUS_ML_BASELINE_HOURS`), `step` = change over 2 min (the training
   data's spacing, so with packets every 30 s it compares with the reading 4 packets back). Range
   checks stay with the threshold layer. A sensor is judged once it has 30 readings (~45 s at 1.5 s
   per packet); until 2 min have passed, `step` uses the oldest reading. The history is in memory,
   so the models are quiet for the first 30 readings after a restart. The models also start over for each
   new batch: when the outflow stops after at least 10 L was discharged (`BATCH_DISCHARGE_LITRES`,
   env `ARGUS_BATCH_DISCHARGE_LITRES`), each node's history is cleared and the next water is learned
   from scratch, since every batch is different wastewater. An ML flag also needs the reading to have moved at least the
   sensor's deadband (`ML_DEADBAND`: 0.15 pH, 0.5 °C, 10 NTU, set from the sensors' rated accuracy /
   ADC resolution), so changes the sensors can't resolve are never reported as anomalies. In `--sim` each packet
   counts as one 2-minute reading. Engine features match the notebook's exactly (verified on
   18,560 rows of `series_data.csv`).

   **Fallback, older models** (used only without `devstep_params.json`; trained in
   `models/training/notebook/wastewater_per_sensor_iforest_onnx.ipynb`) —
   pH and turbidity are scored against the tuned cut-offs in
   `best_params.json`; temperature is scored on its deviation from the
   24 h median and its step from the previous reading, once 30 readings
   exist. Each finding names its sensor, so alerts record which parameter
   the model flagged. (The legacy `edge_engine.py` used a single
   multivariate `LocalOutlierFactor` instead.) The `.onnx` files are
   post-processed by `models/training/merge_iforest_onnx.py` into a single
   tree-ensemble node each, so they load in under a second instead of
   30–75 s (same scores up to float32 rounding — see DEVELOPMENT.md §3.4).

Both run on every packet; results merge into one notification
(`is_threshold`/`is_anomaly`, independently settable) rather than
competing or duplicating. Threshold alerts notify on every packet. An ML
finding notifies only if that sensor wasn't ML-flagged on that node in the
last 5 minutes (`ML_ALERT_QUIET_SECONDS`; each flag restarts it), so one
event gives one notification, while every flagged reading still lands in
`telemetry_logs.ml_flags` for the chart markers.

---

## 9. Known Limitations / Open Items

**Verified against the current source (found while reviewing the latest node firmware).**

- **The PDF report's total volume is inflated.** `interval_volume` holds the node's running `Vol`
  as sent (§3.1), and `server.py` sums it, so the same water is counted on every packet. A per-packet
  delta in the gateway was tried and reverted, to keep the node's value stored unchanged. The fix
  belongs in `server.py`: total the increases of the running total per node, treating a drop as a reset.
- **The orange pilot light ignores the Admin thresholds.** The ESP32 hard-codes
  its own limits, and three of five differ from the gateway defaults despite
  the comment saying they match: turbidity `35` vs `50`, level `85` vs `90`,
  flow `13` vs `10` (pH 7.0–8.5 and temp 26–30 do match). It also has no
  low-level check. Dashboard and panel can therefore disagree.
- **Payload CRC is disabled on the node**, so a corrupted packet can be
  accepted; the regex only checks shape, so a flipped digit would be stored as
  a real reading. Worth finding the root cause of whatever required turning it
  off.
- **Flow noise floor.** Readings under ~0.67 L/min are forced to 0, which could
  erase genuine flow at small (diorama) scale.

**Still open from before:**

- `MAX_NTU = 3000` is an assumed turbidity ceiling, never measured
- Bench-test status of the node ↔ gateway link isn't recorded here — add notes
  once confirmed
- `ml_config` contamination value is display-only — the models use the score
  cut-offs in `best_params.json`, so changing it affects nothing without a
  retrain + redeploy
- No collective/contextual anomaly detection (trend drift, time-of-day
  context) beyond what the per-sensor models' `dev`/`step` features provide
- Chromium kiosk autostart and Wi-Fi hotspot setup are documented in
  `SETUP_RASPI.md` but not yet verified on real Pi hardware end-to-end

---

## 10. Repository Layout

```
app/
├── anomaly_engine.py         # Process 1: LoRa handling, 3 per-sensor ML models + threshold eval, DB writes
├── edge_engine.py            # Legacy single-model gateway (superseded by anomaly_engine.py)
├── server.py                 # Process 2: FastAPI backend + WebSocket + static SPA serving
├── schema.sql                # Reference copy of the DB schema (anomaly_engine.py is authoritative)
├── simulate_data.py          # Laptop-only stand-in for anomaly_engine.py (no hardware needed)
├── requirements.txt          # Pi dependencies (includes hardware libs)
├── requirements-laptop.txt   # Laptop dependencies (excludes Pi-only hardware libs)
├── SETUP_LAPTOP.md
├── SETUP_RASPI.md
├── ARCHITECTURE.md           # This file
├── frontend/                 # React + Vite + Tailwind SPA
├── models/
│   └── training/             # train_anomaly_model.py, test_anomaly_model.py,
│                              # convert_to_onnx.py, merge_iforest_onnx.py, trained .joblib, guides
└── scripts/
    ├── anomaly_engine.service
    └── server.service
```
