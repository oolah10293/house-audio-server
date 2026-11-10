#!/bin/sh
set -eu

INSTALL_DIR=/opt/house-audio-server
SERVICE_FILE=/etc/systemd/system/house-audio-server.service
DEFAULT_FILE=/etc/default/house-audio-server

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo ./install.sh"
    exit 1
fi

# Verify the complete, browser-enabled v0.14.2 release *before*
# overwriting any files on the Pi. This prevents repeating the v0.13.2
# wrong-branch deployment that removed the House Music root page.
for file in \
    house_audio_server.py \
    radio_history.py radio_listening.py radio_metadata.py radio_stations.py \
    state_file.py subwoofers.py \
    web/house.css web/house.html web/house.js \
    web/subwoofers.css web/subwoofers.html web/subwoofers.js \
    systemd/house-audio-server.service
do
    if [ ! -f "$file" ]; then
        echo "Refusing incomplete installation: missing $file" >&2
        exit 1
    fi
done
if ! grep -Fq 'SERVICE_VERSION = "0.14.2"' house_audio_server.py ||
   ! grep -Fq '"/": ("house.html"' house_audio_server.py ||
   ! grep -Fq 'SNAPCAST_MONITOR.volume_fallback = SUBWOOFERS' house_audio_server.py; then
    echo "Refusing installation: not the browser-enabled House Audio v0.14.2 release" >&2
    exit 1
fi
python3 -m py_compile house_audio_server.py subwoofers.py

if ! id houseaudio >/dev/null 2>&1; then
    useradd --system --home /nonexistent --shell /usr/sbin/nologin houseaudio
fi

install -d -o root -g root -m 0755 "$INSTALL_DIR"

for file in \
    house_audio_server.py \
    radio_history.py \
    radio_listening.py \
    radio_metadata.py \
    radio_stations.py \
    state_file.py \
    subwoofers.py
do
    install -o root -g root -m 0755 "$file" "$INSTALL_DIR/$file"
done

install -d -o root -g root -m 0755 "$INSTALL_DIR/web"
for file in \
    web/house.css \
    web/house.html \
    web/house.js \
    web/subwoofers.css \
    web/subwoofers.html \
    web/subwoofers.js
do
    install -o root -g root -m 0644 "$file" "$INSTALL_DIR/$file"
done

install -o root -g root -m 0644 systemd/house-audio-server.service "$SERVICE_FILE"

if [ ! -f "$DEFAULT_FILE" ]; then
    install -o root -g root -m 0644 config/house-audio-server.default "$DEFAULT_FILE"
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
