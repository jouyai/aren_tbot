"""
OrderService — business logic for creating and tracking orders.

Flow for create_order:
  1. Validate target via TargetValidator (URL/username heuristic)
  2. Fetch service; raise if not found or inactive
  3. Check user balance is sufficient
  4. Atomically: debit wallet + create order (status=pending)
  5. Call PPOB API to place the order
  6. On success: update order status → processing, save provider_order_id
  7. On PPOBOrderError: credit wallet back (rollback debit), update order → failed,
     notify user via bot_app if provided

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 7.1, 7.2, 7.3, 7.4, 7.5,
              9.3, 9.5, 11.4
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.integrations.ppob_client import PPOBClient, PPOBError, PPOBOrderError
from bot.models.db_models import Order, Service
from bot.repositories import audit_log_repository, order_repository, service_repository
from bot.services.wallet_service import InsufficientBalanceError, credit, debit, get_balance
from bot.utils.validators import TargetValidator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class ServiceNotFoundError(Exception):
    """Raised when the requested service does not exist or is inactive."""


class InsufficientBalanceOrderError(Exception):
    """Raised when the user's balance is insufficient to place the order.

    Attributes:
        current_balance: User's current balance.
        required_amount: Amount required for the order.
    """

    def __init__(self, current_balance: Decimal, required_amount: Decimal) -> None:
        self.current_balance = current_balance
        self.required_amount = required_amount
        shortfall = required_amount - current_balance
        super().__init__(
            f"Insufficient balance: have {current_balance}, "
            f"need {required_amount} (shortfall {shortfall})"
        )


class InvalidTargetError(Exception):
    """Raised when the order target fails validation for the service type."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = {"success", "failed", "cancelled"}
# Map PPOB API order_status values to our internal status values
_PPOB_STATUS_MAP = {
    "completed": "success",
    "canceled": "failed",
    "partial": "success",   # treat partial as success (provider delivered partial)
    "processing": "processing",
    "pending": "processing",
}


def _infer_service_type(target: str) -> str:
    """Heuristically infer whether a target is a URL, email, or username.

    - Starts with http:// or https://  → 'url'
    - Contains '@'                      → 'email'
    - Otherwise                         → 'username'
    """
    if target.startswith("http://") or target.startswith("https://"):
        return "url"
    if "@" in target:
        return "email"
    return "username"


