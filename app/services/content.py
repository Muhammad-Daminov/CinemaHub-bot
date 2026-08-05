"""
Catalog and delivery logic for the Title/Episode/MediaFile schema.

The language-matching rule matters most here: a user set to Uzbek
should get the uz_dub file when it exists, then uz_sub, then anything
else rather than nothing. Returning None just because the exact match
is missing would hide content the user can still watch.
"""
from dataclasses import dataclass

from sqlalchemy import Integer, case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.content import (
    AudioLanguage,
    Collection,
    ContentType,
    Episode,
    Favorite,
    MediaFile,
    Title,
    WatchHistory,
    title_collections,
)
from app.db.models.user import UILanguage

PAGE_SIZE = 5

# Similarity weights, strongest first. A shared collection is worth more than
# every other signal combined on purpose: "Marvel" is an editorial statement
# that two titles belong together, while a shared genre like Jangari is true
# of a quarter of the catalog and says almost nothing.
COLLECTION_WEIGHT = 10
GENRE_WEIGHT = 3
TYPE_WEIGHT = 2
YEAR_WEIGHT = 1
YEAR_WINDOW = 3

# Ordered fallback chain per UI language — first available wins.
LANGUAGE_PREFERENCE: dict[UILanguage, list[AudioLanguage]] = {
    UILanguage.UZ: [AudioLanguage.UZ_DUB, AudioLanguage.UZ_SUB, AudioLanguage.RU, AudioLanguage.ORIGINAL, AudioLanguage.EN],
    UILanguage.RU: [AudioLanguage.RU, AudioLanguage.UZ_DUB, AudioLanguage.ORIGINAL, AudioLanguage.EN, AudioLanguage.UZ_SUB],
    UILanguage.EN: [AudioLanguage.EN, AudioLanguage.ORIGINAL, AudioLanguage.RU, AudioLanguage.UZ_DUB, AudioLanguage.UZ_SUB],
}


@dataclass
class CollectionSummary:
    collection: Collection
    title_count: int


@dataclass
class TitlePage:
    titles: list[Title]
    page: int
    has_more: bool

    @property
    def has_prev(self) -> bool:
        return self.page > 0


def _has_playable_file(audio_language: AudioLanguage | None = None):
    """
    EXISTS chp_episodes -> chp_media_files for the current Title row.

    The admin panel creates a Title before any file is attached, so
    without this a half-finished entry shows up in the catalog and fails
    the moment someone taps it. Correlated EXISTS keeps this to the one
    query browse() already runs — no per-row lookup.

    `audio_language` narrows the same subquery rather than adding a
    second join: "has a file" and "has a file in Russian" are one
    question, and answering them separately would both duplicate the
    join and let a title through that has *some* file plus a Russian
    file on a different episode.

    Deliberately NOT applied to admin listings: surfacing incomplete
    titles is exactly what that screen is for.
    """
    stmt = (
        select(MediaFile.id)
        .join(Episode, Episode.id == MediaFile.episode_id)
        .where(Episode.title_id == Title.id)
    )
    if audio_language is not None:
        stmt = stmt.where(MediaFile.language == audio_language)
    return stmt.exists()


