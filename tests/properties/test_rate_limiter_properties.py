"""
Property-based tests for RateLimiter.

Property 5: Rate Limiter Sliding Window
  For any user, the number of commands allowed within any 60-second window
  must not exceed 10.

Validates: Requirements 10.1
"""
import time
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bot.middleware.rate_limiter import RateLimiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simulate_calls(
    limiter: RateLimiter,
    user_id: int,
    timestamps: list[float],
) -> list[bool]:
    """
    Simulate a sequence of calls at the given timestamps and return
    a list of booleans indicating whether each call was allowed.

    Uses unittest.mock.patch as a context manager so it is safe to use
    inside Hypothesis @given tests (no function-scoped fixtures).
    """
    results = []
    fake_time = [0.0]

    with patch.object(time, "time", side_effect=lambda: fake_time[0]):
        for ts in timestamps:
            fake_time[0] = ts
            results.append(limiter.is_allowed(user_id=user_id))

    return results


# ---------------------------------------------------------------------------
# Property 5: Rate Limiter Sliding Window
# ---------------------------------------------------------------------------

@settings(max_examples=200)
@given(
    # Generate between 1 and 30 call timestamps within a 120-second span
    timestamps=st.lists(
        st.floats(min_value=0.0, max_value=120.0, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=30,
    ).map(sorted),
    user_id=st.integers(min_value=1, max_value=10_000),
)
def test_property5_rate_limiter_sliding_window(timestamps, user_id):
    """
    **Property 5: Rate Limiter Sliding Window**

    For any sequence of calls, the number of *allowed* calls within any
    60-second window must never exceed 10.

    Validates: Requirements 10.1
    """
    limiter = RateLimiter(max_calls=10, period=60)
    results = _simulate_calls(limiter, user_id, timestamps)

    # For every call that was allowed, count how many other allowed calls
    # fall within the same 60-second window ending at that call's timestamp.
    for ts_i, allowed_i in zip(timestamps, results):
        if not allowed_i:
            continue  # Only inspect allowed calls

        window_start = ts_i - 60.0
        # Count allowed calls in [window_start, ts_i]
        allowed_in_window = sum(
            1
            for ts_j, allowed_j in zip(timestamps, results)
            if allowed_j and window_start <= ts_j <= ts_i
        )

        assert allowed_in_window <= 10, (
            f"Found {allowed_in_window} allowed calls in the 60-second window "
            f"ending at t={ts_i:.2f}. Timestamps: {timestamps}, "
            f"Results: {results}"
        )


@settings(max_examples=100)
@given(
    extra_calls=st.integers(min_value=1, max_value=20),
    user_id=st.integers(min_value=1, max_value=10_000),
)
def test_property5_calls_beyond_limit_are_denied(extra_calls, user_id):
    """
    After exactly 10 allowed calls within a window, all subsequent calls
    in the same window must be denied.

    Validates: Requirements 10.1
    """
    limiter = RateLimiter(max_calls=10, period=60)

    # Fill the window
    for _ in range(10):
        assert limiter.is_allowed(user_id=user_id) is True

    # All additional calls must be denied
    for _ in range(extra_calls):
        assert limiter.is_allowed(user_id=user_id) is False, (
            "A call beyond the 10-per-60s limit should be denied"
        )
