"""
Scheduler — APScheduler-based background job runner.

Jobs registered:
  - sync_processing_orders  : every 5 minutes  (Requirements: 7.4)
  - refresh_cache           : every 1 hour     (Requirements: 5.4)
  - expire_pending_topups   : every 10 minutes (Requirements: 3.6)
  - recover_stale_orders    : once at startup  (Requirements: 11.4)

Each job opens its own DB session via the ``get_session()`` context manager,
calls the appropriate service function, and handles exceptions gracefully so
that a single job failure never crashes the scheduler.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database import get_session
from bot.services import order_service, service_catalog_service, topup_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job wrapper functions
# ---------------------------------------------------------------------------


async def _job_sync_processing_orders(ppob_client, bot_app) -> None:
    """Wrapper: sync all 'processing' orders against the PPOB API."""
    logger.info("Scheduler: starting sync_processing_orders")
    try:
        async with get_session() as session:
            await order_service.sync_processing_orders(
                session=session,
                ppob_client=ppob_client,
                bot_app=bot_app,
            )
        logger.info("Scheduler: sync_processing_orders completed")
    except Exception as exc:
        logger.error("Scheduler: sync_processing_orders failed: %s", exc, exc_info=True)


async def _job_refresh_cache(ppob_client) -> None:
    """Wrapper: refresh the service catalog cache from the PPOB API."""
    logger.info("Scheduler: starting refresh_cache")
    try:
        async with get_session() as session:
            count = await service_catalog_service.refresh_cache(
                session=session,
                ppob_client=ppob_client,
            )
        logger.info("Scheduler: refresh_cache completed (%d services upserted)", count)
    except Exception as exc:
        logger.error("Scheduler: refresh_cache failed: %s", exc, exc_info=True)


async def _job_expire_pending_topups() -> None:
    """Wrapper: expire overdue pending top-up requests."""
    logger.info("Scheduler: starting expire_pending_topups")
    try:
        async with get_session() as session:
            expired = await topup_service.expire_pending_topups(session=session)
        logger.info(
            "Scheduler: expire_pending_topups completed (%d requests expired)",
            len(expired),
        )
    except Exception as exc:
        logger.error(
            "Scheduler: expire_pending_topups failed: %s", exc, exc_info=True
        )


async def _job_recover_stale_orders(ppob_client, bot_app) -> None:
    """Wrapper: recover stale 'processing' orders on startup."""
    logger.info("Scheduler: starting recover_stale_orders")
    try:
        async with get_session() as session:
            await order_service.recover_stale_orders(
                session=session,
                ppob_client=ppob_client,
                bot_app=bot_app,
            )
        logger.info("Scheduler: recover_stale_orders completed")
    except Exception as exc:
        logger.error(
            "Scheduler: recover_stale_orders failed: %s", exc, exc_info=True
        )


# ---------------------------------------------------------------------------
# Scheduler factory
# ---------------------------------------------------------------------------


def setup_scheduler(ppob_client, bot_app) -> AsyncIOScheduler:
    """Create and configure the APScheduler instance.

    Registers all background jobs with their respective triggers.  The caller
    is responsible for calling ``scheduler.start()`` after the event loop and
    bot application are fully initialised.

    Args:
        ppob_client: Initialised PPOBClient instance.
        bot_app:     python-telegram-bot Application instance (used for
                     sending user notifications from within jobs).

    Returns:
        A configured but not-yet-started AsyncIOScheduler.
    """
    scheduler = AsyncIOScheduler()

    # --- Every 5 minutes: sync processing orders (Req 7.4) ---
    scheduler.add_job(
        _job_sync_processing_orders,
        trigger="interval",
        minutes=5,
        args=[ppob_client, bot_app],
        id="sync_processing_orders",
        name="Sync processing orders",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # --- Every 1 hour: refresh service catalog cache (Req 5.4) ---
    scheduler.add_job(
        _job_refresh_cache,
        trigger="interval",
        hours=1,
        args=[ppob_client],
        id="refresh_cache",
        name="Refresh service cache",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # --- Every 10 minutes: expire pending top-ups (Req 3.6) ---
    scheduler.add_job(
        _job_expire_pending_topups,
        trigger="interval",
        minutes=10,
        args=[],
        id="expire_pending_topups",
        name="Expire pending top-ups",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # --- Once at startup (+30 s): recover stale processing orders (Req 11.4) ---
    # Delay 30 detik untuk memberi waktu Neon cold-start selesai
    startup_run_date = datetime.now() + timedelta(seconds=30)
    scheduler.add_job(
        _job_recover_stale_orders,
        trigger="date",
        run_date=startup_run_date,
        args=[ppob_client, bot_app],
        id="recover_stale_orders",
        name="Recover stale orders (startup)",
        replace_existing=True,
    )

    logger.info(
        "Scheduler configured with 4 jobs: "
        "sync_processing_orders (5 min), "
        "refresh_cache (1 hr), "
        "expire_pending_topups (10 min), "
        "recover_stale_orders (startup +10 s)"
    )

    return scheduler
