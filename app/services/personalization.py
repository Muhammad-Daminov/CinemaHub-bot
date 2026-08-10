"""
What a user is into, and the badge that earns them.

Built on `app.services.achievements.watch_stats` rather than beside it —
that module already answers "what has this user actually watched", and a
second query shape asking the same question would drift from it.

Three decisions worth stating:

**Recent behaviour is weighted, not exclusive.** A profile that only
looked at the last month would swing wildly for a casual viewer; one that
only looked at all time would never notice someone moving from films to
anime. Recent watches count once for the long-term tally and again for
the recency bonus, so a shift shows up within weeks without erasing
years.

**A dominant interest has to actually dominate.** Below
`MIN_TITLES_FOR_DOMINANCE` distinct titles, or below `MIN_DOMINANT_SHARE`
of them, there is no dominant type and no badge. Handing someone "Anime
Master" for two clicks would make the badge meaningless and the
personalized feed wrong.

**Nothing here is shared between users.** Every function takes a
`user_id` and every query filters on it. There is no module-level cache,
because a cache keyed on anything less than the user is how one person's
feed ends up in front of another.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content import ContentType, Title, WatchHistory
from app.db.models.personalization import UserInterestProfile
from app.db.models.user import User
from app.services.achievements import watch_stats

logger = logging.getLogger(__name__)

# How far back "recent" reaches, and how much a recent watch is worth on
# top of its long-term count. 2.0 means the last month counts triple.
RECENT_WINDOW_DAYS = 30
RECENT_WEIGHT = 2.0

# Guards against a badge earned by accident.
MIN_TITLES_FOR_DOMINANCE = 3
MIN_DOMINANT_SHARE = 0.4

# A profile older than this is recomputed on read. Long enough that a
# feed render almost never pays for it, short enough that a user's
# interests are never badly out of date.
PROFILE_MAX_AGE = timedelta(hours=24)

# Badge tiers per content type: (threshold on the dominant count, i18n key),
# ascending, highest reached wins. A table rather than branching code, so a
# new tier or a new content type is data — the same shape as
# achievements.RANK_TABLES, and the hook an admin-configurable rule set
# would replace later.
BADGE_TABLES: dict[str, list[tuple[int, str]]] = {
    ContentType.FILM.value: [
        (3, "badge.film.1"),
        (10, "badge.film.2"),
        (25, "badge.film.3"),
        (60, "badge.film.4"),
    ],
    ContentType.SERIAL.value: [
        (2, "badge.serial.1"),
        (5, "badge.serial.2"),
        (12, "badge.serial.3"),
        (25, "badge.serial.4"),
    ],
    ContentType.ANIME.value: [
        (3, "badge.anime.1"),
        (10, "badge.anime.2"),
        (25, "badge.anime.3"),
        (50, "badge.anime.4"),
    ],
    ContentType.DRAMA.value: [
        (2, "badge.drama.1"),
        (8, "badge.drama.2"),
        (20, "badge.drama.3"),
    ],
    ContentType.MULTFILM.value: [
        (2, "badge.multfilm.1"),
        (8, "badge.multfilm.2"),
        (20, "badge.multfilm.3"),
    ],
}


class TargetError(Exception):
    """Raised when a targeting value names an interest or badge that does not exist."""


# The interests something can be targeted at: exactly the content types the
# profile can name as dominant. Derived, so adding a ContentType makes it
# targetable without a second list to remember to update.
KNOWN_INTERESTS = frozenset(item.value for item in ContentType)


def known_badge_keys() -> frozenset[str]:
    """Every badge key that can actually be earned."""
    return frozenset(key for table in BADGE_TABLES.values() for _, key in table)


def known_badge_prefixes() -> frozenset[str]:
    """
    The badge *families* — "badge.anime.", "badge.film.", …

    A family is derived by stripping the tier from a real key rather than
    written out, so a family only exists if badges in it do. That is what
    stops "badge.an" or "badge.anime" (no dot) being accepted as a prefix
    that would match by accident.
    """
    return frozenset(key.rsplit(".", 1)[0] + "." for key in known_badge_keys())


def validate_interest_target(value: str | None) -> str:
    cleaned = (value or "").strip()
    if cleaned not in KNOWN_INTERESTS:
        raise TargetError("Unknown interest")
    return cleaned


def validate_badge_target(value: str | None) -> str:
    """
    An exact badge key or a full family prefix. Nothing else.

    Deliberately not a free-form "starts with" match: `badge.a` would
    silently sweep in anime, and a target that quietly means more people
    than the operator intended is the most expensive kind of bug a
    broadcast can have.
    """
    cleaned = (value or "").strip()
    if cleaned in known_badge_keys() or cleaned in known_badge_prefixes():
        return cleaned
    raise TargetError("Unknown badge")


def _badge_ranges(target: str) -> list[tuple[str, int, int | None]]:
    """
    The (dominant_type, min_count, max_count_exclusive) windows a badge target covers.

    Read straight out of `BADGE_TABLES`, which is the only definition of a
    badge in this codebase. An exact key is a half-open window because
    `InterestProfile.badge_key` reports the *highest* tier reached: someone
    on 30 anime titles holds tier 3, not tier 2, so targeting tier 2 must
    stop where tier 3 begins. A family prefix is everything from its
    lowest threshold upwards.
    """
    ranges: list[tuple[str, int, int | None]] = []
    for content_type, table in BADGE_TABLES.items():
        for index, (threshold, key) in enumerate(table):
            if key == target:
                upper = table[index + 1][0] if index + 1 < len(table) else None
                ranges.append((content_type, threshold, upper))
        if table and target == table[0][1].rsplit(".", 1)[0] + ".":
            ranges.append((content_type, table[0][0], None))
    return ranges


@dataclass(frozen=True)
class InterestProfile:
    """One user's interests, as the rest of the app should read them."""

    user_id: int
    dominant_type: str | None
    dominant_count: int
    total_titles: int
    computed_at: datetime | None = None

    @property
    def badge_key(self) -> str | None:
        """
        The i18n key for this user's title, or None.

        A key, never a rendered string — the badge reads in the viewer's
        own language, and storing the sentence would freeze one language
        for everyone.
        """
        if self.dominant_type is None:
            return None
        table = BADGE_TABLES.get(self.dominant_type, [])
        earned: str | None = None
        for threshold, key in table:
            if self.dominant_count >= threshold:
                earned = key
            else:
                break
        return earned


