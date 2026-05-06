"""
Configuration module — reads all environment variables for the bot.
"""
import logging
import os
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _get_required(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Required environment variable '{key}' is not set.")
    return value


def _get_optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
BOT_TOKEN: str = _get_required("BOT_TOKEN")

_admin_ids_raw: str = _get_required("ADMIN_IDS")
ADMIN_IDS: List[int] = [int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip()]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASE_URL: str = _get_required("DATABASE_URL")

# ---------------------------------------------------------------------------
# PPOB API — toponepanel.com
# ---------------------------------------------------------------------------
PPOB_API_ID: int = int(_get_required("PPOB_API_ID"))
PPOB_API_KEY: str = _get_required("PPOB_API_KEY")

# ---------------------------------------------------------------------------
# Payment Gateway — Pakasir (pakasir.com)
# ---------------------------------------------------------------------------
PAKASIR_PROJECT_SLUG: str = _get_required("PAKASIR_PROJECT_SLUG")
PAKASIR_API_KEY: str = _get_required("PAKASIR_API_KEY")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
WEBHOOK_HOST: str = _get_optional("WEBHOOK_HOST", "https://yourdomain.com")
LOG_LEVEL: str = _get_optional("LOG_LEVEL", "INFO")

# ---------------------------------------------------------------------------
# Logging setup
#
# SYSTEM LOGS (error, warning, info) use Python's standard `logging` module
# and are configured here.  They capture operational events such as startup,
# request errors, and unexpected exceptions.
#
# AUDIT LOGS (financial transactions: top-up, order, wallet adjustments) are
# stored in the `audit_logs` database table via AuditLogRepository and are
# intentionally SEPARATE from system logs.  Never write financial audit
# entries to the system logger.
#
# SECURITY NOTE: API keys (PPOB_API_KEY, PAKASIR_API_KEY) must NEVER appear
# in log output.  Mask them with "***" before logging any payload that may
# contain these values.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

# Confirm effective log level so operators can verify configuration on startup
logger.debug("Logging initialised at level: %s", LOG_LEVEL.upper())
