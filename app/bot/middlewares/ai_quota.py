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

from app.core.i18n import t
from app.db.models.user import User
from app.services.ai_quota import increment_and_check
from app.services.plan_features import ai_daily_limit


class AIQuotaMiddleware(BaseMiddleware):
    """
    Applies the caller's daily AI allowance.

    The number is whatever their plan grants (app.services.plan_features),
    which defaults to the previous behaviour — AI_DAILY_LIMIT_FREE for a
    user without a subscription, unlimited with one — until an
    administrator sets an explicit `ai_daily_limit` on a plan.
    """

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

        # The cap comes from the user's plan, resolved in the one place
        # entitlements are decided. It used to be "premium means unlimited"
        # hardcoded here — the plan tables existed but nothing read them,
        # so every tier bought the same thing.
        limit = await ai_daily_limit(session, user.id)
        if limit is None:
            data["ai_quota_remaining"] = None  # unlimited
            return await handler(event, data)

        count, allowed = await increment_and_check(user.telegram_id, limit)
        if not allowed:
            await event.answer(_("ai.quota_exceeded", limit=limit))
            return None

        data["ai_quota_remaining"] = max(limit - count, 0)
        return await handler(event, data)