async def _notify_user(
    bot_app,
    telegram_id: int,
    message: str,
) -> None:
    """Send a Telegram notification to a user if bot_app is available."""
    if bot_app is None:
        return
    try:
        await bot_app.bot.send_message(chat_id=telegram_id, text=message)
    except Exception as exc:
        logger.warning("Failed to send notification to user %d: %s", telegram_id, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_order(
    session: AsyncSession,
    user_id: int,
    service_id: int,
    target: str,
    ppob_client: PPOBClient,
    bot_app=None,
) -> Order:
    """Create a new order for the given user and service.

    Steps:
      1. Fetch service; raise ServiceNotFoundError if not found/inactive.
      2. Validate target using TargetValidator (URL/username heuristic).
      3. Check user balance; raise InsufficientBalanceOrderError if insufficient.
      4. Atomically debit wallet and create order with status='pending'.
      5. Call PPOB API.
      6. On success: update order → 'processing', save provider_order_id.
      7. On PPOBOrderError: credit wallet back, update order → 'failed'.

    Args:
        session:     Active async DB session (caller manages transaction).
        user_id:     Internal DB user ID.
        service_id:  Internal DB service ID.
        target:      Order target (URL or username).
        ppob_client: PPOB API client instance.
        bot_app:     Optional python-telegram-bot Application for notifications.

    Returns:
        The created :class:`~bot.models.db_models.Order` instance.

    Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 9.3
    """
    # 1. Fetch service
    service = await service_repository.get_by_id(session, service_id)
    if service is None or not service.is_active:
        raise ServiceNotFoundError(
            f"Service with id={service_id} not found or is inactive."
        )

    # 2. Validate target
    service_type = _infer_service_type(target)
    if not TargetValidator.validate(service_type, target):
        raise InvalidTargetError(
            f"Invalid target {target!r} for service type {service_type!r}."
        )

    # 3. Check balance
    balance = await get_balance(session, user_id)
    sell_price: Decimal = service.sell_price
    if balance < sell_price:
        raise InsufficientBalanceOrderError(
            current_balance=balance,
            required_amount=sell_price,
        )

    # 4. Atomically debit wallet + create order (pending)
    order_ref = f"order-pending-{user_id}-{service_id}"
    await debit(
        session,
        user_id=user_id,
        amount=sell_price,
        reason="order_create",
        ref_id=order_ref,
    )

    order = await order_repository.create(
        session=session,
        user_id=user_id,
        service_id=service_id,
        target=target,
        quantity=1,
        amount=sell_price,
    )

    # Update ref_id in audit log to use the real order id
    # (We can't know the order id before creating it, so we use a placeholder above)

    # 5. Call PPOB API
    try:
        provider_id_int = int(service.provider_id)
    except (ValueError, TypeError):
        # provider_id might be a string that can't be cast; pass as-is
        provider_id_int = service.provider_id  # type: ignore[assignment]

    try:
        result = await ppob_client.create_order(
            service_id=provider_id_int,
            target=target,
            quantity=1,
        )

        # 6. Success: update order → processing
        provider_order_id = str(result.get("order", ""))
        order = await order_repository.update_status(
            session=session,
            order_id=order.id,
            status="processing",
            provider_order_id=provider_order_id,
        )

        await audit_log_repository.create_entry(
            session=session,
            user_id=user_id,
            action="order_create",
            amount=sell_price,
            reference_id=str(order.id),
            metadata={
                "service_id": service_id,
                "provider_order_id": provider_order_id,
                "target": target,
            },
        )

        logger.info(
            "Order %d created successfully (provider_order_id=%s)",
            order.id,
            provider_order_id,
        )

    except PPOBOrderError as exc:
        # 7. PPOB rejected the order — rollback debit
        logger.warning(
            "PPOB order creation failed for order %d: %s. Rolling back debit.",
            order.id,
            exc,
        )

        rollback_ref = f"order-failed-rollback-{order.id}"
        await credit(
            session,
            user_id=user_id,
            amount=sell_price,
            reason="order_failed",
            ref_id=rollback_ref,
        )

        order = await order_repository.update_status(
            session=session,
            order_id=order.id,
            status="failed",
            status_message=str(exc),
        )

        await audit_log_repository.create_entry(
            session=session,
            user_id=user_id,
            action="order_failed",
            amount=sell_price,
            reference_id=str(order.id),
            metadata={
                "service_id": service_id,
                "error": str(exc),
                "target": target,
            },
        )

        # Notify user (best-effort)
        if bot_app is not None:
            from bot.repositories import user_repository
            from bot.config import ADMIN_IDS
            user = await user_repository.get_by_telegram_id(session, user_id)
            if user is not None:
                is_maintenance = bot_app.bot_data.get("maintenance_mode", False)
                if is_maintenance:
                    for admin_id in ADMIN_IDS:
                        await _notify_user(
                            bot_app,
                            admin_id,
                            f"⚠️ [MAINTENANCE] PPOB Order Error pada Order #{order.id}:\n{exc}"
                        )
                    await _notify_user(
                        bot_app,
                        user.telegram_id,
                        f"❌ Order #{order.id} gagal diproses. Saldo Anda telah dikembalikan.\n"
                        f"Alasan: {exc}",
                    )
                else:
                    await _notify_user(
                        bot_app,
                        user.telegram_id,
                        f"❌ Order #{order.id} gagal diproses. Saldo Anda telah dikembalikan.\n"
                        f"Harap segera hubungi admin.",
                    )

    except PPOBError as exc:
        # Network/server error after all retries — same rollback logic
        logger.error(
            "PPOB API error for order %d after all retries: %s. Rolling back debit.",
            order.id,
            exc,
        )

        rollback_ref = f"order-failed-rollback-{order.id}"
        await credit(
            session,
            user_id=user_id,
            amount=sell_price,
            reason="order_failed",
            ref_id=rollback_ref,
        )

        order = await order_repository.update_status(
            session=session,
            order_id=order.id,
            status="failed",
            status_message=str(exc),
        )

        await audit_log_repository.create_entry(
            session=session,
            user_id=user_id,
            action="order_failed",
            amount=sell_price,
            reference_id=str(order.id),
            metadata={
                "service_id": service_id,
                "error": str(exc),
                "target": target,
            },
        )

        if bot_app is not None:
            from bot.repositories import user_repository
            from bot.config import ADMIN_IDS
            user = await user_repository.get_by_telegram_id(session, user_id)
            if user is not None:
                is_maintenance = bot_app.bot_data.get("maintenance_mode", False)
                if is_maintenance:
                    for admin_id in ADMIN_IDS:
                        await _notify_user(
                            bot_app,
                            admin_id,
                            f"⚠️ [MAINTENANCE] PPOB Network Error pada Order #{order.id}:\n{exc}"
                        )
                    await _notify_user(
                        bot_app,
                        user.telegram_id,
                        f"❌ Order #{order.id} gagal diproses (error jaringan). "
                        f"Saldo Anda telah dikembalikan.",
                    )
                else:
                    await _notify_user(
                        bot_app,
                        user.telegram_id,
                        f"❌ Order #{order.id} gagal diproses. Saldo Anda telah dikembalikan.\n"
                        f"Harap segera hubungi admin.",
                    )

    return order


async def get_order(
    session: AsyncSession,
    order_id: int,
    user_id: int,
) -> Optional[Order]:
    """Return the order if it belongs to the given user, or None.

    Requirements: 7.1, 7.2
    """
    return await order_repository.get_by_id_and_user(session, order_id, user_id)


async def get_history(
    session: AsyncSession,
    user_id: int,
    limit: int = 10,
) -> list[Order]:
    """Return the most recent *limit* orders for the user, newest first.

    Requirements: 7.3
    """
    return await order_repository.get_user_history(session, user_id, limit)


async def sync_processing_orders(
    session: AsyncSession,
    ppob_client: PPOBClient,
    bot_app=None,
) -> None:
    """Check the status of all 'processing' orders against the PPOB API.

    Processes up to 50 orders per batch (PPOB API limit for bulk status check).
    Updates the local DB status and sends user notifications when an order
    reaches a terminal state (success or failed).

    Requirements: 7.4, 7.5, 9.5
    """
    processing_orders = await order_repository.get_processing_orders(session)
    if not processing_orders:
        logger.debug("sync_processing_orders: no processing orders found.")
        return

    # Process in batches of 50 (PPOB API limit)
    batch_size = 50
    for i in range(0, len(processing_orders), batch_size):
        batch = processing_orders[i : i + batch_size]
        await _sync_batch(session, ppob_client, batch, bot_app)


async def _sync_batch(
    session: AsyncSession,
    ppob_client: PPOBClient,
    orders: list[Order],
    bot_app=None,
) -> None:
    """Sync a batch of up to 50 processing orders."""
    # Only orders that have a provider_order_id can be checked
    checkable = [o for o in orders if o.provider_order_id]
    if not checkable:
        return

    provider_ids = [int(o.provider_order_id) for o in checkable]

    try:
        result = await ppob_client.check_order_status(provider_ids)
    except PPOBError as exc:
        logger.warning("Failed to check order status batch: %s", exc)
        return

    # Build a map: provider_order_id → order_status
    orders_data: dict = result.get("orders", {})

    for order in checkable:
        pid = order.provider_order_id
        order_info = orders_data.get(str(pid)) or orders_data.get(pid)
        if order_info is None:
            logger.debug("No status info for provider_order_id=%s", pid)
            continue

        ppob_status = order_info.get("order_status", "")
        new_status = _PPOB_STATUS_MAP.get(ppob_status, "processing")

        if new_status == order.status:
            # No change — just update last_checked_at
            await order_repository.update_status(
                session=session,
                order_id=order.id,
                status=order.status,
            )
            continue

        # Status changed
        updated_order = await order_repository.update_status(
            session=session,
            order_id=order.id,
            status=new_status,
            status_message=order_info.get("msg"),
        )

        logger.info(
            "Order %d status updated: %s → %s",
            order.id,
            order.status,
            new_status,
        )

        # Write audit log for terminal transitions
        if new_status in _TERMINAL_STATUSES:
            action = "order_success" if new_status == "success" else "order_failed"
            await audit_log_repository.create_entry(
                session=session,
                user_id=order.user_id,
                action=action,
                amount=order.amount,
                reference_id=str(order.id),
                metadata={
                    "provider_order_id": pid,
                    "ppob_status": ppob_status,
                },
            )

            # Notify user
            if bot_app is not None:
                await _notify_order_terminal(bot_app, session, updated_order)


async def _notify_order_terminal(bot_app, session: AsyncSession, order: Order) -> None:
    """Send a terminal-status notification to the order's owner."""
    from bot.repositories import user_repository

    # We need the user's telegram_id — look up by user_id
    result = await session.execute(
        select(Order).where(Order.id == order.id)
    )
    # user relationship may not be loaded; fetch telegram_id directly
    from bot.models.db_models import User
    user_result = await session.execute(
        select(User.telegram_id).where(User.id == order.user_id)
    )
    telegram_id = user_result.scalar_one_or_none()
    if telegram_id is None:
        return

    if order.status == "success":
        msg = (
            f"✅ Order #{order.id} berhasil diselesaikan!\n"
            f"Layanan: {order.service_id}\n"
            f"Target: {order.target}"
        )
    else:
        msg = (
            f"❌ Order #{order.id} gagal.\n"
            f"Layanan: {order.service_id}\n"
            f"Target: {order.target}\n"
            f"Keterangan: {order.status_message or '-'}"
        )

    await _notify_user(bot_app, telegram_id, msg)


async def recover_stale_orders(
    session: AsyncSession,
    ppob_client: PPOBClient,
    bot_app=None,
) -> None:
    """Synchronize 'processing' orders that haven't been updated in > 10 minutes.

    Called once at bot startup to recover from any missed status updates
    during downtime.

    Requirements: 11.4
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=10)

    # Fetch all processing orders
    all_processing = await order_repository.get_processing_orders(session)

    stale = [
        o for o in all_processing
        if (
            # last_checked_at is None and created_at is old enough
            (o.last_checked_at is None and o.created_at < cutoff)
            or
            # last_checked_at exists but is too old
            (o.last_checked_at is not None and o.last_checked_at < cutoff)
        )
    ]

    if not stale:
        logger.info("recover_stale_orders: no stale orders found.")
        return

    logger.info(
        "recover_stale_orders: found %d stale processing orders. Syncing...",
        len(stale),
    )

    # Reuse sync logic in batches of 50
    batch_size = 50
    for i in range(0, len(stale), batch_size):
        batch = stale[i : i + batch_size]
        await _sync_batch(session, ppob_client, batch, bot_app)
