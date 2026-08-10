"""
Interest and badge targeting: who a broadcast reaches, and who decides.

The property under test is that **the server alone decides the audience**.
An admin names a segment; the people in it are derived from materialised
profile state. Nothing in a request can name, add or remove a recipient,
and the tests attack that at both the service layer and over HTTP.

The second property is that the recipient set **freezes at
materialisation**. Interests change, badges are earned, subscriptions
lapse — none of it may alter a send already underway, because a broadcast
that quietly grows its audience during a resume is a broadcast nobody can
account for afterwards.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

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
from app.db.models.system import (
    Broadcast,
    BroadcastAudience,
    BroadcastMessage,
    BroadcastStatus,
    DeliveryStatus,
)
from app.db.models.user import AdminPermission, Subscription, UserRole
from app.db.session import get_db_session
from app.main import app
from app.services.broadcast import (
    BroadcastError,
    audience_size,
    create_broadcast,
    estimate_recipients,
    materialise_recipients,
    run_broadcast,
    validate_target,
)
from app.services.personalization import (
    BADGE_TABLES,
    InterestProfile,
    TargetError,
    badge_condition,
    known_badge_keys,
    known_badge_prefixes,
    refresh_profiles_for_targeting,
    validate_badge_target,
    validate_interest_target,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _profile(session, user, dominant: str | None, count: int, *, total: int | None = None,
                   age_hours: float = 0.0):
    """
    Writes a materialised profile directly.

    Targeting reads this table and only this table, so setting it straight
    is the honest way to test eligibility — deriving it from watch history
    would be testing 9B's arithmetic a second time.
    """
    session.add(
        UserInterestProfile(
            user_id=user.id,
            dominant_type=dominant,
            dominant_count=count,
            total_titles=total if total is not None else max(count, 0),
            computed_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        )
    )
    await session.flush()


async def _watch(session, user, content_type: ContentType, count: int):
    """Real watch history, for the tests that exercise the freshness pass."""
    for index in range(count):
        title = Title(
            content_type=content_type,
            name=f"{content_type.value}-{user.id}-{index}",
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
            WatchHistory(user_id=user.id, title_id=title.id, episode_id=episode.id, watch_count=1)
        )
    await session.flush()


async def _recipient_ids(session, broadcast_id: int) -> set[int]:
    rows = await session.execute(
        select(BroadcastMessage.user_id).where(BroadcastMessage.broadcast_id == broadcast_id)
    )
    return set(rows.scalars())


# ---------- target validation ----------


def test_the_badge_allowlist_is_derived_from_the_badge_tables():
    """
    One definition of a badge, not two.

    If this ever fails it means targeting has grown its own idea of what
    badges exist — the exact duplication this phase was built to avoid.
    """
    expected = {key for table in BADGE_TABLES.values() for _, key in table}
    assert known_badge_keys() == expected
    assert known_badge_prefixes() == {key.rsplit(".", 1)[0] + "." for key in expected}


def test_a_known_interest_is_accepted_and_anything_else_is_not():
    assert validate_interest_target("anime") == "anime"
    for bad in ["", "  ", "anim", "ANIME", "films", "anime; drop table chp_users"]:
        with pytest.raises(TargetError):
            validate_interest_target(bad)


def test_a_known_badge_or_family_is_accepted_and_anything_else_is_not():
    assert validate_badge_target("badge.anime.1") == "badge.anime.1"
    assert validate_badge_target("badge.anime.") == "badge.anime."
    for bad in [
        "badge.anime",  # a family without its dot would match by accident
        "badge.a",
        "badge.",
        "badge.anime.9",
        "badge.unicorn.1",
        "",
        "%",
        "badge.anime.1' OR '1'='1",
    ]:
        with pytest.raises(TargetError):
            validate_badge_target(bad)


def test_a_targeted_audience_without_a_target_is_refused():
    """The dangerous direction: no target must never quietly mean everyone."""
    for audience in (BroadcastAudience.INTEREST, BroadcastAudience.BADGE):
        for missing in (None, "", "   "):
            with pytest.raises(BroadcastError):
                validate_target(audience, missing)


def test_an_untargeted_audience_with_a_stray_target_is_refused():
    for audience in (BroadcastAudience.ALL, BroadcastAudience.PREMIUM, BroadcastAudience.FREE):
        assert validate_target(audience, None) is None
        with pytest.raises(BroadcastError):
            validate_target(audience, "anime")


async def test_creating_a_broadcast_enforces_the_same_rules(db_session):
    actor = await make_user(db_session, 9401)
    with pytest.raises(BroadcastError):
        await create_broadcast(db_session, actor, "hi", BroadcastAudience.INTEREST)
    with pytest.raises(BroadcastError):
        await create_broadcast(
            db_session, actor, "hi", BroadcastAudience.BADGE, target_value="badge.nope.1"
        )
    with pytest.raises(BroadcastError):
        await create_broadcast(
            db_session, actor, "hi", BroadcastAudience.ALL, target_value="anime"
        )

    good = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
    )
    assert good.target_value == "anime"


# ---------- recipient selection ----------


async def test_interest_targeting_selects_exactly_the_matching_profiles(db_session):
    actor = await make_user(db_session, 9410)
    anime = await make_user(db_session, 9411)
    film = await make_user(db_session, 9412)
    undecided = await make_user(db_session, 9413)
    unprofiled = await make_user(db_session, 9414)

    await _profile(db_session, actor, None, 0)
    await _profile(db_session, anime, "anime", 12)
    await _profile(db_session, film, "film", 12)
    await _profile(db_session, undecided, None, 0)  # nothing dominates

    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
    )
    await materialise_recipients(db_session, broadcast)

    recipients = await _recipient_ids(db_session, broadcast.id)
    assert recipients == {anime.id}
    assert film.id not in recipients
    assert undecided.id not in recipients
    assert unprofiled.id not in recipients


async def test_badge_targeting_matches_the_exact_tier_only(db_session):
    """
    A tier is a window, not a floor: a user on 30 anime titles holds tier 3
    and must not also count as tier 2.
    """
    actor = await make_user(db_session, 9420)
    tier2 = await make_user(db_session, 9421)  # badge.anime.2 → [10, 25)
    tier3 = await make_user(db_session, 9422)  # badge.anime.3 → [25, 50)
    tier1 = await make_user(db_session, 9423)

    await _profile(db_session, tier1, "anime", 3)
    await _profile(db_session, tier2, "anime", 12)
    await _profile(db_session, tier3, "anime", 30)

    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.BADGE, target_value="badge.anime.2"
    )
    await materialise_recipients(db_session, broadcast)
    assert await _recipient_ids(db_session, broadcast.id) == {tier2.id}


async def test_a_badge_family_matches_every_tier_of_that_family_and_no_other(db_session):
    actor = await make_user(db_session, 9430)
    low = await make_user(db_session, 9431)
    high = await make_user(db_session, 9432)
    other = await make_user(db_session, 9433)
    below = await make_user(db_session, 9434)

    await _profile(db_session, low, "anime", 3)
    await _profile(db_session, high, "anime", 80)
    await _profile(db_session, other, "film", 80)
    # Dominant anime, but below the first threshold — no badge at all.
    await _profile(db_session, below, "anime", 1, total=3)

    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.BADGE, target_value="badge.anime."
    )
    await materialise_recipients(db_session, broadcast)

    recipients = await _recipient_ids(db_session, broadcast.id)
    assert recipients == {low.id, high.id}
    assert other.id not in recipients
    assert below.id not in recipients


async def test_the_sql_predicate_agrees_with_the_python_badge_property(db_session):
    """
    The database's answer and `InterestProfile.badge_key` are two readings
    of one table. Checked across the whole threshold matrix, including the
    boundaries either side of every tier, because a badge that means one
    thing on a feed and another in a broadcast is worse than no badge.
    """
    counts = sorted({0, 1, 2}
                    | {n for table in BADGE_TABLES.values()
                       for threshold, _ in table
                       for n in (threshold - 1, threshold, threshold + 1)}
                    | {200})

    users: dict[tuple[str, int], int] = {}
    telegram_id = 94400
    for content_type in BADGE_TABLES:
        for count in counts:
            telegram_id += 1
            user = await make_user(db_session, telegram_id)
            await _profile(db_session, user, content_type, count, total=max(count, 3))
            users[(content_type, count)] = user.id

    for target in sorted(known_badge_keys() | known_badge_prefixes()):
        expected = set()
        for (content_type, count), user_id in users.items():
            key = InterestProfile(
                user_id=user_id,
                dominant_type=content_type,
                dominant_count=count,
                total_titles=max(count, 3),
            ).badge_key
            holds = key == target or (target.endswith(".") and key is not None and key.startswith(target))
            if holds:
                expected.add(user_id)

        rows = await db_session.execute(
            select(UserInterestProfile.user_id).where(badge_condition(target))
        )
        actual = {user_id for user_id in rows.scalars() if user_id in set(users.values())}
        assert actual == expected, f"{target} disagreed"


async def test_banned_users_are_excluded_from_targeted_audiences(db_session):
    actor = await make_user(db_session, 9450)
    allowed = await make_user(db_session, 9451)
    banned = await make_user(db_session, 9452)
    banned.is_banned = True
    await _profile(db_session, allowed, "anime", 12)
    await _profile(db_session, banned, "anime", 12)
    await db_session.flush()

    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
    )
    await materialise_recipients(db_session, broadcast)
    assert await _recipient_ids(db_session, broadcast.id) == {allowed.id}


async def test_interest_targeting_ignores_subscription_state(db_session):
    """
    INTEREST is an alternative segment, not an extra condition on PREMIUM.
    Both a paying and a free anime watcher receive it — inventing a hidden
    AND would silently halve an audience the operator thought they chose.
    """
    actor = await make_user(db_session, 9460)
    paying = await make_user(db_session, 9461)
    free = await make_user(db_session, 9462)
    now = datetime.now(timezone.utc)
    db_session.add(
        Subscription(
            user_id=paying.id, started_at=now - timedelta(days=1), expires_at=now + timedelta(days=30)
        )
    )
    await _profile(db_session, paying, "anime", 12)
    await _profile(db_session, free, "anime", 12)
    await db_session.flush()

    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
    )
    await materialise_recipients(db_session, broadcast)
    assert await _recipient_ids(db_session, broadcast.id) == {paying.id, free.id}


# ---------- profile freshness ----------


async def test_a_missing_profile_is_computed_before_targeting(db_session):
    """
    No row means "not looked at yet", not "not interested". The bulk pass
    resolves that before the audience is decided.
    """
    actor = await make_user(db_session, 9470)
    watcher = await make_user(db_session, 9471)
    await _watch(db_session, watcher, ContentType.ANIME, 5)
    assert await count_rows(db_session, UserInterestProfile, user_id=watcher.id) == 0

    await refresh_profiles_for_targeting(db_session)

    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
    )
    await materialise_recipients(db_session, broadcast)
    assert watcher.id in await _recipient_ids(db_session, broadcast.id)


async def test_a_stale_profile_is_refreshed_before_targeting(db_session):
    switcher = await make_user(db_session, 9481)
    # Stored a day and a half ago saying film; has since watched only anime.
    await _profile(db_session, switcher, "film", 9, age_hours=36)
    await _watch(db_session, switcher, ContentType.ANIME, 6)

    await refresh_profiles_for_targeting(db_session)

    refreshed = await db_session.get(UserInterestProfile, switcher.id)
    assert refreshed.dominant_type == "anime"


async def test_a_fresh_profile_is_used_as_stored(db_session):
    """No recomputation: the stored answer wins, history notwithstanding."""
    actor = await make_user(db_session, 9490)
    user = await make_user(db_session, 9491)
    await _profile(db_session, user, "anime", 12, age_hours=1)
    await _watch(db_session, user, ContentType.FILM, 8)

    await refresh_profiles_for_targeting(db_session)

    unchanged = await db_session.get(UserInterestProfile, user.id)
    assert unchanged.dominant_type == "anime"

    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
    )
    await materialise_recipients(db_session, broadcast)
    assert user.id in await _recipient_ids(db_session, broadcast.id)


async def test_an_empty_profile_matches_no_target(db_session):
    """Nothing dominates, so there is nothing to target — not a wildcard."""
    actor = await make_user(db_session, 9495)
    blank = await make_user(db_session, 9496)
    await _profile(db_session, blank, None, 0, total=2)

    for audience, target in (
        (BroadcastAudience.INTEREST, "anime"),
        (BroadcastAudience.BADGE, "badge.anime."),
    ):
        broadcast = await create_broadcast(db_session, actor, "hi", audience, target_value=target)
        await materialise_recipients(db_session, broadcast)
        assert blank.id not in await _recipient_ids(db_session, broadcast.id)


# ---------- estimate ----------


async def test_the_estimate_equals_what_materialisation_creates(db_session):
    actor = await make_user(db_session, 9500)
    for index in range(4):
        user = await make_user(db_session, 9510 + index)
        await _profile(db_session, user, "anime" if index < 3 else "film", 12)

    estimate = await estimate_recipients(db_session, BroadcastAudience.INTEREST, "anime")
    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
    )
    created = await materialise_recipients(db_session, broadcast)

    assert estimate == created == 3
    assert estimate == await audience_size(db_session, BroadcastAudience.INTEREST, "anime")


async def test_the_estimate_refuses_an_invalid_target(db_session):
    for audience, target in (
        (BroadcastAudience.INTEREST, None),
        (BroadcastAudience.INTEREST, "nonsense"),
        (BroadcastAudience.BADGE, "badge.nope."),
        (BroadcastAudience.ALL, "anime"),
    ):
        with pytest.raises(BroadcastError):
            await estimate_recipients(db_session, audience, target)


# ---------- the frozen recipient set ----------


async def test_a_profile_change_after_materialisation_changes_nothing(db_session):
    """
    The set freezes when it is materialised. Someone who becomes an anime
    watcher afterwards must not join a send already addressed.
    """
    actor = await make_user(db_session, 9520)
    inside = await make_user(db_session, 9521)
    outside = await make_user(db_session, 9522)
    await _profile(db_session, inside, "anime", 12)
    await _profile(db_session, outside, "film", 12)

    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
    )
    await materialise_recipients(db_session, broadcast)
    frozen = await _recipient_ids(db_session, broadcast.id)

    # Everything the audience is derived from now changes.
    (await db_session.get(UserInterestProfile, outside.id)).dominant_type = "anime"
    (await db_session.get(UserInterestProfile, inside.id)).dominant_type = "film"
    inside.is_banned = True
    latecomer = await make_user(db_session, 9523)
    await _profile(db_session, latecomer, "anime", 40)
    await db_session.flush()

    # A resume re-runs materialisation; it must add nobody.
    assert await materialise_recipients(db_session, broadcast) == 0
    assert await _recipient_ids(db_session, broadcast.id) == frozen == {inside.id}


async def test_a_subscription_change_after_materialisation_changes_nothing(db_session):
    actor = await make_user(db_session, 9530)
    user = await make_user(db_session, 9531)
    broadcast = await create_broadcast(db_session, actor, "hi", BroadcastAudience.FREE)
    await materialise_recipients(db_session, broadcast)
    frozen = await _recipient_ids(db_session, broadcast.id)

    now = datetime.now(timezone.utc)
    db_session.add(
        Subscription(
            user_id=user.id, started_at=now - timedelta(days=1), expires_at=now + timedelta(days=30)
        )
    )
    await db_session.flush()

    assert await materialise_recipients(db_session, broadcast) == 0
    assert await _recipient_ids(db_session, broadcast.id) == frozen


async def test_resume_delivers_the_frozen_set_and_never_re_targets(db_session, db_factory, monkeypatch):
    """
    A full send through `run_broadcast`, with the audience changing while
    it is in flight. Only the materialised rows may be delivered to.
    """
    class FakeBot:
        def __init__(self):
            self.sent: list[int] = []

        async def send_message(self, chat_id, text, *args, **kwargs):
            self.sent.append(chat_id)

    monkeypatch.setattr("app.services.broadcast.SEND_INTERVAL_SECONDS", 0)

    actor = await make_user(db_session, 9540)
    inside = await make_user(db_session, 9541)
    await _profile(db_session, inside, "anime", 12, age_hours=0)
    await _profile(db_session, actor, "film", 12, age_hours=0)
    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
    )
    await db_session.commit()

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast.id)

    assert bot.sent == [inside.telegram_id]
    async with db_factory() as check:
        row = await check.get(Broadcast, broadcast.id)
        assert row.status == BroadcastStatus.COMPLETED
        # Counted from the rows actually created, not a second audience query.
        assert row.total_recipients == 1
        assert row.sent_count == 1


# ---------- isolation ----------


async def test_one_users_profile_cannot_place_another_in_an_audience(db_session):
    """A → B → A: reversing the two profiles reverses the audience exactly."""
    actor = await make_user(db_session, 9550)
    a = await make_user(db_session, 9551)
    b = await make_user(db_session, 9552)
    await _profile(db_session, a, "anime", 12)
    await _profile(db_session, b, "film", 12)

    async def anime_audience(tag: str) -> set[int]:
        broadcast = await create_broadcast(
            db_session, actor, tag, BroadcastAudience.INTEREST, target_value="anime"
        )
        await materialise_recipients(db_session, broadcast)
        return await _recipient_ids(db_session, broadcast.id)

    assert await anime_audience("first") == {a.id}

    (await db_session.get(UserInterestProfile, a.id)).dominant_type = "film"
    (await db_session.get(UserInterestProfile, b.id)).dominant_type = "anime"
    await db_session.flush()
    assert await anime_audience("second") == {b.id}

    (await db_session.get(UserInterestProfile, a.id)).dominant_type = "anime"
    (await db_session.get(UserInterestProfile, b.id)).dominant_type = "film"
    await db_session.flush()
    assert await anime_audience("third") == {a.id}


async def test_two_targeted_broadcasts_do_not_bleed_into_each_other(db_session):
    actor = await make_user(db_session, 9560)
    anime = await make_user(db_session, 9561)
    film = await make_user(db_session, 9562)
    await _profile(db_session, anime, "anime", 12)
    await _profile(db_session, film, "film", 12)

    first = await create_broadcast(
        db_session, actor, "a", BroadcastAudience.INTEREST, target_value="anime"
    )
    second = await create_broadcast(
        db_session, actor, "f", BroadcastAudience.INTEREST, target_value="film"
    )
    await materialise_recipients(db_session, first)
    await materialise_recipients(db_session, second)

    assert await _recipient_ids(db_session, first.id) == {anime.id}
    assert await _recipient_ids(db_session, second.id) == {film.id}


async def test_concurrent_materialisation_creates_no_duplicate_recipients(db_factory):
    """
    Two workers racing on the same targeted broadcast. The unique
    constraint, not a check, is what makes the result correct.
    """
    import asyncio

    async with db_factory() as setup:
        actor = await make_user(setup, 9570)
        for index in range(5):
            user = await make_user(setup, 9580 + index)
            await _profile(setup, user, "anime", 12)
        broadcast = await create_broadcast(
            setup, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
        )
        broadcast_id = broadcast.id
        await setup.commit()

    async def worker():
        async with db_factory() as session:
            row = await session.get(Broadcast, broadcast_id)
            created = await materialise_recipients(session, row)
            await session.commit()
            return created

    results = await asyncio.gather(worker(), worker(), return_exceptions=True)
    assert any(result == 5 for result in results if not isinstance(result, Exception))

    async with db_factory() as check:
        assert await count_rows(check, BroadcastMessage, broadcast_id=broadcast_id) == 5


async def test_repeated_materialisation_is_a_no_op(db_session):
    actor = await make_user(db_session, 9590)
    user = await make_user(db_session, 9591)
    await _profile(db_session, user, "anime", 12)
    broadcast = await create_broadcast(
        db_session, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
    )

    assert await materialise_recipients(db_session, broadcast) == 1
    assert await materialise_recipients(db_session, broadcast) == 0
    assert await materialise_recipients(db_session, broadcast) == 0
    assert await count_rows(db_session, BroadcastMessage, broadcast_id=broadcast.id) == 1


# ---------- HTTP boundary ----------


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


@pytest.fixture
def no_background_send(monkeypatch):
    """
    Neutralises the background send.

    Creating a broadcast over HTTP schedules a task holding the
    *application's* session factory — `settings.DATABASE_URL`, production.
    Every targeting test that goes through the route must install this or
    the suite reaches across at the real database.
    """
    from app.api import admin as admin_module

    async def no_send(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_module, "run_broadcast", no_send)


async def _admin(session, telegram_id: int):
    admin = await make_user(session, telegram_id)
    admin.role = UserRole.ADMIN
    session.add(AdminPermission(user_id=admin.id, permission="manage_notifications"))
    await session.commit()
    return admin


async def test_a_supplied_user_id_cannot_alter_the_audience(
    db_session, as_user, no_background_send
):
    """
    The spoof attempt from the brief. `extra="forbid"` rejects it outright
    rather than ignoring it, so an attempt to address a broadcast at chosen
    people fails loudly instead of appearing to work.
    """
    admin = await _admin(db_session, 9600)
    anime = await make_user(db_session, 9601)
    victim = await make_user(db_session, 9602)
    await _profile(db_session, anime, "anime", 12)
    await _profile(db_session, victim, "film", 12)
    await db_session.commit()

    async with as_user(admin) as client:
        rejected = await client.post(
            "/api/admin/broadcasts",
            json={
                "message": "hi",
                "audience": "interest",
                "target_value": "anime",
                "user_id": victim.id,
                "user_ids": [victim.id],
                "recipient_ids": [victim.id],
            },
        )
        assert rejected.status_code == 422

        accepted = await client.post(
            "/api/admin/broadcasts",
            json={"message": "hi", "audience": "interest", "target_value": "anime"},
        )

    assert accepted.status_code == 200
    body = accepted.json()
    assert body["audience"] == "interest"
    assert body["target_value"] == "anime"

    await materialise_recipients(db_session, await db_session.get(Broadcast, body["id"]))
    assert await _recipient_ids(db_session, body["id"]) == {anime.id}


async def test_the_http_layer_refuses_unknown_and_malformed_targets(
    db_session, as_user, no_background_send
):
    admin = await _admin(db_session, 9610)

    async with as_user(admin) as client:
        for payload in (
            {"message": "hi", "audience": "interest"},
            {"message": "hi", "audience": "interest", "target_value": "unicorns"},
            {"message": "hi", "audience": "badge", "target_value": "badge.anime"},
            {"message": "hi", "audience": "badge", "target_value": "' OR 1=1 --"},
            {"message": "hi", "audience": "all", "target_value": "anime"},
            {"message": "hi", "audience": "everyone"},
        ):
            response = await client.post("/api/admin/broadcasts", json=payload)
            assert response.status_code == 422, payload


async def test_the_estimate_route_returns_a_count_and_no_identities(db_session, as_user):
    admin = await _admin(db_session, 9620)
    for index in range(3):
        user = await make_user(db_session, 9630 + index)
        await _profile(db_session, user, "anime", 12)
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.get(
            "/api/admin/broadcasts/estimate",
            params={"audience": "interest", "target_value": "anime"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "audience": "interest",
        "target_value": "anime",
        "estimated_recipients": 3,
    }
    # No identity of any kind travels in the response.
    serialised = response.text
    for user_id in await _recipient_ids_of_profiles(db_session):
        assert f'"{user_id}"' not in serialised


async def _recipient_ids_of_profiles(session) -> set[int]:
    rows = await session.execute(select(UserInterestProfile.user_id))
    return set(rows.scalars())


async def test_the_estimate_route_is_permission_gated(db_session, as_user):
    ordinary = await make_user(db_session, 9640)
    await db_session.commit()

    async with as_user(ordinary) as client:
        assert (
            await client.get(
                "/api/admin/broadcasts/estimate",
                params={"audience": "interest", "target_value": "anime"},
            )
        ).status_code == 403
        assert (await client.get("/api/admin/broadcasts/targets")).status_code == 403


async def test_the_target_vocabulary_route_matches_the_validators(db_session, as_user):
    admin = await _admin(db_session, 9650)

    async with as_user(admin) as client:
        response = await client.get("/api/admin/broadcasts/targets")

    assert response.status_code == 200
    body = response.json()
    assert set(body["badges"]) == known_badge_keys()
    assert set(body["badge_families"]) == known_badge_prefixes()
    for interest in body["interests"]:
        assert validate_interest_target(interest) == interest


async def test_the_untargeted_audience_sizes_route_omits_targeted_segments(db_session, as_user):
    """
    A size for INTEREST with no target would have to mean something, and
    every available meaning misleads.
    """
    admin = await _admin(db_session, 9660)

    async with as_user(admin) as client:
        response = await client.get("/api/admin/broadcasts/audience")

    assert response.status_code == 200
    assert {row["audience"] for row in response.json()} == {"all", "premium", "free"}


async def test_no_route_exposes_targeting_internals(db_session):
    """The delivery rows stay internal, targeting or not."""
    schema = str(app.openapi()).lower()
    assert "broadcastmessage" not in schema
    assert "chp_broadcast_messages" not in schema
    assert "user_ids" not in schema
    assert "recipient_ids" not in schema


# ---------- regressions ----------


async def test_untargeted_audiences_are_unchanged(db_session):
    actor = await make_user(db_session, 9670)
    free = await make_user(db_session, 9671)
    paying = await make_user(db_session, 9672)
    now = datetime.now(timezone.utc)
    db_session.add(
        Subscription(
            user_id=paying.id, started_at=now - timedelta(days=1), expires_at=now + timedelta(days=30)
        )
    )
    await db_session.flush()

    expected = {
        BroadcastAudience.ALL: {actor.id, free.id, paying.id},
        BroadcastAudience.PREMIUM: {paying.id},
        BroadcastAudience.FREE: {actor.id, free.id},
    }
    for audience, users in expected.items():
        broadcast = await create_broadcast(db_session, actor, audience.value, audience)
        assert broadcast.target_value is None
        await materialise_recipients(db_session, broadcast)
        assert await _recipient_ids(db_session, broadcast.id) == users
        assert await audience_size(db_session, audience) == len(users)


async def test_an_untargeted_broadcast_pays_no_freshness_cost(db_session, monkeypatch, db_factory):
    """
    ALL/PREMIUM/FREE must cost exactly what they did before this phase — no
    profile pass, no extra queries.
    """
    called = False

    async def spy(*args, **kwargs):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("app.services.broadcast.refresh_profiles_for_targeting", spy)
    monkeypatch.setattr("app.services.broadcast.SEND_INTERVAL_SECONDS", 0)

    class FakeBot:
        async def send_message(self, chat_id, text, *args, **kwargs):
            return None

    async with db_factory() as setup:
        actor = await make_user(setup, 9680)
        broadcast = await create_broadcast(setup, actor, "hi", BroadcastAudience.ALL)
        broadcast_id = broadcast.id
        await setup.commit()

    await run_broadcast(db_factory, FakeBot(), broadcast_id)
    assert called is False


async def test_a_targeted_broadcast_carries_media_and_translations(db_session, db_factory, monkeypatch):
    """9E-B and the localization from 9E-A still apply to a targeted send."""
    from app.db.models.system import BroadcastMedia
    from app.db.models.user import UILanguage
    from app.services.broadcast import set_translations

    class FakeBot:
        def __init__(self):
            self.photos: list[tuple[int, str, str]] = []

        async def send_photo(self, chat_id, file_id, caption=None, *args, **kwargs):
            self.photos.append((chat_id, file_id, caption))

        async def send_message(self, chat_id, text, *args, **kwargs):
            raise AssertionError("a photo broadcast must not fall back to text")

    monkeypatch.setattr("app.services.broadcast.SEND_INTERVAL_SECONDS", 0)

    async with db_factory() as setup:
        actor = await make_user(setup, 9690)
        russian = await make_user(setup, 9691)
        russian.language = UILanguage.RU
        await _profile(setup, russian, "anime", 12)
        broadcast = await create_broadcast(
            setup,
            actor,
            "Salom",
            BroadcastAudience.INTEREST,
            media_type=BroadcastMedia.PHOTO,
            media_file_id="photo-file-id",
            target_value="anime",
        )
        await set_translations(setup, broadcast.id, {UILanguage.RU: "Привет"}, with_media=True)
        broadcast_id = broadcast.id
        await setup.commit()

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)

    assert bot.photos == [(russian.telegram_id, "photo-file-id", "Привет")]


async def test_targeted_recipients_still_reach_terminal_states(db_session, db_factory, monkeypatch):
    """Retry, skip and counter behaviour are the 9E-A machinery, unchanged."""
    from aiogram.exceptions import TelegramForbiddenError

    class FakeBot:
        async def send_message(self, chat_id, text, *args, **kwargs):
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")

    monkeypatch.setattr("app.services.broadcast.SEND_INTERVAL_SECONDS", 0)

    async with db_factory() as setup:
        actor = await make_user(setup, 9700)
        blocked = await make_user(setup, 9701)
        await _profile(setup, blocked, "anime", 12)
        broadcast = await create_broadcast(
            setup, actor, "hi", BroadcastAudience.INTEREST, target_value="anime"
        )
        broadcast_id = broadcast.id
        await setup.commit()

    await run_broadcast(db_factory, FakeBot(), broadcast_id)

    async with db_factory() as check:
        row = await check.get(Broadcast, broadcast_id)
        assert row.status == BroadcastStatus.COMPLETED
        assert row.blocked_count == 1
        message = (
            await check.execute(
                select(BroadcastMessage).where(BroadcastMessage.broadcast_id == broadcast_id)
            )
        ).scalar_one()
        assert message.status == DeliveryStatus.SKIPPED


async def test_targeting_never_builds_sql_from_the_target_string(db_session):
    """
    The target reaches the database as a bound parameter compared against a
    column — never as text spliced into a statement. Asserted on the
    compiled SQL so a future refactor to string building fails here.
    """
    from app.services.broadcast import _audience_filter

    hostile = "badge.anime.1"
    conditions = _audience_filter(BroadcastAudience.BADGE, hostile)
    compiled = str(select(UserInterestProfile.user_id).where(*conditions))
    assert hostile not in compiled
    assert "%" not in compiled  # no LIKE anywhere near a badge match
