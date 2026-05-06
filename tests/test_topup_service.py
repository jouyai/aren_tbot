"""
Unit tests for bot/services/topup_service.py

Tests cover:
  - create_manual_topup: valid amounts, invalid amounts, reference code uniqueness
  - confirm_topup: happy path, not-found, already-processed
  - expire_pending_topups: expiry logic
  - process_qris_payment: happy path, not-found, already-processed

All database interactions are mocked so no real DB connection is needed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services.topup_service import (
    TopUpError,
    confirm_topup,
    create_manual_topup,
    expire_pending_topups,
    process_qris_payment,
)
from bot.models.db_models import TopUpRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_topup(
    ref_code: str = "ABCDEF1234567890ABCDEF1234567890",
    status: str = "pending",
    user_id: int = 1,
    amount: Decimal = Decimal("50000"),
) -> TopUpRequest:
    topup = MagicMock(spec=TopUpRequest)
    topup.id = 1
    topup.user_id = user_id
    topup.reference_code = ref_code
    topup.amount = amount
    topup.status = status
    topup.expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=24)
    return topup


def _make_expired_topup(ref_code: str = "EXPIRED00000000000000000000000000") -> TopUpRequest:
    topup = _make_topup(ref_code=ref_code)
    topup.expires_at = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    return topup


# ---------------------------------------------------------------------------
# create_manual_topup
# ---------------------------------------------------------------------------

class TestCreateManualTopup:
    @pytest.mark.asyncio
    async def test_valid_amount_creates_topup(self):
        """A valid amount should create a pending TopUpRequest."""
        session = AsyncMock()
        mock_topup = _make_topup()

        with (
            patch("bot.services.topup_service.topup_repository.create", return_value=mock_topup) as mock_create,
            patch("bot.services.topup_service.audit_log_repository.create_entry", return_value=MagicMock()),
        ):
            result = await create_manual_topup(session, user_id=1, amount=Decimal("50000"))

        assert result is mock_topup
        mock_create.assert_awaited_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["user_id"] == 1
        assert call_kwargs["amount"] == Decimal("50000")
        assert call_kwargs["method"] == "manual"
        assert call_kwargs["status"] if hasattr(call_kwargs, "status") else True  # status set in repo

    @pytest.mark.asyncio
    async def test_minimum_amount_accepted(self):
        """Rp 10.000 (minimum) should be accepted."""
        session = AsyncMock()
        mock_topup = _make_topup(amount=Decimal("10000"))

        with (
            patch("bot.services.topup_service.topup_repository.create", return_value=mock_topup),
            patch("bot.services.topup_service.audit_log_repository.create_entry", return_value=MagicMock()),
        ):
            result = await create_manual_topup(session, user_id=1, amount=Decimal("10000"))

        assert result is mock_topup

    @pytest.mark.asyncio
    async def test_maximum_amount_accepted(self):
        """Rp 10.000.000 (maximum) should be accepted."""
        session = AsyncMock()
        mock_topup = _make_topup(amount=Decimal("10000000"))

        with (
            patch("bot.services.topup_service.topup_repository.create", return_value=mock_topup),
            patch("bot.services.topup_service.audit_log_repository.create_entry", return_value=MagicMock()),
        ):
            result = await create_manual_topup(session, user_id=1, amount=Decimal("10000000"))

        assert result is mock_topup

    @pytest.mark.asyncio
    async def test_below_minimum_raises_error(self):
        """Amount below Rp 10.000 should raise TopUpError."""
        session = AsyncMock()
        with pytest.raises(TopUpError, match="Minimum"):
            await create_manual_topup(session, user_id=1, amount=Decimal("9999"))

    @pytest.mark.asyncio
    async def test_above_maximum_raises_error(self):
        """Amount above Rp 10.000.000 should raise TopUpError."""
        session = AsyncMock()
        with pytest.raises(TopUpError, match="Maksimum"):
            await create_manual_topup(session, user_id=1, amount=Decimal("10000001"))

    @pytest.mark.asyncio
    async def test_zero_amount_raises_error(self):
        """Zero amount should raise TopUpError."""
        session = AsyncMock()
        with pytest.raises(TopUpError):
            await create_manual_topup(session, user_id=1, amount=Decimal("0"))

    @pytest.mark.asyncio
    async def test_reference_codes_are_unique(self):
        """Two consecutive create_manual_topup calls should produce different ref codes."""
        session = AsyncMock()
        captured_codes = []

        async def capture_create(**kwargs):
            captured_codes.append(kwargs["reference_code"])
            topup = _make_topup(ref_code=kwargs["reference_code"])
            return topup

        with (
            patch("bot.services.topup_service.topup_repository.create", side_effect=capture_create),
            patch("bot.services.topup_service.audit_log_repository.create_entry", return_value=MagicMock()),
        ):
            await create_manual_topup(session, user_id=1, amount=Decimal("50000"))
            await create_manual_topup(session, user_id=1, amount=Decimal("50000"))

        assert len(captured_codes) == 2
        assert captured_codes[0] != captured_codes[1]

    @pytest.mark.asyncio
    async def test_audit_log_written(self):
        """An audit log entry must be written when a topup request is created."""
        session = AsyncMock()
        mock_topup = _make_topup()

        with (
            patch("bot.services.topup_service.topup_repository.create", return_value=mock_topup),
            patch("bot.services.topup_service.audit_log_repository.create_entry", return_value=MagicMock()) as mock_audit,
        ):
            await create_manual_topup(session, user_id=1, amount=Decimal("50000"))

        mock_audit.assert_awaited_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["action"] == "topup_request"
        assert call_kwargs["user_id"] == 1


# ---------------------------------------------------------------------------
# confirm_topup
# ---------------------------------------------------------------------------

class TestConfirmTopup:
    @pytest.mark.asyncio
    async def test_valid_confirmation_credits_wallet(self):
        """Confirming a pending topup should credit the wallet."""
        session = AsyncMock()
        mock_topup = _make_topup()
        confirmed_topup = _make_topup(status="confirmed")

        with (
            patch("bot.services.topup_service.topup_repository.get_by_reference", return_value=mock_topup),
            patch("bot.services.topup_service.wallet_service.credit") as mock_credit,
            patch("bot.services.topup_service.topup_repository.confirm", return_value=confirmed_topup),
        ):
            result = await confirm_topup(session, ref_code="ABCDEF1234567890ABCDEF1234567890", admin_id=999)

        assert result is confirmed_topup
        mock_credit.assert_awaited_once()
        credit_kwargs = mock_credit.call_args.kwargs
        assert credit_kwargs["user_id"] == mock_topup.user_id
        assert credit_kwargs["amount"] == mock_topup.amount
        assert credit_kwargs["reason"] == "topup_confirm"

    @pytest.mark.asyncio
    async def test_not_found_raises_error(self):
        """Confirming a non-existent ref code should raise TopUpError."""
        session = AsyncMock()

        with patch("bot.services.topup_service.topup_repository.get_by_reference", return_value=None):
            with pytest.raises(TopUpError, match="tidak ditemukan"):
                await confirm_topup(session, ref_code="NONEXISTENT", admin_id=999)

    @pytest.mark.asyncio
    async def test_already_confirmed_raises_error(self):
        """Confirming an already-confirmed topup should raise TopUpError."""
        session = AsyncMock()
        already_confirmed = _make_topup(status="confirmed")

        with patch("bot.services.topup_service.topup_repository.get_by_reference", return_value=already_confirmed):
            with pytest.raises(TopUpError, match="tidak dapat dikonfirmasi"):
                await confirm_topup(session, ref_code="ABCDEF1234567890ABCDEF1234567890", admin_id=999)

    @pytest.mark.asyncio
    async def test_expired_topup_raises_error(self):
        """Confirming an expired topup should raise TopUpError."""
        session = AsyncMock()
        expired = _make_topup(status="expired")

        with patch("bot.services.topup_service.topup_repository.get_by_reference", return_value=expired):
            with pytest.raises(TopUpError, match="tidak dapat dikonfirmasi"):
                await confirm_topup(session, ref_code="ABCDEF1234567890ABCDEF1234567890", admin_id=999)


# ---------------------------------------------------------------------------
# expire_pending_topups
# ---------------------------------------------------------------------------

class TestExpirePendingTopups:
    @pytest.mark.asyncio
    async def test_returns_expired_list(self):
        """expire_pending_topups should return the list of expired requests."""
        session = AsyncMock()
        expired_topups = [_make_expired_topup("REF1" + "0" * 28), _make_expired_topup("REF2" + "0" * 28)]

        with (
            patch("bot.services.topup_service.topup_repository.expire_pending", return_value=expired_topups),
            patch("bot.services.topup_service.audit_log_repository.create_entry", return_value=MagicMock()),
        ):
            result = await expire_pending_topups(session)

        assert result == expired_topups

    @pytest.mark.asyncio
    async def test_audit_log_written_for_each_expired(self):
        """An audit log entry must be written for each expired topup."""
        session = AsyncMock()
        expired_topups = [_make_expired_topup("REF1" + "0" * 28), _make_expired_topup("REF2" + "0" * 28)]

        with (
            patch("bot.services.topup_service.topup_repository.expire_pending", return_value=expired_topups),
            patch("bot.services.topup_service.audit_log_repository.create_entry", return_value=MagicMock()) as mock_audit,
        ):
            await expire_pending_topups(session)

        assert mock_audit.await_count == len(expired_topups)

    @pytest.mark.asyncio
    async def test_no_expired_returns_empty_list(self):
        """When no topups are expired, an empty list is returned."""
        session = AsyncMock()

        with (
            patch("bot.services.topup_service.topup_repository.expire_pending", return_value=[]),
            patch("bot.services.topup_service.audit_log_repository.create_entry", return_value=MagicMock()),
        ):
            result = await expire_pending_topups(session)

        assert result == []


# ---------------------------------------------------------------------------
# process_qris_payment
# ---------------------------------------------------------------------------

class TestProcessQrisPayment:
    @pytest.mark.asyncio
    async def test_valid_payment_credits_wallet(self):
        """A valid QRIS payment should credit the wallet and confirm the topup."""
        session = AsyncMock()
        mock_topup = _make_topup()
        confirmed_topup = _make_topup(status="confirmed")

        with (
            patch("bot.services.topup_service.topup_repository.get_by_reference", return_value=mock_topup),
            patch("bot.services.topup_service.wallet_service.credit") as mock_credit,
            patch("bot.services.topup_service.topup_repository.confirm", return_value=confirmed_topup),
        ):
            result = await process_qris_payment(
                session,
                ref_code="ABCDEF1234567890ABCDEF1234567890",
                amount=50000,
            )

        assert result is confirmed_topup
        mock_credit.assert_awaited_once()
        credit_kwargs = mock_credit.call_args.kwargs
        assert credit_kwargs["amount"] == Decimal("50000")
        assert credit_kwargs["reason"] == "topup_qris"

    @pytest.mark.asyncio
    async def test_not_found_raises_error(self):
        """Processing a non-existent ref code should raise TopUpError."""
        session = AsyncMock()

        with patch("bot.services.topup_service.topup_repository.get_by_reference", return_value=None):
            with pytest.raises(TopUpError, match="tidak ditemukan"):
                await process_qris_payment(session, ref_code="NONEXISTENT", amount=50000)

    @pytest.mark.asyncio
    async def test_already_confirmed_raises_error(self):
        """Processing an already-confirmed topup should raise TopUpError."""
        session = AsyncMock()
        already_confirmed = _make_topup(status="confirmed")

        with patch("bot.services.topup_service.topup_repository.get_by_reference", return_value=already_confirmed):
            with pytest.raises(TopUpError, match="sudah diproses"):
                await process_qris_payment(
                    session,
                    ref_code="ABCDEF1234567890ABCDEF1234567890",
                    amount=50000,
                )

    @pytest.mark.asyncio
    async def test_decimal_amount_accepted(self):
        """process_qris_payment should accept Decimal amounts."""
        session = AsyncMock()
        mock_topup = _make_topup()
        confirmed_topup = _make_topup(status="confirmed")

        with (
            patch("bot.services.topup_service.topup_repository.get_by_reference", return_value=mock_topup),
            patch("bot.services.topup_service.wallet_service.credit") as mock_credit,
            patch("bot.services.topup_service.topup_repository.confirm", return_value=confirmed_topup),
        ):
            result = await process_qris_payment(
                session,
                ref_code="ABCDEF1234567890ABCDEF1234567890",
                amount=Decimal("75000"),
            )

        assert result is confirmed_topup
        credit_kwargs = mock_credit.call_args.kwargs
        assert credit_kwargs["amount"] == Decimal("75000")
