"""
Watch statistics and achievement ranks.

Built as a reusable read layer over chp_watch_history, not a one-off
profile feature: continue-watching, the future home banner and
personalised recommendations all need "what has this user actually
watched", and they should all ask the same place.

Rank tables live here as module-level constants so thresholds can be
tuned without touching query or handler code. Each entry is
(threshold, i18n_key) — the label itself is never stored, only the key,
so a rank reads in the viewer's own language.

"Distinct titles", not raw views: rewatching one film ten times is not
ten films, and a rank that could be farmed by replaying the same video
would be worthless.
"""
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content import AudioLanguage, ContentType, Episode, Title, WatchHistory
from app.services.content import _has_playable_file

OVERALL = "overall"

# (threshold, i18n key) — ascending. Highest reached entry wins.
RANK_TABLES: dict[str, list[tuple[int, str]]] = {
    ContentType.FILM.value: [
        (1, "rank.film.1"),
        (10, "rank.film.2"),
        (25, "rank.film.3"),
        (50, "rank.film.4"),
        (100, "rank.film.5"),
    ],
    ContentType.SERIAL.value: [
        (1, "rank.serial.1"),
        (5, "rank.serial.2"),
        (15, "rank.serial.3"),
        (30, "rank.serial.4"),
    ],
    ContentType.ANIME.value: [
        (1, "rank.anime.1"),
        (10, "rank.anime.2"),
        (25, "rank.anime.3"),
    ],
    ContentType.MULTFILM.value: [
        (1, "rank.multfilm.1"),
        (10, "rank.multfilm.2"),
    ],
    OVERALL: [
        (1, "rank.overall.1"),
        (25, "rank.overall.2"),
        (75, "rank.overall.3"),
        (200, "rank.overall.4"),
    ],
}


@dataclass
class WatchStats:
    """Distinct titles watched, split by content type."""

    by_type: dict[str, int] = field(default_factory=dict)
    total: int = 0


@dataclass
class RankProgress:
    """
    Where a user stands in one rank table.

    `remaining` is the headline number — how many more titles until the
    next rank. None means they've maxed the table out.
    """

    category: str
    count: int
    rank_key: str | None
    next_threshold: int | None
    remaining: int | None

    @property
    def is_max(self) -> bool:
        return self.next_threshold is None


def resolve_rank(category: str, count: int) -> RankProgress:
    """Pure function over the rank tables — no DB, trivially testable."""
    table = RANK_TABLES.get(category, [])
    earned: str | None = None
    next_threshold: int | None = None

    for threshold, key in table:
        if count >= threshold:
            earned = key
        else:
            next_threshold = threshold
            break

    return RankProgress(
        category=category,
        count=count,
        rank_key=earned,
        next_threshold=next_threshold,
        remaining=None if next_threshold is None else next_threshold - count,
    )


async def watch_stats(session: AsyncSession, user_id: int) -> WatchStats:
    """
    Distinct titles watched per ContentType, in one grouped query.

    Counting DISTINCT title_id means a 141-episode serial counts once,
    which is what makes "seriallar: 3" mean something.
    """
    result = await session.execute(
        select(Title.content_type, func.count(func.distinct(WatchHistory.title_id)))
        .join(Title, Title.id == WatchHistory.title_id)
        .where(WatchHistory.user_id == user_id)
        .group_by(Title.content_type)
    )
    by_type = {row[0].value: row[1] for row in result.all()}
    return WatchStats(by_type=by_type, total=sum(by_type.values()))


async def current_ranks(session: AsyncSession, user_id: int) -> dict[str, RankProgress]:
    """
    Highest earned rank per category plus overall, with the distance to
    the next one. Categories the user hasn't touched are omitted;
    `overall` is always present so a new user still sees a goal.
    """
    stats = await watch_stats(session, user_id)

    ranks: dict[str, RankProgress] = {}
    for category in RANK_TABLES:
        if category == OVERALL:
            continue
        count = stats.by_type.get(category, 0)
        if count > 0:
            ranks[category] = resolve_rank(category, count)

    ranks[OVERALL] = resolve_rank(OVERALL, stats.total)
    return ranks


@dataclass
class ContinueWatchingItem:
    title: Title
    episode: Episode


async def continue_watching(
    session: AsyncSession,
    user_id: int,
    limit: int = 10,
    audio_language: AudioLanguage | None = None,
) -> list[ContinueWatchingItem]:
    """
    Most recently watched episodes, newest first, with their titles eagerly joined.

    `audio_language` reuses content._has_playable_file so this row answers
    the same "available in this language" question as every other row —
    a title-level test, deliberately not a per-episode one. Filtering the
    joined episode instead would drop a half-watched serial from the row
    whenever the specific episode someone stopped on lacks that track,
    even though the title is perfectly watchable in it.
    """
    stmt = (
        select(Title, Episode)
        .join(WatchHistory, WatchHistory.title_id == Title.id)
        .join(Episode, Episode.id == WatchHistory.episode_id)
        .where(WatchHistory.user_id == user_id)
    )
    if audio_language is not None:
        stmt = stmt.where(_has_playable_file(audio_language))

    result = await session.execute(
        stmt.order_by(WatchHistory.last_watched_at.desc()).limit(limit)
    )
    return [ContinueWatchingItem(title=row[0], episode=row[1]) for row in result.all()]
