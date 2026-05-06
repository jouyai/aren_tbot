"""
Unit tests for WalletService.

Tests use unittest.mock to avoid requiring a real database connection.
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services.wallet_service import (
    InsufficientBalanceError,
    credit,
    debit,
    get_balance,
)
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id: int = 1, balance: Decimal = Decimal("100.00")) -> SimpleNamespace:
    """Create a minimal user-like object for testing (no ORM instrumentation needed)."""
    return SimpleNamespace(
        id=user_id,
        telegram_id=100,
        username="testuser",
        balance=balance,
        is_active=True,
    )


def _make_session(user: SimpleNamespace | None = None) -> AsyncMock:
    """Return a mock AsyncSession that returns *user* from execute()."""
    session = AsyncMock()

    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = user
    scalar_result.scalar_one.return_value = user.balance if user else None

    session.execute = AsyncMock(return_value=scalar_result)
    return session


# ---------------------------------------------------------------------------
# get_balance
# ---------------------------------------------------------------------------

class TestGetBalance:
    @pytest.mark.asyncio
    async def test_returns_balance_for_existing_user(self):
        session = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = Decimal("250.00")
        session.execute = AsyncMock(return_value=scalar_result)

        balance = await get_balance(session, user_id=1)
        assert balance == Decimal("250.00")

    @pytest.mark.asyncio
    async def test_raises_for_missing_user(self):
        session = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=scalar_result)

        with pytest.raises(ValueError, match="not found"):
            await get_balance(session, user_id=999)


# ---------------------------------------------------------------------------
# credit
# ---------------------------------------------------------------------------

class TestCredit:
    @pytest.mark.asyncio
    async def test_credit_increases_balance(self):
        user = _make_user(balance=Decimal("100.00"))
        session = _make_session(user)

        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
        ):
            await credit(session, user_id=1, amount=Decimal("50.00"),
                         reason="topup_confirm", ref_id="REF001")

        # Verify UPDATE was called (execute called at least twice: SELECT + UPDATE)
        assert session.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_credit_writes_audit_log(self):
        user = _make_user(balance=Decimal("100.00"))
        session = _make_session(user)

        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
        ) as mock_audit:
            await credit(session, user_id=1, amount=Decimal("50.00"),
                         reason="topup_confirm", ref_id="REF001")

        mock_audit.assert_awaited_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["amount"] == Decimal("50.00")
        assert call_kwargs["balance_before"] == Decimal("100.00")
        assert call_kwargs["balance_after"] == Decimal("150.00")
        assert call_kwargs["reference_id"] == "REF001"
        assert call_kwargs["action"] == "topup_confirm"

    @pytest.mark.asyncio
    async def test_credit_raises_for_zero_amount(self):
        user = _make_user()
        session = _make_session(user)

        with pytest.raises(ValueError, match="positive"):
            await credit(session, user_id=1, amount=Decimal("0"),
                         reason="topup", ref_id="REF")

    @pytest.mark.asyncio
    async def test_credit_raises_for_negative_amount(self):
        user = _make_user()
        session = _make_session(user)

        with pytest.raises(ValueError, match="positive"):
            await credit(session, user_id=1, amount=Decimal("-10"),
                         reason="topup", ref_id="REF")

    @pytest.mark.asyncio
    async def test_credit_raises_for_missing_user(self):
        session = _make_session(user=None)

        with pytest.raises(ValueError, match="not found"):
            await credit(session, user_id=999, amount=Decimal("50.00"),
                         reason="topup", ref_id="REF")


# ---------------------------------------------------------------------------
# debit
# ---------------------------------------------------------------------------

class TestDebit:
    @pytest.mark.asyncio
    async def test_debit_decreases_balance(self):
        user = _make_user(balance=Decimal("100.00"))
        session = _make_session(user)

        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
        ) as mock_audit:
            await debit(session, user_id=1, amount=Decimal("40.00"),
                        reason="order_create", ref_id="ORD001")

        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["balance_before"] == Decimal("100.00")
        assert call_kwargs["balance_after"] == Decimal("60.00")

    @pytest.mark.asyncio
    async def test_debit_exact_balance_succeeds(self):
        """Debit of exactly the full balance should succeed (result = 0)."""
        user = _make_user(balance=Decimal("100.00"))
        session = _make_session(user)

        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
        ) as mock_audit:
            await debit(session, user_id=1, amount=Decimal("100.00"),
                        reason="order_create", ref_id="ORD002")

        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["balance_after"] == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_debit_raises_insufficient_balance(self):
        user = _make_user(balance=Decimal("50.00"))
        session = _make_session(user)

        with pytest.raises(InsufficientBalanceError) as exc_info:
            await debit(session, user_id=1, amount=Decimal("100.00"),
                        reason="order_create", ref_id="ORD003")

        err = exc_info.value
        assert err.current_balance == Decimal("50.00")
        assert err.requested_amount == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_debit_raises_for_zero_amount(self):
        user = _make_user()
        session = _make_session(user)

        with pytest.raises(ValueError, match="positive"):
            await debit(session, user_id=1, amount=Decimal("0"),
                        reason="order", ref_id="REF")

    @pytest.mark.asyncio
    async def test_debit_raises_for_missing_user(self):
        session = _make_session(user=None)

        with pytest.raises(ValueError, match="not found"):
            await debit(session, user_id=999, amount=Decimal("10.00"),
                        reason="order", ref_id="REF")

    @pytest.mark.asyncio
    async def test_debit_writes_audit_log(self):
        user = _make_user(balance=Decimal("200.00"))
        session = _make_session(user)

        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
        ) as mock_audit:
            await debit(session, user_id=1, amount=Decimal("75.00"),
                        reason="order_create", ref_id="ORD004")

        mock_audit.assert_awaited_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["amount"] == Decimal("75.00")
        assert call_kwargs["reference_id"] == "ORD004"

    @pytest.mark.asyncio
    async def test_debit_no_audit_log_on_insufficient_balance(self):
        """Audit log must NOT be written when debit fails."""
        user = _make_user(balance=Decimal("10.00"))
        session = _make_session(user)

        with patch(
            "bot.services.wallet_service.audit_log_repository.create_entry",
            new_callable=AsyncMock,
        ) as mock_audit:
            with pytest.raises(InsufficientBalanceError):
                await debit(session, user_id=1, amount=Decimal("100.00"),
                            reason="order_create", ref_id="ORD005")

        mock_audit.assert_not_awaited()


# ---------------------------------------------------------------------------
# InsufficientBalanceError
# ---------------------------------------------------------------------------

class TestInsufficientBalanceError:
    def test_attributes(self):
        err = InsufficientBalanceError(
            current_balance=Decimal("30.00"),
            requested_amount=Decimal("50.00"),
        )
        assert err.current_balance == Decimal("30.00")
        assert err.requested_amount == Decimal("50.00")
        assert "shortfall" in str(err).lower() or "20" in str(err)

    def test_is_exception(self):
        err = InsufficientBalanceError(Decimal("0"), Decimal("1"))
        assert isinstance(err, Exception)
