"""
Anti-spam / rate-limiting middleware.

Backed by Redis (not an in-memory dict) so the limit holds correctly
across process restarts and if the bot ever runs with >1 worker.
One SET NX EX per update: if the key already exists, the user is
within their cooldown window and the update is dropped.
"""
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.core.redis import get_redis

DEFAULT_COOLDOWN_SECONDS = 1
THROTTLE_KEY_PREFIX = "throttle:"


class ThrottlingMiddleware(BaseMiddleware):
    """Drops updates from a user that arrive faster than `cooldown_seconds` apart."""

    def __init__(self, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> None:
        self.cooldown_seconds = cooldown_seconds

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        redis = get_redis()
        key = f"{THROTTLE_KEY_PREFIX}{user.id}"

        # NX: only set if not already present. Returns None if the key exists.
        acquired = await redis.set(key, "1", nx=True, ex=self.cooldown_seconds)
        if not acquired:
            return None  # silently drop — avoids spamming "slow down" replies

        return await handler(event, data)
