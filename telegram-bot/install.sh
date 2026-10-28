#!/usr/bin/env bash
# Install the Meshtastic -> Telegram bridge as a systemd service.
# Run on the Pi:  sudo bash ~/mesh-telegram-install.sh   (copy mesh-telegram-bridge.py to ~/ first, see README)
set -euo pipefail

USER_NAME=${SUDO_USER:?run with sudo from your own user: sudo bash ~/mesh-telegram-install.sh}
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)

read -rsp "Telegram bot token (hidden): " TOKEN; echo
read -rp  "Telegram chat id: " CHAT_ID
read -rp  "Forward every LongFast channel message too? [Y/n]: " ALL
CHANNEL=1; [ "${ALL:-Y}" = "n" ] || [ "${ALL:-Y}" = "N" ] && CHANNEL=0

install -d -m 755 /etc/mesh-telegram
umask 077
cat > /etc/mesh-telegram/env <<EOF
TG_TOKEN=$TOKEN
TG_CHAT_ID=$CHAT_ID
TG_CHANNEL=$CHANNEL
EOF
chmod 600 /etc/mesh-telegram/env

cat > /etc/systemd/system/mesh-telegram.service <<EOF
[Unit]
Description=Meshtastic to Telegram bridge
After=network-online.target mesh-logger.service
Wants=network-online.target

[Service]
User=$USER_NAME
EnvironmentFile=/etc/mesh-telegram/env
ExecStart=$HOME_DIR/mesh-bridge-venv/bin/python $HOME_DIR/mesh-telegram-bridge.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now mesh-telegram
sync
sleep 4
systemctl is-active mesh-telegram
journalctl -u mesh-telegram --no-pager -n 5 | cut -c1-150

# Smoke test: sends one message to the chat.
curl -s --max-time 10 "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=✅ Mesh-міст запущено" | head -c 120; echo
