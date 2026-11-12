#!/bin/sh
# Restore the most recent server backup made by install-cd.sh.
# Run from unpacked CD installer directory: sudo sh ./rollback-cd.sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run: sudo sh ./rollback-cd.sh" >&2
  exit 1
fi

BACKUP_DIR=$(find /var/backups/house-audio-server -mindepth 1 -maxdepth 1 \
  -type d -name 'pre-cd-*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n 1 | cut -d' ' -f2-) || true

if [ -z "$BACKUP_DIR" ] ||
   [ ! -f "$BACKUP_DIR/house-audio-server/house_audio_server.py" ] ||
   [ ! -f "$BACKUP_DIR/house-audio-server.service" ]; then
  echo "Cannot find a complete pre-CD House Music backup. No changes made." >&2
  exit 1
fi

echo "Restoring previous House Music from: $BACKUP_DIR"
systemctl stop house-audio-server.service || true
rm -rf /opt/house-audio-server
cp -a "$BACKUP_DIR/house-audio-server" /opt/house-audio-server
cp -a "$BACKUP_DIR/house-audio-server.service" /etc/systemd/system/house-audio-server.service
systemctl daemon-reload
systemctl restart house-audio-server.service
systemctl status house-audio-server.service --no-pager
echo "Previous House Music restored. The separate CD gateway is left untouched."
