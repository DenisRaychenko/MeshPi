#!/usr/bin/env bash
# Nightly backup of the panel on the Docker host: history/graphs DB, password hash, session key, .env.
# Keeps 14 days. Install the schedule once:   ./backup.sh --install-cron
# Restore: stop the container, copy messages-*.db back to data/messages.db and the files from files-*.tgz, start it.
set -euo pipefail
cd "$(dirname "$0")"

DEST=${BACKUP_DIR:-$HOME/backups/meshpi}
KEEP_DAYS=${KEEP_DAYS:-14}

if [ "${1:-}" = "--install-cron" ]; then
  self=$(readlink -f "$0")
  mkdir -p "$DEST"
  ( crontab -l 2>/dev/null | grep -v 'meshpi.*backup.sh' || true
    echo "45 3 * * * $self >> $DEST/backup.log 2>&1" ) | crontab -
  echo "cron installed: 03:45 every night -> $DEST"
  exit 0
fi

umask 077                         # the backup holds the password hash and the session key
mkdir -p "$DEST"
stamp=$(date +%Y%m%d-%H%M)

if [ -f data/messages.db ]; then
  python3 - data/messages.db "$DEST/messages-$stamp.db" <<'PY'
import os, sqlite3, sys
src, dst = sys.argv[1:3]
s = sqlite3.connect(src, timeout=30)
d = sqlite3.connect(dst)
s.backup(d)
d.commit(); d.close(); s.close()
fd = os.open(dst, os.O_RDONLY); os.fsync(fd); os.close(fd)
PY
fi

tar --ignore-failed-read -czf "$DEST/files-$stamp.tgz" .env docker-compose.yml \
  data/password data/location.json data/secret.key data/noauth data/pong 2>/dev/null || true

sync
find "$DEST" -type f \( -name 'messages-*.db' -o -name 'files-*.tgz' \) -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) backup ok: $stamp"
