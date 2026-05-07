"""
FastAPI webhook handler for Pakasir QRIS payment callbacks.

Endpoint: POST /webhook/payment

Flow:
  1. Parse payload — only process if status == "completed"
  2. Idempotency check — skip if already processed
  3. Verify with Pakasir Transaction Detail API
  4. Credit wallet via topup_service.process_qris_payment
  5. Send Telegram notification to user

The handler uses module-level instances (pakasir_client, bot_app) that are
injected at application startup via set_dependencies().

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Request

from bot.database import get_session
from bot.repositories import audit_log_repository, topup_repository
from bot.services import topup_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(title="PPOB Bot Webhook Handler")

# ---------------------------------------------------------------------------
# Module-level dependency holders — set at startup via set_dependencies()
# ---------------------------------------------------------------------------
_pakasir_client = None   # PakasirClient instance
_bot_app = None          # python-telegram-bot Application instance


def set_dependencies(pakasir_client, bot_app=None) -> None:
    """Inject runtime dependencies into the webhook handler.

    Call this once during application startup before the webhook server
    starts accepting requests.

    Parameters
    ----------
    pakasir_client:
        An initialised ``PakasirClient`` instance.
    bot_app:
        The ``python-telegram-bot`` ``Application`` instance used to send
        Telegram notifications.  May be ``None`` in testing.
    """
    global _pakasir_client, _bot_app
    _pakasir_client = pakasir_client
    _bot_app = bot_app


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@app.post("/webhook/payment")
async def payment_webhook(request: Request) -> dict:
    """Handle a payment callback from Pakasir.

    Expected payload::

        {
            "amount": 50000,
            "order_id": "TOPUP-<reference_code>",
            "project": "slug-proyek-kamu",
            "status": "completed",
            "payment_method": "qris",
            "completed_at": "2024-09-10T08:07:02.819+07:00"
        }

    Returns one of:
      - ``{"status": "ok"}``                — payment processed successfully
      - ``{"status": "ignored"}``           — status is not "completed"
      - ``{"status": "already_processed"}`` — idempotency guard triggered
      - HTTP 400                            — verification with Pakasir failed

    Requirements: 4.2, 4.3, 4.4, 4.5
    """
    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("payment_webhook: failed to parse JSON body: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    order_id: Optional[str] = payload.get("order_id")
    amount: Optional[int] = payload.get("amount")
    status: Optional[str] = payload.get("status")

    logger.info(
        "payment_webhook received: order_id=%s amount=%s status=%s",
        order_id,
        amount,
        status,
    )

    # ------------------------------------------------------------------ #
    # 1. Only process completed payments
    # ------------------------------------------------------------------ #
    if status != "completed":
        logger.info("payment_webhook: ignoring non-completed status=%s", status)
        return {"status": "ignored"}

    if not order_id or amount is None:
        logger.warning("payment_webhook: missing order_id or amount in payload")
        raise HTTPException(status_code=400, detail="Missing order_id or amount")

    # ------------------------------------------------------------------ #
    # 2. Parse reference code from order_id
    # ------------------------------------------------------------------ #
    ref_code = order_id.removeprefix("TOPUP-")
    if ref_code == order_id:
        # order_id did not start with "TOPUP-" — not our transaction
        logger.warning("payment_webhook: unexpected order_id format: %s", order_id)
        raise HTTPException(status_code=400, detail="Unexpected order_id format")

    # ------------------------------------------------------------------ #
    # 3. Idempotency check
    # ------------------------------------------------------------------ #
    async with get_session() as session:
        already_processed = await topup_repository.is_already_processed(session, ref_code)

    if already_processed:
        logger.info("payment_webhook: already processed ref_code=%s", ref_code)
        return {"status": "already_processed"}

    # ------------------------------------------------------------------ #
    # 4. Verify with Pakasir Transaction Detail API
    # ------------------------------------------------------------------ #
    if _pakasir_client is None:
        logger.error("payment_webhook: pakasir_client not initialised")
        raise HTTPException(status_code=503, detail="Payment client not available")

    verified = await _pakasir_client.verify_transaction(order_id=order_id, amount=amount)
    if not verified:
        async with get_session() as session:
            await audit_log_repository.create_entry(
                session=session,
                user_id=None,
                action="webhook_invalid",
                reference_id=ref_code,
                metadata={"order_id": order_id, "amount": amount},
            )
        logger.warning(
            "payment_webhook: verification failed for order_id=%s amount=%s",
            order_id,
            amount,
        )
        raise HTTPException(status_code=400, detail="Transaction not verified")

    # ------------------------------------------------------------------ #
    # 5. Credit wallet + ambil telegram_id dalam satu session
    # ------------------------------------------------------------------ #
    telegram_id: Optional[int] = None
    async with get_session() as session:
        confirmed_topup = await topup_service.process_qris_payment(
            session=session,
            ref_code=ref_code,
            amount=amount,
        )
        # Ambil telegram_id di sini — dalam session yang sama — untuk
        # menghindari event-loop conflict saat membuka session baru dari
        # thread uvicorn yang terpisah.
        try:
            from bot.repositories import user_repository
            user = await user_repository.get_by_id(session, confirmed_topup.user_id)
            if user is not None:
                telegram_id = user.telegram_id
        except Exception as exc:
            logger.warning("payment_webhook: failed to fetch telegram_id: %s", exc)

    logger.info(
        "payment_webhook: wallet credited ref_code=%s user_id=%s telegram_id=%s amount=%s",
        ref_code,
        confirmed_topup.user_id,
        telegram_id,
        amount,
    )

    # ------------------------------------------------------------------ #
    # 6. Send Telegram notification to user
    # ------------------------------------------------------------------ #
    await _notify_user(telegram_id, amount)

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check() -> dict:
    """Simple health check endpoint."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _notify_user(telegram_id: Optional[int], amount: int) -> None:
    """Send a Telegram notification to the user after a successful top-up.

    Accepts *telegram_id* directly (no extra DB lookup) to avoid event-loop
    conflicts when called from the uvicorn background thread.
    Silently swallows errors so a notification failure never causes the
    webhook to return an error response.
    """
    if _bot_app is None:
        logger.debug("_notify_user: bot_app not set, skipping notification")
        return

    if telegram_id is None:
        logger.warning("_notify_user: telegram_id is None, cannot send notification")
        return

    try:
        formatted_amount = f"Rp {amount:,}".replace(",", ".")
        message = (
            f"✅ *Top Up Berhasil!*\n\n"
            f"💰 Nominal: *{formatted_amount}*\n"
            f"💳 Metode: QRIS\n\n"
            f"Saldo kamu sudah bertambah. Ketik /saldo untuk cek saldo terbaru."
        )
        await _bot_app.bot.send_message(
            chat_id=telegram_id,
            text=message,
            parse_mode="Markdown",
        )
        logger.info("_notify_user: notification sent to telegram_id=%s", telegram_id)
    except Exception as exc:
        logger.warning("_notify_user: failed to send notification to %s: %s", telegram_id, exc)
