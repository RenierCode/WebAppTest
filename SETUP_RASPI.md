# Setting Up Wastewater Hub on the Raspberry Pi

This is the real deployment: `anomaly_engine.py` talking to actual LoRa
hardware, `server.py` serving the kiosk display and phones on a Wi-Fi
hotspot. Follow this after you've bench-tested the node firmware (the Arduino and ESP32 sketches, kept outside this repo) against
`anomaly_engine.py`'s radio code — packets arriving and `ACK,` replies
coming back — so a failure here isn't a radio problem in disguise.

## 0. What you need

- Raspberry Pi 4B, 64-bit Raspberry Pi OS **Lite** (headless) flashed and
  booted, with SSH access
- Ra-02 (SX1278) LoRa module wired per the pinout below
- A USB Wi-Fi adapter if you want the hotspot on a separate interface from
  your management/internet connection (recommended — using the Pi's only
  Wi-Fi adapter for both internet access and the hotspot at the same time
  needs a different setup than what's below)
- The Pi's LCD touchscreen connected via HDMI or DSI — **not SPI**, since
  those pins are already committed to the LoRa module

## 1. Wire the LoRa module

Matches the pin assignments already in `anomaly_engine.py`'s `init_radio()`:

| SX1278 pin | Pi pin |
|---|---|
| VCC | 3.3V (physical pin 1 or 17) |
| GND | GND (physical pin 6 or 9) |
| MISO | SPI0 MISO |
| MOSI | SPI0 MOSI |
| SCK | SPI0 SCLK |
| NSS | SPI0 CE0 |
| RST | GPIO22 |
| DIO0 | GPIO4 |

Enable SPI first if it isn't already:

```bash
sudo raspi-config
# Interface Options -> SPI -> Enable
sudo reboot
```

## 2. Get the app onto the Pi and set up Python

```bash
mkdir -p /home/pi/argus/app-main && cd /home/pi/argus/app-main
# copy the repo here, so this folder matches the layout: server.py,
# anomaly_engine.py, schema.sql, requirements.txt, frontend/, models/,
# scripts/ -- anomaly_engine.py and the .service files assume
# exactly this path (override with WASTEWATER_DB / WASTEWATER_MODELS_DIR)

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

If `pyLoRa` or `RPi.GPIO` fail to build, you're likely missing build tools —
`sudo apt install python3-dev build-essential` first, then retry.

The Reports page previews PDFs as page images rendered with `pdftoppm`; Raspberry Pi OS
usually has it already, otherwise: `sudo apt install poppler-utils`.

## 3. Build the frontend

```bash
cd frontend
npm install    # if npm isn't installed: sudo apt install nodejs npm
npm run build
cd ..
```

## 4. Check the trained ONNX models

`anomaly_engine.py` loads three per-sensor models from
`models/training/notebook/`: the pH and turbidity files named in
`best_params.json` (with their score cut-offs) plus
`iforest_temperature_degC.onnx`. They're in the repo; if you retrain on
your laptop (see `wastewater_per_sensor_iforest_onnx.ipynb`), copy the new
`.onnx` files and `best_params.json` over:

```bash
scp models/training/notebook/*.onnx models/training/notebook/best_params.json \
    pi@<pi-ip>:/home/pi/argus/app-main/models/training/notebook/
```

Unlike the old `edge_engine.py`, `anomaly_engine.py` won't start if a model
file is missing.

## 5. Test both processes manually before wiring up services

```bash
# Terminal 1
source venv/bin/activate
python anomaly_engine.py
# should print "anomaly_engine.py running on 434.0 MHz"

# Terminal 2
source venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 3000
```

Power on an ESP32 node and confirm in `anomaly_engine.py`'s output that you
see `SYNC_REQ -> ACK,<time>` and then `Rx Success` lines as telemetry
arrives. Open `http://<pi-ip>:3000` from another device on the same network
and confirm the dashboard updates live. Get this working before automating
anything — a service that silently fails to start is much harder to debug
than a process you can watch directly.

## 6. Set up the Wi-Fi hotspot

This lets phones connect directly to the Pi with no internet required,
matching `GATEWAY_IP=192.168.4.1` already assumed throughout `server.py`
(QR code download URLs, etc.). Using `NetworkManager`, which current
Raspberry Pi OS ships with by default:

```bash
sudo nmcli connection add type wifi ifname wlan0 con-name WastewaterHub \
    autoconnect yes ssid "WastewaterHub"
sudo nmcli connection modify WastewaterHub 802-11-wireless.mode ap \
    802-11-wireless.band bg ipv4.method shared \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "choose-a-real-password-here"
sudo nmcli connection up WastewaterHub
```

`ipv4.method shared` makes NetworkManager hand out DHCP addresses and
typically assigns itself `10.42.0.1`, not `192.168.4.1` — set it explicitly
to match what the rest of the app assumes:

```bash
sudo nmcli connection modify WastewaterHub ipv4.addresses 192.168.4.1/24
sudo nmcli connection up WastewaterHub
```

Verify: `ip addr show wlan0` should show `192.168.4.1`. Connect a phone to
the `WastewaterHub` SSID and confirm it gets an address in that range and
can reach `http://192.168.4.1:3000`.

**Join-the-hotspot QR code.** The Reports page shows two QR codes: one that joins this hotspot,
then the report's download link. For the first one, give `server.py` the same name and password
(in `scripts/server.service`, or before starting `uvicorn` by hand):

```bash
export WASTEWATER_HOTSPOT_SSID="WastewaterHub"
export WASTEWATER_HOTSPOT_PASSWORD="choose-a-real-password-here"   # or WASTEWATER_HOTSPOT_OPEN=1
```

Without them the page just names the network to join. Anyone who can open the Reports page
can read this password from its QR code.

If you used a second Wi-Fi adapter for internet/management, replace
`wlan0` above with whichever interface should host the hotspot
(`ip link` to list them), leaving the other free for your normal network.

## 7. systemd services for anomaly_engine.py and server.py

`scripts/anomaly_engine.service` and `scripts/server.service` are already
written for this. Both set `WASTEWATER_DB` to the same file so the
dashboard reads what the gateway writes. If an older `edge_engine.service`
is installed, remove it first — two daemons can't share the radio:

```bash
sudo systemctl disable --now edge_engine.service 2>/dev/null
sudo rm -f /etc/systemd/system/edge_engine.service
```

Install them:

```bash
sudo cp scripts/anomaly_engine.service scripts/server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable anomaly_engine.service server.service
sudo systemctl start anomaly_engine.service server.service
```

Check they're actually running and watch for errors:

```bash
sudo systemctl status anomaly_engine.service
sudo journalctl -u anomaly_engine.service -f     # live log tail
sudo journalctl -u server.service -f
```

## 8. Chromium kiosk autostart

The Pi should boot straight into the dashboard on its LCD with no manual
login/launch step. Simplest approach on Raspberry Pi OS with the desktop
environment installed — autostart via LXDE/Wayfire's autostart file:

```bash
mkdir -p ~/.config/wayfire.ini.d   # or ~/.config/lxsession/LXDE-pi/ on older OS versions
```

For **Wayfire** (current Raspberry Pi OS default compositor), edit
`~/.config/wayfire.ini` and add:

```ini
[autostart]
kiosk = chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --incognito http://localhost:3000
```

For **LXDE** (older Raspberry Pi OS), instead create/edit
`~/.config/lxsession/LXDE-pi/autostart` and add:

```
@chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --incognito http://localhost:3000
```

`--incognito` avoids a stale "restore session?" prompt after an unclean
shutdown, which would otherwise sit in front of the kiosk view forever
waiting for a click that never comes. Reboot to test:

```bash
sudo reboot
```

The Pi should come up, `anomaly_engine.service`/`server.service` start via
systemd, and Chromium launches full-screen straight into the dashboard —
no keyboard/mouse interaction needed.

## 9. Flash the node hardware (Arduino + ESP32)

Two boards, two sketches, both flashed from the Arduino IDE. The Arduino
reads the sensors and prints one line every 1.5 s; the ESP32 shows it on the
OLED, relays it over LoRa, and drives the pilot lights.

**Arduino** (`arduino_sensor_node.ino`, kept outside this repo) — sensors
only, no display:
1. Library Manager: install `OneWire` (nothing else is needed)
2. **Tools → Board → Arduino Uno** (or Nano/Mega) and the correct port
3. Wire the sensors per the pin table in `ARCHITECTURE.md` §2.1 — they connect
   directly, no voltage dividers, since the Arduino is natively 5V
4. **Unplug the ESP32 wire from Arduino pin 0 before uploading** — that pin is
   shared with the USB programmer, and leaving it connected causes upload
   failures
5. Upload, then open Serial Monitor at **115200** baud: you should see a
   `PH:…,PH_V:…,NTU:…,…,VOL:…` line every ~1.5 s. Reconnect the ESP32 wire
   afterwards

**ESP32** (`esp32_lora_node.ino`, kept outside this repo):
1. Install the ESP32 board package, then via Library Manager: `RadioHead`
   (by Mike McCauley), `Adafruit GFX Library`, `Adafruit SH110X`.
   **Confirm your OLED chip first** — this sketch drives an **SH1106**; an
   SSD1306 needs a different library and simply won't work with this one
2. **Tools → Board → ESP32 Dev Module**
3. Wire LoRa, OLED, the three relays and the Arduino link per `ARCHITECTURE.md`
   §2.2 (Arduino TX → GPIO16 *through a 10k/20k divider*; shared GND)
4. Upload, open Serial Monitor at 115200. After `LoRa OK. Starting 60s standby…`
   you'll see `Dispatching: SYNC_REQ|NODE:1` every 5 s until
   `anomaly_engine.py` is running and in range, then `Received Reply: ACK,HH:MM:SS`.
   After the 60 s standby the ESP32 relays each Arduino line; the OLED shows
   `TX OK` and the green pilot light comes on. `NO ACK` + red light means the
   gateway isn't answering

## Troubleshooting checklist

| Symptom | Likely cause |
|---|---|
| `anomaly_engine.py` prints nothing after "running on 434.0 MHz" | ESP32 not powered, out of range, or on the wrong frequency (must match 434.0 MHz on both ends) |
| ESP32 Serial shows `SENT` but never `ACK received` | Check `anomaly_engine.py` is actually running; check `LoRaGateway.transmit()`'s broadcast framing is compatible with your specific ESP32 addressing (see the caveat noted when this was built) |
| Dashboard doesn't update | Check `journalctl -u server.service` for errors; confirm `database.db` is actually receiving new rows (`sqlite3 database.db "SELECT COUNT(*) FROM telemetry_logs"`) |
| Kiosk shows the "frontend build not found" message | `npm run build` wasn't run, or was run somewhere other than `/home/pi/argus/app-main/frontend` |
| Phone can't reach `192.168.4.1:3000` | Confirm phone actually joined the `WastewaterHub` SSID, not your regular Wi-Fi; check `ip addr show wlan0` still shows `192.168.4.1` |
