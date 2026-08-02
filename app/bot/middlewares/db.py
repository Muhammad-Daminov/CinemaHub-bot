"""
Injects a fresh AsyncSession into every update's handler data, committing
on success and rolling back on any exception raised inside the handler.

Usage inside a handler: add `session: AsyncSession` as a parameter —
aiogram resolves it automatically from the middleware's data dict.
"""
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.db.session import AsyncSessionFactory


class DbSessionMiddleware(BaseMiddleware):
    """One DB transaction per update — commit on success, rollback on error."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with AsyncSessionFactory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
