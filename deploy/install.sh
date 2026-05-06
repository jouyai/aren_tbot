#!/bin/bash
# =============================================================================
# One-shot installer untuk Telegram Bot PPOB/SMM
# Jalankan di VPS Ubuntu 22.04/24.04 sebagai root:
#   bash <(curl -sSL https://raw.githubusercontent.com/jouyai/aren_tbot/main/deploy/install.sh)
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

BOT_DIR="/opt/aren-tbot"

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════╗"
echo "║   Aren Bot PPOB/SMM — Auto Installer     ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ---------------------------------------------------------------------------
# Collect configuration
# ---------------------------------------------------------------------------
echo -e "${YELLOW}Masukkan konfigurasi bot:${NC}"
echo ""

read -p "BOT_TOKEN (dari @BotFather): " BOT_TOKEN
read -p "ADMIN_IDS (Telegram ID kamu, pisah koma jika lebih dari 1): " ADMIN_IDS
read -p "PPOB_API_ID (dari toponepanel.com): " PPOB_API_ID
read -p "PPOB_API_KEY (dari toponepanel.com): " PPOB_API_KEY
read -p "PAKASIR_PROJECT_SLUG (dari pakasir.com): " PAKASIR_PROJECT_SLUG
read -p "PAKASIR_API_KEY (dari pakasir.com): " PAKASIR_API_KEY
read -p "WEBHOOK_HOST (domain/IP VPS, contoh: https://123.45.67.89): " WEBHOOK_HOST

echo ""
echo -e "${YELLOW}Konfigurasi yang akan digunakan:${NC}"
echo "  BOT_TOKEN      : ${BOT_TOKEN:0:20}..."
echo "  ADMIN_IDS      : $ADMIN_IDS"
echo "  PPOB_API_ID    : $PPOB_API_ID"
echo "  PPOB_API_KEY   : ${PPOB_API_KEY:0:10}..."
echo "  PAKASIR_SLUG   : $PAKASIR_PROJECT_SLUG"
echo "  WEBHOOK_HOST   : $WEBHOOK_HOST"
echo ""
read -p "Lanjutkan? (y/n): " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Dibatalkan."
    exit 0
fi

# ---------------------------------------------------------------------------
# 1. Update & install packages
# ---------------------------------------------------------------------------
echo ""
echo -e "${CYAN}[1/6] Installing system packages...${NC}"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    postgresql postgresql-contrib \
    git curl nano

# ---------------------------------------------------------------------------
# 2. Setup PostgreSQL
# ---------------------------------------------------------------------------
echo -e "${CYAN}[2/6] Setting up PostgreSQL...${NC}"

systemctl start postgresql
systemctl enable postgresql

DB_NAME="botppob"
DB_USER="botuser"
DB_PASS=$(openssl rand -hex 16)

sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" 2>/dev/null || true

DATABASE_URL="postgresql+asyncpg://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"
echo -e "  ${GREEN}✓ Database siap${NC}"

# ---------------------------------------------------------------------------
# 3. Clone / update repository
# ---------------------------------------------------------------------------
echo -e "${CYAN}[3/6] Cloning repository...${NC}"

if [ -d "$BOT_DIR/.git" ]; then
    cd "$BOT_DIR" && git pull -q
    echo -e "  ${GREEN}✓ Repository updated${NC}"
else
    git clone -q https://github.com/jouyai/aren_tbot.git "$BOT_DIR"
    echo -e "  ${GREEN}✓ Repository cloned${NC}"
fi

cd "$BOT_DIR"

# ---------------------------------------------------------------------------
# 4. Python virtual environment & dependencies
# ---------------------------------------------------------------------------
echo -e "${CYAN}[4/6] Installing Python dependencies...${NC}"

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo -e "  ${GREEN}✓ Dependencies installed${NC}"

# ---------------------------------------------------------------------------
# 5. Write .env
# ---------------------------------------------------------------------------
echo -e "${CYAN}[5/6] Writing .env...${NC}"

cat > "$BOT_DIR/.env" << EOF
# Telegram
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS

# Database
DATABASE_URL=$DATABASE_URL

# PPOB API - toponepanel.com
PPOB_API_ID=$PPOB_API_ID
PPOB_API_KEY=$PPOB_API_KEY

# Payment Gateway - Pakasir
PAKASIR_PROJECT_SLUG=$PAKASIR_PROJECT_SLUG
PAKASIR_API_KEY=$PAKASIR_API_KEY

# App
LOG_LEVEL=INFO
WEBHOOK_HOST=$WEBHOOK_HOST
EOF

chmod 600 "$BOT_DIR/.env"
echo -e "  ${GREEN}✓ .env written${NC}"

# ---------------------------------------------------------------------------
# 6. Run migrations
# ---------------------------------------------------------------------------
echo -e "${CYAN}[6/6] Running database migrations...${NC}"

cd "$BOT_DIR"
source venv/bin/activate
alembic upgrade head

echo -e "  ${GREEN}✓ Migrations done${NC}"

# ---------------------------------------------------------------------------
# 7. Create systemd service
# ---------------------------------------------------------------------------
echo -e "${CYAN}[7/7] Creating systemd service...${NC}"

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
systemctl start aren-tbot

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
sleep 3

echo ""
echo -e "${GREEN}"
echo "╔══════════════════════════════════════════╗"
echo "║          Instalasi Selesai! 🎉           ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

STATUS=$(systemctl is-active aren-tbot)
if [ "$STATUS" = "active" ]; then
    echo -e "  Status bot: ${GREEN}● RUNNING${NC}"
else
    echo -e "  Status bot: ${RED}● $STATUS${NC}"
fi

echo ""
echo "Perintah berguna:"
echo "  Lihat logs  : journalctl -u aren-tbot -f"
echo "  Stop bot    : systemctl stop aren-tbot"
echo "  Restart bot : systemctl restart aren-tbot"
echo "  Status      : systemctl status aren-tbot"
echo "  Update bot  : bash $BOT_DIR/deploy/update.sh"
echo ""
