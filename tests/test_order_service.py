"""
Unit and property-based tests for OrderService.

**Property 3: Order Amount Equals Service Sell Price**
The amount charged on an order must equal the service's sell_price at the
time the order is created.

**Validates: Requirements 6.3**
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from bot.services.order_service import (
    InsufficientBalanceOrderError,
    InvalidTargetError,
    ServiceNotFoundError,
    create_order,
    get_history,
    get_order,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_positive_cents = st.integers(min_value=1, max_value=100_000_000_00)
_non_negative_cents = st.integers(min_value=0, max_value=100_000_000_00)


def _cents_to_decimal(cents: int) -> Decimal:
    return Decimal(cents) / Decimal("100")


# ---------------------------------------------------------------------------
# Mock builders
# ---------------------------------------------------------------------------


def _make_service(
    service_id: int = 1,
    provider_id: str = "101",
    base_price: Decimal = Decimal("10000.00"),
    margin: Decimal = Decimal("500.00"),
    is_active: bool = True,
) -> SimpleNamespace:
    """Build a minimal Service-like object."""
    sell_price = base_price + margin
    return SimpleNamespace(
        id=service_id,
        provider_id=provider_id,
        name="Test Service",
        description="A test service",
        base_price=base_price,
        margin=margin,
        sell_price=sell_price,
        category="test",
        is_active=is_active,
    )


def _make_order(
    order_id: int = 1,
    user_id: int = 1,
    service_id: int = 1,
    amount: Decimal = Decimal("10500.00"),
    status: str = "pending",
    provider_order_id: Optional[str] = None,
) -> SimpleNamespace:
    """Build a minimal Order-like object."""
    return SimpleNamespace(
        id=order_id,
        user_id=user_id,
        service_id=service_id,
        target="https://example.com/profile",
        quantity=1,
        amount=amount,
        status=status,
        provider_order_id=provider_order_id,
        status_message=None,
        last_checked_at=None,
    )


def _make_session() -> AsyncMock:
    """Return a minimal mock AsyncSession."""
    session = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=scalar_result)
    return session


def _make_ppob_client(
    order_id: int = 9999,
    success: bool = True,
    error_msg: str = "Provider error",
) -> AsyncMock:
    """Return a mock PPOBClient."""
    from bot.integrations.ppob_client import PPOBOrderError

    client = AsyncMock()
    if success:
        client.create_order = AsyncMock(
            return_value={"status": True, "msg": "Order created", "order": order_id}
        )
    else:
        client.create_order = AsyncMock(
            side_effect=PPOBOrderError(error_msg)
        )
    return client


def _run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Property 3: Order Amount Equals Service Sell Price
# ---------------------------------------------------------------------------


@given(
    base_price_cents=_positive_cents,
    margin_cents=_non_negative_cents,
)
@settings(max_examples=100)
def test_property3_order_amount_equals_sell_price(
    base_price_cents: int,
    margin_cents: int,
) -> None:
    """Property 3: Order amount equals service sell_price for any base_price and margin.

    For any service with any base_price and margin, the order amount charged
    must equal sell_price = base_price + margin.

    **Validates: Requirements 6.3**
    """
    base_price = _cents_to_decimal(base_price_cents)
    margin = _cents_to_decimal(margin_cents)
    sell_price = base_price + margin

    service = _make_service(base_price=base_price, margin=margin)
    # The user must have enough balance to place the order
    user_balance = sell_price + Decimal("1.00")  # always sufficient

    captured: dict = {}

    async def _run() -> None:
        session = _make_session()
        ppob_client = _make_ppob_client(order_id=42, success=True)

        # The order that will be "created" in the DB
        created_order = _make_order(
            order_id=1,
            user_id=1,
            service_id=service.id,
            amount=sell_price,
            status="pending",
        )
        # After PPOB success, status becomes "processing"
        processing_order = _make_order(
            order_id=1,
            user_id=1,
            service_id=service.id,
            amount=sell_price,
            status="processing",
            provider_order_id="42",
        )

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
            ),
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
            ) as mock_audit,
        ):
            order = await create_order(
                session=session,
                user_id=1,
                service_id=service.id,
                target="https://example.com/profile",
                ppob_client=ppob_client,
            )
            captured["order"] = order
            captured["order_create_call"] = mock_audit.call_args_list

    _run_async(_run())

    order = captured["order"]
    assert order.amount == sell_price, (
        f"order.amount={order.amount} != sell_price={sell_price} "
        f"(base_price={base_price}, margin={margin})"
    )


@given(
    base_price_cents=_positive_cents,
    margin_cents=_non_negative_cents,
)
@settings(max_examples=100)
def test_property3_debit_amount_equals_sell_price(
    base_price_cents: int,
    margin_cents: int,
) -> None:
    """Property 3 (debit): The wallet debit amount equals sell_price.

    Verifies that the amount debited from the wallet is exactly sell_price,
    not base_price or any other value.

    **Validates: Requirements 6.3**
    """
    base_price = _cents_to_decimal(base_price_cents)
    margin = _cents_to_decimal(margin_cents)
    sell_price = base_price + margin

    service = _make_service(base_price=base_price, margin=margin)
    user_balance = sell_price + Decimal("1.00")

    captured: dict = {}

    async def _run() -> None:
        session = _make_session()
        ppob_client = _make_ppob_client(order_id=42, success=True)

        created_order = _make_order(
            order_id=1,
            user_id=1,
            service_id=service.id,
            amount=sell_price,
            status="pending",
        )
        processing_order = _make_order(
            order_id=1,
            user_id=1,
            service_id=service.id,
            amount=sell_price,
            status="processing",
            provider_order_id="42",
        )

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
            await create_order(
                session=session,
                user_id=1,
                service_id=service.id,
                target="https://example.com/profile",
                ppob_client=ppob_client,
            )
            captured["debit_call"] = mock_debit.call_args

    _run_async(_run())

    debit_kwargs = captured["debit_call"].kwargs
    debited_amount = debit_kwargs.get("amount")
    assert debited_amount == sell_price, (
        f"Debited amount={debited_amount} != sell_price={sell_price} "
        f"(base_price={base_price}, margin={margin})"
    )


# ---------------------------------------------------------------------------
# Unit tests for create_order
# ---------------------------------------------------------------------------


class TestCreateOrder:
    """Unit tests for the create_order function."""

    @pytest.mark.asyncio
    async def test_raises_service_not_found(self):
        """Raises ServiceNotFoundError when service does not exist."""
        session = _make_session()
        ppob_client = _make_ppob_client()

        with patch(
            "bot.services.order_service.service_repository.get_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(ServiceNotFoundError):
                await create_order(
                    session=session,
                    user_id=1,
                    service_id=999,
                    target="https://example.com",
                    ppob_client=ppob_client,
                )

    @pytest.mark.asyncio
    async def test_raises_service_not_found_when_inactive(self):
        """Raises ServiceNotFoundError when service is inactive."""
        session = _make_session()
        ppob_client = _make_ppob_client()
        inactive_service = _make_service(is_active=False)

        with patch(
            "bot.services.order_service.service_repository.get_by_id",
            new_callable=AsyncMock,
            return_value=inactive_service,
        ):
            with pytest.raises(ServiceNotFoundError):
                await create_order(
                    session=session,
                    user_id=1,
                    service_id=1,
                    target="https://example.com",
                    ppob_client=ppob_client,
                )

    @pytest.mark.asyncio
    async def test_raises_invalid_target_for_bad_url(self):
        """Raises InvalidTargetError for a malformed URL target."""
        session = _make_session()
        ppob_client = _make_ppob_client()
        service = _make_service()

        with patch(
            "bot.services.order_service.service_repository.get_by_id",
            new_callable=AsyncMock,
            return_value=service,
        ):
            with pytest.raises(InvalidTargetError):
                await create_order(
                    session=session,
                    user_id=1,
                    service_id=1,
                    target="http://invalid url with spaces",
                    ppob_client=ppob_client,
                )

    @pytest.mark.asyncio
    async def test_raises_insufficient_balance(self):
        """Raises InsufficientBalanceOrderError when balance < sell_price."""
        session = _make_session()
        ppob_client = _make_ppob_client()
        service = _make_service(base_price=Decimal("10000"), margin=Decimal("500"))

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=Decimal("100.00"),  # much less than 10500
            ),
        ):
            with pytest.raises(InsufficientBalanceOrderError) as exc_info:
                await create_order(
                    session=session,
                    user_id=1,
                    service_id=1,
                    target="https://example.com/profile",
                    ppob_client=ppob_client,
                )

            err = exc_info.value
            assert err.current_balance == Decimal("100.00")
            assert err.required_amount == Decimal("10500.00")

    @pytest.mark.asyncio
    async def test_successful_order_returns_processing_status(self):
        """A successful PPOB call results in order status='processing'."""
        session = _make_session()
        ppob_client = _make_ppob_client(order_id=777, success=True)
        service = _make_service(base_price=Decimal("5000"), margin=Decimal("200"))
        sell_price = Decimal("5200.00")

        created_order = _make_order(amount=sell_price, status="pending")
        processing_order = _make_order(
            amount=sell_price, status="processing", provider_order_id="777"
        )

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=Decimal("10000.00"),
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
                service_id=1,
                target="https://example.com/profile",
                ppob_client=ppob_client,
            )

        assert order.status == "processing"
        assert order.provider_order_id == "777"
        assert order.amount == sell_price

    @pytest.mark.asyncio
    async def test_ppob_failure_rolls_back_debit(self):
        """When PPOB returns an error, the debit is rolled back via credit."""
        from bot.integrations.ppob_client import PPOBOrderError

        session = _make_session()
        ppob_client = _make_ppob_client(success=False, error_msg="Service unavailable")
        service = _make_service(base_price=Decimal("5000"), margin=Decimal("200"))
        sell_price = Decimal("5200.00")

        created_order = _make_order(amount=sell_price, status="pending")
        failed_order = _make_order(amount=sell_price, status="failed")

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=Decimal("10000.00"),
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
                service_id=1,
                target="https://example.com/profile",
                ppob_client=ppob_client,
            )

        # Debit was called once
        mock_debit.assert_awaited_once()
        # Credit (rollback) was called once with the same amount
        mock_credit.assert_awaited_once()
        credit_kwargs = mock_credit.call_args.kwargs
        assert credit_kwargs["amount"] == sell_price
        assert credit_kwargs["reason"] == "order_failed"

        # Order status is failed
        assert order.status == "failed"

    @pytest.mark.asyncio
    async def test_order_amount_equals_sell_price(self):
        """The order amount must equal the service sell_price (unit test)."""
        session = _make_session()
        ppob_client = _make_ppob_client(order_id=1, success=True)
        service = _make_service(
            base_price=Decimal("8000.00"), margin=Decimal("1000.00")
        )
        sell_price = Decimal("9000.00")

        created_order = _make_order(amount=sell_price, status="pending")
        processing_order = _make_order(
            amount=sell_price, status="processing", provider_order_id="1"
        )

        with (
            patch(
                "bot.services.order_service.service_repository.get_by_id",
                new_callable=AsyncMock,
                return_value=service,
            ),
            patch(
                "bot.services.order_service.get_balance",
                new_callable=AsyncMock,
                return_value=Decimal("20000.00"),
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
                service_id=1,
                target="https://example.com/profile",
                ppob_client=ppob_client,
            )

        assert order.amount == sell_price
        assert order.amount == service.base_price + service.margin


# ---------------------------------------------------------------------------
# Unit tests for get_order
# ---------------------------------------------------------------------------


class TestGetOrder:
    @pytest.mark.asyncio
    async def test_returns_order_for_owner(self):
        """Returns the order when it belongs to the requesting user."""
        session = _make_session()
        expected_order = _make_order(order_id=5, user_id=1)

        with patch(
            "bot.services.order_service.order_repository.get_by_id_and_user",
            new_callable=AsyncMock,
            return_value=expected_order,
        ):
            order = await get_order(session, order_id=5, user_id=1)

        assert order is expected_order

    @pytest.mark.asyncio
    async def test_returns_none_for_non_owner(self):
        """Returns None when the order belongs to a different user."""
        session = _make_session()

        with patch(
            "bot.services.order_service.order_repository.get_by_id_and_user",
            new_callable=AsyncMock,
            return_value=None,
        ):
            order = await get_order(session, order_id=5, user_id=99)

        assert order is None

    @pytest.mark.asyncio
    async def test_returns_none_for_nonexistent_order(self):
        """Returns None when the order does not exist."""
        session = _make_session()

        with patch(
            "bot.services.order_service.order_repository.get_by_id_and_user",
            new_callable=AsyncMock,
            return_value=None,
        ):
            order = await get_order(session, order_id=9999, user_id=1)

        assert order is None


# ---------------------------------------------------------------------------
# Unit tests for get_history
# ---------------------------------------------------------------------------


class TestGetHistory:
    @pytest.mark.asyncio
    async def test_returns_list_of_orders(self):
        """Returns the user's order history."""
        session = _make_session()
        orders = [_make_order(order_id=i) for i in range(1, 6)]

        with patch(
            "bot.services.order_service.order_repository.get_user_history",
            new_callable=AsyncMock,
            return_value=orders,
        ):
            history = await get_history(session, user_id=1, limit=10)

        assert len(history) == 5

    @pytest.mark.asyncio
    async def test_default_limit_is_10(self):
        """Default limit is 10."""
        session = _make_session()

        with patch(
            "bot.services.order_service.order_repository.get_user_history",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_history:
            await get_history(session, user_id=1)

        mock_history.assert_awaited_once_with(session, 1, 10)

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_new_user(self):
        """Returns an empty list when the user has no orders."""
        session = _make_session()

        with patch(
            "bot.services.order_service.order_repository.get_user_history",
            new_callable=AsyncMock,
            return_value=[],
        ):
            history = await get_history(session, user_id=1)

        assert history == []
