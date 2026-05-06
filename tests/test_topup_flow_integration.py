"""
Integration tests — end-to-end topup manual flow.

Tests the complete flow:
  1. Create topup request (create_manual_topup) — unique ref code generated
  2. Admin confirms topup (confirm_topup) — wallet is credited
  3. Balance increases (wallet_service.credit called with correct amount)
  4. Audit log recorded (audit_log_repository.create_entry called)

All repository calls are mocked so no real database connection is needed.

Requirements: 3.1, 3.4, 3.5, 10.3, 10.4
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from bot.services.topup_service import (
    TopUpError,
    confirm_topup,
    create_manual_topup,
)
from bot.models.db_models import TopUpRequest


# ---------------------------------------------------------------------------
# Helpers / mock builders
# ---------------------------------------------------------------------------

def _make_topup(
    topup_id: int = 1,
    user_id: int = 1,
    ref_code: str = "TESTREF0000000000000000000000001",
    amount: Decimal = Decimal("50000.00"),
    status: str = "pending",
    method: str = "manual",
) -> MagicMock:
    topup = MagicMock(spec=TopUpRequest)
    topup.id = topup_id
    topup.user_id = user_id
    topup.reference_code = ref_code
    topup.amount = amount
    topup.status = status
    topup.method = method
    topup.expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=24)
    topup.confirmed_at = None
    topup.confirmed_by = None
    return topup


def _make_session() -> AsyncMock:
    session = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=scalar_result)
    return session


# ---------------------------------------------------------------------------
# Integration test: full topup manual flow
# ---------------------------------------------------------------------------

class TestTopupFlowIntegration:
    """End-to-end integration tests for the manual topup flow.

    Requirements: 3.1, 3.4, 3.5, 10.3, 10.4
    """

    @pytest.mark.asyncio
    async def test_step1_create_topup_request_returns_pending_topup(self):
        """Step 1: create_manual_topup creates a pending TopUpRequest.

        Requirements: 3.1
        """
        session = _make_session()
        mock_topup = _make_topup(status="pending")

        with (
            patch(
                "bot.services.topup_service.topup_repository.create",
                new_callable=AsyncMock,
                return_value=mock_topup,
            ) as mock_create,
            patch(
                "bot.services.topup_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
        ):
            result = await create_manual_topup(
                session, user_id=1, amount=Decimal("50000.00")
            )

        assert result is mock_topup
        assert result.status == "pending"
        mock_create.assert_awaited_once()
        create_kwargs = mock_create.call_args.kwargs
        assert create_kwargs["user_id"] == 1
        assert create_kwargs["amount"] == Decimal("50000.00")
        assert create_kwargs["method"] == "manual"

    @pytest.mark.asyncio
    async def test_step1_topup_request_has_unique_reference_code(self):
        """Step 1: Each topup request gets a unique reference code.

        Requirements: 3.1
        """
        session = _make_session()
        captured_codes: list[str] = []

        async def capture_create(**kwargs):
            code = kwargs["reference_code"]
            captured_codes.append(code)
            return _make_topup(ref_code=code)

        with (
            patch(
                "bot.services.topup_service.topup_repository.create",
                side_effect=capture_create,
            ),
            patch(
                "bot.services.topup_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
        ):
            await create_manual_topup(session, user_id=1, amount=Decimal("50000.00"))
            await create_manual_topup(session, user_id=1, amount=Decimal("75000.00"))
            await create_manual_topup(session, user_id=2, amount=Decimal("100000.00"))

        # All three reference codes must be unique (Requirement 3.1)
        assert len(captured_codes) == 3
        assert len(set(captured_codes)) == 3, (
            f"Reference codes are not unique: {captured_codes}"
        )

    @pytest.mark.asyncio
    async def test_step1_topup_request_expires_in_24_hours(self):
        """Step 1: The topup request expires 24 hours from creation.

        Requirements: 3.1
        """
        session = _make_session()
        captured_expires_at: list[datetime] = []

        async def capture_create(**kwargs):
            captured_expires_at.append(kwargs["expires_at"])
            return _make_topup(ref_code=kwargs["reference_code"])

        with (
            patch(
                "bot.services.topup_service.topup_repository.create",
                side_effect=capture_create,
            ),
            patch(
                "bot.services.topup_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
        ):
            await create_manual_topup(session, user_id=1, amount=Decimal("50000.00"))

        assert len(captured_expires_at) == 1
        expires_at = captured_expires_at[0]
        now = datetime.now(tz=timezone.utc)
        # expires_at should be approximately 24 hours from now (within 5 seconds tolerance)
        expected_expiry = now + timedelta(hours=24)
        diff = abs((expires_at - expected_expiry).total_seconds())
        assert diff < 5, f"expires_at={expires_at} is not ~24h from now"

    @pytest.mark.asyncio
    async def test_step1_audit_log_written_on_topup_request_creation(self):
        """Step 1: An audit log entry is written when a topup request is created.

        Requirements: 10.3
        """
        session = _make_session()
        mock_topup = _make_topup()

        with (
            patch(
                "bot.services.topup_service.topup_repository.create",
                new_callable=AsyncMock,
                return_value=mock_topup,
            ),
            patch(
                "bot.services.topup_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ) as mock_audit,
        ):
            await create_manual_topup(session, user_id=1, amount=Decimal("50000.00"))

        mock_audit.assert_awaited_once()
        audit_kwargs = mock_audit.call_args.kwargs
        assert audit_kwargs["action"] == "topup_request"
        assert audit_kwargs["user_id"] == 1
        assert audit_kwargs["amount"] == Decimal("50000.00")

    @pytest.mark.asyncio
    async def test_step2_admin_confirms_topup_credits_wallet(self):
        """Step 2: Admin confirmation credits the user's wallet with the topup amount.

        Requirements: 3.4
        """
        session = _make_session()
        pending_topup = _make_topup(
            user_id=1,
            ref_code="CONFIRMREF000000000000000000001",
            amount=Decimal("100000.00"),
            status="pending",
        )
        confirmed_topup = _make_topup(
            user_id=1,
            ref_code="CONFIRMREF000000000000000000001",
            amount=Decimal("100000.00"),
            status="confirmed",
        )

        with (
            patch(
                "bot.services.topup_service.topup_repository.get_by_reference",
                new_callable=AsyncMock,
                return_value=pending_topup,
            ),
            patch(
                "bot.services.topup_service.wallet_service.credit",
                new_callable=AsyncMock,
            ) as mock_credit,
            patch(
                "bot.services.topup_service.topup_repository.confirm",
                new_callable=AsyncMock,
                return_value=confirmed_topup,
            ),
        ):
            result = await confirm_topup(
                session,
                ref_code="CONFIRMREF000000000000000000001",
                admin_id=999,
            )

        # Wallet was credited (Requirement 3.4)
        mock_credit.assert_awaited_once()
        credit_kwargs = mock_credit.call_args.kwargs
        assert credit_kwargs["user_id"] == 1
        assert credit_kwargs["amount"] == Decimal("100000.00")
        assert credit_kwargs["reason"] == "topup_confirm"
        assert credit_kwargs["ref_id"] == "CONFIRMREF000000000000000000001"

        # TopUp status is confirmed
        assert result.status == "confirmed"

    @pytest.mark.asyncio
    async def test_step2_balance_increases_after_confirmation(self):
        """Step 2: The wallet balance increases by the topup amount after confirmation.

        Verifies that wallet_service.credit is called with the exact topup amount,
        which will increase the user's balance.

        Requirements: 3.4, 10.4
        """
        session = _make_session()
        topup_amount = Decimal("75000.00")
        pending_topup = _make_topup(
            user_id=2,
            ref_code="BALANCEREF00000000000000000001",
            amount=topup_amount,
            status="pending",
        )
        confirmed_topup = _make_topup(
            user_id=2,
            ref_code="BALANCEREF00000000000000000001",
            amount=topup_amount,
            status="confirmed",
        )

        credited_amount: list[Decimal] = []

        async def capture_credit(**kwargs):
            credited_amount.append(kwargs["amount"])

        with (
            patch(
                "bot.services.topup_service.topup_repository.get_by_reference",
                new_callable=AsyncMock,
                return_value=pending_topup,
            ),
            patch(
                "bot.services.topup_service.wallet_service.credit",
                side_effect=capture_credit,
            ),
            patch(
                "bot.services.topup_service.topup_repository.confirm",
                new_callable=AsyncMock,
                return_value=confirmed_topup,
            ),
        ):
            await confirm_topup(
                session,
                ref_code="BALANCEREF00000000000000000001",
                admin_id=999,
            )

        # The credited amount equals the topup amount (Requirement 3.4)
        assert len(credited_amount) == 1
        assert credited_amount[0] == topup_amount

    @pytest.mark.asyncio
    async def test_step3_audit_log_recorded_on_confirmation(self):
        """Step 3: An audit log entry is recorded when the topup is confirmed.

        The wallet_service.credit call internally writes an audit log entry.
        This test verifies that credit is called (which triggers the audit log).

        Requirements: 10.3, 10.4
        """
        session = _make_session()
        pending_topup = _make_topup(
            user_id=1,
            ref_code="AUDITREF0000000000000000000001",
            amount=Decimal("50000.00"),
            status="pending",
        )
        confirmed_topup = _make_topup(
            user_id=1,
            ref_code="AUDITREF0000000000000000000001",
            amount=Decimal("50000.00"),
            status="confirmed",
        )

        with (
            patch(
                "bot.services.topup_service.topup_repository.get_by_reference",
                new_callable=AsyncMock,
                return_value=pending_topup,
            ),
            patch(
                "bot.services.topup_service.wallet_service.credit",
                new_callable=AsyncMock,
            ) as mock_credit,
            patch(
                "bot.services.topup_service.topup_repository.confirm",
                new_callable=AsyncMock,
                return_value=confirmed_topup,
            ),
        ):
            await confirm_topup(
                session,
                ref_code="AUDITREF0000000000000000000001",
                admin_id=999,
            )

        # wallet_service.credit is called — it internally writes the audit log
        # (Requirement 10.3, 10.4)
        mock_credit.assert_awaited_once()
        credit_kwargs = mock_credit.call_args.kwargs
        # The ref_id passed to credit becomes the audit log reference_id
        assert credit_kwargs["ref_id"] == "AUDITREF0000000000000000000001"
        assert credit_kwargs["reason"] == "topup_confirm"

    @pytest.mark.asyncio
    async def test_step3_confirm_not_found_raises_error(self):
        """Step 3: Confirming a non-existent reference code raises TopUpError.

        Requirements: 3.5
        """
        session = _make_session()

        with patch(
            "bot.services.topup_service.topup_repository.get_by_reference",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(TopUpError, match="tidak ditemukan"):
                await confirm_topup(
                    session,
                    ref_code="NONEXISTENT000000000000000000",
                    admin_id=999,
                )

    @pytest.mark.asyncio
    async def test_step3_confirm_already_confirmed_raises_error(self):
        """Step 3: Confirming an already-confirmed topup raises TopUpError.

        Requirements: 3.5
        """
        session = _make_session()
        already_confirmed = _make_topup(status="confirmed")

        with patch(
            "bot.services.topup_service.topup_repository.get_by_reference",
            new_callable=AsyncMock,
            return_value=already_confirmed,
        ):
            with pytest.raises(TopUpError, match="tidak dapat dikonfirmasi"):
                await confirm_topup(
                    session,
                    ref_code="TESTREF0000000000000000000001",
                    admin_id=999,
                )

    @pytest.mark.asyncio
    async def test_step3_confirm_expired_topup_raises_error(self):
        """Step 3: Confirming an expired topup raises TopUpError.

        Requirements: 3.5
        """
        session = _make_session()
        expired_topup = _make_topup(status="expired")

        with patch(
            "bot.services.topup_service.topup_repository.get_by_reference",
            new_callable=AsyncMock,
            return_value=expired_topup,
        ):
            with pytest.raises(TopUpError, match="tidak dapat dikonfirmasi"):
                await confirm_topup(
                    session,
                    ref_code="TESTREF0000000000000000000001",
                    admin_id=999,
                )

    @pytest.mark.asyncio
    async def test_full_topup_flow_end_to_end(self):
        """Full end-to-end flow: create topup → admin confirms → balance credited → audit logged.

        Requirements: 3.1, 3.4, 3.5, 10.3, 10.4
        """
        session = _make_session()
        user_id = 5
        topup_amount = Decimal("200000.00")
        admin_id = 999

        # --- Step 1: Create topup request ---
        captured_ref_code: list[str] = []

        async def capture_create(**kwargs):
            captured_ref_code.append(kwargs["reference_code"])
            return _make_topup(
                user_id=user_id,
                ref_code=kwargs["reference_code"],
                amount=topup_amount,
                status="pending",
            )

        with (
            patch(
                "bot.services.topup_service.topup_repository.create",
                side_effect=capture_create,
            ),
            patch(
                "bot.services.topup_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ) as mock_audit_create,
        ):
            topup_request = await create_manual_topup(
                session, user_id=user_id, amount=topup_amount
            )

        # Topup request created with pending status (Requirement 3.1)
        assert topup_request.status == "pending"
        assert topup_request.amount == topup_amount
        assert len(captured_ref_code) == 1
        ref_code = captured_ref_code[0]
        assert len(ref_code) > 0

        # Audit log written for request creation (Requirement 10.3)
        mock_audit_create.assert_awaited_once()
        audit_kwargs = mock_audit_create.call_args.kwargs
        assert audit_kwargs["action"] == "topup_request"

        # --- Step 2: Admin confirms topup ---
        pending_topup = _make_topup(
            user_id=user_id,
            ref_code=ref_code,
            amount=topup_amount,
            status="pending",
        )
        confirmed_topup = _make_topup(
            user_id=user_id,
            ref_code=ref_code,
            amount=topup_amount,
            status="confirmed",
        )

        credited_calls: list[dict] = []

        async def capture_credit(**kwargs):
            credited_calls.append(kwargs)

        with (
            patch(
                "bot.services.topup_service.topup_repository.get_by_reference",
                new_callable=AsyncMock,
                return_value=pending_topup,
            ),
            patch(
                "bot.services.topup_service.wallet_service.credit",
                side_effect=capture_credit,
            ),
            patch(
                "bot.services.topup_service.topup_repository.confirm",
                new_callable=AsyncMock,
                return_value=confirmed_topup,
            ),
        ):
            confirmed = await confirm_topup(
                session, ref_code=ref_code, admin_id=admin_id
            )

        # Topup confirmed (Requirement 3.4)
        assert confirmed.status == "confirmed"

        # Wallet credited with correct amount (Requirement 3.4)
        assert len(credited_calls) == 1
        credit_call = credited_calls[0]
        assert credit_call["user_id"] == user_id
        assert credit_call["amount"] == topup_amount
        assert credit_call["reason"] == "topup_confirm"
        assert credit_call["ref_id"] == ref_code

    @pytest.mark.asyncio
    async def test_topup_amount_validation_minimum(self):
        """Topup amount below minimum (Rp 10.000) is rejected.

        Requirements: 3.1 (via validate_topup_amount)
        """
        session = _make_session()

        with pytest.raises(TopUpError):
            await create_manual_topup(session, user_id=1, amount=Decimal("9999.00"))

    @pytest.mark.asyncio
    async def test_topup_amount_validation_maximum(self):
        """Topup amount above maximum (Rp 10.000.000) is rejected.

        Requirements: 3.1 (via validate_topup_amount)
        """
        session = _make_session()

        with pytest.raises(TopUpError):
            await create_manual_topup(
                session, user_id=1, amount=Decimal("10000001.00")
            )

    @pytest.mark.asyncio
    async def test_topup_amount_validation_minimum_accepted(self):
        """Topup amount at minimum (Rp 10.000) is accepted.

        Requirements: 3.1
        """
        session = _make_session()
        mock_topup = _make_topup(amount=Decimal("10000.00"))

        with (
            patch(
                "bot.services.topup_service.topup_repository.create",
                new_callable=AsyncMock,
                return_value=mock_topup,
            ),
            patch(
                "bot.services.topup_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
        ):
            result = await create_manual_topup(
                session, user_id=1, amount=Decimal("10000.00")
            )

        assert result is mock_topup

    @pytest.mark.asyncio
    async def test_topup_amount_validation_maximum_accepted(self):
        """Topup amount at maximum (Rp 10.000.000) is accepted.

        Requirements: 3.1
        """
        session = _make_session()
        mock_topup = _make_topup(amount=Decimal("10000000.00"))

        with (
            patch(
                "bot.services.topup_service.topup_repository.create",
                new_callable=AsyncMock,
                return_value=mock_topup,
            ),
            patch(
                "bot.services.topup_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
        ):
            result = await create_manual_topup(
                session, user_id=1, amount=Decimal("10000000.00")
            )

        assert result is mock_topup

    @pytest.mark.asyncio
    async def test_wallet_credit_called_with_correct_ref_id(self):
        """wallet_service.credit is called with the topup reference code as ref_id.

        This ensures the audit log entry links back to the topup request.

        Requirements: 10.3, 10.4
        """
        session = _make_session()
        ref_code = "REFIDTEST000000000000000000001"
        pending_topup = _make_topup(
            user_id=1,
            ref_code=ref_code,
            amount=Decimal("50000.00"),
            status="pending",
        )
        confirmed_topup = _make_topup(
            user_id=1,
            ref_code=ref_code,
            amount=Decimal("50000.00"),
            status="confirmed",
        )

        with (
            patch(
                "bot.services.topup_service.topup_repository.get_by_reference",
                new_callable=AsyncMock,
                return_value=pending_topup,
            ),
            patch(
                "bot.services.topup_service.wallet_service.credit",
                new_callable=AsyncMock,
            ) as mock_credit,
            patch(
                "bot.services.topup_service.topup_repository.confirm",
                new_callable=AsyncMock,
                return_value=confirmed_topup,
            ),
        ):
            await confirm_topup(session, ref_code=ref_code, admin_id=999)

        credit_kwargs = mock_credit.call_args.kwargs
        # ref_id must be the reference code so audit log can trace back to topup
        assert credit_kwargs["ref_id"] == ref_code

    @pytest.mark.asyncio
    async def test_multiple_topup_requests_have_distinct_ref_codes(self):
        """Multiple topup requests for the same user all have distinct ref codes.

        Requirements: 3.1
        """
        session = _make_session()
        captured_codes: list[str] = []

        async def capture_create(**kwargs):
            code = kwargs["reference_code"]
            captured_codes.append(code)
            return _make_topup(user_id=1, ref_code=code)

        with (
            patch(
                "bot.services.topup_service.topup_repository.create",
                side_effect=capture_create,
            ),
            patch(
                "bot.services.topup_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
        ):
            for amount in [10000, 50000, 100000, 500000, 1000000]:
                await create_manual_topup(
                    session, user_id=1, amount=Decimal(str(amount))
                )

        assert len(captured_codes) == 5
        assert len(set(captured_codes)) == 5, (
            f"Duplicate reference codes found: {captured_codes}"
        )
