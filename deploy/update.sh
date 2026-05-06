#!/bin/bash
# Script untuk update bot ke versi terbaru dari GitHub
# Jalankan: bash /opt/aren-tbot/deploy/update.sh

set -e

BOT_DIR="/opt/aren-tbot"
echo "Updating bot..."

cd "$BOT_DIR"
git pull

source venv/bin/activate
pip install -r requirements.txt -q

# Run migrations
alembic upgrade head

# Restart service
systemctl restart aren-tbot

echo "Update selesai!"
systemctl status aren-tbot --no-pager
