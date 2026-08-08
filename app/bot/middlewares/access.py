"""
Two gates every bot update passes: is this user banned, and have they
joined the channel we require?

Both are enforced here rather than in each handler. A per-handler check
is a rule that holds until someone adds a handler and forgets it, and
"forgot to check the ban" is indistinguishable from "not banned" until an
abusive user is found still using the platform.

Runs after DbSessionMiddleware (it needs the session) and after
I18nMiddleware (it needs the user's language to explain the refusal in
it — a gate the user cannot read is a bot that appears broken).

Two things deliberately pass through:

  /start        registration itself. A user with no row has no language,
                no referral capture and no way to be told anything; gating
                it would make the join prompt the first thing an
                unregistered account ever sees and the last, since it
                could never be recorded as having arrived.
  the recheck   the "I have joined" button, which exists precisely to be
                pressed by someone who is not yet through the gate.
"""
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    User as TelegramUser,
)
from sqlalchemy import select

from app.bot.keyboards.main_menu import SET_LANG_PREFIX
from app.db.models.user import User, UserRole
from app.services.membership import check_access
from app.services.settings_store import MembershipConfig

logger = logging.getLogger(__name__)

MEMBERSHIP_CHECK_CALLBACK = "membership:check"


def membership_keyboard(config: MembershipConfig, _) -> InlineKeyboardMarkup:
    """Join link plus a recheck button. The link is omitted for a channel with no public URL."""
    rows: list[list[InlineKeyboardButton]] = []
    if config.invite_url:
        rows.append([InlineKeyboardButton(text=_("membership.join"), url=config.invite_url)])
    rows.append(
        [InlineKeyboardButton(text=_("membership.check"), callback_data=MEMBERSHIP_CHECK_CALLBACK)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _reply(event: TelegramObject, text: str, markup=None) -> None:
    """Answers whichever kind of update this is, silently ignoring the kinds that cannot be answered."""
    if isinstance(event, CallbackQuery):
        # Sent as a message, not an alert: an alert caps at 200 characters
        # and cannot carry the join button, which is the only useful part.
        await event.answer()
        if event.message is not None:
            await event.message.answer(text, reply_markup=markup)
    elif isinstance(event, Message):
        await event.answer(text, reply_markup=markup)


def _inner_event(event: TelegramObject) -> TelegramObject:
    """The Message/CallbackQuery inside an Update — this middleware sits on update.middleware."""
    for attribute in ("message", "callback_query", "edited_message"):
        inner = getattr(event, attribute, None)
        if inner is not None:
            return inner
    return event


def _is_exempt(inner: TelegramObject) -> bool:
    if isinstance(inner, CallbackQuery):
        data = inner.data or ""
        # The language picker is the second half of /start: gating it would
        # strand a new user between "choose a language" and a join prompt
        # written in a language they never chose.
        return data == MEMBERSHIP_CHECK_CALLBACK or data.startswith(SET_LANG_PREFIX)
    text = getattr(inner, "text", None) or ""
    return text.startswith("/start")


class AccessMiddleware(BaseMiddleware):
    """Blocks banned users outright, and non-members from everything but /start."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_user: TelegramUser | None = data.get("event_from_user")
        session = data.get("session")
        if telegram_user is None or session is None:
            return await handler(event, data)

        user = (
            await session.execute(select(User).where(User.telegram_id == telegram_user.id))
        ).scalar_one_or_none()
        if user is None:
            # Not registered yet — /start is the only thing that can help them.
            return await handler(event, data)

        translate = data.get("_")
        inner = _inner_event(event)

        # Deliberately ahead of the /start exemption: a banned user must not
        # be able to restart their way back in.
        if user.is_banned and user.role != UserRole.SUPER_ADMIN:
            await _reply(inner, translate("common.banned"))
            return None

        if _is_exempt(inner):
            return await handler(event, data)

        from app.bot.instance import bot

        allowed, config = await check_access(session, bot, user)
        if not allowed:
            await _reply(
                inner,
                translate("membership.required", channel=config.channel),
                membership_keyboard(config, translate),
            )
            return None

        return await handler(event, data)
