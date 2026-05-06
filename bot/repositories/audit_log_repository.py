"""
Audit log repository — append-only data access layer for the `audit_logs` table.

No UPDATE or DELETE operations are exposed; the audit log is immutable by design.

Requirements: 10.3
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.db_models import AuditLog


async def create_entry(
    session: AsyncSession,
    user_id: Optional[int],
    action: str,
    amount: Optional[Decimal] = None,
    balance_before: Optional[Decimal] = None,
    balance_after: Optional[Decimal] = None,
    reference_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> AuditLog:
    """Append a new audit log entry.

    This is the only write operation allowed on audit_logs — there is no
    update or delete method, making the log append-only.

    Requirements: 10.3
    """
    entry = AuditLog(
        user_id=user_id,
        action=action,
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        reference_id=reference_id,
        extra_data=metadata,
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry)
    return entry


async def is_reference_processed(session: AsyncSession, ref: str) -> bool:
    """Return True if an audit log entry with the given reference_id already exists.

    Used for idempotency checks — e.g. to prevent double-crediting a wallet
    when the same webhook is delivered more than once.

    Requirements: 10.3
    """
    result = await session.execute(
        select(AuditLog.id).where(AuditLog.reference_id == ref).limit(1)
    )
    return result.scalar_one_or_none() is not None
