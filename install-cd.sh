#!/bin/sh
# Install the complete House Music server; back up and automatically restore existing server on failure.
# Run from the extracted package directory: sudo sh ./install-cd.sh
set -eu

SERVICE=house-audio-server.service
INSTALL_DIR=/opt/house-audio-server
SERVICE_PATH=/etc/systemd/system/house-audio-server.service
BACKUP_ROOT=/var/backups/house-audio-server
BACKUP_DIR="$BACKUP_ROOT/pre-cd-$(date +%Y%m%d-%H%M%S)-$$"
INSTALL_STARTED=0
GATEWAY_SERVICE=house-audio-input-gateway.service
GATEWAY_TARGET=/home/ads-suck/gateway.py
GATEWAY_BACKUP_READY=0

if [ "$(id -u)" -ne 0 ]; then
  echo "Run: sudo sh ./install-cd.sh" >&2
  exit 1
fi

# Validate the complete package before touching the installed version.
for file in install.sh house_audio_server.py input_sources.py auto_cd.py pi_input/gateway.py \
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
if ! grep -Fq 'SERVICE_VERSION = "0.14.7"' house_audio_server.py ||
   ! grep -Fq 'data-view="inputs"' web/house.html ||
   ! grep -Fq '> Sources</button>' web/house.html ||
   ! grep -Fq 'source-category' web/house.js ||
   ! grep -Fq 'id="default-playlist-control"' web/house.html ||
   ! grep -Fq 'id="main-panel"' web/house.html ||
   ! grep -Fq '"/inputs/play"' house_audio_server.py ||
   ! grep -Fq 'reason="ejected"' auto_cd.py ||
   ! grep -Fq 'input_sources.py' install.sh ||
   ! grep -Fq 'def run_batched(' house_audio_server.py ||
   ! grep -Fq 'startup_trace event=' house_audio_server.py; then
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

if [ ! -f "$GATEWAY_TARGET" ] || ! systemctl cat "$GATEWAY_SERVICE" >/dev/null 2>&1; then
  echo "Installation refused: existing CD gateway/service missing." >&2
  exit 1
fi
python3 -m py_compile pi_input/gateway.py
python3 - <<'PY'
from urllib.request import urlopen
with urlopen('http://127.0.0.1:8768/sources', timeout=3) as response:
    assert response.status == 200, 'Existing gateway is unavailable'
PY
mkdir -p "$BACKUP_DIR"
cp -a "$INSTALL_DIR" "$BACKUP_DIR/house-audio-server"
cp -a "$SERVICE_PATH" "$BACKUP_DIR/house-audio-server.service"
cp -a "$GATEWAY_TARGET" "$BACKUP_DIR/gateway.py"
GATEWAY_BACKUP_READY=1
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
    if [ "$GATEWAY_BACKUP_READY" -eq 1 ]; then
      cp -a "$BACKUP_DIR/gateway.py" "$GATEWAY_TARGET"
      systemctl restart "$GATEWAY_SERVICE" || echo "WARNING: gateway restart failed after rollback." >&2
    fi
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
# The event gateway is a separate service and must be updated alongside
# the server. Otherwise bootSession/discRevision are lost in transit.
cp pi_input/gateway.py "$GATEWAY_TARGET"
systemctl restart "$GATEWAY_SERVICE"
python3 - <<'PY'
import json, time
from urllib.request import urlopen
for attempt in range(15):
    try:
        with urlopen('http://127.0.0.1:8768/sources', timeout=2) as response:
            data = json.load(response)
        assert isinstance(data.get('sources'), list)
        break
    except Exception:
        time.sleep(0.5)
else:
    raise SystemExit('Updated CD gateway did not start successfully')
PY
sh ./install.sh
if [ -f SOURCE_SHA.txt ]; then
  install -o root -g root -m 0644 SOURCE_SHA.txt "$INSTALL_DIR/SOURCE_SHA.txt"
fi

# Validate the actual installed browser and server version, not just systemd's PID.
python3 - <<'PY'
import hashlib
import json
import time
from pathlib import Path
from urllib.request import urlopen

for source in [*Path('.').glob('*.py'), *Path('web').glob('*')]:
    if source.is_file():
        installed = Path('/opt/house-audio-server') / source
        if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(installed.read_bytes()).digest():
            raise SystemExit('Installed file differs from package: ' + str(source))
if Path('pi_input/gateway.py').read_bytes() != Path('/home/ads-suck/gateway.py').read_bytes():
    raise SystemExit('Installed gateway differs from package')

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
        if health.get("version") != "0.14.7":
            raise RuntimeError("Unexpected installed server version: " + str(health.get("version")))
        if health.get("status") != "ok":
            raise RuntimeError("House service dependencies/startup are not healthy yet")
        if 'data-view="inputs"' not in browser or 'drawInputs' not in js:
            raise RuntimeError("CD player browser controls are missing")
        if 'id="default-playlist-control"' not in browser or 'id="main-panel"' not in browser:
            raise RuntimeError("Updated Library layout is missing")
        if '> Sources</button>' not in browser or 'source-category' not in js:
            raise RuntimeError("Sources page update is missing")
        with urlopen(base + "/session", timeout=2) as response:
            session = json.load(response)
        if "unmutedControllerCount" not in session.get("sessionPolicy", {}):
            raise RuntimeError("Updated session policy is missing")
        print("Installed House Music 0.14.7; browser, service and session policy verified.")
        break
    except Exception as exc:
        last_error = exc
        time.sleep(0.5)
else:
    raise SystemExit("Installed server validation failed: " + str(last_error))
PY

trap - 0
echo
echo "House Music server 0.14.7 installed."
echo "Open: http://192.168.1.12:8787/"
echo "CD source gateway updated in its existing independent service on port 8768."
echo "Rollback copy: $BACKUP_DIR"
