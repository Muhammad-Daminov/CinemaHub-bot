"""
Interest profiles and badges — and above all, that they never leak.

The brief's hard rule: "User A's personalization must NEVER affect User
B." That is not a property you get by writing careful code once; it is a
property you keep by testing it, because the way it breaks is subtle — a
cache keyed on something less than the user, a query missing a filter, a
module-level dict. The isolation section below exists to fail loudly if
any of those appear.

The other rule worth pinning is the guard against accidental titles: a
badge earned from two random watches would make every badge meaningless.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.db.models.content import (
    AudioLanguage,
    ContentType,
    Episode,
    MediaFile,
    Title,
    VideoQuality,
    WatchHistory,
)
from app.db.models.personalization import UserInterestProfile
from app.db.session import get_db_session
from app.main import app
from app.services.personalization import (
    MIN_DOMINANT_SHARE,
    MIN_TITLES_FOR_DOMINANCE,
    InterestProfile,
    compute_profile,
    get_profile,
    recalculate_stale_profiles,
    store_profile,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _watch(session, user, content_type: ContentType, count: int, *, days_ago: int = 0):
    """Records `count` distinct watched titles of one kind for one user."""
    watched_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    for index in range(count):
        title = Title(
            content_type=content_type,
            name=f"{content_type.value}-{user.id}-{index}-{days_ago}",
            is_active=True,
        )
        session.add(title)
        await session.flush()
        episode = Episode(title_id=title.id, season=1, number=1)
        session.add(episode)
        await session.flush()
        session.add(
            MediaFile(
                episode_id=episode.id,
                file_id=f"f{episode.id}",
                language=AudioLanguage.UZ_DUB,
                quality=VideoQuality.HD_720,
            )
        )
        session.add(
            WatchHistory(
                user_id=user.id,
                title_id=title.id,
                episode_id=episode.id,
                watch_count=1,
                last_watched_at=watched_at,
            )
        )
    await session.flush()


# ---------- dominant interest ----------


@pytest.mark.parametrize(
    "content_type,expected_badge_prefix",
    [
        (ContentType.ANIME, "badge.anime."),
        (ContentType.FILM, "badge.film."),
        (ContentType.DRAMA, "badge.drama."),
        (ContentType.SERIAL, "badge.serial."),
        (ContentType.MULTFILM, "badge.multfilm."),
    ],
)
async def test_a_dedicated_viewer_gets_the_matching_badge(
    db_session, content_type, expected_badge_prefix
):
    user = await make_user(db_session, 9700 + hash(content_type.value) % 90)
    await _watch(db_session, user, content_type, 12)

    profile = await compute_profile(db_session, user.id)

    assert profile.dominant_type == content_type.value
    assert profile.badge_key is not None
    assert profile.badge_key.startswith(expected_badge_prefix)


async def test_badges_tier_up_with_more_watching(db_session):
    """The table is ascending and the highest reached wins."""
    user = await make_user(db_session, 9801)
    await _watch(db_session, user, ContentType.ANIME, 3)
    first = (await compute_profile(db_session, user.id)).badge_key

    await _watch(db_session, user, ContentType.ANIME, 25)
    later = (await compute_profile(db_session, user.id)).badge_key

    assert first == "badge.anime.1"
    assert later == "badge.anime.3"


async def test_a_mixed_viewer_has_no_dominant_type(db_session):
    """Nothing dominates, so no badge — an even split is not an interest."""
    user = await make_user(db_session, 9802)
    await _watch(db_session, user, ContentType.FILM, 5)
    await _watch(db_session, user, ContentType.ANIME, 5)
    await _watch(db_session, user, ContentType.DRAMA, 5)

    profile = await compute_profile(db_session, user.id)

    assert profile.dominant_type is None
    assert profile.badge_key is None
    assert profile.total_titles == 15


async def test_one_random_watch_earns_nothing(db_session):
    """The guard the brief asks for, stated directly."""
    user = await make_user(db_session, 9803)
    await _watch(db_session, user, ContentType.ANIME, 1)

    profile = await compute_profile(db_session, user.id)

    assert profile.total_titles < MIN_TITLES_FOR_DOMINANCE
    assert profile.dominant_type is None
    assert profile.badge_key is None


async def test_a_minority_interest_does_not_dominate(db_session):
    """Below the share threshold, the most-watched type still isn't dominant."""
    user = await make_user(db_session, 9804)
    await _watch(db_session, user, ContentType.FILM, 3)
    await _watch(db_session, user, ContentType.ANIME, 3)
    await _watch(db_session, user, ContentType.DRAMA, 2)
    await _watch(db_session, user, ContentType.SERIAL, 2)

    profile = await compute_profile(db_session, user.id)
    assert 3 / 10 < MIN_DOMINANT_SHARE, "precondition: the leader is under the threshold"
    assert profile.dominant_type is None


