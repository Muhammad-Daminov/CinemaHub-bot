"""
Handlers for watch requests, triggered either by an inline
'Tomosha qilish' button (callback_query) or by the Mini App calling
Telegram.WebApp.sendData(...) (message.web_app_data).

`watch:<episode_id>` always means "play this episode now". Choosing
*which* episode (season/episode navigation for serials) happens in
handlers/catalog.py, which calls deliver_and_warn() here once it has
resolved one.
"""
import json
import logging

from aiogram import F, Router
from aiogram.client.bot import Bot
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.movie import WATCH_CALLBACK_PREFIX, get_resend_keyboard
from app.db.models.content import Episode, Title
from app.db.models.user import UILanguage, User
from app.services.access import access_message_key, check_title_access
from app.services.content import content_service
from app.services.streaming import streaming_service

router = Router(name="streaming")
logger = logging.getLogger(__name__)


async def _viewer(session: AsyncSession, telegram_id: int) -> tuple[int | None, UILanguage]:
    """
    (user_id, language) in one query — the language drives audio-track
    selection, the id is what watch history is keyed on.
    """
    result = await session.execute(
        select(User.id, User.language).where(User.telegram_id == telegram_id)
    )
    row = result.first()
    if row is None:
        return None, UILanguage.UZ
    return row[0], row[1] or UILanguage.UZ


async def deliver_and_warn(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    telegram_id: int,
    episode: Episode,
    lang: UILanguage,
    _,
) -> Message | None:
    """
    Resolves the best file for this viewer, delivers it and posts the
    send confirmation. Returns None if nothing could be delivered.
    Shared with handlers/catalog.py so both entry points behave identically.
    """
    title = await session.get(Title, episode.title_id)
    if title is None:
        return None

    # Every bot route to a file passes through here — code search, name
    # search, genres, collections, the episode picker — so this is where
    # the canonical check belongs. Placing it in each caller would mean
    # one forgotten caller is an open door to paid content.
    #
    # The refusal is spoken here too, because returning None would surface
    # as "send error" and tell a subscriber-to-be nothing about why.
    viewer = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if viewer is not None:
        access = await check_title_access(session, bot, viewer, title)
        if not access.allowed:
            await bot.send_message(
                chat_id,
                _(access_message_key(access.decision), channel=access.membership.channel),
            )
            return None

    user_id, language = await _viewer(session, telegram_id)
    media_file = await content_service.pick_file(session, episode.id, language)
    if media_file is None:
        logger.warning("No media file for episode_id=%s", episode.id)
        return None

    try:
        sent_message = await streaming_service.deliver_episode(
            bot=bot,
            session=session,
            chat_id=chat_id,
            title=title,
            episode=episode,
            media_file=media_file,
            lang=lang,
            user_id=user_id,
        )
    except ValueError:
        logger.warning("Delivery failed for episode_id=%s: no valid source", episode.id)
        return None

    # The resend control stays: it is useful on its own, and is no longer
    # attached to a deletion notice.
    await sent_message.answer(
        _("streaming.sent"),
        reply_markup=get_resend_keyboard(episode.id, lang),
    )
    return sent_message


@router.callback_query(F.data.startswith(WATCH_CALLBACK_PREFIX))
async def handle_watch_callback(
    callback: CallbackQuery, session: AsyncSession, lang: UILanguage, _
) -> None:
    episode_id = int(callback.data.removeprefix(WATCH_CALLBACK_PREFIX))
    episode = await session.get(Episode, episode_id)

    if episode is None:
        await callback.answer(_("streaming.unavailable"), show_alert=True)
        return

    result = await deliver_and_warn(
        callback.bot, session, callback.message.chat.id, callback.from_user.id, episode, lang, _
    )
    if result is None:
        await callback.answer(_("streaming.send_error"), show_alert=True)
        return
    await callback.answer()


@router.message(F.web_app_data)
async def handle_webapp_watch_request(
    message: Message, session: AsyncSession, lang: UILanguage, _
) -> None:
    """Mini App sends {"action": "watch", "movie_id": <title_id>} via Telegram.WebApp.sendData()."""
    try:
        payload = json.loads(message.web_app_data.data)
        title_id = int(payload["movie_id"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        await message.answer(_("streaming.bad_request"))
        return

    if payload.get("action") != "watch":
        return

    # The Mini App only ever lists titles, so resolve to the first episode.
    episodes = await content_service.list_episodes(session, title_id)
    if not episodes:
        await message.answer(_("streaming.not_available"))
        return

    result = await deliver_and_warn(
        message.bot, session, message.chat.id, message.from_user.id, episodes[0], lang, _
    )
    if result is None:
        await message.answer(_("streaming.send_error"))
