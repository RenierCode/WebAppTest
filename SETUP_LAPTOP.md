# Setting Up ARGUS on a Laptop (Windows)

This runs the web app — `server.py` and the dashboard — on a Windows laptop, for development,
testing reports, or demos. It doesn't need the Raspberry Pi or the LoRa radio: the dashboard
reads whatever is in the database, and the `testing/` scripts can fill a database with
realistic sample data. For the real deployment, see [SETUP_RASPI.md](SETUP_RASPI.md).

All commands are for **PowerShell** (the default terminal in VS Code on Windows).

## 0. What you need

- Windows 10 or 11, with internet access for the installs
- About 1 GB of free disk space (Python packages, Node modules)

## 1. Install the tools (once per laptop)

Install all four with `winget`:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Git.Git -e
winget install --id oschwartz10612.Poppler -e
```

| Tool | Used for |
|---|---|
| Python 3.10 or newer | `server.py`, the anomaly engine and the test scripts |
| Node.js (LTS) | building the dashboard (`frontend/`) |
| Git | getting the code and pushing changes |
| Poppler | the page previews of PDF reports on the Reports page; without it everything else works, but the preview stays empty |

**Then close VS Code completely (every window) and reopen it.** Installers add these tools to
the PATH, but a terminal only picks that up when VS Code itself restarts — opening a new
terminal tab is not enough. Check:

```powershell
python --version
node --version
git --version
pdftoppm -v
```

Each should print a version. If one says *"is not recognized"*, see [Troubleshooting](#troubleshooting).

## 2. Get the code

```powershell
cd $HOME\Documents
git clone https://github.com/<owner>/<repo>.git argus
cd argus
```

(Or download the ZIP from GitHub and extract it — then `cd` into the extracted folder, the one
that contains `server.py`.)

## 3. Set up Python

From the project folder (the one with `server.py`):

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements-laptop.txt
```

Your prompt now starts with `(venv)`. Activate it again in every new terminal before running
anything (`.\venv\Scripts\Activate.ps1`); `deactivate` leaves it.

Use **`requirements-laptop.txt`**, not `requirements.txt` — the latter includes `pyLoRa`,
`RPi.GPIO` and `spidev`, which only install on a Raspberry Pi.

## 4. Build the dashboard

```powershell
cd frontend
npm install
npm run build
cd ..
```

This creates `frontend/dist`, which `server.py` serves. Run `npm run build` again whenever you
change anything in `frontend/src`.

## 5. Run it

```powershell
uvicorn server:app --host 0.0.0.0 --port 3000 --reload
```

Open **http://localhost:3000**. `--reload` restarts the server by itself whenever a `.py` file
changes; leave it off for a demo. Stop the server with **Ctrl+C**.

The first time, Windows Firewall may ask whether Python can use the network: allow it on
**private** networks if phones on the same Wi-Fi should reach the dashboard at
`http://<laptop-ip>:3000` (find the IP with `ipconfig`, under your Wi-Fi adapter's IPv4 Address).

**Working on the dashboard?** Instead of rebuilding after every change, keep `uvicorn` running
and, in a second terminal, run `npm run dev` inside `frontend/`. Open the address Vite prints
(usually http://localhost:5173): it reloads as you edit and forwards `/api` and `/ws` to the
server on port 3000.

## 6. Choose the data

By default the server uses `database.db` in the project folder. The `testing/` scripts build
separate databases with known contents, so the real one is never touched:

| Script | Builds | Good for |
|---|---|---|
| `python testing\generate_report_test_db.py` | `database_report_test.db` — 20 days (Oct 6–26), a reading every 2 min, 1–3 anomaly events a day | report generation |
| `python testing\generate_explainer_test_db.py` | `database_explainer_test.db` — Oct 23–26, a reading every 15 s, single anomalies 30–45 min apart, plus the true answer for every reading | the dashboard's "Why this reading is or isn't an anomaly" panel, and `testing\evaluate_detection.py` |

Both run every reading through the real engine (ML models, threshold rules, alerts). To use one,
set `WASTEWATER_DB` **before** starting the server, in the same terminal:

```powershell
$env:WASTEWATER_DB = "database_explainer_test.db"
uvicorn server:app --host 0.0.0.0 --port 3000
```

Back to the real database: `Remove-Item Env:WASTEWATER_DB`, then restart the server.
(`$env:` settings only last for that terminal.)

To compare the ML model with the threshold rules and the empirical (3σ) rule against the known
answers:

```powershell
python testing\evaluate_detection.py
```

## 7. What doesn't run on a laptop

- **`anomaly_engine.py` with the radio.** It needs the Pi and the LoRa module; on a laptop the
  data comes from the database (step 6) instead. Its `--sim` mode expects a `simulate_data.py`
  to send packets, which isn't in this repository.
- **The hotspot.** The QR codes on the Reports page point at the Pi's hotspot (`192.168.4.1`)
  unless the page was opened through the laptop's own network address; for a phone to download
  a report from the laptop, open the dashboard at `http://<laptop-ip>:3000` instead of
  `localhost`.

## 8. Checks

The test scripts print `ALL SCENARIOS PASSED` when everything works:

```powershell
python tests\test_ml_alerts.py
python tests\test_volume.py
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `python`, `node`, `git` or `pdftoppm` *"is not recognized"* right after installing | Close VS Code completely and reopen it (step 1). For one terminal only, Poppler can be added by hand: `$env:Path += ";$((Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\oschwartz10612.Poppler*\*\Library\bin").FullName)"` |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again |
| `pip install` fails building `RPi.GPIO` or `spidev` | You used `requirements.txt`; use `requirements-laptop.txt` |
| `No module named fastapi` (or uvicorn) | The venv isn't active: `.\venv\Scripts\Activate.ps1` |
| The page says the frontend build wasn't found | Run `npm run build` inside `frontend/` (step 4) |
| PDF preview on the Reports page stays empty | Poppler isn't installed or not on the PATH: check `pdftoppm -v` in the terminal that started the server, then restart the server |
| A code change doesn't show up | `.py` change: restart `uvicorn` (or use `--reload`). Dashboard change: `npm run build`, then refresh the browser |
| `address already in use` on port 3000 | Another server is still running. Find it with `Get-NetTCPConnection -LocalPort 3000 -State Listen` and stop that process (`Stop-Process -Id <OwningProcess>`), or close the other terminal |
| The dashboard shows old data or the wrong dates | Check which database the server uses: `$env:WASTEWATER_DB` in the terminal that started it (empty = `database.db`) |
