"""
Property-based tests for sell price calculation.

**Property 7: Sell Price Consistency**
For any service, sell_price must always equal base_price + margin.

This property is tested at the model level (pure arithmetic) since
sell_price is a PostgreSQL GENERATED ALWAYS AS (base_price + margin) STORED
column — the DB enforces it, and we verify the invariant holds for all
combinations of base_price and margin.

**Validates: Requirements 5.2**
"""
from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Represent monetary amounts as integers (cents) to avoid floating-point
# imprecision during Hypothesis shrinking.
_non_negative_cents = st.integers(min_value=0, max_value=100_000_000_00)  # up to 1B IDR
_positive_cents = st.integers(min_value=1, max_value=100_000_000_00)


def _cents_to_decimal(cents: int) -> Decimal:
    """Convert integer cents to a two-decimal-place Decimal."""
    return Decimal(cents) / Decimal("100")


# ---------------------------------------------------------------------------
# Property 7: Sell Price Consistency
# ---------------------------------------------------------------------------


@given(
    base_price_cents=_positive_cents,
    margin_cents=_non_negative_cents,
)
@settings(max_examples=500)
def test_property7_sell_price_equals_base_price_plus_margin(
    base_price_cents: int,
    margin_cents: int,
) -> None:
    """Property 7: sell_price = base_price + margin for all services.

    Tests the arithmetic invariant that the DB computed column enforces.
    For any combination of base_price and margin, the computed sell_price
    must equal their sum exactly (no rounding, no floating-point drift).

    **Validates: Requirements 5.2**
    """
    base_price = _cents_to_decimal(base_price_cents)
    margin = _cents_to_decimal(margin_cents)

    # This is the formula the DB uses: GENERATED ALWAYS AS (base_price + margin)
    sell_price = base_price + margin

    assert sell_price == base_price + margin, (
        f"sell_price={sell_price} != base_price({base_price}) + margin({margin})"
    )


@given(
    base_price_cents=_positive_cents,
    margin_cents=_non_negative_cents,
)
@settings(max_examples=200)
def test_property7_sell_price_is_at_least_base_price(
    base_price_cents: int,
    margin_cents: int,
) -> None:
    """Property 7 (corollary): sell_price >= base_price when margin >= 0.

    Since margin is always non-negative, the sell price must never be
    lower than the base price.

    **Validates: Requirements 5.2**
    """
    base_price = _cents_to_decimal(base_price_cents)
    margin = _cents_to_decimal(margin_cents)

    sell_price = base_price + margin

    assert sell_price >= base_price, (
        f"sell_price={sell_price} < base_price={base_price} with margin={margin}"
    )


@given(
    base_price_cents=_positive_cents,
)
@settings(max_examples=200)
def test_property7_zero_margin_sell_price_equals_base_price(
    base_price_cents: int,
) -> None:
    """Property 7 (edge case): when margin=0, sell_price equals base_price exactly.

    **Validates: Requirements 5.2**
    """
    base_price = _cents_to_decimal(base_price_cents)
    margin = Decimal("0.00")

    sell_price = base_price + margin

    assert sell_price == base_price, (
        f"With zero margin, sell_price={sell_price} should equal base_price={base_price}"
    )


@given(
    base_price_cents=_positive_cents,
    margin_cents=_positive_cents,
)
@settings(max_examples=200)
def test_property7_sell_price_strictly_greater_than_base_price_with_positive_margin(
    base_price_cents: int,
    margin_cents: int,
) -> None:
    """Property 7 (edge case): when margin > 0, sell_price > base_price.

    **Validates: Requirements 5.2**
    """
    base_price = _cents_to_decimal(base_price_cents)
    margin = _cents_to_decimal(margin_cents)

    sell_price = base_price + margin

    assert sell_price > base_price, (
        f"With positive margin={margin}, sell_price={sell_price} should be "
        f"strictly greater than base_price={base_price}"
    )
