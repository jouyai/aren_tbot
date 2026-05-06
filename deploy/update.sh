#!/bin/bash
# Update bot ke versi terbaru dari GitHub
# Jalankan: bash /opt/aren-tbot/deploy/update.sh

set -e
BOT_DIR="/opt/aren-tbot"
echo "Updating bot..."

# Backup .env
cp "$BOT_DIR/.env" /tmp/aren_tbot_env_backup

# Download latest ZIP
wget -q "https://github.com/jouyai/aren_tbot/archive/refs/heads/main.zip" -O /tmp/aren_tbot.zip
unzip -q /tmp/aren_tbot.zip -d /tmp/
rsync -a --exclude='.env' /tmp/aren_tbot-main/ "$BOT_DIR/"
rm -rf /tmp/aren_tbot-main /tmp/aren_tbot.zip

# Restore .env
cp /tmp/aren_tbot_env_backup "$BOT_DIR/.env"

cd "$BOT_DIR"
source venv/bin/activate
pip install -r requirements.txt -q
alembic upgrade head

systemctl restart aren-tbot
sleep 2
echo "Update selesai!"
systemctl status aren-tbot --no-pager