async def test_exactly_at_the_share_threshold_qualifies(db_session):
    """
    The boundary, stated rather than left to chance: "at least 40% of what
    they watch" includes 40%. Written as its own test because a threshold
    with an ambiguous edge is where a rule quietly changes meaning.
    """
    user = await make_user(db_session, 9808)
    await _watch(db_session, user, ContentType.FILM, 4)
    await _watch(db_session, user, ContentType.ANIME, 3)
    await _watch(db_session, user, ContentType.DRAMA, 3)

    profile = await compute_profile(db_session, user.id)
    assert 4 / 10 == MIN_DOMINANT_SHARE
    assert profile.dominant_type == ContentType.FILM.value


async def test_a_user_with_no_history_has_an_empty_profile(db_session):
    user = await make_user(db_session, 9805)
    profile = await compute_profile(db_session, user.id)

    assert profile == InterestProfile(
        user_id=user.id, dominant_type=None, dominant_count=0, total_titles=0
    )
    assert profile.badge_key is None


async def test_recent_watching_shifts_the_profile(db_session):
    """
    Someone moving from films to anime is noticed without erasing history:
    recent watches count for the long-term tally *and* the recency bonus.
    """
    user = await make_user(db_session, 9806)
    await _watch(db_session, user, ContentType.FILM, 6, days_ago=300)
    await _watch(db_session, user, ContentType.ANIME, 6, days_ago=2)

    profile = await compute_profile(db_session, user.id)
    assert profile.dominant_type == ContentType.ANIME.value


async def test_the_same_history_always_yields_the_same_profile(db_session):
    """A badge that flickered between two types would read as a bug."""
    user = await make_user(db_session, 9807)
    await _watch(db_session, user, ContentType.FILM, 5)
    await _watch(db_session, user, ContentType.ANIME, 5)

    results = {(await compute_profile(db_session, user.id)).dominant_type for _ in range(5)}
    assert len(results) == 1


# ---------- isolation: the rule that matters most ----------


async def test_two_users_hold_entirely_separate_profiles(db_session):
    anime_fan = await make_user(db_session, 9810)
    drama_fan = await make_user(db_session, 9811)
    await _watch(db_session, anime_fan, ContentType.ANIME, 12)
    await _watch(db_session, drama_fan, ContentType.DRAMA, 12)

    first = await get_profile(db_session, anime_fan.id)
    second = await get_profile(db_session, drama_fan.id)

    assert first.dominant_type == ContentType.ANIME.value
    assert second.dominant_type == ContentType.DRAMA.value
    assert first.badge_key != second.badge_key


async def test_one_users_activity_never_changes_anothers_profile(db_session):
    """
    The explicit rule: User A's personalization must never affect User B.
    B's profile is captured, A then watches heavily, and B is re-read.
    """
    a = await make_user(db_session, 9812)
    b = await make_user(db_session, 9813)
    await _watch(db_session, b, ContentType.FILM, 8)

    before = await get_profile(db_session, b.id)

    await _watch(db_session, a, ContentType.ANIME, 40)
    await get_profile(db_session, a.id)

    after = await compute_profile(db_session, b.id)
    assert after.dominant_type == before.dominant_type == ContentType.FILM.value
    assert after.total_titles == before.total_titles == 8


async def test_a_user_with_no_history_is_unaffected_by_a_heavy_user(db_session):
    """The empty case leaks most easily — a missing filter shows up here."""
    heavy = await make_user(db_session, 9814)
    fresh = await make_user(db_session, 9815)
    await _watch(db_session, heavy, ContentType.ANIME, 30)
    await get_profile(db_session, heavy.id)

    profile = await get_profile(db_session, fresh.id)
    assert profile.total_titles == 0
    assert profile.dominant_type is None
    assert profile.badge_key is None


async def test_each_user_gets_exactly_one_stored_row(db_session):
    users = [await make_user(db_session, 9820 + index) for index in range(4)]
    for user in users:
        await _watch(db_session, user, ContentType.FILM, 4)
        await get_profile(db_session, user.id)
        await get_profile(db_session, user.id)  # a second read must not duplicate

    assert await count_rows(db_session, UserInterestProfile) == len(users)
    for user in users:
        assert await count_rows(db_session, UserInterestProfile, user_id=user.id) == 1


