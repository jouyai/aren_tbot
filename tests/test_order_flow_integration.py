"""
Integration tests — end-to-end order flow.

Tests the complete flow:
  1. User registration (get_or_create_user)
  2. Check balance (get_balance)
  3. Create order (create_order) — with mock PPOB API client
  4. Check order status (get_order)

All repository calls are mocked so no real database connection is needed.
The PPOB API client is also mocked to avoid real API calls.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services import user_service, wallet_service, order_service
from bot.services.order_service import (
    InsufficientBalanceOrderError,
    ServiceNotFoundError,
    create_order,
    get_order,
)
from bot.services.user_service import get_or_create_user
from bot.services.wallet_service import get_balance


# ---------------------------------------------------------------------------
# Helpers / mock builders
# ---------------------------------------------------------------------------

def _make_user(
    user_id: int = 1,
    telegram_id: int = 100001,
    username: str = "testuser",
    balance: Decimal = Decimal("0.00"),
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        telegram_id=telegram_id,
        username=username,
        balance=balance,
        is_active=True,
    )


def _make_service(
    service_id: int = 1,
    provider_id: str = "101",
    base_price: Decimal = Decimal("10000.00"),
    margin: Decimal = Decimal("500.00"),
    is_active: bool = True,
) -> SimpleNamespace:
    sell_price = base_price + margin
    return SimpleNamespace(
        id=service_id,
        provider_id=provider_id,
        name="Instagram Followers",
        description="1000 followers",
        base_price=base_price,
        margin=margin,
        sell_price=sell_price,
        category="smm",
        is_active=is_active,
    )


def _make_order(
    order_id: int = 1,
    user_id: int = 1,
    service_id: int = 1,
    amount: Decimal = Decimal("10500.00"),
    status: str = "pending",
    provider_order_id: Optional[str] = None,
    target: str = "https://instagram.com/testuser",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=order_id,
        user_id=user_id,
        service_id=service_id,
        target=target,
        quantity=1,
        amount=amount,
        status=status,
        provider_order_id=provider_order_id,
        status_message=None,
        last_checked_at=None,
    )


def _make_session() -> AsyncMock:
    session = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=scalar_result)
    return session


def _make_ppob_client(
    provider_order_id: int = 9001,
    success: bool = True,
    error_msg: str = "Provider error",
) -> AsyncMock:
    from bot.integrations.ppob_client import PPOBOrderError

    client = AsyncMock()
    if success:
        client.create_order = AsyncMock(
            return_value={
                "status": True,
                "msg": "Order created",
                "order": provider_order_id,
            }
        )
    else:
        client.create_order = AsyncMock(side_effect=PPOBOrderError(error_msg))
    return client


# ---------------------------------------------------------------------------
# Integration test: full order flow (happy path)
# ---------------------------------------------------------------------------

class TestOrderFlowIntegration:
    """End-to-end integration tests for the order flow.

    Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
    """

    @pytest.mark.asyncio
    async def test_step1_user_registration_creates_new_user(self):
        """Step 1: A new user is created when they first interact with the bot.

        Requirements: 6.1 (user must exist before placing an order)
        """
        session = _make_session()
        new_user = _make_user(user_id=1, telegram_id=100001, username="alice")

        with (
            patch(
                "bot.services.user_service.user_repository.get_by_telegram_id",
                new_callable=AsyncMock,
                return_value=None,  # user does not exist yet
            ),
            patch(
                "bot.services.user_service.user_repository.create",
                new_callable=AsyncMock,
                return_value=new_user,
            ),
        ):
            user, created = await get_or_create_user(
                session, telegram_id=100001, username="alice"
            )

        assert created is True
        assert user.telegram_id == 100001
        assert user.username == "alice"
        assert user.balance == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_step1_existing_user_not_duplicated(self):
        """Step 1: An existing user is returned without creating a duplicate.

        Requirements: 6.1
        """
        session = _make_session()
        existing_user = _make_user(user_id=1, telegram_id=100001, username="alice")

        with patch(
            "bot.services.user_service.user_repository.get_by_telegram_id",
            new_callable=AsyncMock,
            return_value=existing_user,
        ):
            user, created = await get_or_create_user(
                session, telegram_id=100001, username="alice"
            )

        assert created is False
        assert user is existing_user

    @pytest.mark.asyncio
    async def test_step2_check_balance_returns_current_balance(self):
        """Step 2: Balance check returns the user's current wallet balance.

        Requirements: 6.2
        """
        session = _make_session()
        user = _make_user(user_id=1, balance=Decimal("50000.00"))

        # Mock the DB execute to return the balance
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = Decimal("50000.00")
        session.execute = AsyncMock(return_value=scalar_result)

        balance = await get_balance(session, user_id=user.id)

        assert balance == Decimal("50000.00")

    @pytest.mark.asyncio
    async def test_step2_check_balance_raises_for_unknown_user(self):
        """Step 2: get_balance raises ValueError for a non-existent user.

        Requirements: 6.2
        """
        session = _make_session()

        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=scalar_result)

        with pytest.raises(ValueError, match="not found"):
            await get_balance(session, user_id=9999)

    @pytest.mark.asyncio
    async def test_step3_create_order_deducts_balance_and_creates_order(self):
        """Step 3: Creating an order deducts the sell_price from the wallet
        and creates an order record with the correct amount.

        Requirements: 6.3
        """
        session = _make_session()
        service = _make_service(base_price=Decimal("10000.00"), margin=Decimal("500.00"))
        sell_price = service.sell_price  # 10500.00
        user_balance = Decimal("20000.00")

        created_order = _make_order(amount=sell_price, status="pending")
        processing_order = _make_order(
            amount=sell_price, status="processing", provider_order_id="9001"
        )
        ppob_client = _make_ppob_client(provider_order_id=9001, success=True)

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=user_balance,
            ),
            patch(
                "bot.services.order_service.debit",
                new_callable=AsyncMock,
            ) as mock_debit,
            patch(
                "bot.services.order_service.credit",
                new_callable=AsyncMock,
            ),
            patch(
                "bot.services.order_service.order_repository.create",
                new_callable=AsyncMock,
                return_value=created_order,
            ),
            patch(
                "bot.services.order_service.order_repository.update_status",
                new_callable=AsyncMock,
                return_value=processing_order,
            ),
            patch(
                "bot.services.order_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
            ),
        ):
            order = await create_order(
                session=session,
                user_id=1,
                service_id=service.id,
                target="https://instagram.com/testuser",
                ppob_client=ppob_client,
            )

        # Order amount equals sell_price (Requirement 6.3)
        assert order.amount == sell_price
        # Wallet was debited with the correct amount
        mock_debit.assert_awaited_once()
        debit_kwargs = mock_debit.call_args.kwargs
        assert debit_kwargs["amount"] == sell_price

    @pytest.mark.asyncio
    async def test_step3_create_order_sets_processing_status_after_ppob_success(self):
        """Step 3: After PPOB API accepts the order, status becomes 'processing'
        and provider_order_id is stored.

        Requirements: 6.4
        """
        session = _make_session()
        service = _make_service()
        sell_price = service.sell_price

        created_order = _make_order(amount=sell_price, status="pending")
        processing_order = _make_order(
            amount=sell_price, status="processing", provider_order_id="9001"
        )
        ppob_client = _make_ppob_client(provider_order_id=9001, success=True)

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=Decimal("50000.00"),
            ),
            patch("bot.services.order_service.debit", new_callable=AsyncMock),
            patch("bot.services.order_service.credit", new_callable=AsyncMock),
            patch(
                "bot.services.order_service.order_repository.create",
                new_callable=AsyncMock,
                return_value=created_order,
            ),
            patch(
                "bot.services.order_service.order_repository.update_status",
                new_callable=AsyncMock,
                return_value=processing_order,
            ) as mock_update_status,
            patch(
                "bot.services.order_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
            ),
        ):
            order = await create_order(
                session=session,
                user_id=1,
                service_id=service.id,
                target="https://instagram.com/testuser",
                ppob_client=ppob_client,
            )

        # Status updated to 'processing' with provider_order_id (Requirement 6.4)
        assert order.status == "processing"
        assert order.provider_order_id == "9001"

        # update_status was called with status='processing' and provider_order_id
        update_call_kwargs = mock_update_status.call_args.kwargs
        assert update_call_kwargs["status"] == "processing"
        assert update_call_kwargs["provider_order_id"] == "9001"

    @pytest.mark.asyncio
    async def test_step3_ppob_failure_rolls_back_balance_and_sets_failed(self):
        """Step 3: When PPOB API rejects the order, the deducted balance is
        returned and the order status becomes 'failed'.

        Requirements: 6.5
        """
        session = _make_session()
        service = _make_service()
        sell_price = service.sell_price

        created_order = _make_order(amount=sell_price, status="pending")
        failed_order = _make_order(amount=sell_price, status="failed")
        ppob_client = _make_ppob_client(success=False, error_msg="Service unavailable")

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=Decimal("50000.00"),
            ),
            patch(
                "bot.services.order_service.debit",
                new_callable=AsyncMock,
            ) as mock_debit,
            patch(
                "bot.services.order_service.credit",
                new_callable=AsyncMock,
            ) as mock_credit,
            patch(
                "bot.services.order_service.order_repository.create",
                new_callable=AsyncMock,
                return_value=created_order,
            ),
            patch(
                "bot.services.order_service.order_repository.update_status",
                new_callable=AsyncMock,
                return_value=failed_order,
            ),
            patch(
                "bot.services.order_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
            ),
        ):
            order = await create_order(
                session=session,
                user_id=1,
                service_id=service.id,
                target="https://instagram.com/testuser",
                ppob_client=ppob_client,
            )

        # Balance was debited then credited back (Requirement 6.5)
        mock_debit.assert_awaited_once()
        mock_credit.assert_awaited_once()
        credit_kwargs = mock_credit.call_args.kwargs
        assert credit_kwargs["amount"] == sell_price
        assert credit_kwargs["reason"] == "order_failed"

        # Order status is 'failed' (Requirement 6.5)
        assert order.status == "failed"

    @pytest.mark.asyncio
    async def test_step3_insufficient_balance_rejects_order(self):
        """Step 3: Order is rejected when the user's balance is insufficient.

        Requirements: 6.2
        """
        session = _make_session()
        service = _make_service(base_price=Decimal("10000.00"), margin=Decimal("500.00"))
        ppob_client = _make_ppob_client()

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=Decimal("100.00"),  # far below sell_price of 10500
            ),
        ):
            with pytest.raises(InsufficientBalanceOrderError) as exc_info:
                await create_order(
                    session=session,
                    user_id=1,
                    service_id=service.id,
                    target="https://instagram.com/testuser",
                    ppob_client=ppob_client,
                )

        err = exc_info.value
        assert err.current_balance == Decimal("100.00")
        assert err.required_amount == service.sell_price

    @pytest.mark.asyncio
    async def test_step4_check_order_status_returns_order_for_owner(self):
        """Step 4: Checking order status returns the order for the correct user.

        Requirements: 6.6
        """
        session = _make_session()
        expected_order = _make_order(
            order_id=42,
            user_id=1,
            status="processing",
            provider_order_id="9001",
        )

        with patch(
            "bot.services.order_service.order_repository.get_by_id_and_user",
            new_callable=AsyncMock,
            return_value=expected_order,
        ):
            order = await get_order(session, order_id=42, user_id=1)

        assert order is expected_order
        assert order.status == "processing"
        assert order.provider_order_id == "9001"

    @pytest.mark.asyncio
    async def test_step4_check_order_status_returns_none_for_wrong_user(self):
        """Step 4: Checking another user's order returns None (access control).

        Requirements: 6.6
        """
        session = _make_session()

        with patch(
            "bot.services.order_service.order_repository.get_by_id_and_user",
            new_callable=AsyncMock,
            return_value=None,
        ):
            order = await get_order(session, order_id=42, user_id=999)

        assert order is None

    @pytest.mark.asyncio
    async def test_full_order_flow_end_to_end(self):
        """Full end-to-end flow: register → check balance → create order → check status.

        Simulates the complete user journey from registration to order status check.

        Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
        """
        session = _make_session()
        telegram_id = 200001
        initial_balance = Decimal("50000.00")
        service = _make_service(base_price=Decimal("10000.00"), margin=Decimal("500.00"))
        sell_price = service.sell_price  # 10500.00

        # --- Step 1: Register user ---
        new_user = _make_user(
            user_id=1, telegram_id=telegram_id, username="bob", balance=initial_balance
        )
        with (
            patch(
                "bot.services.user_service.user_repository.get_by_telegram_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "bot.services.user_service.user_repository.create",
                new_callable=AsyncMock,
                return_value=new_user,
            ),
        ):
            user, created = await get_or_create_user(
                session, telegram_id=telegram_id, username="bob"
            )

        assert created is True
        assert user.id == 1

        # --- Step 2: Check balance ---
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = initial_balance
        session.execute = AsyncMock(return_value=scalar_result)

        balance = await get_balance(session, user_id=user.id)
        assert balance == initial_balance
        assert balance >= sell_price  # sufficient for the order

        # --- Step 3: Create order ---
        created_order = _make_order(
            order_id=10, user_id=user.id, service_id=service.id,
            amount=sell_price, status="pending",
        )
        processing_order = _make_order(
            order_id=10, user_id=user.id, service_id=service.id,
            amount=sell_price, status="processing", provider_order_id="9001",
        )
        ppob_client = _make_ppob_client(provider_order_id=9001, success=True)

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=initial_balance,
            ),
            patch("bot.services.order_service.debit", new_callable=AsyncMock) as mock_debit,
            patch("bot.services.order_service.credit", new_callable=AsyncMock),
            patch(
                "bot.services.order_service.order_repository.create",
                new_callable=AsyncMock,
                return_value=created_order,
            ),
            patch(
                "bot.services.order_service.order_repository.update_status",
                new_callable=AsyncMock,
                return_value=processing_order,
            ),
            patch(
                "bot.services.order_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
            ),
        ):
            order = await create_order(
                session=session,
                user_id=user.id,
                service_id=service.id,
                target="https://instagram.com/bob",
                ppob_client=ppob_client,
            )

        # Order created with correct amount (Requirement 6.3)
        assert order.amount == sell_price
        # Wallet debited (Requirement 6.3)
        mock_debit.assert_awaited_once()
        # Order is processing (Requirement 6.4)
        assert order.status == "processing"
        assert order.provider_order_id == "9001"

        # --- Step 4: Check order status ---
        with patch(
            "bot.services.order_service.order_repository.get_by_id_and_user",
            new_callable=AsyncMock,
            return_value=processing_order,
        ):
            fetched_order = await get_order(session, order_id=order.id, user_id=user.id)

        assert fetched_order is not None
        assert fetched_order.status == "processing"
        assert fetched_order.amount == sell_price

    @pytest.mark.asyncio
    async def test_order_amount_equals_service_sell_price(self):
        """The order amount must always equal the service's sell_price.

        Requirements: 6.3
        """
        session = _make_session()
        base_price = Decimal("8000.00")
        margin = Decimal("1200.00")
        service = _make_service(base_price=base_price, margin=margin)
        sell_price = base_price + margin  # 9200.00

        created_order = _make_order(amount=sell_price, status="pending")
        processing_order = _make_order(
            amount=sell_price, status="processing", provider_order_id="5555"
        )
        ppob_client = _make_ppob_client(provider_order_id=5555, success=True)

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=Decimal("100000.00"),
            ),
            patch("bot.services.order_service.debit", new_callable=AsyncMock),
            patch("bot.services.order_service.credit", new_callable=AsyncMock),
            patch(
                "bot.services.order_service.order_repository.create",
                new_callable=AsyncMock,
                return_value=created_order,
            ),
            patch(
                "bot.services.order_service.order_repository.update_status",
                new_callable=AsyncMock,
                return_value=processing_order,
            ),
            patch(
                "bot.services.order_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
            ),
        ):
            order = await create_order(
                session=session,
                user_id=1,
                service_id=service.id,
                target="https://instagram.com/testuser",
                ppob_client=ppob_client,
            )

        assert order.amount == sell_price
        assert order.amount == service.base_price + service.margin

    @pytest.mark.asyncio
    async def test_inactive_service_raises_service_not_found(self):
        """Ordering an inactive service raises ServiceNotFoundError.

        Requirements: 6.1
        """
        session = _make_session()
        inactive_service = _make_service(is_active=False)
        ppob_client = _make_ppob_client()

        with patch(
            "bot.services.order_service.service_repository.get_by_id",
            new_callable=AsyncMock,
            return_value=inactive_service,
        ):
            with pytest.raises(ServiceNotFoundError):
                await create_order(
                    session=session,
                    user_id=1,
                    service_id=inactive_service.id,
                    target="https://instagram.com/testuser",
                    ppob_client=ppob_client,
                )

    @pytest.mark.asyncio
    async def test_audit_log_written_on_successful_order(self):
        """An audit log entry is written when an order is successfully created.

        Requirements: 6.3, 6.4
        """
        session = _make_session()
        service = _make_service()
        sell_price = service.sell_price

        created_order = _make_order(amount=sell_price, status="pending")
        processing_order = _make_order(
            amount=sell_price, status="processing", provider_order_id="7777"
        )
        ppob_client = _make_ppob_client(provider_order_id=7777, success=True)

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=Decimal("50000.00"),
            ),
            patch("bot.services.order_service.debit", new_callable=AsyncMock),
            patch("bot.services.order_service.credit", new_callable=AsyncMock),
            patch(
                "bot.services.order_service.order_repository.create",
                new_callable=AsyncMock,
                return_value=created_order,
            ),
            patch(
                "bot.services.order_service.order_repository.update_status",
                new_callable=AsyncMock,
                return_value=processing_order,
            ),
            patch(
                "bot.services.order_service.audit_log_repository.create_entry",
                new_callable=AsyncMock,
            ) as mock_audit,
        ):
            await create_order(
                session=session,
                user_id=1,
                service_id=service.id,
                target="https://instagram.com/testuser",
                ppob_client=ppob_client,
            )

        # Audit log must be written (Requirement 6.3)
        mock_audit.assert_awaited()
        # Check the audit log action
        audit_calls = [call.kwargs for call in mock_audit.call_args_list]
        actions = [c.get("action") for c in audit_calls]
        assert "order_create" in actions
