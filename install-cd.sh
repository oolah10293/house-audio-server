#!/bin/sh
# Install CD-enabled House Music; back up and automatically restore existing server on failure.
# Run from the extracted package directory: sudo sh ./install-cd.sh
set -eu

SERVICE=house-audio-server.service
INSTALL_DIR=/opt/house-audio-server
SERVICE_PATH=/etc/systemd/system/house-audio-server.service
BACKUP_ROOT=/var/backups/house-audio-server
BACKUP_DIR="$BACKUP_ROOT/pre-cd-$(date +%Y%m%d-%H%M%S)-$$"
INSTALL_STARTED=0

if [ "$(id -u)" -ne 0 ]; then
  echo "Run: sudo sh ./install-cd.sh" >&2
  exit 1
fi

# Validate the complete package before touching the installed version.
for file in install.sh house_audio_server.py input_sources.py auto_cd.py \
  radio_history.py radio_listening.py radio_metadata.py radio_stations.py \
  state_file.py subwoofers.py \
  web/house.html web/house.css web/house.js \
  web/subwoofers.html web/subwoofers.css web/subwoofers.js \
  systemd/house-audio-server.service
do
  if [ ! -f "$file" ]; then
    echo "Installation refused: missing $file" >&2
    exit 1
  fi
done
if ! grep -Fq 'SERVICE_VERSION = "0.14.2-cd1"' house_audio_server.py ||
   ! grep -Fq 'data-view="inputs"' web/house.html ||
   ! grep -Fq '"/inputs/play"' house_audio_server.py ||
   ! grep -Fq 'input_sources.py' install.sh; then
  echo "Installation refused: package is not the complete CD integration." >&2
  exit 1
fi

python3 -m py_compile house_audio_server.py input_sources.py auto_cd.py \
  radio_history.py radio_listening.py radio_metadata.py radio_stations.py \
  state_file.py subwoofers.py

if [ ! -d "$INSTALL_DIR" ] || [ ! -f "$SERVICE_PATH" ]; then
  echo "Installation refused: existing House Music installation not found." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
cp -a "$INSTALL_DIR" "$BACKUP_DIR/house-audio-server"
cp -a "$SERVICE_PATH" "$BACKUP_DIR/house-audio-server.service"
echo "Previous House Music saved at: $BACKUP_DIR"

rollback_on_error() {
  result=$?
  trap - 0
  if [ "$result" -ne 0 ] && [ "$INSTALL_STARTED" -eq 1 ]; then
    echo "CD installation check failed. Restoring previous House Music files..." >&2
    systemctl stop "$SERVICE" || true
    rm -rf "$INSTALL_DIR"
    cp -a "$BACKUP_DIR/house-audio-server" "$INSTALL_DIR"
    cp -a "$BACKUP_DIR/house-audio-server.service" "$SERVICE_PATH"
    systemctl daemon-reload || true
    if systemctl restart "$SERVICE"; then
      echo "Previous House Music restored and restarted." >&2
    else
      echo "WARNING: Previous files restored, but service restart failed." >&2
      echo "Inspect: systemctl status house-audio-server --no-pager" >&2
    fi
  fi
  exit "$result"
}
trap rollback_on_error 0
trap 'exit 130' 2
trap 'exit 143' 15

INSTALL_STARTED=1
sh ./install.sh

# Validate the actual installed browser and server version, not just systemd's PID.
python3 - <<'PY'
import json
import time
from urllib.request import urlopen

base = "http://127.0.0.1:8787"
last_error = None
for attempt in range(20):
    try:
        with urlopen(base + "/health", timeout=2) as response:
            health = json.load(response)
        with urlopen(base + "/", timeout=2) as response:
            browser = response.read().decode("utf-8")
        with urlopen(base + "/house.js", timeout=2) as response:
            js = response.read().decode("utf-8")
        if health.get("version") != "0.14.2-cd1":
            raise RuntimeError("Unexpected installed server version: " + str(health.get("version")))
        if 'data-view="inputs"' not in browser or 'drawInputs' not in js:
            raise RuntimeError("CD player browser controls are missing")
        print("Installed House Music 0.14.2-cd1; browser and service health endpoint verified.")
        break
    except Exception as exc:
        last_error = exc
        time.sleep(0.5)
else:
    raise SystemExit("Installed server validation failed: " + str(last_error))
PY

trap - 0
echo
echo "House Music CD integration installed."
echo "Open: http://192.168.1.12:8787/  and select CD players."
echo "CD source gateway remains its existing independent service on port 8768."
echo "Rollback copy: $BACKUP_DIR"
