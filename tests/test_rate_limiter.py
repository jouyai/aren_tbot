"""
Unit tests for RateLimiter (bot/middleware/rate_limiter.py).
"""
import time

import pytest

from bot.middleware.rate_limiter import RateLimiter


class TestRateLimiterBasic:
    """Basic behaviour tests for RateLimiter."""

    def test_first_call_is_allowed(self):
        limiter = RateLimiter(max_calls=10, period=60)
        assert limiter.is_allowed(user_id=1) is True

    def test_calls_up_to_limit_are_allowed(self):
        limiter = RateLimiter(max_calls=5, period=60)
        for _ in range(5):
            assert limiter.is_allowed(user_id=42) is True

    def test_call_exceeding_limit_is_denied(self):
        limiter = RateLimiter(max_calls=5, period=60)
        for _ in range(5):
            limiter.is_allowed(user_id=42)
        # 6th call should be denied
        assert limiter.is_allowed(user_id=42) is False

    def test_different_users_are_independent(self):
        limiter = RateLimiter(max_calls=2, period=60)
        # Exhaust user 1
        limiter.is_allowed(user_id=1)
        limiter.is_allowed(user_id=1)
        assert limiter.is_allowed(user_id=1) is False
        # User 2 should still be allowed
        assert limiter.is_allowed(user_id=2) is True

    def test_reset_clears_user_history(self):
        limiter = RateLimiter(max_calls=2, period=60)
        limiter.is_allowed(user_id=99)
        limiter.is_allowed(user_id=99)
        assert limiter.is_allowed(user_id=99) is False
        limiter.reset(user_id=99)
        assert limiter.is_allowed(user_id=99) is True

    def test_default_limits_are_10_per_60_seconds(self):
        limiter = RateLimiter()
        assert limiter.max_calls == 10
        assert limiter.period == 60

    def test_exactly_10_calls_allowed_in_window(self):
        limiter = RateLimiter(max_calls=10, period=60)
        results = [limiter.is_allowed(user_id=7) for _ in range(10)]
        assert all(results), "All 10 calls within the window should be allowed"
        assert limiter.is_allowed(user_id=7) is False, "11th call should be denied"


class TestRateLimiterSlidingWindow:
    """Tests that verify the sliding window eviction logic."""

    def test_old_calls_are_evicted_after_window_expires(self, monkeypatch):
        """Calls made before the window should not count against the limit."""
        limiter = RateLimiter(max_calls=3, period=60)
        fake_time = [0.0]

        monkeypatch.setattr(time, "time", lambda: fake_time[0])

        # Make 3 calls at t=0 — window is now full
        for _ in range(3):
            limiter.is_allowed(user_id=5)
        assert limiter.is_allowed(user_id=5) is False

        # Advance time past the window
        fake_time[0] = 61.0

        # Old calls should have been evicted; new calls should be allowed
        assert limiter.is_allowed(user_id=5) is True

    def test_partial_eviction_keeps_recent_calls(self, monkeypatch):
        """Only calls outside the window are evicted; recent ones remain."""
        limiter = RateLimiter(max_calls=3, period=60)
        fake_time = [0.0]

        monkeypatch.setattr(time, "time", lambda: fake_time[0])

        # 2 calls at t=0
        limiter.is_allowed(user_id=10)
        limiter.is_allowed(user_id=10)

        # 1 call at t=50 (still inside window)
        fake_time[0] = 50.0
        limiter.is_allowed(user_id=10)

        # At t=65: the 2 calls from t=0 are evicted, but the t=50 call remains
        fake_time[0] = 65.0
        # 1 call remains → 2 more should be allowed before hitting limit
        assert limiter.is_allowed(user_id=10) is True
        assert limiter.is_allowed(user_id=10) is True
        assert limiter.is_allowed(user_id=10) is False
