"""
AdminService — admin operations for wallet management and broadcasting.

Provides:
  - add_balance: credit a user's wallet with audit log
  - deduct_balance: debit a user's wallet with balance validation and audit log
  - broadcast: send a message to all active users with rate-limit-friendly delays

Requirements: 8.2, 8.3, 8.4, 8.5
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from bot.repositories import audit_log_repository, user_repository
from bot.services.wallet_service import InsufficientBalanceError, credit, debit, get_balance

if TYPE_CHECKING:
    # Avoid circular imports at runtime; only used for type hints.
    from telegram.ext import Application

logger = logging.getLogger(__name__)


@dataclass
class BroadcastResult:
    """Result summary of a broadcast operation.

    Attributes:
        total:   Total number of active users targeted.
        success: Number of users who received the message successfully.
        failed:  Number of users for whom the send failed.
    """

    total: int
    success: int
    failed: int


def _make_ref_id(action: str, admin_id: int) -> str:
    """Generate a unique reference ID for admin audit log entries.

    Format: ``admin-<action>-<admin_id>-<timestamp_ms>``
    """
    timestamp_ms = int(time.time() * 1000)
    return f"admin-{action}-{admin_id}-{timestamp_ms}"


async def add_balance(
    session: AsyncSession,
    target_telegram_id: int,
    amount: Decimal,
    admin_id: int,
) -> None:
    """Credit *amount* to the wallet of the user identified by *target_telegram_id*.

    Steps:
      1. Fetch user by telegram_id; raise ValueError if not found.
      2. Call wallet_service.credit with reason="admin_add_balance".
      3. Write an additional audit log entry recording the admin's telegram_id
         in the metadata.

    The caller is responsible for committing (or rolling back) the session.

    Requirements: 8.2
    """
    user = await user_repository.get_by_telegram_id(session, target_telegram_id)
    if user is None:
        raise ValueError(
            f"User with telegram_id={target_telegram_id} not found."
        )

    ref_id = _make_ref_id("add_balance", admin_id)

    # Credit the wallet (also writes an audit log entry via wallet_service)
    await credit(
        session=session,
        user_id=user.id,
        amount=amount,
        reason="admin_add_balance",
        ref_id=ref_id,
    )

    # Write a supplementary audit log entry that captures the admin's identity
    await audit_log_repository.create_entry(
        session=session,
        user_id=user.id,
        action="admin_add_balance",
        amount=amount,
        reference_id=ref_id,
        metadata={
            "admin_telegram_id": admin_id,
            "target_telegram_id": target_telegram_id,
        },
    )

    logger.info(
        "Admin %s added balance %.2f to user telegram_id=%s (ref=%s)",
        admin_id,
        amount,
        target_telegram_id,
        ref_id,
    )


async def deduct_balance(
    session: AsyncSession,
    target_telegram_id: int,
    amount: Decimal,
    admin_id: int,
) -> None:
    """Debit *amount* from the wallet of the user identified by *target_telegram_id*.

    Steps:
      1. Fetch user by telegram_id; raise ValueError if not found.
      2. Check current balance; raise InsufficientBalanceError if balance < amount.
      3. Call wallet_service.debit with reason="admin_deduct_balance".
      4. Write an additional audit log entry recording the admin's telegram_id
         in the metadata.

    The caller is responsible for committing (or rolling back) the session.

    Requirements: 8.3, 8.4
    """
    user = await user_repository.get_by_telegram_id(session, target_telegram_id)
    if user is None:
        raise ValueError(
            f"User with telegram_id={target_telegram_id} not found."
        )

    # Pre-check balance to give a meaningful error before attempting the debit.
    current_balance = await get_balance(session, user.id)
    if current_balance < amount:
        raise InsufficientBalanceError(
            current_balance=current_balance,
            requested_amount=amount,
        )

    ref_id = _make_ref_id("deduct_balance", admin_id)

    # Debit the wallet (also writes an audit log entry via wallet_service)
    await debit(
        session=session,
        user_id=user.id,
        amount=amount,
        reason="admin_deduct_balance",
        ref_id=ref_id,
    )

    # Write a supplementary audit log entry that captures the admin's identity
    await audit_log_repository.create_entry(
        session=session,
        user_id=user.id,
        action="admin_deduct_balance",
        amount=amount,
        reference_id=ref_id,
        metadata={
            "admin_telegram_id": admin_id,
            "target_telegram_id": target_telegram_id,
        },
    )

    logger.info(
        "Admin %s deducted balance %.2f from user telegram_id=%s (ref=%s)",
        admin_id,
        amount,
        target_telegram_id,
        ref_id,
    )


async def broadcast(
    session: AsyncSession,
    message: str,
    admin_id: int,
    bot_app: "Application",
) -> BroadcastResult:
    """Send *message* to all active users, respecting Telegram rate limits.

    Steps:
      1. Fetch all active users via user_repository.get_all_active.
      2. For each user, send the message via bot_app.bot.send_message.
      3. Sleep 0.05 seconds between sends to avoid hitting Telegram rate limits.
      4. Track success/failure counts per user (individual failures do not abort
         the broadcast).
      5. Return a BroadcastResult with total, success, and failed counts.

    Requirements: 8.5
    """
    active_users = await user_repository.get_all_active(session)
    total = len(active_users)
    success = 0
    failed = 0

    logger.info(
        "Admin %s starting broadcast to %d active users.", admin_id, total
    )

    for user in active_users:
        try:
            await bot_app.bot.send_message(
                chat_id=user.telegram_id,
                text=message,
            )
            success += 1
        except Exception as exc:  # noqa: BLE001
            # Log the failure but continue to the next user so a single
            # blocked/deactivated account does not abort the entire broadcast.
            logger.warning(
                "Broadcast failed for telegram_id=%s: %s",
                user.telegram_id,
                exc,
            )
            failed += 1

        # Small delay to stay well within Telegram's rate limits
        # (30 messages/second global; 1 message/second per chat).
        await asyncio.sleep(0.05)

    result = BroadcastResult(total=total, success=success, failed=failed)
    logger.info(
        "Broadcast by admin %s complete: total=%d success=%d failed=%d",
        admin_id,
        result.total,
        result.success,
        result.failed,
    )
    return result
