"""
User repository — data access layer for the `users` table.

All methods accept an AsyncSession as the first parameter and use
SQLAlchemy 2.0 async style.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.db_models import User


async def get_by_telegram_id(
    session: AsyncSession, telegram_id: int
) -> Optional[User]:
    """Return the User with the given Telegram ID, or None if not found.

    Requirements: 1.2, 1.3
    """
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str],
) -> User:
    """Create and persist a new User with zero balance.

    Requirements: 1.1, 1.3
    """
    user = User(telegram_id=telegram_id, username=username)
    session.add(user)
    await session.flush()  # populate auto-generated id without committing
    await session.refresh(user)
    return user


async def update_username(
    session: AsyncSession, telegram_id: int, username: str
) -> None:
    """Update the stored username for the given Telegram ID.

    Requirements: 2.2
    """
    await session.execute(
        update(User)
        .where(User.telegram_id == telegram_id)
        .values(username=username)
    )


async def get_all_active(session: AsyncSession) -> list[User]:
    """Return all users where is_active is True.

    Used by AdminService.broadcast to reach every active user.
    Requirements: 1.1
    """
    result = await session.execute(
        select(User).where(User.is_active == True)  # noqa: E712
    )
    return list(result.scalars().all())
