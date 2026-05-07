"""
Main entry point for the Telegram Bot PPOB/SMM Reseller.

Wires all components together:
  - Initialises external clients (PPOBClient, PakasirClient)
  - Builds the Telegram Application
  - Registers all command handlers (user + admin) and error handler
  - Injects dependencies into the webhook handler
  - Starts the APScheduler background scheduler
  - Runs the Telegram bot (polling) and FastAPI webhook server (uvicorn)
    concurrently

Requirements: all
"""
from __future__ import annotations

import asyncio
import logging
import threading

import uvicorn
from telegram import Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, TypeHandler, filters

from bot.config import (
    BOT_TOKEN,
    PAKASIR_API_KEY,
    PAKASIR_PROJECT_SLUG,
    PPOB_API_ID,
    PPOB_API_KEY,
)
from bot.handlers.admin_commands import (
    handle_addsaldo,
    handle_broadcast,
    handle_confirm_topup,
    handle_kurangsaldo,
    handle_setharga,
    handle_maintenance,
)
from bot.handlers.callbacks import handle_callback
from bot.handlers.user_commands import (
    handle_cekorder,
    handle_history,
    handle_order,
    handle_profile,
    handle_saldo,
    handle_services,
    handle_start,
    handle_text_message,
    handle_topup,
)
from bot.integrations.pakasir_client import PakasirClient
from bot.integrations.ppob_client import PPOBClient
from bot.integrations.webhook_handler import app as webhook_app
from bot.integrations.webhook_handler import set_dependencies
from bot.scheduler import setup_scheduler
from bot.utils.error_handler import global_error_handler, unknown_command_handler
from bot.middleware.maintenance_guard import maintenance_check

logger = logging.getLogger(__name__)


def _start_uvicorn() -> None:
    """Run the FastAPI webhook server in a background daemon thread."""
    config = uvicorn.Config(
        webhook_app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)
    # uvicorn.Server.run() creates its own event loop, which is fine in a thread
    server.run()


async def _broadcast_startup(application) -> None:
    """Kirim notifikasi ke semua user aktif bahwa bot baru online."""
    from bot.database import get_session
    from bot.repositories import user_repository

    message = (
        "🟢 *Bot PPOB/SMM Online!*\n\n"
        "Bot baru saja dinyalakan dan siap melayani.\n"
        "Ketik /start atau gunakan tombol di bawah untuk memulai."
    )

    try:
        async with get_session() as session:
            users = await user_repository.get_all_active(session)

        logger.info("Startup broadcast to %d users", len(users))
        success = 0
        for user in users:
            try:
                await application.bot.send_message(
                    chat_id=user.telegram_id,
                    text=message,
                    parse_mode="Markdown",
                )
                success += 1
                await asyncio.sleep(0.05)  # rate limit Telegram
            except Exception as e:
                logger.debug("Startup broadcast failed for %d: %s", user.telegram_id, e)

        logger.info("Startup broadcast done: %d/%d sent", success, len(users))
    except Exception as exc:
        logger.warning("Startup broadcast error (non-fatal): %s", exc)


def main() -> None:
    """Initialise and run the bot with all components wired together.

    python-telegram-bot v20's run_polling() manages its own event loop
    internally, so this function must be a plain (non-async) function called
    directly — NOT wrapped in asyncio.run().
    """

    # ------------------------------------------------------------------
    # 1. Initialise external clients
    # ------------------------------------------------------------------
    ppob_client = PPOBClient(api_id=PPOB_API_ID, api_key=PPOB_API_KEY)
    pakasir_client = PakasirClient(
        project_slug=PAKASIR_PROJECT_SLUG,
        api_key=PAKASIR_API_KEY,
    )
    logger.info("External clients initialised (PPOBClient, PakasirClient)")

    # ------------------------------------------------------------------
    # 2. Build the Telegram Application
    # ------------------------------------------------------------------
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Store shared dependencies in bot_data so handlers can access them
    app.bot_data["ppob_client"] = ppob_client
    app.bot_data["pakasir_client"] = pakasir_client
    app.bot_data["bot_app"] = app

    # Register maintenance middleware at group -1 so it runs before any other handlers
    app.add_handler(TypeHandler(Update, maintenance_check), group=-1)

    # ------------------------------------------------------------------
    # 3. Register user command handlers
    # ------------------------------------------------------------------
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("profile", handle_profile))
    app.add_handler(CommandHandler("saldo", handle_saldo))
    app.add_handler(CommandHandler("topup", handle_topup))
    app.add_handler(CommandHandler("services", handle_services))
    app.add_handler(CommandHandler("layanan", handle_services))
    app.add_handler(CommandHandler("order", handle_order))
    app.add_handler(CommandHandler("cekorder", handle_cekorder))
    app.add_handler(CommandHandler("history", handle_history))

    # ------------------------------------------------------------------
    # 4. Register admin command handlers
    # ------------------------------------------------------------------
    app.add_handler(CommandHandler("confirm_topup", handle_confirm_topup))
    app.add_handler(CommandHandler("addsaldo", handle_addsaldo))
    app.add_handler(CommandHandler("kurangsaldo", handle_kurangsaldo))
    app.add_handler(CommandHandler("setharga", handle_setharga))
    app.add_handler(CommandHandler("broadcast", handle_broadcast))
    app.add_handler(CommandHandler("maintenance", handle_maintenance))

    # ------------------------------------------------------------------
    # 5. Register text message handler (ReplyKeyboard button taps)
    # ------------------------------------------------------------------
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # ------------------------------------------------------------------
    # 6. Register unknown command handler (must be last)
    # ------------------------------------------------------------------
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command_handler))

    # ------------------------------------------------------------------
    # 6. Register callback query handler (inline buttons)
    # ------------------------------------------------------------------
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ------------------------------------------------------------------
    # 7. Register global error handler
    # ------------------------------------------------------------------
    app.add_error_handler(global_error_handler)

    logger.info("All command handlers, callback handler, and error handler registered")

    # ------------------------------------------------------------------
    # 7. Inject dependencies into the webhook handler
    # ------------------------------------------------------------------
    set_dependencies(pakasir_client, bot_app=app)
    logger.info("Webhook handler dependencies injected")

    # ------------------------------------------------------------------
    # 8. Start uvicorn in a background daemon thread
    #    (daemon=True means it exits automatically when the main thread exits)
    # ------------------------------------------------------------------
    uvicorn_thread = threading.Thread(target=_start_uvicorn, daemon=True)
    uvicorn_thread.start()
    logger.info("Uvicorn webhook server starting on 0.0.0.0:8000")

    # ------------------------------------------------------------------
    # 9. Set up the scheduler — it will be started inside post_init
    #    so it runs in the same event loop as the bot
    # ------------------------------------------------------------------
    scheduler = setup_scheduler(ppob_client, bot_app=app)

    async def post_init(application) -> None:
        # Wake up Neon database sebelum scheduler dan broadcast
        from bot.database import wake_up_db
        logger.info("Waking up Neon database...")
        db_ok = await wake_up_db(max_attempts=5)
        if not db_ok:
            logger.error("Could not connect to database after startup — bot may not work correctly")

        scheduler.start()
        logger.info("Scheduler started")

        # Startup announcement ke semua user aktif
        await _broadcast_startup(application)

    async def post_shutdown(application) -> None:
        scheduler.shutdown(wait=False)
        await ppob_client.close()
        await pakasir_client.close()
        logger.info("Shutdown complete")

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    # ------------------------------------------------------------------
    # 10. Run the Telegram bot — run_polling() manages its own event loop
    # ------------------------------------------------------------------
    logger.info("Starting Telegram bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
