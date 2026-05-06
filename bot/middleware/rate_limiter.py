"""
Rate limiter middleware using a sliding window algorithm.

Limits each user to a maximum of 10 commands per 60-second window.
Requirement: 10.1
"""
import functools
import time
from collections import deque
from typing import Callable


class RateLimiter:
    """
    Sliding window rate limiter.

    Tracks per-user command timestamps in a deque and evicts entries
    that fall outside the current window before each check.
    """

    def __init__(self, max_calls: int = 10, period: int = 60) -> None:
        self.max_calls = max_calls
        self.period = period
        self._calls: dict[int, deque] = {}

    def is_allowed(self, user_id: int) -> bool:
        """
        Check whether the user is allowed to make another call.

        Returns True and records the call if within the limit.
        Returns False without recording if the limit has been reached.
        """
        now = time.time()
        window: deque = self._calls.setdefault(user_id, deque())

        # Evict timestamps that are outside the sliding window
        while window and window[0] < now - self.period:
            window.popleft()

        if len(window) >= self.max_calls:
            return False

        window.append(now)
        return True

    def reset(self, user_id: int) -> None:
        """Clear the call history for a specific user (useful for testing)."""
        self._calls.pop(user_id, None)


# Global singleton used by the `rate_limit` decorator
_rate_limiter = RateLimiter(max_calls=10, period=60)


def rate_limit(func: Callable) -> Callable:
    """
    Decorator for python-telegram-bot command handlers.

    Checks the global rate limiter before invoking the handler.
    If the user has exceeded the limit, sends a warning message and returns.
    """

    @functools.wraps(func)
    async def wrapper(update, context):
        user = update.effective_user
        if user is None:
            # No user context — allow through (e.g. channel posts)
            return await func(update, context)

        if not _rate_limiter.is_allowed(user.id):
            await update.effective_message.reply_text(
                "⚠️ Terlalu banyak permintaan. "
                "Harap tunggu sebentar sebelum mengirim perintah lagi."
            )
            return

        return await func(update, context)

    return wrapper
