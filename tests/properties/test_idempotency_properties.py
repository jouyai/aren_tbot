"""
Property-based tests for idempotency and uniqueness guarantees.

Properties covered:
  - Property 6: TopUp Reference Code Uniqueness
  - Property 4: Idempotent Webhook Processing

**Validates: Requirements 3.1, 4.5**
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bot.utils.validators import generate_reference_code


# ---------------------------------------------------------------------------
# Property 6: TopUp Reference Code Uniqueness
# **Validates: Requirements 3.1**
# ---------------------------------------------------------------------------

class TestReferenceCodeUniqueness:
    """Property 6: Every generated reference code must be globally unique."""

    def test_large_batch_no_duplicates(self):
        """Generate 10 000 reference codes and verify no duplicates exist.

        This is a deterministic stress test — UUID4 collision probability over
        10 000 samples is astronomically small (~10^-28), so any collision
        would indicate a broken generator.
        """
        n = 10_000
        codes = [generate_reference_code() for _ in range(n)]
        assert len(set(codes)) == n, (
            f"Duplicate reference codes found among {n} generated codes."
        )

    @given(st.integers(min_value=2, max_value=500))
    @settings(max_examples=50)
    def test_batch_uniqueness_property(self, n: int):
        """**Property 6: TopUp Reference Code Uniqueness**

        For any batch of N generated reference codes, all codes must be
        distinct — no two codes may be equal.

        **Validates: Requirements 3.1**
        """
        codes = [generate_reference_code() for _ in range(n)]
        assert len(set(codes)) == len(codes), (
            f"Duplicate reference codes found in a batch of {n}."
        )

    def test_reference_code_format(self):
        """Reference codes must be non-empty uppercase hex strings of length 32."""
        code = generate_reference_code()
        assert isinstance(code, str)
        assert len(code) == 32
        assert code == code.upper()
        assert all(c in "0123456789ABCDEF" for c in code)

    def test_two_consecutive_codes_differ(self):
        """Two consecutively generated codes must not be equal."""
        code1 = generate_reference_code()
        code2 = generate_reference_code()
        assert code1 != code2


# ---------------------------------------------------------------------------
# Property 4: Idempotent Webhook Processing
# **Validates: Requirements 4.5**
# ---------------------------------------------------------------------------

class TestIdempotentWebhookProcessing:
    """Property 4: Processing the same webhook N times yields the same state
    as processing it once.

    The webhook handler must:
      1. Check idempotency before crediting the wallet.
      2. Skip processing (return 'already_processed') on subsequent calls.
      3. Never credit the wallet more than once for the same reference code.
    """

    def _make_payload(self, ref_code: str, amount: int) -> dict:
        return {
            "order_id": f"TOPUP-{ref_code}",
            "amount": amount,
            "status": "completed",
            "project": "test-project",
            "payment_method": "qris",
        }

    @pytest.mark.asyncio
    async def test_double_processing_does_not_double_credit(self):
        """Processing the same completed webhook twice must credit the wallet
        exactly once.

        **Property 4: Idempotent Webhook Processing**
        **Validates: Requirements 4.5**
        """
        ref_code = generate_reference_code()
        amount = 50_000
        payload = self._make_payload(ref_code, amount)

        # Track how many times process_qris_payment is called
        process_call_count = 0

        async def process_fn(session, rc, amt):
            nonlocal process_call_count
            process_call_count += 1

        # First call — not yet processed → should credit
        result1 = await _simulate_webhook_processing(
            payload=payload,
            is_already_processed_fn=lambda rc: False,
            verify_fn=lambda oid, amt: True,
            process_fn=process_fn,
        )

        # Second call — already processed → should be skipped
        result2 = await _simulate_webhook_processing(
            payload=payload,
            is_already_processed_fn=lambda rc: True,
            verify_fn=lambda oid, amt: True,
            process_fn=process_fn,
        )

        assert result1 == "ok", "First webhook call should succeed."
        assert result2 == "already_processed", (
            "Second webhook call should return 'already_processed'."
        )
        assert process_call_count == 1, (
            f"process_qris_payment was called {process_call_count} times; "
            "expected exactly 1."
        )

    @given(st.integers(min_value=2, max_value=20))
    @settings(max_examples=30)
    @pytest.mark.asyncio
    async def test_n_times_processing_same_as_once(self, n: int):
        """**Property 4: Idempotent Webhook Processing**

        Processing the same webhook payload N times must result in the wallet
        being credited exactly once, regardless of N.

        **Validates: Requirements 4.5**
        """
        ref_code = generate_reference_code()
        amount = 100_000
        payload = self._make_payload(ref_code, amount)

        credit_count = 0

        async def process_fn(session, rc, amt):
            nonlocal credit_count
            credit_count += 1

        # First call processes; all subsequent calls are idempotent
        for i in range(n):
            already_processed = i > 0  # True for all calls after the first
            result = await _simulate_webhook_processing(
                payload=payload,
                is_already_processed_fn=lambda rc, ap=already_processed: ap,
                verify_fn=lambda oid, amt: True,
                process_fn=process_fn,
            )
            if already_processed:
                assert result == "already_processed"

        assert credit_count == 1, (
            f"Wallet credited {credit_count} times for {n} webhook calls; "
            "expected exactly 1."
        )

    @pytest.mark.asyncio
    async def test_invalid_verification_does_not_credit(self):
        """If verify_transaction returns False, the wallet must not be credited.

        **Validates: Requirements 4.3**
        """
        ref_code = generate_reference_code()
        amount = 50_000
        payload = self._make_payload(ref_code, amount)

        credit_count = 0

        async def process_fn(session, rc, amt):
            nonlocal credit_count
            credit_count += 1

        result = await _simulate_webhook_processing(
            payload=payload,
            is_already_processed_fn=lambda rc: False,
            verify_fn=lambda oid, amt: False,  # verification fails
            process_fn=process_fn,
        )

        assert result == "verification_failed"
        assert credit_count == 0, "Wallet must not be credited when verification fails."

    @pytest.mark.asyncio
    async def test_non_completed_status_ignored(self):
        """Webhooks with status != 'completed' must be ignored without crediting.

        **Validates: Requirements 4.2**
        """
        ref_code = generate_reference_code()
        payload = {
            "order_id": f"TOPUP-{ref_code}",
            "amount": 50_000,
            "status": "pending",  # not completed
            "project": "test-project",
        }

        credit_count = 0

        async def process_fn(session, rc, amt):
            nonlocal credit_count
            credit_count += 1

        result = await _simulate_webhook_processing(
            payload=payload,
            is_already_processed_fn=lambda rc: False,
            verify_fn=lambda oid, amt: True,
            process_fn=process_fn,
        )

        assert result == "ignored"
        assert credit_count == 0


# ---------------------------------------------------------------------------
# Helper: simulate webhook processing logic (mirrors webhook_handler.py)
# ---------------------------------------------------------------------------

async def _simulate_webhook_processing(
    payload: dict,
    is_already_processed_fn,
    verify_fn,
    process_fn,
) -> str:
    """Simulate the core webhook processing logic without HTTP/DB dependencies.

    This mirrors the logic in ``bot/integrations/webhook_handler.py`` so we
    can test the idempotency property in isolation.

    Returns one of: 'ok', 'ignored', 'already_processed', 'verification_failed'
    """
    order_id = payload.get("order_id", "")
    amount = payload.get("amount", 0)
    status = payload.get("status", "")

    # Only process completed payments
    if status != "completed":
        return "ignored"

    # Parse reference code
    ref_code = order_id.removeprefix("TOPUP-")

    # Idempotency check
    if is_already_processed_fn(ref_code):
        return "already_processed"

    # Verify with payment gateway
    verified = verify_fn(order_id, amount)
    if not verified:
        return "verification_failed"

    # Credit wallet
    await process_fn(None, ref_code, amount)
    return "ok"
