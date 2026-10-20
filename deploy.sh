#!/usr/bin/env bash
# Deploy the panel to the Docker host.   ./deploy.sh [user@host]      (default chechen@10.10.92.30)
# First run also creates .env with a random admin password (read it with: ssh <host> cat ~/meshpi/.env).
# The previous version stays in ~/meshpi/webui.prev for a quick rollback.
set -euo pipefail
HOST=${1:-chechen@10.10.92.30}
cd "$(dirname "$0")"

ssh "$HOST" "mkdir -p ~/meshpi/webui.new"
scp -r webui/app.py webui/requirements.txt webui/constraints.txt webui/templates webui/static "$HOST:meshpi/webui.new/"
scp Dockerfile docker-compose.yml backup.sh "$HOST:meshpi/"

ssh "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
cd ~/meshpi
sed -i 's/\r$//' Dockerfile docker-compose.yml backup.sh webui.new/requirements.txt webui.new/constraints.txt
chmod +x backup.sh
mkdir -p data
if [ ! -f .env ]; then
  umask 077
  pw="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 || true)"
  printf 'MESHPI_USER=admin\nMESHPI_PASSWORD=%s\n' "$pw" > .env
  echo "created .env with a random password"
fi
chmod 600 .env
if [ -d webui ]; then
  [ -d webui.prev ] && mv webui.prev "webui.prev.$(date +%s)"
  mv webui webui.prev
fi
mv webui.new webui
docker compose build
docker compose up -d
docker ps --filter name=meshpi --format '{{.Names}}  {{.Status}}  {{.Ports}}'
REMOTE
