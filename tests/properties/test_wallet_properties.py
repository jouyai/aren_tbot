"""
Property-based tests for WalletService.

Uses the `hypothesis` library to verify universal properties across many
randomly generated inputs.  Database operations are mocked so no real DB
connection is required.

**Validates: Requirements 6.2, 8.4, 10.3, 10.4, 10.5**
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from bot.services.wallet_service import (
    InsufficientBalanceError,
    credit,
    debit,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Represent monetary amounts as integers (cents) then convert to Decimal to
# avoid floating-point imprecision in Hypothesis shrinking.
_positive_cents = st.integers(min_value=1, max_value=10_000_000_00)  # up to 100M IDR


def _cents_to_decimal(cents: int) -> Decimal:
    return Decimal(cents) / Decimal("100")


def _make_user(balance: Decimal) -> SimpleNamespace:
    """Build a minimal user-like object without touching the DB."""
    return SimpleNamespace(
        id=1,
        telegram_id=100,
        username="testuser",
        balance=balance,
        is_active=True,
    )


def _make_session(user: SimpleNamespace) -> AsyncMock:
    """Return a mock AsyncSession that returns *user* from SELECT ... FOR UPDATE."""
    session = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = user
    session.execute = AsyncMock(return_value=scalar_result)
    return session


def _run_async(coro):
    """Run an async coroutine synchronously, compatible with Python 3.10+."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Property 1: Wallet Balance Non-Negative After Debit
#
# For any initial balance and debit amount where amount <= balance,
# the resulting balance_after recorded in the audit log must be >= 0.
#
# **Validates: Requirements 6.2, 8.4**
# ---------------------------------------------------------------------------

@given(
    initial_cents=_positive_cents,
    debit_cents=_positive_cents,
)
@settings(max_examples=200)
def test_property1_balance_non_negative_after_successful_debit(
    initial_cents: int,
    debit_cents: int,
) -> None:
    """Property 1: Wallet Balance Non-Negative After Debit.

    For any debit where amount <= initial_balance, the resulting balance
    must be >= 0.

    **Validates: Requirements 6.2, 8.4**
    """
    # Constrain: debit must be <= initial balance for the operation to succeed
    assume(debit_cents <= initial_cents)

    initial_balance = _cents_to_decimal(initial_cents)
    debit_amount = _cents_to_decimal(debit_cents)

    user = _make_user(balance=initial_balance)
    session = _make_session(user)

    captured: dict = {}

    async def _run() -> None:
        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
        ) as mock_audit:
            await debit(
                session,
                user_id=1,
                amount=debit_amount,
                reason="order_create",
                ref_id="REF-PROP1",
            )
            captured["kwargs"] = mock_audit.call_args.kwargs

    _run_async(_run())

    balance_after = captured["kwargs"]["balance_after"]
    assert balance_after >= Decimal("0"), (
        f"balance_after={balance_after} is negative! "
        f"initial={initial_balance}, debit={debit_amount}"
    )


@given(
    initial_cents=_positive_cents,
    debit_cents=_positive_cents,
)
@settings(max_examples=200)
def test_property1_debit_raises_when_amount_exceeds_balance(
    initial_cents: int,
    debit_cents: int,
) -> None:
    """Property 1 (error path): debit raises InsufficientBalanceError when amount > balance.

    **Validates: Requirements 6.2, 8.4**
    """
    # Constrain: debit must exceed initial balance
    assume(debit_cents > initial_cents)

    initial_balance = _cents_to_decimal(initial_cents)
    debit_amount = _cents_to_decimal(debit_cents)

    user = _make_user(balance=initial_balance)
    session = _make_session(user)

    captured: dict = {}

    async def _run() -> None:
        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
        ):
            try:
                await debit(
                    session,
                    user_id=1,
                    amount=debit_amount,
                    reason="order_create",
                    ref_id="REF-PROP1-ERR",
                )
                captured["raised"] = False
            except InsufficientBalanceError as e:
                captured["raised"] = True
                captured["err"] = e

    _run_async(_run())

    assert captured.get("raised"), "Expected InsufficientBalanceError to be raised"
    err = captured["err"]
    assert err.current_balance == initial_balance
    assert err.requested_amount == debit_amount


# ---------------------------------------------------------------------------
# Property 2: Atomic Balance + Audit Log Consistency
#
# For any wallet mutation (credit or debit):
#   - If the operation succeeds, an audit log entry MUST be created.
#   - If audit log creation fails (raises), the balance update must NOT
#     be committed (the session.execute for UPDATE must not have been
#     called after the failure, or the caller's transaction rolls back).
#
# **Validates: Requirements 10.3, 10.4, 10.5**
# ---------------------------------------------------------------------------

