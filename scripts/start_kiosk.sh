#!/usr/bin/env bash
# Opens the ARGUS dashboard full-screen in Chromium.
# Started from ~/.config/labwc/autostart (see scripts/install_autostart.sh),
# so it runs once the Pi's desktop has loaded.
#
# Override the address with:  ARGUS_URL=http://192.168.4.1:3000 ./start_kiosk.sh
set -u

URL="${ARGUS_URL:-http://localhost:3000}"

command -v curl >/dev/null 2>&1 || { echo "start_kiosk: curl is required (sudo apt install curl)" >&2; exit 1; }

# systemd starts server.service in parallel with the desktop. If Chromium opens
# first it lands on a "can't connect" page and just sits there, so wait until the
# server actually answers (normally a few seconds).
until curl -fs -o /dev/null "$URL"; do
  sleep 2
done

# Current Raspberry Pi OS calls the binary "chromium"; older releases "chromium-browser".
BROWSER="$(command -v chromium || command -v chromium-browser || true)"
if [ -z "$BROWSER" ]; then
  echo "start_kiosk: Chromium not found (sudo apt install chromium)" >&2
  exit 1
fi

# Deliberately NOT --incognito: the Preferences page keeps its settings in the
# browser's localStorage, and incognito would wipe them on every boot.
# --disable-session-crashed-bubble hides the "restore pages?" prompt that a
# power cut would otherwise leave sitting on screen waiting for a click.
exec "$BROWSER" "$URL" \
  --kiosk --noerrdialogs --disable-infobars --no-first-run \
  --disable-session-crashed-bubble --start-maximized