async def test_recalculating_everyone_keeps_profiles_distinct(db_session):
    """The sweep touches many users in one pass — a shared accumulator would show here."""
    anime_fan = await make_user(db_session, 9830)
    film_fan = await make_user(db_session, 9831)
    await _watch(db_session, anime_fan, ContentType.ANIME, 10)
    await _watch(db_session, film_fan, ContentType.FILM, 10)
    for user in (anime_fan, film_fan):
        stale = await compute_profile(db_session, user.id)
        await store_profile(db_session, stale)
        row = await db_session.get(UserInterestProfile, user.id)
        row.computed_at = datetime.now(timezone.utc) - timedelta(days=3)
    await db_session.flush()

    assert await recalculate_stale_profiles(db_session) == 2

    assert (await db_session.get(UserInterestProfile, anime_fan.id, populate_existing=True)).dominant_type == ContentType.ANIME.value
    assert (await db_session.get(UserInterestProfile, film_fan.id, populate_existing=True)).dominant_type == ContentType.FILM.value


# ---------- storage and freshness ----------


async def test_a_fresh_profile_is_served_from_storage(db_session):
    user = await make_user(db_session, 9840)
    await _watch(db_session, user, ContentType.FILM, 5)
    first = await get_profile(db_session, user.id)

    # More watching, but the stored profile is still fresh, so the feed
    # keeps the cached answer rather than paying for the aggregation.
    await _watch(db_session, user, ContentType.ANIME, 20)
    second = await get_profile(db_session, user.id)

    assert second.dominant_type == first.dominant_type == ContentType.FILM.value


async def test_a_stale_profile_is_recomputed_on_read(db_session):
    user = await make_user(db_session, 9841)
    await _watch(db_session, user, ContentType.FILM, 5)
    await get_profile(db_session, user.id)

    row = await db_session.get(UserInterestProfile, user.id)
    row.computed_at = datetime.now(timezone.utc) - timedelta(days=3)
    await db_session.flush()
    await _watch(db_session, user, ContentType.ANIME, 30)

    refreshed = await get_profile(db_session, user.id)
    assert refreshed.dominant_type == ContentType.ANIME.value


async def test_storing_twice_updates_rather_than_duplicating(db_session):
    """The upsert exists because the sweep and a live read can collide."""
    user = await make_user(db_session, 9842)
    await store_profile(db_session, InterestProfile(user.id, "film", 3, 5))
    await store_profile(db_session, InterestProfile(user.id, "anime", 9, 12))

    assert await count_rows(db_session, UserInterestProfile, user_id=user.id) == 1
    row = await db_session.get(UserInterestProfile, user.id, populate_existing=True)
    assert row.dominant_type == "anime"
    assert row.dominant_count == 9


# ---------- the API is scoped to the caller ----------


@pytest.fixture
def as_user(db_session):
    def _install(user) -> AsyncClient:
        async def override_session():
            yield db_session

        async def override_user():
            return user

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _install
    app.dependency_overrides.clear()


async def test_the_endpoint_returns_the_callers_own_profile(db_session, as_user):
    user = await make_user(db_session, 9850)
    await _watch(db_session, user, ContentType.ANIME, 12)
    await db_session.commit()

    async with as_user(user) as client:
        payload = (await client.get("/api/auth/me/personalization")).json()

    assert payload["dominant_type"] == ContentType.ANIME.value
    assert payload["badge_key"].startswith("badge.anime.")


async def test_two_callers_receive_their_own_answers(db_session, as_user):
    """End to end: the same endpoint, two users, no bleed."""
    anime_fan = await make_user(db_session, 9851)
    film_fan = await make_user(db_session, 9852)
    await _watch(db_session, anime_fan, ContentType.ANIME, 12)
    await _watch(db_session, film_fan, ContentType.FILM, 12)
    await db_session.commit()

    async with as_user(anime_fan) as client:
        first = (await client.get("/api/auth/me/personalization")).json()
    async with as_user(film_fan) as client:
        second = (await client.get("/api/auth/me/personalization")).json()

    assert first["dominant_type"] == ContentType.ANIME.value
    assert second["dominant_type"] == ContentType.FILM.value


async def test_the_endpoint_takes_no_user_id(db_session, as_user):
    """
    There is no parameter to point at someone else. A `user_id` here would
    be an open invitation, so the route derives identity from the verified
    caller and ignores anything in the query string.
    """
    a = await make_user(db_session, 9853)
    b = await make_user(db_session, 9854)
    await _watch(db_session, b, ContentType.DRAMA, 12)
    await db_session.commit()

    async with as_user(a) as client:
        payload = (await client.get(f"/api/auth/me/personalization?user_id={b.id}")).json()

    assert payload["total_titles"] == 0, "the query parameter must be ignored"
    assert payload["dominant_type"] is None


async def test_an_unauthenticated_caller_is_refused(db_session):
    async def override_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/auth/me/personalization")).status_code == 422
    app.dependency_overrides.clear()
