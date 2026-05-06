"""
ServiceCatalogService — business logic for the services catalog.

Responsibilities:
  - Serve the list of active services from the DB (with fallback to cache)
  - Refresh the local cache from the PPOB API
  - Allow admins to set per-service margins (with audit log)

Sell price is a PostgreSQL GENERATED ALWAYS AS (base_price + margin) STORED
column, so the DB always keeps it consistent.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from bot.integrations.ppob_client import PPOBClient, PPOBError
from bot.models.db_models import Service
from bot.repositories import audit_log_repository, service_repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_services(
    session: AsyncSession,
    ppob_client: Optional[PPOBClient] = None,
    use_cache: bool = True,
) -> tuple[list[Service], bool]:
    """Return the list of active services.

    When *use_cache* is True (default) the services are read directly from the
    local DB cache — no PPOB API call is made.

    When *use_cache* is False the function attempts to refresh the cache first
    by calling :func:`refresh_cache`.  If the PPOB API is unreachable the
    function falls back to the last cached data and returns a flag indicating
    that the data may be stale.

    Returns:
        A ``(services, is_fresh)`` tuple where *is_fresh* is ``True`` when the
        data was just fetched from the PPOB API (or read from a warm cache) and
        ``False`` when the PPOB API was unreachable and stale cache is used.

    Requirements: 5.1, 5.3
    """
    if not use_cache and ppob_client is not None:
        try:
            await refresh_cache(session, ppob_client)
            services = await service_repository.get_all_active(session)
            return services, True
        except PPOBError as exc:
            logger.warning(
                "PPOB API unreachable while refreshing service cache: %s. "
                "Falling back to last cached data.",
                exc,
            )
            services = await service_repository.get_all_active(session)
            return services, False

    # Default: serve from local DB cache
    services = await service_repository.get_all_active(session)
    return services, True


async def refresh_cache(
    session: AsyncSession,
    ppob_client: PPOBClient,
) -> int:
    """Fetch the service list from the PPOB API and upsert into the local DB.

    Each service returned by the API is upserted via
    :func:`service_repository.upsert_from_api`, which preserves the existing
    margin and only updates provider-supplied fields (name, description,
    base_price, category, cached_at).

    Returns:
        The number of services upserted.

    Raises:
        PPOBError: if the PPOB API call fails after all retries.

    Requirements: 5.4
    """
    raw_services: list[dict] = await ppob_client.get_services()

    upserted = 0
    for svc in raw_services:
        provider_id = str(svc.get("id", ""))
        if not provider_id:
            logger.warning("Skipping service with missing id: %s", svc)
            continue

        name: str = svc.get("name", "")
        description: Optional[str] = svc.get("description") or None
        # The PPOB API returns price as a number (IDR, no decimals)
        raw_price = svc.get("price", 0)
        try:
            base_price = Decimal(str(raw_price))
        except Exception:
            logger.warning(
                "Skipping service %s — invalid price: %r", provider_id, raw_price
            )
            continue

        category: Optional[str] = svc.get("category") or None

        await service_repository.upsert_from_api(
            session=session,
            provider_id=provider_id,
            name=name,
            description=description,
            base_price=base_price,
            category=category,
        )
        upserted += 1

    logger.info("Service cache refreshed: %d services upserted.", upserted)
    return upserted


async def set_margin(
    session: AsyncSession,
    service_id: int,
    margin: Decimal,
    admin_id: int,
) -> Service:
    """Update the margin for a service and write an audit log entry.

    The sell_price computed column is automatically recalculated by PostgreSQL
    after the margin update.

    Args:
        session:    Active async DB session (caller manages transaction).
        service_id: Primary key of the service to update.
        margin:     New margin value (must be >= 0).
        admin_id:   Telegram ID of the admin performing the change (for audit).

    Returns:
        The updated :class:`~bot.models.db_models.Service` instance.

    Raises:
        ValueError: if *margin* is negative or the service is not found.

    Requirements: 5.5
    """
    if margin < Decimal("0"):
        raise ValueError(f"Margin must be non-negative, got {margin}.")

    # Fetch current service to capture old margin for the audit log
    service = await service_repository.get_by_id(session, service_id)
    if service is None:
        raise ValueError(f"Service with id={service_id} not found.")

    old_margin = service.margin

    # Update margin in DB (sell_price is recomputed automatically)
    updated_service = await service_repository.update_margin(session, service_id, margin)

    # Write audit log entry
    await audit_log_repository.create_entry(
        session=session,
        user_id=None,  # admin action — no user_id in audit_logs for admin ops
        action="admin_set_margin",
        amount=margin,
        reference_id=str(service_id),
        metadata={
            "admin_telegram_id": admin_id,
            "service_id": service_id,
            "service_name": service.name,
            "old_margin": str(old_margin),
            "new_margin": str(margin),
        },
    )

    logger.info(
        "Margin updated for service %d (%s): %s → %s by admin %d",
        service_id,
        service.name,
        old_margin,
        margin,
        admin_id,
    )

    return updated_service
