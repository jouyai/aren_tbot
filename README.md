# Telegram Bot PPOB/SMM Reseller

Bot Telegram untuk platform reseller layanan digital (PPOB/SMM). Pengguna dapat membeli layanan seperti followers, likes, dan akun premium, serta melakukan top up saldo secara manual maupun otomatis via QRIS.

## Tech Stack

- **Python** 3.11+
- **python-telegram-bot** v20+ (async)
- **PostgreSQL** via [Neon](https://neon.tech) (free tier, serverless)
- **SQLAlchemy** 2.0 async + asyncpg
- **FastAPI** + uvicorn (webhook endpoint)
- **APScheduler** (background jobs)
- **httpx** (async HTTP client)
- **Pakasir** (payment gateway, QRIS)
- **toponepanel.com** (PPOB/SMM provider)

## Setup

### 1. Clone & install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env dengan nilai yang sesuai
```

### 3. Setup database (Neon)

1. Buat akun di [neon.tech](https://neon.tech)
2. Buat project baru
3. Salin connection string dari dashboard → masukkan ke `DATABASE_URL` di `.env`

### 4. Jalankan migrasi database

```bash
alembic upgrade head
```

### 5. Jalankan bot

```bash
python -m bot.main
```

## Project Structure

```
telegram-bot-ppob-smm/
├── bot/                    # Source code utama
│   ├── config.py           # Konfigurasi environment variables
│   ├── database.py         # Async engine & session factory
│   ├── main.py             # Entry point
│   ├── scheduler.py        # Background jobs (APScheduler)
│   ├── handlers/           # Telegram command handlers
│   ├── middleware/         # Rate limiter & admin guard
│   ├── services/           # Business logic layer
│   ├── repositories/       # Data access layer
│   ├── integrations/       # PPOB API & payment gateway clients
│   ├── models/             # SQLAlchemy ORM models
│   └── utils/              # Validators & formatters
├── tests/                  # Unit & property-based tests
├── alembic/                # Database migrations
├── .env.example            # Template environment variables
├── requirements.txt        # Python dependencies
└── alembic.ini             # Alembic configuration
```

## Environment Variables

| Variable | Deskripsi |
|---|---|
| `BOT_TOKEN` | Token bot Telegram dari @BotFather |
| `ADMIN_IDS` | Telegram ID admin, dipisah koma |
| `DATABASE_URL` | PostgreSQL connection string (Neon) |
| `PPOB_API_ID` | API ID dari toponepanel.com |
| `PPOB_API_KEY` | API Key dari toponepanel.com |
| `PAKASIR_PROJECT_SLUG` | Slug proyek dari pakasir.com |
| `PAKASIR_API_KEY` | API Key dari pakasir.com |
| `WEBHOOK_HOST` | URL publik server untuk webhook Pakasir |
| `LOG_LEVEL` | Level logging: DEBUG, INFO, WARNING, ERROR |

## Running Tests

```bash
pytest tests/
```
