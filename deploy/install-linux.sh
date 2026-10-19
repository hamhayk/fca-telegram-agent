#!/usr/bin/env bash
# Installs the agent as an always-on service on Debian/Ubuntu (Google Cloud, Hetzner, DigitalOcean...).
# Usage, from inside the unzipped telegram-agent folder:  bash deploy/install-linux.sh
set -euo pipefail
APP_DIR=/opt/telegram-agent

sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv unzip >/dev/null
sudo id fca >/dev/null 2>&1 || sudo useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin fca
sudo mkdir -p "$APP_DIR"
sudo cp -r fca_agent run.py requirements.txt .env.example "$APP_DIR"/
[ -f .env ] && sudo cp .env "$APP_DIR"/
sudo python3 -m venv "$APP_DIR/.venv"
sudo "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  read -rp "Bot token from @BotFather: " TOKEN
  read -rp "Your Telegram id: " ADMIN
  printf 'BOT_TOKEN=%s\nADMIN_IDS=%s\nDB_PATH=data/agent.db\nBACKUP_DIR=data/backups\n' "$TOKEN" "$ADMIN" | sudo tee "$APP_DIR/.env" >/dev/null
fi
sudo chmod 600 "$APP_DIR/.env"
sudo mkdir -p "$APP_DIR/data"
sudo chown -R fca:fca "$APP_DIR"

sudo cp deploy/fca-agent.service /etc/systemd/system/fca-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now fca-agent
sleep 3
sudo systemctl --no-pager status fca-agent | head -5
echo
echo "Done. Send /start to your bot in Telegram. Logs: sudo journalctl -u fca-agent -f"