def similarity_score(title_id: int):
    """
    Weighted "how like title_id is this row" expression, evaluated by
    Postgres against the candidate Title in the enclosing query.

    Every term is a correlated scalar subquery over the *source* title id
    rather than values fetched in Python first, so scoring and ranking
    happen in one round trip and no candidate ever crosses the wire just
    to be discarded.

    Exposed (not underscore-private) so a caller can select it alongside
    the rows to see why something ranked where it did.
    """
    source_links = title_collections.alias("source_links")

    shared_collections = (
        select(func.count())
        .select_from(title_collections)
        .where(
            title_collections.c.title_id == Title.id,
            title_collections.c.collection_id.in_(
                select(source_links.c.collection_id).where(source_links.c.title_id == title_id)
            ),
        )
        .correlate(Title)
        .scalar_subquery()
    )

    # count(*) of this row's genres that also appear on the source row.
    # unnest in the FROM clause is implicitly LATERAL, so it sees Title.
    # Both sides are unnested to a set of scalars: comparing a token
    # against the raw genres column would be varchar = varchar[].
    source_genres = select(func.unnest(Title.genres)).where(Title.id == title_id)
    genre_token = func.unnest(Title.genres).column_valued("genre_token")
    genre_overlap = (
        select(func.count())
        .where(genre_token.in_(source_genres))
        .correlate(Title)
        .scalar_subquery()
    )

    source_type = select(Title.content_type).where(Title.id == title_id).scalar_subquery()
    source_year = select(Title.year).where(Title.id == title_id).scalar_subquery()

    return (
        shared_collections * COLLECTION_WEIGHT
        + genre_overlap * GENRE_WEIGHT
        + case((Title.content_type == source_type, TYPE_WEIGHT), else_=0)
        # abs() over a NULL year yields NULL, which is not true, so a title
        # with no year simply scores 0 here instead of erroring.
        + case((func.abs(Title.year - source_year) <= YEAR_WINDOW, YEAR_WEIGHT), else_=0)
    ).cast(Integer)


