"""
Order repository — data access layer for the `orders` table.

Requirements: 6.3, 6.4, 7.1, 7.3, 7.4
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.db_models import Order


async def create(
    session: AsyncSession,
    user_id: int,
    service_id: int,
    target: str,
    quantity: int,
    amount: Decimal,
) -> Order:
    """Create a new Order with status 'pending'.

    Requirements: 6.3
    """
    order = Order(
        user_id=user_id,
        service_id=service_id,
        target=target,
        quantity=quantity,
        amount=amount,
        status="pending",
    )
    session.add(order)
    await session.flush()
    await session.refresh(order)
    return order


async def get_by_id_and_user(
    session: AsyncSession, order_id: int, user_id: int
) -> Optional[Order]:
    """Return the Order only if it belongs to the given user, or None.

    Prevents users from viewing orders that belong to other users.

    Requirements: 7.1, 7.2
    """
    result = await session.execute(
        select(Order).where(
            Order.id == order_id,
            Order.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def get_processing_orders(session: AsyncSession) -> list[Order]:
    """Return all orders currently in 'processing' status.

    Used by the scheduler to poll PPOB API for status updates.

    Requirements: 7.4
    """
    result = await session.execute(
        select(Order).where(Order.status == "processing")
    )
    return list(result.scalars().all())


async def update_status(
    session: AsyncSession,
    order_id: int,
    status: str,
    provider_order_id: Optional[str] = None,
    status_message: Optional[str] = None,
) -> Order:
    """Update the status (and optionally provider_order_id / status_message) of an order.

    Also sets last_checked_at to the current UTC time.

    Requirements: 6.4, 7.3, 7.4
    """
    now = datetime.now(tz=timezone.utc)
    values: dict = {"status": status, "last_checked_at": now}
    if provider_order_id is not None:
        values["provider_order_id"] = provider_order_id
    if status_message is not None:
        values["status_message"] = status_message

    await session.execute(
        update(Order).where(Order.id == order_id).values(**values)
    )

    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order is None:
        raise ValueError(f"Order with id={order_id} not found.")
    return order


async def get_user_history(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[Order]:
    """Return the most recent `limit` orders for a user, newest first.

    Requirements: 7.3
    """
    result = await session.execute(
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
