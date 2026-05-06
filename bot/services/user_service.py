"""
UserService — business logic layer for user account management.

Wraps the user_repository and order_repository to provide higher-level
operations used by command handlers.

Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.db_models import Order, User
from bot.repositories import user_repository


@dataclass
class UserProfile:
    """Aggregated profile data returned by get_profile()."""

    id: int
    telegram_id: int
    username: Optional[str]
    balance: Decimal
    is_active: bool
    total_orders: int


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str],
) -> tuple[User, bool]:
    """Return (user, created) — create a new account if one does not exist.

    If the user already exists, the existing record is returned unchanged.
    The caller is responsible for committing the session.

    Requirements: 1.1, 1.2, 1.3, 1.4
    """
    user = await user_repository.get_by_telegram_id(session, telegram_id)
    if user is not None:
        return user, False

    user = await user_repository.create(session, telegram_id, username)
    return user, True


async def get_profile(
    session: AsyncSession,
    telegram_id: int,
) -> Optional[UserProfile]:
    """Return a full profile including total order count, or None if not found.

    Requirements: 2.1
    """
    user = await user_repository.get_by_telegram_id(session, telegram_id)
    if user is None:
        return None

    # Count total orders for this user
    result = await session.execute(
        select(func.count()).select_from(Order).where(Order.user_id == user.id)
    )
    total_orders: int = result.scalar_one()

    return UserProfile(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        balance=user.balance,
        is_active=user.is_active,
        total_orders=total_orders,
    )


async def update_username(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str],
) -> None:
    """Update the stored username if it has changed.

    Silently does nothing if the user does not exist or the username is None.

    Requirements: 2.2
    """
    if username is None:
        return

    user = await user_repository.get_by_telegram_id(session, telegram_id)
    if user is None:
        return

    if user.username != username:
        await user_repository.update_username(session, telegram_id, username)
