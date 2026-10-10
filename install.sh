#!/bin/sh
set -eu

INSTALL_DIR=/opt/house-audio-server
SERVICE_FILE=/etc/systemd/system/house-audio-server.service
DEFAULT_FILE=/etc/default/house-audio-server

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo ./install.sh"
    exit 1
fi

if ! id houseaudio >/dev/null 2>&1; then
    useradd --system --home /nonexistent --shell /usr/sbin/nologin houseaudio
fi

install -d -o root -g root -m 0755 "$INSTALL_DIR"
install -o root -g root -m 0755 house_audio_server.py "$INSTALL_DIR/house_audio_server.py"
install -o root -g root -m 0644 radio_metadata.py "$INSTALL_DIR/radio_metadata.py"
install -o root -g root -m 0644 radio_history.py "$INSTALL_DIR/radio_history.py"
install -o root -g root -m 0644 radio_stations.py "$INSTALL_DIR/radio_stations.py"
install -o root -g root -m 0644 systemd/house-audio-server.service "$SERVICE_FILE"

if [ ! -f "$DEFAULT_FILE" ]; then
    install -o root -g root -m 0644 config/house-audio-server.default "$DEFAULT_FILE"
else
    # v0.9.2 removes the RAM recorder. Keep all other existing local settings.
    sed -i -E \
        -e '/^[[:space:]]*(export[[:space:]]+)?DIAGNOSTICS_(ENABLED|POLL_SECONDS|STALL_WARN_SECONDS|HISTORY_LIMIT)[[:space:]]*=/d' \
        -e '/^# Lightweight in-memory renderer diagnostics\. Records only state changes and$/d' \
        -e '/^# stalls, not every sample\.$/d' \
        "$DEFAULT_FILE"
fi

systemctl daemon-reload
systemctl enable house-audio-server.service
systemctl restart house-audio-server.service

echo
echo "Installed and restarted house-audio-server."
echo "Local health check:"
echo "  curl http://127.0.0.1:8787/health"
echo
echo "This installer does NOT start MPD or Snapserver."
