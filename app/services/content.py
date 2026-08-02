"""
Catalog and delivery logic for the Title/Episode/MediaFile schema.

The language-matching rule matters most here: a user set to Uzbek
should get the uz_dub file when it exists, then uz_sub, then anything
else rather than nothing. Returning None just because the exact match
is missing would hide content the user can still watch.
"""
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.content import AudioLanguage, ContentType, Episode, MediaFile, Title
from app.db.models.user import UILanguage

PAGE_SIZE = 5

# Ordered fallback chain per UI language — first available wins.
LANGUAGE_PREFERENCE: dict[UILanguage, list[AudioLanguage]] = {
    UILanguage.UZ: [AudioLanguage.UZ_DUB, AudioLanguage.UZ_SUB, AudioLanguage.RU, AudioLanguage.ORIGINAL, AudioLanguage.EN],
    UILanguage.RU: [AudioLanguage.RU, AudioLanguage.UZ_DUB, AudioLanguage.ORIGINAL, AudioLanguage.EN, AudioLanguage.UZ_SUB],
    UILanguage.EN: [AudioLanguage.EN, AudioLanguage.ORIGINAL, AudioLanguage.RU, AudioLanguage.UZ_DUB, AudioLanguage.UZ_SUB],
}


@dataclass
class TitlePage:
    titles: list[Title]
    page: int
    has_more: bool

    @property
    def has_prev(self) -> bool:
        return self.page > 0


def _has_playable_file():
    """
    EXISTS chp_episodes -> chp_media_files for the current Title row.

    The admin panel creates a Title before any file is attached, so
    without this a half-finished entry shows up in the catalog and fails
    the moment someone taps it. Correlated EXISTS keeps this to the one
    query browse() already runs — no per-row lookup.

    Deliberately NOT applied to admin listings: surfacing incomplete
    titles is exactly what that screen is for.
    """
    return (
        select(MediaFile.id)
        .join(Episode, Episode.id == MediaFile.episode_id)
        .where(Episode.title_id == Title.id)
        .exists()
    )


class ContentService:
    # ---------- browsing ----------

    async def browse(
        self,
        session: AsyncSession,
        page: int = 0,
        content_type: ContentType | None = None,
        genre: str | None = None,
        query: str | None = None,
        order_by_popularity: bool = True,
    ) -> TitlePage:
        stmt = select(Title).where(Title.is_active.is_(True), _has_playable_file())

        if content_type:
            stmt = stmt.where(Title.content_type == content_type)
        if genre:
            stmt = stmt.where(Title.genres.any(genre))
        if query:
            stmt = stmt.where(Title.name.ilike(f"%{query.strip()}%"))

        stmt = stmt.order_by(Title.view_count.desc(), Title.name) if order_by_popularity else stmt.order_by(Title.created_at.desc())

        # Fetch one extra row to detect a next page without a COUNT query.
        result = await session.execute(stmt.offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1))
        rows = list(result.scalars())
        return TitlePage(titles=rows[:PAGE_SIZE], page=page, has_more=len(rows) > PAGE_SIZE)

    async def get_title(self, session: AsyncSession, title_id: int) -> Title | None:
        result = await session.execute(
            select(Title).where(Title.id == title_id).options(selectinload(Title.episodes))
        )
        return result.scalar_one_or_none()

    async def list_genres(self, session: AsyncSession, limit: int = 24) -> list[str]:
        genre_col = func.unnest(Title.genres).label("genre")
        stmt = (
            select(genre_col)
            .where(Title.is_active.is_(True), Title.genres.is_not(None))
            .group_by(genre_col)
            .order_by(func.count().desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return [row.genre for row in result.all()]

    async def list_episodes(self, session: AsyncSession, title_id: int, season: int | None = None) -> list[Episode]:
        stmt = select(Episode).where(Episode.title_id == title_id)
        if season is not None:
            stmt = stmt.where(Episode.season == season)
        result = await session.execute(stmt.order_by(Episode.season, Episode.number))
        return list(result.scalars())

    async def list_seasons(self, session: AsyncSession, title_id: int) -> list[int]:
        result = await session.execute(
            select(Episode.season).where(Episode.title_id == title_id).group_by(Episode.season).order_by(Episode.season)
        )
        return [row.season for row in result.all()]

    # ---------- delivery ----------

    async def pick_file(
        self, session: AsyncSession, episode_id: int, ui_language: UILanguage
    ) -> MediaFile | None:
        """
        Best available file for this viewer: exact language match first,
        then the fallback chain, then any file at all. Returns None only
        when the episode genuinely has no files.
        """
        result = await session.execute(select(MediaFile).where(MediaFile.episode_id == episode_id))
        files = list(result.scalars())
        if not files:
            return None

        by_language: dict[AudioLanguage, list[MediaFile]] = {}
        for media_file in files:
            by_language.setdefault(media_file.language, []).append(media_file)

        for preferred in LANGUAGE_PREFERENCE.get(ui_language, []):
            if preferred in by_language:
                # Highest quality among files in that language.
                return max(by_language[preferred], key=lambda f: f.quality.value)

        return files[0]

    async def available_languages(self, session: AsyncSession, episode_id: int) -> list[AudioLanguage]:
        result = await session.execute(
            select(MediaFile.language).where(MediaFile.episode_id == episode_id).group_by(MediaFile.language)
        )
        return [row.language for row in result.all()]


content_service = ContentService()
