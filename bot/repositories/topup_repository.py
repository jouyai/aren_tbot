"""
TopUp repository — data access layer for the `topup_requests` table.

Requirements: 3.1, 3.4, 3.5, 3.6, 4.5
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.db_models import TopUpRequest


async def create(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    method: str,
    reference_code: str,
    expires_at: datetime,
) -> TopUpRequest:
    """Create a new TopUpRequest with status 'pending'.

    Requirements: 3.1
    """
    topup = TopUpRequest(
        user_id=user_id,
        amount=amount,
        method=method,
        reference_code=reference_code,
        expires_at=expires_at,
        status="pending",
    )
    session.add(topup)
    await session.flush()
    await session.refresh(topup)
    return topup


async def get_by_reference(
    session: AsyncSession, reference_code: str
) -> Optional[TopUpRequest]:
    """Return the TopUpRequest with the given reference code, or None.

    Requirements: 3.4, 3.5
    """
    result = await session.execute(
        select(TopUpRequest).where(TopUpRequest.reference_code == reference_code)
    )
    return result.scalar_one_or_none()


async def confirm(
    session: AsyncSession, reference_code: str, admin_id: int
) -> TopUpRequest:
    """Mark a pending TopUpRequest as 'confirmed' by an admin.

    Raises ValueError if the request is not found or is not in 'pending' status.

    Requirements: 3.4, 3.5
    """
    topup = await get_by_reference(session, reference_code)
    if topup is None:
        raise ValueError(f"TopUpRequest with reference '{reference_code}' not found.")
    if topup.status != "pending":
        raise ValueError(
            f"TopUpRequest '{reference_code}' cannot be confirmed "
            f"(current status: {topup.status})."
        )

    now = datetime.now(tz=timezone.utc)
    await session.execute(
        update(TopUpRequest)
        .where(TopUpRequest.reference_code == reference_code)
        .values(
            status="confirmed",
            confirmed_at=now,
            confirmed_by=admin_id,
        )
    )
    await session.refresh(topup)
    return topup


async def expire_pending(session: AsyncSession) -> list[TopUpRequest]:
    """Find all pending TopUpRequests past their expiry time, mark them 'expired',
    and return the list so callers can notify affected users.

    Requirements: 3.6
    """
    now = datetime.now(tz=timezone.utc)

    # Fetch the rows we are about to expire so we can return them.
    result = await session.execute(
        select(TopUpRequest).where(
            TopUpRequest.status == "pending",
            TopUpRequest.expires_at < now,
        )
    )
    expired_requests: list[TopUpRequest] = list(result.scalars().all())

    if not expired_requests:
        return []

    expired_ids = [r.id for r in expired_requests]
    await session.execute(
        update(TopUpRequest)
        .where(TopUpRequest.id.in_(expired_ids))
        .values(status="expired")
    )

    # Refresh each object so callers see the updated status.
    for req in expired_requests:
        await session.refresh(req)

    return expired_requests


async def is_already_processed(session: AsyncSession, reference_code: str) -> bool:
    """Return True if the TopUpRequest with the given reference code has been confirmed.

    Used for idempotency — prevents double-crediting when a webhook is
    delivered more than once.

    Requirements: 4.5
    """
    result = await session.execute(
        select(TopUpRequest.id)
        .where(
            TopUpRequest.reference_code == reference_code,
            TopUpRequest.status == "confirmed",
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None
