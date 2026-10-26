# Handoff note — read this before ARCHITECTURE.md

Written at the end of a long Claude.ai chat session. This covers what that
conversation changed/discovered that ARCHITECTURE.md doesn't (fully)
reflect yet, plus a few things that only exist as reasoning in that chat,
not as code or docs anywhere. Read this first; it tells you where
ARCHITECTURE.md is accurate vs. stale.

---

## 1. ARCHITECTURE.md — status after this session

An earlier version of this note said ARCHITECTURE.md's hardware and protocol
sections were still accurate. **That was wrong** — I hadn't checked them
against the real code. They described a design several iterations old
(`SoftwareSerial` 9600 baud, OLED on the Arduino, `ACK:<id>|TIME:<unix>`
replies, no `Vol`/`Time` fields). They have now been **rewritten from the
actual sketches in `firmware/` and from `anomaly_engine.py`**: title (ARGUS),
the diagram, §2 hardware + calibration table, §3 protocols, the §4 stack table,
and §9 limitations. §8 (anomaly detection) was already correct.

Source of truth for the sensor node is now:
- `firmware/arduino_sensor_node/arduino_sensor_node.ino` (sensors only)
- `firmware/esp32_lora_node/esp32_lora_node.ino` (LoRa + SH1106 OLED + pilot lights)

The older `firmware/arduino_sensors_oled/` and `firmware/esp32_lora_gateway/`
folders were superseded and removed. `edge_engine.py` remains legacy — don't
develop against it.
---

## 2. Real bugs found and fixed this session (verified, not just patched)

### `anomaly_engine.py` — hardcoded path bug
`DB_PATH`/`MODELS_DIR` fell back to a hardcoded `/home/pi/argus/app-main/...`
path if `WASTEWATER_DB`/`WASTEWATER_MODELS_DIR` env vars weren't set —
meaning running it directly (not via the systemd service, which does set
those vars) from anywhere else threw `sqlite3.OperationalError: unable to
open database file`. Fixed to fall back to a `BASE_DIR`-relative path
(same pattern `server.py` already used). Verified by actually reproducing
the old failure and confirming the fix resolves it, not just inspection.

### `server.py` — PDF reports had no charts
Reports were text-only. Added real matplotlib-generated trend charts
(pH/turbidity/temperature) to the generated PDF, each showing:
- the node's actual acceptable-range band (shaded) and limit lines (dashed),
  pulled from `system_thresholds`
- real anomaly markers — red dots for genuine `threshold_flags` hits,
  purple for genuine `ml_flags` hits (the actual stored columns, not
  recomputed/simulated)

**New dependency**: `matplotlib` added to `requirements.txt` and
`requirements-laptop.txt` — needs `pip install -r requirements.txt` again
before this works.

Verified end-to-end: generated a real PDF with realistic fake data,
rendered it to an image, visually confirmed the charts, bands, and anomaly
markers all render correctly with no layout overlap.

### `frontend/src/pages/Dashboard.jsx` + `TelemetryChart.jsx` — clickable charts
Inline dashboard charts now cap at a fixed 50 points (not a time window —
predictable density regardless of data rate) and have no visible range
selector. Clicking a chart opens a modal with a larger version of just
that chart, with its own independent 1h/6h/24h range selector and its own
data fetch. `TelemetryChart.jsx` just gained a `height` prop so one
component serves both views.

**Not yet verified**: this session had no working Node.js build toolchain
(no network access in the sandbox, and the original `node_modules` was
Windows-built, wouldn't run on the Linux sandbox either). The JSX was
checked for structural balance and manually reviewed line-by-line, but
**never actually compiled**. Run `npm run build` (or `npm run dev`) as the
real first test of these two files.

---

## 3. A real methodology finding, not written anywhere else

While discussing why the tuned per-sensor models (`best_params.json`) both
landed on `"features": "level only"`: this isn't a tuning failure, it's
**mathematically unavoidable given how they were validated**. The
threshold-breach label used for scoring is *itself* a deterministic
function of `level` (`ph < 7.0 or ph > 8.5`) — so `level` scores a perfect
AUC of 1.0 predicting that label, by construction. No feature combination
can ever beat a tautology. This was confirmed empirically against the
real 122,577-row dataset, not just reasoned about:

- A model trained on `dev`+`step` **only** (level excluded entirely) still
  flagged **1,134 genuinely in-range points** the threshold rule
  structurally cannot see — real detection capability, just invisible to
  a validation metric that only rewards reproducing the rule.

**If anyone picks the per-sensor model tuning back up**, the fix isn't
more grid search — it's changing what the models get validated against
(exclude "level only" as a candidate entirely, or validate against labels
that aren't themselves derived from the threshold rule). Also worth
knowing: for the *old* combined model, `LocalOutlierFactor` beat
`IsolationForest` head-to-head at jointly weighing multiple features
rather than collapsing to one axis — worth testing whether the same holds
for the per-sensor case, rather than assuming IsolationForest is the right
choice for the three models.

---

## 4. Open items specific to this session (in addition to ARCHITECTURE.md's own list)

- [x] Real `npm run build` of the Dashboard.jsx changes — compiles cleanly
      (2026-10-05, Vite 5.4); still worth a visual check in the browser
- [ ] Run `anomaly_engine.py` for real after the path fix, confirm
      `database.db` actually appears next to the script
- [ ] Generate a real PDF report (not the synthetic test data used to
      verify the chart code) and sanity-check it against actual
      `telemetry_logs` data
- [ ] **Report volume is double-counted.** By decision, `anomaly_engine.py` stores the
      node's running `Vol` exactly as sent, in both `interval_volume` and `volume_total`
      (the Dashboard's "Volume (from node)" card, same number as the ESP32 OLED). A
      per-packet delta in `interval_volume` was tried and reverted. `server.py` still sums
      `interval_volume` for the PDF report, which over-counts (8 L poured reported as 324 L
      in an earlier test). Fix it in `server.py`: total the increases of the running total
      per node, treating a drop as a reset. Covered by `python tests/test_volume.py`.
- [ ] Confirm on real hardware that `ACK,` replies still arrive after the change
      (the ACK path is untouched, but this is the first edit to `handle_data`)
- [ ] Reconcile the ESP32's hard-coded orange-light thresholds with the gateway
      (3 of 5 differ) — or have the gateway send them down
