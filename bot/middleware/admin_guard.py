"""
Admin guard middleware.

Provides the `require_admin` decorator that silently ignores any command
sent by a user whose Telegram ID is not in the ADMIN_IDS list.

Requirements: 8.1, 8.6
"""
import functools
from typing import Callable

from bot.config import ADMIN_IDS


def require_admin(func: Callable) -> Callable:
    """
    Decorator for python-telegram-bot command handlers.

    If the calling user is not in ADMIN_IDS, the handler is silently
    ignored — no response is sent, which avoids confirming the existence
    of admin-only features to unauthorised users (Requirement 8.6).
    """

    @functools.wraps(func)
    async def wrapper(update, context):
        user = update.effective_user
        if user is None or user.id not in ADMIN_IDS:
            return  # Silent ignore
        return await func(update, context)

    return wrapper
