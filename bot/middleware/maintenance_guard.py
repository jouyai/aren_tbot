"""
Maintenance mode middleware.

Blocks non-admins when MAINTENANCE_MODE is True.
"""
from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.config import ADMIN_IDS


async def maintenance_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Check if the bot is in maintenance mode.
    If yes, block non-admins, send a maintenance message, and stop further processing.
    """
    is_maintenance = context.bot_data.get("maintenance_mode", False)
    if not is_maintenance:
        return

    user = update.effective_user
    if user is None:
        return

    # Let admins pass
    if user.id in ADMIN_IDS:
        return

    # Non-admin: block and notify
    if update.message:
        await update.message.reply_text(
            "🚧 *Mode Maintenance / Testing* 🚧\n\n"
            "Bot sedang dalam mode perbaikan atau pengujian oleh Admin.\n"
            "Silakan coba lagi beberapa saat nanti ya. Mohon maaf atas ketidaknyamanannya. 🙏",
            parse_mode="Markdown"
        )
    elif update.callback_query:
        await update.callback_query.answer("Bot sedang maintenance. Coba lagi nanti.", show_alert=True)

    # Stop this update from being processed by other handlers
    raise ApplicationHandlerStop()