async def _recent_counts(session: AsyncSession, user_id: int) -> dict[str, int]:
    """Distinct titles per content type watched inside the recency window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_WINDOW_DAYS)
    result = await session.execute(
        select(Title.content_type, func.count(func.distinct(WatchHistory.title_id)))
        .join(Title, Title.id == WatchHistory.title_id)
        .where(WatchHistory.user_id == user_id, WatchHistory.last_watched_at >= cutoff)
        .group_by(Title.content_type)
    )
    return {row[0].value: row[1] for row in result.all()}


def _dominant(
    long_term: dict[str, int], recent: dict[str, int], total: int
) -> tuple[str | None, int]:
    """
    Picks the dominant type, or (None, 0) when nothing qualifies.

    Scoring blends long-term and recent; the *share* test that follows
    uses the raw long-term count, because the badge is a claim about how
    much someone has watched, not about how they scored.
    """
    if total < MIN_TITLES_FOR_DOMINANCE:
        return None, 0

    scored = {
        content_type: count + RECENT_WEIGHT * recent.get(content_type, 0)
        for content_type, count in long_term.items()
    }
    if not scored:
        return None, 0

    # Ties broken by name so the same history always yields the same
    # profile — a badge that flickered between two types on every
    # recalculation would look like a bug to the user.
    best = max(sorted(scored), key=lambda key: scored[key])
    count = long_term.get(best, 0)

    if count / total < MIN_DOMINANT_SHARE:
        return None, 0
    return best, count


async def compute_profile(session: AsyncSession, user_id: int) -> InterestProfile:
    """
    Derives the profile from watch history. Pure read — stores nothing.

    Separated from persistence so the rules can be tested against a
    history without a write, and so a caller that only wants the answer
    (an admin inspecting a user, say) never mutates state.
    """
    stats = await watch_stats(session, user_id)
    recent = await _recent_counts(session, user_id)
    dominant, count = _dominant(stats.by_type, recent, stats.total)
    return InterestProfile(
        user_id=user_id,
        dominant_type=dominant,
        dominant_count=count,
        total_titles=stats.total,
    )


async def store_profile(session: AsyncSession, profile: InterestProfile) -> None:
    """
    Upserts the materialised row.

    ON CONFLICT rather than select-then-insert: the cron sweep and a live
    read can recalculate the same user at the same moment, and the primary
    key would turn that into an IntegrityError halfway through a request.
    """
    await session.execute(
        pg_insert(UserInterestProfile)
        .values(
            user_id=profile.user_id,
            dominant_type=profile.dominant_type,
            dominant_count=profile.dominant_count,
            total_titles=profile.total_titles,
            computed_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "dominant_type": profile.dominant_type,
                "dominant_count": profile.dominant_count,
                "total_titles": profile.total_titles,
                "computed_at": datetime.now(timezone.utc),
            },
        )
    )
    await session.flush()


async def get_profile(
    session: AsyncSession, user_id: int, max_age: timedelta = PROFILE_MAX_AGE
) -> InterestProfile:
    """
    This user's profile, recomputing it when missing or stale.

    Always scoped to `user_id`; there is no path here that can return
    another user's row. A fresh row is served as-is, which is what keeps
    feed rendering off the aggregation query.
    """
    stored = await session.get(UserInterestProfile, user_id)
    if stored is not None:
        age = datetime.now(timezone.utc) - stored.computed_at
        if age <= max_age:
            return InterestProfile(
                user_id=stored.user_id,
                dominant_type=stored.dominant_type,
                dominant_count=stored.dominant_count,
                total_titles=stored.total_titles,
                computed_at=stored.computed_at,
            )

    profile = await compute_profile(session, user_id)
    await store_profile(session, profile)
    return profile


def interest_condition(target: str):
    """
    SQL predicate for "this user's dominant interest is `target`".

    Reads the materialised profile column. A user with no profile row, or
    with `dominant_type` NULL because nothing dominates yet, does not match
    — which is correct: they have no established interest, and inventing
    one to fill an audience is the same mistake as handing out a badge for
    two clicks.
    """
    return UserInterestProfile.dominant_type == target


def badge_condition(target: str):
    """
    SQL predicate for "this user holds `target`" — an exact badge or a family.

    Built from `_badge_ranges`, so the database and
    `InterestProfile.badge_key` are two readings of one table rather than
    two definitions of a badge. `tests/test_broadcast_targeting.py` pins
    them together across the whole threshold matrix.
    """
    ranges = _badge_ranges(target)
    if not ranges:
        return false()

    clauses = []
    for content_type, low, high in ranges:
        conditions = [
            UserInterestProfile.dominant_type == content_type,
            UserInterestProfile.dominant_count >= low,
        ]
        if high is not None:
            conditions.append(UserInterestProfile.dominant_count < high)
        clauses.append(and_(*conditions))
    return or_(*clauses)


# How many profiles one targeting pass will bring up to date. Bounded
# because this runs inside the request that creates a broadcast: an
# unbounded pre-pass over a large user table would turn one button press
# into a request that never returns.
TARGETING_REFRESH_LIMIT = 2000


async def refresh_profiles_for_targeting(
    session: AsyncSession, limit: int = TARGETING_REFRESH_LIMIT
) -> int:
    """
    Brings missing and stale profiles up to date before a targeted audience
    is resolved. Returns how many were written.

    This exists because of a contract clash. Phase 9B's `get_profile`
    computes on read, so "no row" means "not looked at yet", not "no
    interests" — but a broadcast cannot call it once per user without
    turning an audience query into thousands of aggregations. So freshness
    is applied **once, in bulk, before** materialisation, and eligibility
    is then read purely from the materialised table.

    The resulting semantics, stated plainly because targeting has to be
    predictable:

    * **missing** — computed here, then treated as any other profile. A
      user with no watch history gets a row saying so and is excluded.
    * **stale** — recomputed here, so the send uses current interests.
    * **fresh** — used as stored; no recomputation.
    * **empty** (`dominant_type` NULL) — never matches any INTEREST or
      BADGE target. Nothing dominates, so there is nothing to target.

    Beyond `limit` users, the remainder keep whatever their stored profile
    says — best-effort freshness, never a partial audience: an unrefreshed
    user is still considered, just on older data.
    """
    profiled = select(UserInterestProfile.user_id)
    missing = (
        await session.execute(
            select(User.id)
            .where(User.is_banned.is_(False), User.id.not_in(profiled))
            .order_by(User.id)
            .limit(limit)
        )
    ).scalars().all()

    for user_id in missing:
        await store_profile(session, await compute_profile(session, user_id))

    remaining = limit - len(missing)
    if remaining > 0:
        return len(missing) + await recalculate_stale_profiles(session, limit=remaining)
    return len(missing)


async def recalculate_stale_profiles(
    session: AsyncSession, limit: int = 500, max_age: timedelta = PROFILE_MAX_AGE
) -> int:
    """
    Refreshes profiles that have aged out. Returns how many were rewritten.

    Batched and bounded so the scheduled run has a predictable cost on a
    catalog with hundreds of thousands of users; the rest are picked up on
    the next run, or lazily by `get_profile` when they are next read.
    """
    cutoff = datetime.now(timezone.utc) - max_age
    stale = (
        await session.execute(
            select(UserInterestProfile.user_id)
            .where(UserInterestProfile.computed_at < cutoff)
            .order_by(UserInterestProfile.computed_at)
            .limit(limit)
        )
    ).scalars().all()

    for user_id in stale:
        await store_profile(session, await compute_profile(session, user_id))
    return len(stale)
