"""
Global error handling for the Telegram bot.

Provides:
- global_error_handler: registered with python-telegram-bot's error handler
- notify_admins_critical: sends critical alerts to all configured admins
- unknown_command_handler: responds to unrecognized commands with a command list

Requirements: 11.1, 11.2, 11.3
"""
from __future__ import annotations

import logging

import httpx
import sqlalchemy.exc
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Critical error types that warrant admin notification
_CRITICAL_ERROR_TYPES = (
    sqlalchemy.exc.SQLAlchemyError,
    httpx.ConnectError,
    httpx.TimeoutException,
    ConnectionError,
    OSError,
)

# Available user commands shown when an unknown command is received
_USER_COMMANDS = [
    ("/start", "Daftar atau lihat pesan sambutan"),
    ("/profile", "Lihat profil dan informasi akun"),
    ("/saldo", "Cek saldo Wallet terkini"),
    ("/topup", "Top up saldo (contoh: /topup 50000)"),
    ("/services", "Lihat daftar layanan tersedia"),
    ("/layanan", "Lihat daftar layanan tersedia"),
    ("/order", "Buat pesanan (contoh: /order <service_id> <target>)"),
    ("/cekorder", "Cek status pesanan (contoh: /cekorder <order_id>)"),
    ("/history", "Lihat 10 transaksi terakhir"),
]


async def global_error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Catch-all error handler registered with python-telegram-bot.

    - Logs the full exception with traceback to the system logger.
    - Sends a generic, user-friendly error message without exposing stack traces.
    - Notifies all admins when the error is classified as critical.

    Requirements: 11.1, 11.2
    """
    # Log the full exception with traceback for debugging
    logger.error("Exception while handling an update:", exc_info=context.error)

    # Send a generic message to the user — never expose stack traces
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Terjadi kesalahan. Silakan coba lagi nanti."
            )
        except Exception:
            logger.exception("Failed to send error message to user")

    # Notify admins for critical errors
    if isinstance(context.error, _CRITICAL_ERROR_TYPES):
        error_summary = (
            f"Critical error: {type(context.error).__name__}: {context.error}"
        )
        await notify_admins_critical(context.bot, error_summary)


async def notify_admins_critical(bot, message: str) -> None:
    """Send a critical alert message to all configured admin Telegram IDs.

    Individual send failures are caught and logged so that one unreachable
    admin does not prevent others from receiving the notification.

    Requirements: 11.2
    """
    # Import here to avoid circular imports at module load time
    from bot.config import ADMIN_IDS  # noqa: PLC0415

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=f"🚨 *CRITICAL ALERT*\n\n{message}",
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception(
                "Failed to send critical alert to admin %s", admin_id
            )


async def unknown_command_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handler for commands that are not recognized by the bot.

    Displays a list of all available user commands with short descriptions.

    Requirements: 11.3
    """
    lines = ["❓ Perintah tidak dikenali. Berikut daftar perintah yang tersedia:\n"]
    for command, description in _USER_COMMANDS:
        lines.append(f"{command} — {description}")

    await update.effective_message.reply_text("\n".join(lines))
