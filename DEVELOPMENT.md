# Development Log — `local-moises` branch

This file covers what changed on the `local-moises` branch compared with `main`, the results
of the new anomaly-detection work, and what still has to happen before it can be merged.
For how the system works overall, see [ARCHITECTURE.md](ARCHITECTURE.md) and [README.md](README.md).

---

## 1. Project at a glance

**ARGUS** (formerly "Wastewater Hub") is an offline, edge-based water-quality monitoring station.

| Layer | What it does | Where |
|---|---|---|
| Sensors | Arduino reads pH, turbidity, temperature, level and flow, and shows them on an OLED | `firmware/arduino_sensors_oled/` |
| Radio relay | ESP32 wraps readings into packets and sends them over LoRa (434 MHz) | `firmware/esp32_lora_gateway/` |
| Gateway daemon | Raspberry Pi receives packets, runs ONNX anomaly model + threshold rules, writes SQLite | `edge_engine.py` |
| API / web server | FastAPI: REST, live WebSocket, PDF/QR reports, serves the SPA | `server.py` |
| Dashboard | React + Vite + Tailwind SPA | `frontend/` |
| Model training | Offline scripts and notebooks that produce `.onnx` models | `models/training/` |

Everything runs on the Pi's local hotspot with no internet connection.

---

## 2. Changes on this branch

### 2.1 Committed (`c0a0053` "logo and aboutus")

**Rebranding to ARGUS**
- `frontend/index.html`: page title changed from "Wastewater Hub" to "ARGUS".
- `frontend/src/assets/logo.svg`: new logo (two overlapping green and blue ellipses).
- `frontend/src/main.jsx`: logo is injected as the SVG favicon at runtime.
- `frontend/src/components/Sidebar.jsx`: header shows the logo and "ARGUS"; nav labels
  renamed ("Historical Logs", "Reports & QR Export").

**About page** (`frontend/src/pages/About.jsx`)
- Hero heading changed to "THE ARGUS PROJECT".
- Removed: the hero stat tiles, the "Sense → Transmit → Analyze → Visualize → Respond" flow,
  and the "Our Vision" section.
- The "Team Behind ARGUS" section moved up, directly after the intro blocks.

**Admin page hidden**
- `App.jsx` and `Sidebar.jsx`: the Admin & Thresholds view and its nav item are commented out.
  The backend admin routes are unchanged.

**Dashboard** (`frontend/src/pages/Dashboard.jsx`)
- The live/offline status dot colors were swapped (see 4.1).

**Firmware** (`arduino_sensors_oled.ino`)
- Semicolons were added after every `#include` line (see 4.1).

**Other**
- Rebuilt `frontend/dist/` bundle.
- `database.db`, `database.db-shm`, `database.db-wal` updated with local test data (~4.5 MB).

### 2.2 Committed (`3bd4b0d` "Add Jupyter notebook for Isolation Forest model training")
- `models/training/notebook/dataset/series_data.csv`: 122,577 rows of real sensor data at
  2-minute intervals, 2023-05-09 to 2023-10-26, with columns `ph`, `temperature_degC`, `turbidity_fnu`.
- `models/training/notebook/isolation_forest.ipynb`: placeholder (one empty cell).

### 2.3 Not committed yet
| File | What it is |
|---|---|
| `wastewater_per_sensor_iforest_onnx.ipynb` | The main new work: per-sensor Isolation Forest training, evaluation, tuning and ONNX export |
| `iforest_ph.onnx`, `iforest_temperature_degC.onnx`, `iforest_turbidity_fnu.onnx` | Baseline per-sensor models (2–3 MB each) |
| `iforest_ph_tuned.onnx`, `iforest_turbidity_fnu_tuned.onnx` | Tuned models (2.7 MB and 6.9 MB) |
| `best_params.json` | Tuned settings and score cut-offs (needed to use the tuned models) |
| `anomaly_results.csv`, `tuned_anomaly_results.csv` | Test-period predictions (5.5 MB and 2 MB) |
| `isolation_forest.ipynb` | Modified: notebook metadata only |

### 2.4 Simulator rewrite (`simulate_data.py`, not committed yet)
The laptop simulator now mimics the real chain stage by stage:
- **Arduino:** replays real readings from `series_data.csv`, starting at the models' unseen
  test period. Tank level and flow are generated with a daily pattern.
- **ESP32 + LoRa:** builds the firmware's exact `DATA|` packet and drops about 2% of packets.
- **Pi gateway:** parses packets with `edge_engine.py`'s regex, then runs the three per-sensor
  ONNX models (tuned pH, tuned turbidity, baseline temperature) and the threshold rules.

Optional injected events lasting several readings: pH shock, turbidity surge, temperature step,
tank overflow, pump dry-run, flow surge, and a disconnected temperature probe (-127 °C, which
`edge_engine.py` rejects as malformed).

