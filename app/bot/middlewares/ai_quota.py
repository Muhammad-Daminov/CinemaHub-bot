"""
Enforces the daily AI recommendation quota.

Intentionally NOT registered dispatcher-wide — attach this only to
app.bot.handlers.ai's router (`router.message.middleware(...)`), since
it does a DB + Redis round trip that every other handler shouldn't
pay for.
"""
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.i18n import t
from app.db.models.user import User
from app.services.ai_quota import increment_and_check
from app.services.subscriptions import is_user_premium


class AIQuotaMiddleware(BaseMiddleware):
    """Free users: capped at settings.AI_DAILY_LIMIT_FREE/day. Premium: unlimited."""

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession = data["session"]
        # I18nMiddleware is registered on dispatcher.update, which aiogram runs
        # before any router-level middleware like this one, so `_` is already here.
        _ = data.get("_", t)

        result = await session.execute(select(User).where(User.telegram_id == event.from_user.id))
        user = result.scalar_one_or_none()
        if user is None:
            await event.answer(_("common.need_start"))
            return None

        if await is_user_premium(session, user.id):
            data["ai_quota_remaining"] = None  # unlimited
            return await handler(event, data)

        count, allowed = await increment_and_check(user.telegram_id, settings.AI_DAILY_LIMIT_FREE)
        if not allowed:
            await event.answer(_("ai.quota_exceeded", limit=settings.AI_DAILY_LIMIT_FREE))
            return None

        data["ai_quota_remaining"] = max(settings.AI_DAILY_LIMIT_FREE - count, 0)
        return await handler(event, data)
