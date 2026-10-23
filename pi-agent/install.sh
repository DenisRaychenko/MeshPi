#!/usr/bin/env bash
# Встановлення mesh-radio-agent на Raspberry Pi з meshtasticd (лише читання).
# Запуск на Pi:  sudo bash install.sh        (mesh-radio-agent.py має лежати поруч)
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Запустіть через sudo"; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$HERE/mesh-radio-agent.py" ] || { echo "Немає $HERE/mesh-radio-agent.py"; exit 1; }
command -v python3 >/dev/null || { echo "Потрібен python3"; exit 1; }
command -v journalctl >/dev/null || { echo "Потрібен systemd (journalctl)"; exit 1; }

ENVF=/etc/mesh-radio-agent/env
LIB=/usr/local/lib/mesh-radio-agent

read -rp "Порт агента [8097]: " PORT; PORT=${PORT:-8097}
read -rp "IP сервера панелі, якому дозволено питати (Enter: будь-який із токеном): " ALLOW

# окремий користувач без оболонки; читає журнал через групу systemd-journal
id meshagent >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin meshagent
usermod -aG systemd-journal meshagent

install -d -m 755 "$LIB"
install -m 755 -o root -g root "$HERE/mesh-radio-agent.py" "$LIB/mesh-radio-agent.py"

install -d -m 755 /etc/mesh-radio-agent
if [ -f "$ENVF" ] && grep -q '^AGENT_TOKEN=' "$ENVF"; then
  TOKEN=$(grep '^AGENT_TOKEN=' "$ENVF" | cut -d= -f2-)
  echo "Токен лишається тим самим (файл $ENVF уже є)."
else
  TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
fi
umask 077
cat > "$ENVF" <<EOF
AGENT_TOKEN=$TOKEN
AGENT_PORT=$PORT
AGENT_ALLOW=$ALLOW
EOF
chmod 600 "$ENVF"
umask 022

cat > /etc/systemd/system/mesh-radio-agent.service <<EOF
[Unit]
Description=Read-only radio info agent for the Meshpi panel (meshtasticd power/chip cap)
After=network-online.target meshtasticd.service
Wants=network-online.target

[Service]
User=meshagent
Group=meshagent
SupplementaryGroups=systemd-journal
EnvironmentFile=$ENVF
ExecStart=/usr/bin/python3 $LIB/mesh-radio-agent.py
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
MemoryMax=48M

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now mesh-radio-agent
sleep 2
systemctl is-active mesh-radio-agent

echo "--- перевірка:"
curl -s --max-time 5 "http://127.0.0.1:$PORT/ping"; echo
curl -s --max-time 12 -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/radio.json"; echo
IP=$(hostname -I | awk '{print $1}')
cat <<EOF

Готово. У веб-панелі: Система → «Вузли панелі» → ✎ біля цього вузла →
  Адреса агента:  http://$IP:$PORT
  Токен агента:   $TOKEN
(токен показано лише тут і зберігається в $ENVF; нікому його не передавайте)
Якщо на Pi увімкнено брандмауер, дозвольте порт $PORT з сервера панелі:  sudo ufw allow from <IP-сервера> to any port $PORT
EOF