On the test period, the simulator's ML flags match the notebook's `tuned_anomaly_results.csv`
on 100% of rows. New nodes get `max_turbidity=500` instead of 160 (`--reset-thresholds` applies it
to existing nodes). The three models load in under a second (see 3.4); `--no-ml` skips them.

### 2.5 Dashboard charts: threshold vs. model markers (not committed yet)
- `telemetry_logs` has two new columns, `threshold_flags` and `ml_flags`: comma-separated
  parameters each layer flagged (e.g. `ph,turbidity`), or NULL. They are added by `schema.sql`,
  and older databases get them through `ALTER TABLE` migrations in `edge_engine.py`, `server.py`
  and `simulate_data.py`.
- `edge_engine.py` writes them too. Its current single model can't say which parameter it
  flagged, so a model flag marks all three charts.
- `TelemetryChart.jsx` was redesigned:
  - **Markers:** a red dot marks a threshold breach and a purple diamond marks a model flag.
    When both fire, the dot sits inside the diamond.
  - **Toggles:** "Threshold" and "ML model" buttons show or hide each marker type on all three
    charts, and each chart shows a count per type.
  - **Axes and tooltip:** dashed lines show this node's limits, the y-axis fits the data (it
    was clipped at 220 NTU), and the tooltip names which layer flagged the reading.
  - **Time:** timestamps are now read as UTC; they used to be shown 8 h off.
  - **Older rows:** rows from before these columns existed get threshold markers recomputed
    from the current limits, and no model markers.

### 2.6 Historical Logs and Notifications filters (not committed yet)
- **Shared time helper:** `frontend/src/time.js` reads the database's UTC timestamps. The
  chart, Historical Logs and Notifications all use it.
- **Historical Logs:**
  - Node dropdown ("Overall" or one node) plus a new "Flagged by" dropdown with: All readings,
    Any alert, Threshold only, ML anomaly only, Threshold + ML anomaly.
  - The new filter runs on the server (`/api/telemetry/rows?flag=all|flagged|threshold|ml|both`),
    so paging and totals stay correct.
  - The table and CSV show which parameters each layer flagged.
  - The date inputs are converted from local days to UTC, and the "to" date now covers the
    whole day.
- **Notifications:** node and alert-type dropdowns. The header counts follow the selected
  node, and ML badges use the same violet as the chart.
- Rows from before `threshold_flags`/`ml_flags` existed only match "All readings" and
  "Any alert".

---

## 3. The per-sensor Isolation Forest notebook

### 3.1 Approach
Instead of one model for all sensors (what `edge_engine.py` expects today), the notebook trains
**one Isolation Forest per sensor**, so an alert can say *which* sensor was unusual.

1. **Compliance check (rules):** each reading is labelled `below` / `within` / `above` the
   DAO 2016-08 Class SB range: pH 7.0–8.5, temperature 26–30 °C, turbidity 0–500 FNU
   (the turbidity limit is a project choice; the Order regulates TSS, not turbidity).
2. **Features per sensor:**
   - `level`: the value itself (turbidity uses `log1p`)
   - `dev`: distance from the median of the previous 24 h
   - `step`: change since the previous reading

   Temperature uses only `dev` and `step`.
3. **Split by time:** first 80% for training (to 2023-09-22 06:44), last 20% for testing
   (98,038 / 24,510 rows).
4. **Model:** `StandardScaler` → `IsolationForest(n_estimators=200, max_samples=1024,
   contamination=0.01)`, exported with `skl2onnx` (opset 15, ai.onnx.ml 3).
5. **Checks:** ONNX Runtime and scikit-learn agree on 100% of test rows for all three models.

### 3.2 Baseline results (test period)
| Sensor | Rows flagged |
|---|---|
| pH | 253 (1.03%) |
| Temperature | 113 (0.46%) |
| Turbidity | 1,000 (4.08%) |
| Any sensor | 1,308 (5.34%) |

Compared with the threshold rules for pH and turbidity together: recall 0.92, precision 0.47, F1 0.62.

### 3.3 Tuning
- Data split: fit 60%, validation 20%, test 20%. Settings were chosen on validation only.
- Grid: features (`all` vs `level only`) × `n_estimators` {100, 300} × `max_samples` {256, 1024, 4096}.
- `contamination` is replaced by a **`score_cutoff`** chosen for the best F1 on validation.
- Temperature was not tuned: every row is below the 26 °C limit, so there is nothing to tune
  against. It keeps the baseline model.

| Sensor | Winning settings | Test F1 before → tuned | Precision | Recall | Count error |
|---|---|---|---|---|---|
| pH | level only, 300 trees, 256 samples, cutoff −0.2497 | 0.37 → **0.73** | 1.00 | 0.58 | 42.5% ❌ |
| Turbidity | level only, 300 trees, 1024 samples, cutoff −0.2674 | 0.67 → **0.995** | 1.00 | 0.99 | 1.0% ✅ |
| Any (pH + turbidity) | | 0.62 → **0.95** | 1.00 | 0.91 | 8.9% ❌ |

