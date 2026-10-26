# Wastewater Hub — Backend

FastAPI supervisory server for the offline wastewater quality station. This
process reads and writes `database.db` only — it never touches the LoRa
radio or the ML models. `anomaly_engine.py` (the separate gateway daemon) is the
only process that ingests sensor packets and runs the anomaly models.

## Files
- `anomaly_engine.py` — Process 1: LoRa radio ingest, three per-sensor ONNX models (pH, turbidity,
  temperature), threshold checks, DB writes
- `edge_engine.py` — legacy single-model gateway, superseded by `anomaly_engine.py`; kept for reference
- `server.py` — Process 2: the FastAPI app (REST routes + `/ws/telemetry/live` WebSocket), also
  serves the built React SPA from `frontend/dist` on the same port
- `schema.sql` — reference copy of the schema `anomaly_engine.py` creates on boot: `telemetry_logs`,
  `notification_logs`, `system_thresholds`, plus `nodes` and `ml_config` (server.py-only tables
  for labels and the ML sensitivity UI — see note below)
- `frontend/` — Vite + React + Tailwind SPA: Dashboard, Notifications, Historical Logs, Reports/QR,
  Admin, matching the sidebar/view spec exactly
- `requirements.txt` — Python dependencies for both processes
- `scripts/anomaly_engine.service`, `scripts/server.service` — systemd units for both processes

## A note on schema ownership

`anomaly_engine.py` is the authoritative writer of `telemetry_logs`, `notification_logs`, and
`system_thresholds` — its `init_db()` and `DEFAULT_THRESHOLDS` are the source of truth for those
tables' shape and for the limits a newly seen node gets (pH 7.0–8.5, turbidity 0–50 NTU,
temperature 26–30 °C, level 5–90 %, flow 10 L/min). `schema.sql` and `server.py` mirror those
values. It also registers `nodes` and `ml_config` rows for each new node; `server.py` edits labels.

`notification_logs.params` lists the parameters an alert is about (`"ph,turbidity"`), written by
`anomaly_engine.py`; `server.py` backfills it from the message text for older rows. It backs the
parameter filter on the Notifications page.

**`ml_config` is UI-only for now.** The per-sensor models use the score cut-offs in
`models/training/notebook/best_params.json`; `anomaly_engine.py` doesn't read `ml_config` at
inference time. Adjusting the slider in Admin will save a value, but it won't change model
behavior until `anomaly_engine.py` is updated to look this value up per node.

## Install on the Pi

(On a Windows laptop, for development, testing or demos, follow [SETUP_LAPTOP.md](SETUP_LAPTOP.md) instead.)

```bash
sudo apt install python3-venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configure

Environment variables (all optional, sensible defaults for the Pi's standard layout):

| Variable | Default | Purpose |
|---|---|---|
| `WASTEWATER_DB` | `database.db` next to the script (`anomaly_engine.py`: `/home/pi/argus/app-main/database.db`) | Shared SQLite file — both processes must point at the same one; the `.service` files set it explicitly |
| `WASTEWATER_MODELS_DIR` | `/home/pi/argus/app-main/models/training/notebook` | `anomaly_engine.py` only: the three ONNX models and `best_params.json` |
| `WASTEWATER_REPORTS` | `reports/` next to `server.py` | Where generated PDFs are saved and served from |
| `WASTEWATER_GATEWAY_IP` | `192.168.4.1` | The Pi's hotspot IP, used in QR download URLs when the dashboard is opened via localhost |
| `WASTEWATER_GATEWAY_PORT` | `3000` | Port used in those QR download URLs |

## Build the frontend

```bash
cd frontend
npm install
npm run build      # outputs frontend/dist
```

`server.py` looks for `frontend/dist` (override with `WASTEWATER_FRONTEND_DIST`) and serves it
directly on port 3000, so once it's built, `http://localhost:3000` in Chromium kiosk mode — and
`http://192.168.4.1:3000` for phones on the hotspot — both get the full SPA and the API from one
process. If you'd rather iterate on the UI with hot reload, run `npm run dev` inside `frontend/`
instead (proxies `/api` and `/ws` to `server.py` on :3000 — see `vite.config.js`) and just don't
rebuild `dist/` until you're ready to deploy.

## Run

```bash
uvicorn server:app --host 0.0.0.0 --port 3000
```

Point the kiosk Chromium instance at `http://localhost:3000`, and phones on
the hotspot at `http://192.168.4.1:3000` (adjust for your actual hotspot IP/port).

## How the pieces connect

- **`anomaly_engine.py`** inserts rows into `telemetry_logs` and `notification_logs`
  as it reads LoRa packets. Per packet, the three per-sensor Isolation Forest models run
  first, then the if/else threshold check, then any findings from both are merged into
  **exactly one** `notification_logs` row — a packet that breaches two thresholds and also
  gets ML-flagged still produces a single notification with a combined message.
  `is_threshold` / `is_anomaly` say which layer fired; `params` says which parameters.
- **`server.py`** polls `telemetry_logs` once a second for rows newer than
  the last one it broadcast, and pushes each new row over
  `/ws/telemetry/live` to every connected browser (kiosk + phones), and
  serves the built React app on the same port.
- **`frontend/`** renders that stream live via `src/api.js`'s
  `connectTelemetrySocket()`, and calls the REST routes below for everything else.

## REST routes implemented

| Route | Purpose |
|---|---|
| `GET /api/telemetry/latest?node_id=` | Newest row for a node |
| `GET /api/telemetry/history?node_id=&timeframe=1h|6h|24h` | Series for the dashboard charts |
| `GET /api/telemetry/rows?node_id=&start=&end=&page=&page_size=` | Paginated browser for the Historical Logs view |
| `GET /api/notifications?node_id=&alert_type=&param=&page=&page_size=` | One page of active alerts plus DSS header counts (Notifications view). `alert_type`: all, threshold, ml, both. `param`: ph, turbidity, temperature, level, flow |
| `GET /api/notifications/active` | All unacknowledged alerts (unpaginated) |
| `GET /api/notifications/summary` | Active / threshold / ML / both counts and latest timestamp |
| `PUT /api/notifications/acknowledge/{id}` | Marks an alert acknowledged |
| `POST /api/reports/generate` | Builds the offline PDF, returns its QR-ready download URL |
| `GET /api/reports/download/{filename}` | Serves the generated PDF |
| `GET /api/admin/thresholds` / `PUT /api/admin/thresholds` | Read/update per-node hardware+chemistry limits |
| `GET /api/admin/ml-config` / `PUT /api/admin/ml-config` | Read/update the contamination slider (UI-only, see note above) |
| `GET /api/admin/nodes` / `PUT /api/admin/nodes` | Node label registry |
| `GET /api/admin/diagnostics` | DB size, row count, live WebSocket client count |

## Status / what's left for a real deploy

Done: `anomaly_engine.py`, `server.py`, schema, and `frontend/` all exist and share one
consistent data model. Still to verify on real hardware:

1. **Models** — `anomaly_engine.py` loads the three ONNX models named in
   `models/training/notebook/best_params.json` at startup and won't start without them.
2. **LoRa hardware loop** — `anomaly_engine.py` drives the SX127x through `pyLoRa` (reset on
   GPIO 22, DIO0 on GPIO 4, 434 MHz). Bench-test with a real ESP32 node before field use.
3. **Chromium kiosk autostart** — the `.service` files bring up `anomaly_engine.service` and
   `server.service` on boot, but launching Chromium in kiosk mode pointed at `localhost:3000`
   still needs its own autostart entry (e.g. an `.xinitrc`/`labwc` autostart line or a third
   systemd user unit) — not included yet.
