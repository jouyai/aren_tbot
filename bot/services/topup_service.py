"""
TopUpService — business logic for manual and automatic top-up flows.

Functions:
  - create_manual_topup   — create a pending TopUpRequest with a unique ref code
  - confirm_topup         — admin confirms a pending request, credits wallet
  - expire_pending_topups — mark overdue pending requests as expired
  - process_qris_payment  — credit wallet after a verified QRIS webhook

Requirements: 3.1, 3.2, 3.4, 3.5, 3.6
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.db_models import TopUpRequest
from bot.repositories import audit_log_repository, topup_repository
from bot.services import wallet_service
from bot.utils.validators import generate_reference_code, validate_topup_amount

logger = logging.getLogger(__name__)

# How long a pending top-up request stays valid before it expires.
TOPUP_EXPIRY_HOURS = 24


class TopUpError(Exception):
    """Raised when a top-up operation cannot be completed."""


# ---------------------------------------------------------------------------
# Manual top-up
# ---------------------------------------------------------------------------

async def create_manual_topup(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
) -> TopUpRequest:
    """Create a new manual TopUpRequest for *user_id* with the given *amount*.

    Validates the amount, generates a unique reference code, and persists a
    ``pending`` TopUpRequest that expires in 24 hours.

    Raises:
        TopUpError: if the amount is outside the allowed range.

    Requirements: 3.1, 3.2
    """
    valid, error_msg = validate_topup_amount(amount)
    if not valid:
        raise TopUpError(error_msg)

    reference_code = generate_reference_code()
    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=TOPUP_EXPIRY_HOURS)

    topup = await topup_repository.create(
        session=session,
        user_id=user_id,
        amount=amount,
        method="manual",
        reference_code=reference_code,
        expires_at=expires_at,
    )

    # Write audit log entry for the request creation
    await audit_log_repository.create_entry(
        session=session,
        user_id=user_id,
        action="topup_request",
        amount=amount,
        reference_id=reference_code,
        metadata={
            "method": "manual",
            "expires_at": expires_at.isoformat(),
        },
    )

    logger.info(
        "Manual top-up request created: user_id=%s ref=%s amount=%s",
        user_id,
        reference_code,
        amount,
    )
    return topup


# ---------------------------------------------------------------------------
# Admin confirmation
# ---------------------------------------------------------------------------

async def confirm_topup(
    session: AsyncSession,
    ref_code: str,
    admin_id: int,
) -> TopUpRequest:
    """Confirm a pending TopUpRequest identified by *ref_code*.

    Steps:
      1. Validate the reference code exists and is still ``pending``.
      2. Credit the user's wallet atomically.
      3. Mark the TopUpRequest as ``confirmed``.
      4. Write an audit log entry.

    Raises:
        TopUpError: if the reference code is not found or already processed.

    Requirements: 3.4, 3.5
    """
    topup = await topup_repository.get_by_reference(session, ref_code)
    if topup is None:
        raise TopUpError(
            f"Kode referensi '{ref_code}' tidak ditemukan."
        )
    if topup.status != "pending":
        raise TopUpError(
            f"Kode referensi '{ref_code}' tidak dapat dikonfirmasi "
            f"(status saat ini: {topup.status})."
        )

    # Credit wallet — uses SELECT FOR UPDATE internally
    await wallet_service.credit(
        session=session,
        user_id=topup.user_id,
        amount=topup.amount,
        reason="topup_confirm",
        ref_id=ref_code,
    )

    # Update TopUpRequest status
    confirmed_topup = await topup_repository.confirm(
        session=session,
        reference_code=ref_code,
        admin_id=admin_id,
    )

    logger.info(
        "Top-up confirmed: ref=%s user_id=%s amount=%s admin_id=%s",
        ref_code,
        topup.user_id,
        topup.amount,
        admin_id,
    )
    return confirmed_topup


# ---------------------------------------------------------------------------
# Expiry sweep (called by scheduler)
# ---------------------------------------------------------------------------

async def expire_pending_topups(session: AsyncSession) -> List[TopUpRequest]:
    """Mark all overdue pending TopUpRequests as ``expired``.

    Returns the list of expired requests so the caller can notify affected
    users.

    Requirements: 3.6
    """
    expired = await topup_repository.expire_pending(session)

    for topup in expired:
        await audit_log_repository.create_entry(
            session=session,
            user_id=topup.user_id,
            action="topup_expire",
            amount=topup.amount,
            reference_id=topup.reference_code,
            metadata={"expires_at": topup.expires_at.isoformat()},
        )
        logger.info(
            "Top-up expired: ref=%s user_id=%s amount=%s",
            topup.reference_code,
            topup.user_id,
            topup.amount,
        )

    return expired


# ---------------------------------------------------------------------------
# QRIS / automatic payment processing (called by webhook handler)
# ---------------------------------------------------------------------------

async def process_qris_payment(
    session: AsyncSession,
    ref_code: str,
    amount: int | Decimal,
) -> TopUpRequest:
    """Credit the user's wallet after a verified QRIS payment.

    This function is called by the webhook handler once the payment has been
    verified with the Pakasir Transaction Detail API.  It:
      1. Looks up the TopUpRequest by *ref_code*.
      2. Credits the wallet with *amount* (the original requested amount,
         not the total_payment which includes the gateway fee).
      3. Marks the TopUpRequest as ``confirmed``.
      4. Writes an audit log entry.

    Raises:
        TopUpError: if the reference code is not found or already processed.

    Requirements: 3.4, 4.2, 4.5
    """
    decimal_amount = Decimal(str(amount))

    topup = await topup_repository.get_by_reference(session, ref_code)
    if topup is None:
        raise TopUpError(
            f"Kode referensi '{ref_code}' tidak ditemukan."
        )
    if topup.status != "pending":
        raise TopUpError(
            f"Kode referensi '{ref_code}' sudah diproses "
            f"(status: {topup.status})."
        )

    # Credit wallet
    await wallet_service.credit(
        session=session,
        user_id=topup.user_id,
        amount=decimal_amount,
        reason="topup_qris",
        ref_id=ref_code,
    )

    # Mark as confirmed (system-confirmed, no admin_id)
    confirmed_topup = await topup_repository.confirm(
        session=session,
        reference_code=ref_code,
        admin_id=0,  # 0 = system / payment gateway
    )

    logger.info(
        "QRIS payment processed: ref=%s user_id=%s amount=%s",
        ref_code,
        topup.user_id,
        decimal_amount,
    )
    return confirmed_topup