class ContentService:
    # ---------- browsing ----------

    async def browse(
        self,
        session: AsyncSession,
        page: int = 0,
        content_type: ContentType | None = None,
        genre: str | None = None,
        query: str | None = None,
        collection_id: int | None = None,
        audio_language: AudioLanguage | None = None,
        order_by_popularity: bool = True,
    ) -> TitlePage:
        stmt = select(Title).where(
            Title.is_active.is_(True), _has_playable_file(audio_language)
        )

        if collection_id is not None:
            # Explicit join through the association table — never a lazy
            # `title.collections` walk, which raises under async.
            stmt = stmt.join(
                title_collections, title_collections.c.title_id == Title.id
            ).where(title_collections.c.collection_id == collection_id)
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

    async def list_collections(self, session: AsyncSession) -> list[CollectionSummary]:
        """
        Active collections with how many playable titles each holds, in one
        query. The count uses the same playable-file gate as browse(), so a
        collection never advertises titles the catalog would then hide.
        """
        title_count = (
            select(func.count(title_collections.c.title_id))
            .where(
                title_collections.c.collection_id == Collection.id,
                title_collections.c.title_id.in_(
                    select(Title.id).where(Title.is_active.is_(True), _has_playable_file())
                ),
            )
            .scalar_subquery()
        )
        result = await session.execute(
            select(Collection, title_count)
            .where(Collection.is_active.is_(True))
            .order_by(Collection.sort_order, Collection.name)
        )
        return [CollectionSummary(collection=row[0], title_count=row[1]) for row in result.all()]

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

    # ---------- discovery ----------

    async def similar_titles(
        self,
        session: AsyncSession,
        title_id: int,
        limit: int = 10,
        audio_language: AudioLanguage | None = None,
    ) -> list[Title]:
        """
        Titles most like this one, best first.

        Ranked by shared collections, then genre overlap, then content
        type, then release proximity — see similarity_score(). Ties break
        on popularity so the better-known of two equally-similar titles
        leads.

        Rows scoring 0 are dropped rather than padded out with filler: an
        empty "O'xshash" row is honest, a row of unrelated titles is not.
        """
        score = similarity_score(title_id)
        result = await session.execute(
            select(Title)
            .where(
                Title.id != title_id,
                Title.is_active.is_(True),
                _has_playable_file(audio_language),
                score > 0,
            )
            .order_by(score.desc(), Title.view_count.desc(), Title.name)
            .limit(limit)
        )
        return list(result.scalars())

    async def recommended_for_user(
        self,
        session: AsyncSession,
        user_id: int,
        limit: int = 10,
        audio_language: AudioLanguage | None = None,
    ) -> list[Title]:
        """
        Unwatched titles matching what this user actually watches.

        Preferences come from their own history rather than a stored
        profile, so they stay current for free. The first query returns
        one small row per watched title (a heavy user has hundreds, not
        millions) and the vocabulary is folded up here; the candidate set
        itself is never pulled into Python.

        No history -> popularity. A brand-new user seeing an empty
        "Siz uchun" row would be worse than seeing the obvious picks.
        """
        history = await session.execute(
            select(Title.content_type, Title.genres)
            .join(WatchHistory, WatchHistory.title_id == Title.id)
            .where(WatchHistory.user_id == user_id)
        )
        rows = history.all()

        preferred_types = {row[0] for row in rows}
        preferred_genres = {genre for row in rows if row[1] for genre in row[1]}

        stmt = select(Title).where(
            Title.is_active.is_(True), _has_playable_file(audio_language)
        )

        if preferred_types or preferred_genres:
            signals = []
            if preferred_types:
                signals.append(Title.content_type.in_(preferred_types))
            if preferred_genres:
                signals.append(Title.genres.overlap(list(preferred_genres)))
            stmt = stmt.where(
                or_(*signals),
                Title.id.not_in(
                    select(WatchHistory.title_id).where(WatchHistory.user_id == user_id)
                ),
            )

        result = await session.execute(
            stmt.order_by(Title.view_count.desc(), Title.name).limit(limit)
        )
        return list(result.scalars())

    # ---------- favourites ----------

    async def toggle_favorite(self, session: AsyncSession, user_id: int, title_id: int) -> bool:
        """
        Adds or removes the favourite, returning the resulting state
        (True = saved, False = removed).

        The insert is ON CONFLICT DO NOTHING rather than select-then-insert:
        a double-tap on a slow connection sends two toggles that interleave,
        and the naive version raises on the unique constraint instead of just
        settling on one state. rowcount then tells us which way it went — 1
        means we inserted, 0 means the row already existed and this tap is
        the un-favourite.
        """
        result = await session.execute(
            pg_insert(Favorite)
            .values(user_id=user_id, title_id=title_id)
            .on_conflict_do_nothing(constraint="uq_favorite_per_user")
        )
        if result.rowcount:
            await session.flush()
            return True

        await session.execute(
            delete(Favorite).where(Favorite.user_id == user_id, Favorite.title_id == title_id)
        )
        await session.flush()
        return False

    async def list_favorites(self, session: AsyncSession, user_id: int, page: int = 0) -> TitlePage:
        """
        Saved titles, most recently saved first — same page shape and
        playable-file gate as browse(), so the pagination keyboard and the
        card renderer work unchanged.
        """
        stmt = (
            select(Title)
            .join(Favorite, Favorite.title_id == Title.id)
            .where(Favorite.user_id == user_id, Title.is_active.is_(True), _has_playable_file())
            .order_by(Favorite.created_at.desc(), Title.name)
        )
        result = await session.execute(stmt.offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1))
        rows = list(result.scalars())
        return TitlePage(titles=rows[:PAGE_SIZE], page=page, has_more=len(rows) > PAGE_SIZE)

    async def is_favorite(self, session: AsyncSession, user_id: int, title_id: int) -> bool:
        result = await session.execute(
            select(Favorite.id).where(Favorite.user_id == user_id, Favorite.title_id == title_id)
        )
        return result.scalar_one_or_none() is not None

    async def favorite_title_ids(
        self, session: AsyncSession, user_id: int, title_ids: list[int]
    ) -> set[int]:
        """
        Which of these titles the user has saved, in one query.

        A page renders five cards and each needs its heart in the right
        state; asking is_favorite() per card would be five round trips for
        information one IN-clause already has.
        """
        if not title_ids:
            return set()
        result = await session.execute(
            select(Favorite.title_id).where(
                Favorite.user_id == user_id, Favorite.title_id.in_(title_ids)
            )
        )
        return set(result.scalars())

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
