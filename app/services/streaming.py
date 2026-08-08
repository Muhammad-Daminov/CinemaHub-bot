"""
Core delivery logic for the Title/Episode/MediaFile catalog.

Primary path: send directly by Telegram `file_id` (zero storage cost,
one API call). Fallback: if a `file_id` has expired/gone invalid,
`copy_message` from the chat the file was originally posted in
re-delivers it without the "Forwarded from" tag and without us ever
holding the video bytes ourselves. Those coordinates now live on the
MediaFile row (source_chat_id / source_message_id) rather than being
passed in by the caller.

Which file gets sent is decided upstream by
ContentService.pick_file(), which honours the viewer's language with a
fallback chain — this module just delivers what it's handed.
"""
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import t
from app.db.models.content import Episode, MediaFile, Title, WatchHistory
from app.db.models.user import UILanguage
from app.services.content import content_service


class StreamingService:
    """Delivers one MediaFile to a chat. Delivered messages are never auto-removed."""

    async def deliver_episode(
        self,
        bot: Bot,
        session: AsyncSession,
        chat_id: int,
        title: Title,
        episode: Episode,
        media_file: MediaFile,
        lang: UILanguage = UILanguage.UZ,
        user_id: int | None = None,
    ) -> Message:
        """
        Sends `media_file` to `chat_id`. Raises ValueError if there is no way
        to deliver it (send failed and no source-message fallback available).
        """
        # Resolved here rather than at each call site: the bot and the Mini
        # App both arrive through this method, so one lookup keeps the
        # caption in the viewer's language on either route.
        localized = await content_service.localized_title(session, title, lang)
        sent_message = await self._send_by_file_id_or_fallback(
            bot, chat_id, title, episode, media_file, lang, localized.name
        )

        # Both counters matter: the episode drives "most-watched episode"
        # ordering, the title drives catalog popularity.
        episode.view_count += 1
        title.view_count += 1

        if user_id is not None:
            await self._record_watch(session, user_id, title.id, episode.id)

        await session.flush()
        return sent_message

    @staticmethod
    async def _record_watch(
        session: AsyncSession, user_id: int, title_id: int, episode_id: int
    ) -> None:
        """
        Upsert the user's history row for this episode.

        A single INSERT ... ON CONFLICT DO UPDATE rather than
        select-then-insert: two devices hitting play at once would both
        see "no row" and race to insert, and the unique constraint would
        turn that into an IntegrityError mid-delivery.
        """
        statement = (
            pg_insert(WatchHistory)
            .values(user_id=user_id, title_id=title_id, episode_id=episode_id, watch_count=1)
            .on_conflict_do_update(
                index_elements=[WatchHistory.user_id, WatchHistory.episode_id],
                set_={
                    "watch_count": WatchHistory.watch_count + 1,
                    "last_watched_at": func.now(),
                },
            )
        )
        await session.execute(statement)

    async def _send_by_file_id_or_fallback(
        self,
        bot: Bot,
        chat_id: int,
        title: Title,
        episode: Episode,
        media_file: MediaFile,
        lang: UILanguage,
        name: str | None = None,
    ) -> Message:
        caption = self._build_caption(title, episode, lang, name)

        try:
            return await bot.send_video(chat_id=chat_id, video=media_file.file_id, caption=caption)
        except TelegramBadRequest:
            pass  # file_id expired/invalid — fall through to the source-message fallback

        if media_file.source_chat_id and media_file.source_message_id:
            return await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=media_file.source_chat_id,
                message_id=media_file.source_message_id,
                caption=caption,
            )

        raise ValueError(
            f"MediaFile {media_file.id} has an invalid file_id and no source fallback"
        )

    @staticmethod
    def _build_caption(
        title: Title, episode: Episode, lang: UILanguage, name: str | None = None
    ) -> str:
        """`name` is the localised title; the stored name is the fallback."""
        parts = [f"🎬 <b>{name or title.name}</b>"]
        if title.year:
            parts.append(f"({title.year})")
        if not title.is_single_episode:
            label = episode.name or t(
                "streaming.caption_episode", lang, season=episode.season, number=episode.number
            )
            parts.append(f"— {label}")
        if title.rating:
            parts.append(f"⭐ {title.rating}")
        return " ".join(parts)


streaming_service = StreamingService()
