"""
WalletService — atomic wallet operations with audit log.

All mutations (credit / debit) use SELECT ... FOR UPDATE to prevent
race conditions and write an audit log entry within the same session.
The caller is responsible for managing the transaction boundary
(commit / rollback).

Requirements: 3.4, 6.2, 6.3, 8.2, 8.3, 8.4, 10.3, 10.4, 10.5
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.db_models import AuditLog, User
from bot.repositories import audit_log_repository


class InsufficientBalanceError(Exception):
    """Raised when a debit would make the wallet balance go below zero.

    Attributes:
        current_balance: The balance at the time of the failed debit.
        requested_amount: The amount that was requested.
    """

    def __init__(self, current_balance: Decimal, requested_amount: Decimal) -> None:
        self.current_balance = current_balance
        self.requested_amount = requested_amount
        shortfall = requested_amount - current_balance
        super().__init__(
            f"Insufficient balance: have {current_balance}, "
            f"need {requested_amount} (shortfall {shortfall})"
        )


async def get_balance(session: AsyncSession, user_id: int) -> Decimal:
    """Return the current wallet balance for the given user.

    Requirements: 6.2
    """
    result = await session.execute(
        select(User.balance).where(User.id == user_id)
    )
    balance = result.scalar_one_or_none()
    if balance is None:
        raise ValueError(f"User with id={user_id} not found.")
    return balance


async def credit(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    reason: str,
    ref_id: str,
) -> None:
    """Add *amount* to the user's wallet atomically and write an audit log entry.

    Steps (all within the caller's transaction):
      1. SELECT user FOR UPDATE  — lock the row
      2. Calculate new balance   — balance + amount
      3. UPDATE user.balance
      4. INSERT audit_log entry

    Requirements: 3.4, 8.2, 10.3, 10.4, 10.5
    """
    if amount <= Decimal("0"):
        raise ValueError(f"Credit amount must be positive, got {amount}.")

    # 1. Lock the row
    result = await session.execute(
        select(User).where(User.id == user_id).with_for_update()
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError(f"User with id={user_id} not found.")

    # 2. Calculate new balance
    balance_before = user.balance
    balance_after = balance_before + amount

    # 3. Update balance
    await session.execute(
        update(User).where(User.id == user_id).values(balance=balance_after)
    )

    # 4. Write audit log
    await audit_log_repository.create_entry(
        session=session,
        user_id=user_id,
        action=reason,
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        reference_id=ref_id,
    )


async def debit(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    reason: str,
    ref_id: str,
) -> None:
    """Subtract *amount* from the user's wallet atomically and write an audit log entry.

    Raises InsufficientBalanceError if the current balance is less than *amount*.

    Steps (all within the caller's transaction):
      1. SELECT user FOR UPDATE  — lock the row
      2. Validate balance ≥ amount
      3. Calculate new balance   — balance - amount
      4. UPDATE user.balance
      5. INSERT audit_log entry

    Requirements: 6.2, 6.3, 8.3, 8.4, 10.3, 10.4, 10.5
    """
    if amount <= Decimal("0"):
        raise ValueError(f"Debit amount must be positive, got {amount}.")

    # 1. Lock the row
    result = await session.execute(
        select(User).where(User.id == user_id).with_for_update()
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError(f"User with id={user_id} not found.")

    # 2. Validate balance
    balance_before = user.balance
    if balance_before < amount:
        raise InsufficientBalanceError(
            current_balance=balance_before,
            requested_amount=amount,
        )

    # 3. Calculate new balance
    balance_after = balance_before - amount

    # 4. Update balance
    await session.execute(
        update(User).where(User.id == user_id).values(balance=balance_after)
    )

    # 5. Write audit log
    await audit_log_repository.create_entry(
        session=session,
        user_id=user_id,
        action=reason,
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        reference_id=ref_id,
    )
