"""
Resolves the current user's UI language and injects a bound translator.

Runs after DbSessionMiddleware because it reads User.language through
that middleware's session. Handlers then take `_` (and optionally
`lang`) as parameters:

    async def handler(message: Message, _) -> None:
        await message.answer(_("menu.movies"))

Only the language column is selected, not the whole User row — this runs
on every single update, so it stays one narrow query.
"""
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TelegramUser

from app.core.i18n import t
from app.db.models.user import UILanguage, User
from sqlalchemy import select


class I18nMiddleware(BaseMiddleware):
    """Puts `lang` (UILanguage) and `_` (translator bound to it) into handler data."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        lang = UILanguage.UZ

        telegram_user: TelegramUser | None = data.get("event_from_user")
        session = data.get("session")

        if telegram_user is not None and session is not None:
            result = await session.execute(
                select(User.language).where(User.telegram_id == telegram_user.id)
            )
            # Unknown user (hasn't hit /start yet) -> default UZ.
            lang = result.scalar_one_or_none() or UILanguage.UZ

        data["lang"] = lang
        data["_"] = partial(t, lang=lang)
        return await handler(event, data)
