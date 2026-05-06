#!/bin/bash
# =============================================================================
# Setup script untuk Telegram Bot PPOB/SMM di Ubuntu 22.04/24.04
# Jalankan sebagai root: bash setup.sh
# =============================================================================

set -e  # exit on error

echo "============================================"
echo "  Setup Bot PPOB/SMM - Ubuntu 22.04/24.04"
echo "============================================"

# ---------------------------------------------------------------------------
# 1. Update system
# ---------------------------------------------------------------------------
echo "[1/7] Updating system..."
apt-get update -qq
apt-get upgrade -y -qq

# ---------------------------------------------------------------------------
# 2. Install dependencies
# ---------------------------------------------------------------------------
echo "[2/7] Installing dependencies..."
apt-get install -y -qq \
    python3.12 \
    python3.12-venv \
    python3-pip \
    postgresql \
    postgresql-contrib \
    git \
    curl \
    nano

# ---------------------------------------------------------------------------
# 3. Setup PostgreSQL
# ---------------------------------------------------------------------------
echo "[3/7] Setting up PostgreSQL..."

# Start and enable PostgreSQL
systemctl start postgresql
systemctl enable postgresql

# Create database and user
DB_NAME="botppob"
DB_USER="botuser"
DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')

sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || echo "User already exists"
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || echo "Database already exists"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" 2>/dev/null

echo ""
echo ">>> DATABASE_URL yang akan digunakan:"
echo "    postgresql+asyncpg://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"
echo ""

# ---------------------------------------------------------------------------
# 4. Clone repository
# ---------------------------------------------------------------------------
echo "[4/7] Cloning repository..."
BOT_DIR="/opt/aren-tbot"

if [ -d "$BOT_DIR" ]; then
    echo "Directory exists, pulling latest..."
    cd "$BOT_DIR"
    git pull
else
    git clone https://github.com/jouyai/aren_tbot.git "$BOT_DIR"
    cd "$BOT_DIR"
fi

# ---------------------------------------------------------------------------
# 5. Setup Python virtual environment
# ---------------------------------------------------------------------------
echo "[5/7] Setting up Python virtual environment..."
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ---------------------------------------------------------------------------
# 6. Create .env file
# ---------------------------------------------------------------------------
echo "[6/7] Creating .env file..."

if [ ! -f "$BOT_DIR/.env" ]; then
    cat > "$BOT_DIR/.env" << EOF
# Telegram
BOT_TOKEN=GANTI_DENGAN_BOT_TOKEN
ADMIN_IDS=GANTI_DENGAN_TELEGRAM_ID

# Database (sudah diisi otomatis)
DATABASE_URL=postgresql+asyncpg://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME

# PPOB API - toponepanel.com
PPOB_API_ID=GANTI
PPOB_API_KEY=GANTI

# Payment Gateway - Pakasir
PAKASIR_PROJECT_SLUG=GANTI
PAKASIR_API_KEY=GANTI

# App
LOG_LEVEL=INFO
WEBHOOK_HOST=https://DOMAIN_ATAU_IP_VPS_KAMU
EOF
    echo ""
    echo ">>> File .env dibuat di $BOT_DIR/.env"
    echo ">>> EDIT FILE INI SEBELUM LANJUT: nano $BOT_DIR/.env"
    echo ""
else
    echo ".env already exists, skipping..."
fi

# ---------------------------------------------------------------------------
# 7. Create systemd service
# ---------------------------------------------------------------------------
echo "[7/7] Creating systemd service..."

cat > /etc/systemd/system/aren-tbot.service << EOF
[Unit]
Description=Aren Telegram Bot PPOB/SMM
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=$BOT_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStartPre=$BOT_DIR/venv/bin/alembic upgrade head
ExecStart=$BOT_DIR/venv/bin/python -m bot.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable aren-tbot

echo ""
echo "============================================"
echo "  Setup selesai!"
echo "============================================"
echo ""
echo "LANGKAH SELANJUTNYA:"
echo ""
echo "1. Edit file .env:"
echo "   nano $BOT_DIR/.env"
echo ""
echo "2. Isi semua nilai yang bertuliskan GANTI_DENGAN_..."
echo "   DATABASE_URL sudah terisi otomatis."
echo ""
echo "3. Jalankan bot:"
echo "   systemctl start aren-tbot"
echo ""
echo "4. Cek status:"
echo "   systemctl status aren-tbot"
echo ""
echo "5. Lihat logs:"
echo "   journalctl -u aren-tbot -f"
echo ""
echo "DATABASE_URL (simpan ini):"
echo "postgresql+asyncpg://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"
echo ""