@given(
    initial_cents=_positive_cents,
    credit_cents=_positive_cents,
)
@settings(max_examples=200)
def test_property2_credit_always_creates_audit_log(
    initial_cents: int,
    credit_cents: int,
) -> None:
    """Property 2: Successful credit always produces an audit log entry.

    **Validates: Requirements 10.3, 10.4, 10.5**
    """
    initial_balance = _cents_to_decimal(initial_cents)
    credit_amount = _cents_to_decimal(credit_cents)

    user = _make_user(balance=initial_balance)
    session = _make_session(user)

    audit_calls: list = []

    async def _run() -> None:
        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
        ) as mock_audit:
            await credit(
                session,
                user_id=1,
                amount=credit_amount,
                reason="topup_confirm",
                ref_id="REF-PROP2-CREDIT",
            )
            audit_calls.extend(mock_audit.call_args_list)

    _run_async(_run())

    assert len(audit_calls) == 1, (
        f"Expected exactly 1 audit log entry for credit, got {len(audit_calls)}"
    )
    kwargs = audit_calls[0].kwargs
    assert kwargs["balance_after"] == initial_balance + credit_amount
    assert kwargs["balance_before"] == initial_balance
    assert kwargs["amount"] == credit_amount


@given(
    initial_cents=_positive_cents,
    debit_cents=_positive_cents,
)
@settings(max_examples=200)
def test_property2_debit_always_creates_audit_log_on_success(
    initial_cents: int,
    debit_cents: int,
) -> None:
    """Property 2: Successful debit always produces an audit log entry.

    **Validates: Requirements 10.3, 10.4, 10.5**
    """
    assume(debit_cents <= initial_cents)

    initial_balance = _cents_to_decimal(initial_cents)
    debit_amount = _cents_to_decimal(debit_cents)

    user = _make_user(balance=initial_balance)
    session = _make_session(user)

    audit_calls: list = []

    async def _run() -> None:
        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
        ) as mock_audit:
            await debit(
                session,
                user_id=1,
                amount=debit_amount,
                reason="order_create",
                ref_id="REF-PROP2-DEBIT",
            )
            audit_calls.extend(mock_audit.call_args_list)

    _run_async(_run())

    assert len(audit_calls) == 1, (
        f"Expected exactly 1 audit log entry for debit, got {len(audit_calls)}"
    )
    kwargs = audit_calls[0].kwargs
    assert kwargs["balance_after"] == initial_balance - debit_amount
    assert kwargs["balance_before"] == initial_balance
    assert kwargs["amount"] == debit_amount


@given(
    initial_cents=_positive_cents,
    debit_cents=_positive_cents,
)
@settings(max_examples=200)
def test_property2_no_audit_log_when_debit_fails(
    initial_cents: int,
    debit_cents: int,
) -> None:
    """Property 2 (error path): No audit log entry when debit fails due to insufficient balance.

    If the balance check fails, the audit log must NOT be written —
    ensuring the log only reflects committed balance changes.

    **Validates: Requirements 10.3, 10.4, 10.5**
    """
    assume(debit_cents > initial_cents)

    initial_balance = _cents_to_decimal(initial_cents)
    debit_amount = _cents_to_decimal(debit_cents)

    user = _make_user(balance=initial_balance)
    session = _make_session(user)

    captured: dict = {"audit_called": False}

    async def _run() -> None:
        async def _fake_audit(**kwargs):
            captured["audit_called"] = True

        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            side_effect=_fake_audit,
        ):
            try:
                await debit(
                    session,
                    user_id=1,
                    amount=debit_amount,
                    reason="order_create",
                    ref_id="REF-PROP2-FAIL",
                )
            except InsufficientBalanceError:
                pass  # expected

    _run_async(_run())

    assert not captured["audit_called"], (
        "Audit log must NOT be written when debit fails due to insufficient balance"
    )


@given(
    initial_cents=_positive_cents,
    credit_cents=_positive_cents,
)
@settings(max_examples=100)
def test_property2_balance_not_updated_when_audit_log_fails(
    initial_cents: int,
    credit_cents: int,
) -> None:
    """Property 2 (atomicity): If audit log creation raises, the service must
    propagate the exception so the caller can roll back the transaction,
    leaving the DB in a consistent state.

    **Validates: Requirements 10.4, 10.5**
    """
    initial_balance = _cents_to_decimal(initial_cents)
    credit_amount = _cents_to_decimal(credit_cents)

    user = _make_user(balance=initial_balance)
    session = _make_session(user)

    captured: dict = {"raised": False}

    async def _run() -> None:
        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB write failed"),
        ):
            try:
                await credit(
                    session,
                    user_id=1,
                    amount=credit_amount,
                    reason="topup_confirm",
                    ref_id="REF-PROP2-ATOMIC",
                )
            except RuntimeError as e:
                if "DB write failed" in str(e):
                    captured["raised"] = True

    _run_async(_run())

    assert captured["raised"], (
        "Service must propagate audit log failure so the caller can roll back"
    )
