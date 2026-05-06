"""
Service repository — data access layer for the `services` table.

Requirements: 5.1, 5.4, 5.5
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.db_models import Service


async def get_all_active(session: AsyncSession) -> list[Service]:
    """Return all services where is_active is True, ordered by category then name.

    Requirements: 5.1
    """
    result = await session.execute(
        select(Service)
        .where(Service.is_active == True)  # noqa: E712
        .order_by(Service.category, Service.name)
    )
    return list(result.scalars().all())


async def get_by_id(session: AsyncSession, service_id: int) -> Optional[Service]:
    """Return the Service with the given primary key, or None.

    Requirements: 5.1
    """
    result = await session.execute(
        select(Service).where(Service.id == service_id)
    )
    return result.scalar_one_or_none()


async def get_by_provider_id(
    session: AsyncSession, provider_id: str
) -> Optional[Service]:
    """Return the Service with the given provider_id, or None.

    Requirements: 5.4
    """
    result = await session.execute(
        select(Service).where(Service.provider_id == provider_id)
    )
    return result.scalar_one_or_none()


async def upsert_from_api(
    session: AsyncSession,
    provider_id: str,
    name: str,
    description: Optional[str],
    base_price: Decimal,
    category: Optional[str],
) -> Service:
    """Insert a new Service or update an existing one (by provider_id).

    Uses PostgreSQL INSERT … ON CONFLICT DO UPDATE so the operation is
    atomic and safe for concurrent callers.

    The margin is preserved on update — only provider-supplied fields
    (name, description, base_price, category, cached_at) are refreshed.

    Requirements: 5.4
    """
    now = datetime.now(tz=timezone.utc)

    stmt = (
        pg_insert(Service)
        .values(
            provider_id=provider_id,
            name=name,
            description=description,
            base_price=base_price,
            category=category,
            is_active=True,
            cached_at=now,
        )
        .on_conflict_do_update(
            index_elements=["provider_id"],
            set_={
                "name": name,
                "description": description,
                "base_price": base_price,
                "category": category,
                "is_active": True,
                "cached_at": now,
                "updated_at": now,
            },
        )
        .returning(Service.id)
    )

    result = await session.execute(stmt)
    service_id: int = result.scalar_one()

    # Re-fetch the full ORM object so the caller gets a proper Service instance.
    service = await get_by_id(session, service_id)
    assert service is not None  # guaranteed by the upsert above
    return service


async def update_margin(
    session: AsyncSession, service_id: int, margin: Decimal
) -> Service:
    """Update the margin for a service and return the updated Service.

    The sell_price computed column is automatically recalculated by the DB.

    Requirements: 5.5
    """
    await session.execute(
        update(Service)
        .where(Service.id == service_id)
        .values(margin=margin)
    )

    service = await get_by_id(session, service_id)
    if service is None:
        raise ValueError(f"Service with id={service_id} not found.")
    await session.refresh(service)
    return service
