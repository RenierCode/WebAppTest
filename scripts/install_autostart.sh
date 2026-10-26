#!/usr/bin/env bash
# Makes ARGUS start by itself every time the Pi powers on:
#   1. anomaly_engine.service + server.service -> systemd (start at boot, restart on failure)
#   2. the dashboard in full-screen Chromium   -> labwc desktop autostart
#
# Run it as your normal login user, NOT root (it calls sudo only where it must):
#   ./scripts/install_autostart.sh             install / re-install (safe to repeat)
#   ./scripts/install_autostart.sh --dry-run   show what it WOULD do, change nothing
#
# The two .service files in this folder are templates written for user "pi" in
# /home/pi/argus/app-main. Current Raspberry Pi OS makes you choose your own
# username, so this fills in your real user and folder instead of assuming them.
set -euo pipefail

DRY_RUN=0
case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=1 ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

if [[ $EUID -eq 0 ]]; then
  echo "Run this as your normal user (the one you log in to the Pi's desktop with), not root." >&2
  echo "The services and the kiosk need to belong to that user." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
UNIT_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"             # overridable, for testing
AUTOSTART="${LABWC_AUTOSTART:-$HOME/.config/labwc/autostart}"
TEMPLATE_ROOT="/home/pi/argus/app-main"

if [[ "$ROOT" =~ [[:space:]] ]]; then
  echo "The project folder path contains a space ($ROOT); the systemd unit files can't handle that." >&2
  echo "Move the project to a path without spaces and run this again." >&2
  exit 1
fi

say()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
run()  { if (( DRY_RUN )); then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }

render() {   # a unit template -> the same unit, for THIS user and folder
  local text
  text="$(<"$1")"
  text="${text//"$TEMPLATE_ROOT"/"$ROOT"}"
  text="${text//User=pi/User=$RUN_USER}"
  printf '%s\n' "$text"
}

# ---------------------------------------------------------------- preflight
say "ARGUS autostart for user '$RUN_USER', project in $ROOT"
(( DRY_RUN )) && say "(dry run: nothing will be changed)"
fatal=0
for exe in python3 uvicorn; do
  if [[ ! -x "$ROOT/venv/bin/$exe" ]]; then
    warn "$ROOT/venv/bin/$exe is missing -- create the virtualenv first (SETUP_RASPI.md section 2); the services can't start without it"
    fatal=1
  fi
done
id -nG | tr ' ' '\n' | grep -qxE 'spi' || warn "user '$RUN_USER' is not in the 'spi' group; the radio may not open (sudo usermod -aG spi,gpio $RUN_USER, then log out and in)"
[[ -d "$ROOT/frontend/dist" ]] || warn "frontend/dist not found -- run: cd frontend && npm install && npm run build"
command -v curl >/dev/null 2>&1 || warn "curl not found; the kiosk launcher needs it (sudo apt install curl)"
command -v chromium >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1 || warn "Chromium not found (sudo apt install chromium)"
if (( fatal && ! DRY_RUN )); then
  echo "Fix the problems above and run this again." >&2
  exit 1
fi

# ------------------------------------------------------- 1. systemd services
say ""
say "== 1/2  systemd services"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
for unit in anomaly_engine.service server.service; do
  render "$ROOT/scripts/$unit" > "$tmp/$unit"
  if (( DRY_RUN )); then
    say "--- would install $UNIT_DIR/$unit:"
    sed 's/^/    /' "$tmp/$unit"
  else
    run sudo install -m 644 "$tmp/$unit" "$UNIT_DIR/$unit"
  fi
done

# The old single-model gateway must not run: two daemons can't share the radio.
if systemctl list-unit-files edge_engine.service 2>/dev/null | grep -q '^edge_engine'; then
  say "Removing the old edge_engine.service"
  run sudo systemctl disable --now edge_engine.service
  run sudo rm -f "$UNIT_DIR/edge_engine.service"
fi

# Clock sync from a phone (server.py /api/system/time): the server sets the Pi's clock with
# "sudo date". Allow exactly that command without a password, nothing else.
SUDOERS_FILE=/etc/sudoers.d/argus-clock
# ...and the dashboard's Shut down button runs "sudo systemctl poweroff" (that command only).
{ printf '%s ALL=(root) NOPASSWD: /usr/bin/date -u -s @*\n' "$RUN_USER"
  printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff\n' "$RUN_USER"; } > "$tmp/argus-clock"
if (( DRY_RUN )); then
  say "--- would install $SUDOERS_FILE:"
  sed 's/^/    /' "$tmp/argus-clock"
elif sudo visudo -cf "$tmp/argus-clock" >/dev/null; then
  run sudo install -m 440 "$tmp/argus-clock" "$SUDOERS_FILE"
else
  warn "skipped $SUDOERS_FILE (visudo rejected it); clock sync from a phone won't be able to set the time"
fi

run sudo systemctl daemon-reload
run sudo systemctl enable --now anomaly_engine.service server.service

# ------------------------------------------------------- 2. kiosk autostart
say ""
say "== 2/2  full-screen kiosk ($AUTOSTART)"
run chmod +x "$ROOT/scripts/start_kiosk.sh"
LINE="\"$ROOT/scripts/start_kiosk.sh\" &"
if (( DRY_RUN )); then
  say "  [dry-run] would make that file contain:"
  say "    $LINE"
else
  mkdir -p "$(dirname "$AUTOSTART")"
  # keep a one-time backup of whatever was there before
  if [[ -f "$AUTOSTART" && ! -f "$AUTOSTART.bak-argus" ]]; then cp "$AUTOSTART" "$AUTOSTART.bak-argus"; fi
  {
    # drop any earlier ARGUS line (so re-running, or moving the folder, never duplicates it)
    if [[ -f "$AUTOSTART" ]]; then grep -vF -e "start_kiosk.sh" -e "# ARGUS kiosk" "$AUTOSTART" || true; fi
    printf '# ARGUS kiosk (added by scripts/install_autostart.sh)\n%s\n' "$LINE"
  } > "$AUTOSTART.new"
  mv "$AUTOSTART.new" "$AUTOSTART"
fi

# ------------------------------------------------------------------ summary
say ""
if (( DRY_RUN )); then
  say "Dry run finished. Run again without --dry-run to apply."
else
  for u in anomaly_engine server; do
    printf '  %-16s %s\n' "$u.service" "$(systemctl is-active "$u.service" 2>&1 || true)"
  done
  say ""
  say "Done. Reboot to test:  sudo reboot"
  say "Afterwards: the dashboard should open full-screen on the Pi's screen by itself."
  say "Data appears with the node's first packets; ML flags start once a node has sent 30 readings."
  say "Logs:  sudo journalctl -u anomaly_engine.service -f      sudo journalctl -u server.service -f"
fi