The target was a count error of 5% or less. Turbidity meets it; pH and the combined result do not.

**Caveat:** the tuned models are scored against the threshold rules, and both chose
`level only`, so they effectively learn the threshold again. This works as a cross-check, but it no
longer catches in-range readings that behave oddly (the "model only" early warnings). This
trade-off needs a decision before the tuned models replace the baseline.

### 3.4 Merged ONNX graphs (load time)
skl2onnx exports each tree as its own `TreeEnsembleRegressor` plus ~25 small ops, so a
300-tree model is an ~8,000-node graph that took ONNX Runtime 30–75 s to load and ~11 ms per
reading to score. `models/training/merge_iforest_onnx.py` precomputes each leaf's depth and
puts all trees into one `TreeEnsembleRegressor` (11 nodes): under 0.4 s to load, ~0.05 ms per
reading. Inputs, outputs and the trees themselves are unchanged; scores differ by float32
rounding only (≤ 5e-7), and on `series_data.csv` plus 20k random rows per model every flag
and label matched the originals. All five `.onnx` files in `notebook/` are merged. The
notebook's export cells call `merge_trees()` before saving, so a retrain stays fast; a model
exported some other way needs `python merge_iforest_onnx.py <file.onnx>` (needs `onnx`).

---

## 4. Open issues and next steps

### 4.1 Probable bugs from this branch
- [ ] **Dashboard status dot is inverted.** In `Dashboard.jsx` the online state now shows a
  pulsing red `bg-alert` dot and the offline state shows a blue dot. If this was not intentional,
  revert it.
- [ ] **`#include <...>;` in the Arduino sketch.** Preprocessor directives don't take semicolons.
  GCC only warns ("extra tokens at end of #include directive"), but the semicolons should be removed.
- [ ] **Hard-coded Windows path** in the notebook's settings cell
  (`C:\Users\Lenovo\app\...`). Replace it with a relative path such as `dataset/series_data*.csv`.

### 4.2 Integrating the new models into `edge_engine.py`
`AnomalyModel.evaluate()` currently loads one model from `MODEL_PATH` and sends it
`[[ph, turbidity, temp]]` (3 features). The new models are not compatible with that:

- [ ] Load one ONNX session per sensor. `SensorModels` and `Gateway.remember_temp` in
  `simulate_data.py` already do this and can be ported. Loading takes under a second once
  the models are merged (see 3.4).
- [ ] **Tuned models (level only):** send a single feature (pH as-is, turbidity as
  `log1p(turbidity)`), then flag when `score <= score_cutoff` from `best_params.json`.
  Do **not** use the model's `label` output.
- [ ] **Baseline/temperature model:** needs `dev` and `step`, so the engine has to keep a rolling
  per-node history (24 h median with at least 30 readings, plus the previous reading).
- [ ] Decide how per-sensor flags map to `notification_logs.is_anomaly`
  (a single any-sensor flag, or new per-sensor columns).
- [ ] Copy the chosen `.onnx` files and `best_params.json` into `models/` on the Pi.

### 4.3 Model / data decisions
- [ ] **Temperature range:** 100% of rows are below 26 °C, so the temperature rule fires on every
  reading. Check the limit against the DAO text and the site's real conditions.
- [ ] Improve pH recall (0.58): try `all` features with a cut-off, or a larger grid.
- [ ] Decide between baseline (catches unusual behaviour) and tuned (agrees with the rules) per sensor.

### 4.4 Repository housekeeping
- [ ] Add a `.gitignore`. The repo currently tracks `database.db*`, `__pycache__/` and
  `frontend/dist/`, and this branch would add about 25 MB of `.onnx` and result CSV files.
  Suggestion: ignore `database.db*`, `__pycache__/` and `*_results.csv`; commit only the `.onnx`
  files that will actually be deployed (or use Git LFS).
- [ ] Delete the empty `isolation_forest.ipynb` or put it to use.
- [ ] Update `ARCHITECTURE.md` and `README.md`. They still describe the model as
  LocalOutlierFactor with a single `anomaly_pipeline.onnx`, and the name as "Wastewater Hub".
- [ ] Add `skl2onnx`, `onnxruntime`, `matplotlib` and `jupyter` to `requirements-laptop.txt`
  for notebook work.

---

## 5. Reproducing the notebook

```bash
# On the laptop, from the repo root
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements-laptop.txt
pip install jupyter matplotlib skl2onnx onnxruntime
cd models/training/notebook
jupyter notebook wastewater_per_sensor_iforest_onnx.ipynb
```

Run all cells. Outputs (`.onnx`, `best_params.json`, `*_results.csv`) are written next to the notebook.
Training uses `random_state=42`, so results are reproducible.
