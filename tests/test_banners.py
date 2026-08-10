"""
Personalized hero banners.

The rule that dominates this file: **a banner selection belongs to one
viewer**. Resolution runs per request against the caller's own interest
profile, with no shared cache, and the isolation section proves it — two
users with opposite tastes must never see each other's slides.

The second theme is that a campaign only shows when it should: disabled,
not yet started, and already finished all mean invisible, regardless of
how well its targeting matches.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.db.models.banner import Banner, BannerAudience
from app.db.models.content import (
    AudioLanguage,
    ContentType,
    Episode,
    MediaFile,
    Title,
    VideoQuality,
    WatchHistory,
)
from app.db.models.user import UserRole
from app.db.session import get_db_session
from app.main import app
from app.services.banners import (
    ALLOWED_LABEL_KEYS,
    BannerError,
    create_banner,
    resolve_for_user,
)
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _title(session, name="Promo", content_type=ContentType.FILM) -> Title:
    title = Title(content_type=content_type, name=name, is_active=True)
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
    await session.flush()
    return title


async def _watch(session, user, content_type: ContentType, count: int):
    """Gives `user` a dominant interest — reuses Phase 9B's rules, not a stub."""
    for index in range(count):
        title = await _title(session, f"{content_type.value}-{user.id}-{index}", content_type)
        session.add(
            WatchHistory(
                user_id=user.id,
                title_id=title.id,
                episode_id=(await session.execute(
                    __import__("sqlalchemy").select(Episode.id).where(Episode.title_id == title.id)
                )).scalar_one(),
                watch_count=1,
                last_watched_at=datetime.now(timezone.utc),
            )
        )
    await session.flush()


async def _banner(session, **fields) -> Banner:
    defaults = {"headline": "Promo", "audience": BannerAudience.GLOBAL, "priority": 0}
    return await create_banner(session, **{**defaults, **fields})


# ---------- targeting ----------


async def test_a_global_banner_reaches_everyone(db_session):
    await _banner(db_session, headline="Everyone")
    for telegram_id in (9901, 9902):
        user = await make_user(db_session, telegram_id)
        resolved = await resolve_for_user(db_session, user)
        assert [b.headline for b in resolved] == ["Everyone"]


async def test_a_content_type_banner_reaches_only_that_audience(db_session):
    await _banner(
        db_session,
        headline="Anime week",
        audience=BannerAudience.CONTENT_TYPE,
        target_value=ContentType.ANIME.value,
    )
    anime_fan = await make_user(db_session, 9903)
    film_fan = await make_user(db_session, 9904)
    await _watch(db_session, anime_fan, ContentType.ANIME, 10)
    await _watch(db_session, film_fan, ContentType.FILM, 10)

    assert [b.headline for b in await resolve_for_user(db_session, anime_fan)] == ["Anime week"]
    assert await resolve_for_user(db_session, film_fan) == []


async def test_a_badge_banner_matches_every_tier_by_prefix(db_session):
    """One campaign targets "badge.anime." rather than one per tier."""
    await _banner(
        db_session,
        headline="For anime fans",
        audience=BannerAudience.BADGE,
        target_value="badge.anime.",
    )
    user = await make_user(db_session, 9905)
    await _watch(db_session, user, ContentType.ANIME, 12)

    assert [b.headline for b in await resolve_for_user(db_session, user)] == ["For anime fans"]


async def test_a_user_with_no_dominant_interest_sees_only_global(db_session):
    """A new viewer has no profile to target, and must still see something."""
    await _banner(db_session, headline="Everyone")
    await _banner(
        db_session,
        headline="Anime",
        audience=BannerAudience.CONTENT_TYPE,
        target_value=ContentType.ANIME.value,
    )
    user = await make_user(db_session, 9906)

    assert [b.headline for b in await resolve_for_user(db_session, user)] == ["Everyone"]


async def test_premium_and_free_targeting_split_the_audience(db_session):
    from datetime import timedelta as td

    from app.db.models.user import Subscription
    from tests.conftest import make_paid_plan

    await _banner(db_session, headline="Premium only", audience=BannerAudience.PREMIUM)
    await _banner(db_session, headline="Upgrade", audience=BannerAudience.FREE)

    subscriber = await make_user(db_session, 9907)
    plan = await make_paid_plan(db_session)
    db_session.add(
        Subscription(
            user_id=subscriber.id,
            plan_id=plan.id,
            started_at=datetime.now(timezone.utc) - td(days=1),
            expires_at=datetime.now(timezone.utc) + td(days=30),
        )
    )
    free_user = await make_user(db_session, 9908)
    await db_session.flush()

    assert [b.headline for b in await resolve_for_user(db_session, subscriber)] == ["Premium only"]
    assert [b.headline for b in await resolve_for_user(db_session, free_user)] == ["Upgrade"]


async def test_an_upcoming_title_needs_no_catalog_entry(db_session):
    """"Avengers: Doomsday — coming soon" has nothing to link to yet."""
    banner = await _banner(
        db_session,
        headline="Avengers: Doomsday",
        label_key="banner.label.coming_soon",
        image_url="https://example.test/doomsday.jpg",
        title_id=None,
    )
    user = await make_user(db_session, 9909)

    resolved = await resolve_for_user(db_session, user)
    assert resolved[0].title_id is None
    assert resolved[0].label_key == "banner.label.coming_soon"
    assert banner.title_id is None


# ---------- isolation ----------


async def test_two_users_receive_entirely_different_selections(db_session):
    await _banner(
        db_session,
        headline="Anime",
        audience=BannerAudience.CONTENT_TYPE,
        target_value=ContentType.ANIME.value,
    )
    await _banner(
        db_session,
        headline="Drama",
        audience=BannerAudience.CONTENT_TYPE,
        target_value=ContentType.DRAMA.value,
    )
    anime_fan = await make_user(db_session, 9910)
    drama_fan = await make_user(db_session, 9911)
    await _watch(db_session, anime_fan, ContentType.ANIME, 10)
    await _watch(db_session, drama_fan, ContentType.DRAMA, 10)

    assert [b.headline for b in await resolve_for_user(db_session, anime_fan)] == ["Anime"]
    assert [b.headline for b in await resolve_for_user(db_session, drama_fan)] == ["Drama"]


async def test_resolving_for_one_user_does_not_affect_the_next(db_session):
    """
    A shared cache would show up here: resolve for a heavy anime viewer,
    then for someone with no history, and check the second is unaffected.
    """
    await _banner(
        db_session,
        headline="Anime",
        audience=BannerAudience.CONTENT_TYPE,
        target_value=ContentType.ANIME.value,
    )
    anime_fan = await make_user(db_session, 9912)
    newcomer = await make_user(db_session, 9913)
    await _watch(db_session, anime_fan, ContentType.ANIME, 12)

    assert len(await resolve_for_user(db_session, anime_fan)) == 1
    assert await resolve_for_user(db_session, newcomer) == []
    # And back again — order of resolution must not matter either.
    assert len(await resolve_for_user(db_session, anime_fan)) == 1


async def test_repeated_resolution_is_stable_for_the_same_user(db_session):
    await _banner(db_session, headline="A", priority=10)
    await _banner(db_session, headline="B", priority=5)
    user = await make_user(db_session, 9914)

    runs = {tuple(b.headline for b in await resolve_for_user(db_session, user)) for _ in range(4)}
    assert len(runs) == 1, "the same viewer must get the same slides in the same order"


# ---------- windows and state ----------


async def test_a_disabled_banner_is_invisible(db_session):
    await _banner(db_session, headline="Off", is_active=False)
    user = await make_user(db_session, 9920)
    assert await resolve_for_user(db_session, user) == []


async def test_a_future_banner_is_not_shown_yet(db_session):
    await _banner(
        db_session,
        headline="Later",
        starts_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    user = await make_user(db_session, 9921)
    assert await resolve_for_user(db_session, user) == []


async def test_an_expired_banner_is_gone(db_session):
    await _banner(
        db_session,
        headline="Over",
        starts_at=datetime.now(timezone.utc) - timedelta(days=10),
        ends_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    user = await make_user(db_session, 9922)
    assert await resolve_for_user(db_session, user) == []


async def test_a_banner_inside_its_window_is_shown(db_session):
    await _banner(
        db_session,
        headline="Live",
        starts_at=datetime.now(timezone.utc) - timedelta(hours=1),
        ends_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    user = await make_user(db_session, 9923)
    assert [b.headline for b in await resolve_for_user(db_session, user)] == ["Live"]


# ---------- ordering ----------


async def test_higher_priority_comes_first(db_session):
    await _banner(db_session, headline="Low", priority=1)
    await _banner(db_session, headline="High", priority=99)
    user = await make_user(db_session, 9930)

    assert [b.headline for b in await resolve_for_user(db_session, user)][0] == "High"


async def test_personalized_slides_lead_global_ones(db_session):
    """A slide chosen for this viewer outranks one shown to everybody."""
    await _banner(db_session, headline="Everyone", priority=99)
    await _banner(
        db_session,
        headline="For you",
        audience=BannerAudience.CONTENT_TYPE,
        target_value=ContentType.ANIME.value,
        priority=1,
    )
    user = await make_user(db_session, 9931)
    await _watch(db_session, user, ContentType.ANIME, 10)

    assert [b.headline for b in await resolve_for_user(db_session, user)] == [
        "For you",
        "Everyone",
    ]


async def test_the_slide_count_is_bounded(db_session):
    from app.services.banners import MAX_BANNERS

    for index in range(MAX_BANNERS + 4):
        await _banner(db_session, headline=f"B{index}")
    user = await make_user(db_session, 9932)

    assert len(await resolve_for_user(db_session, user)) == MAX_BANNERS


# ---------- validation: no markup, no arbitrary URLs ----------


@pytest.mark.parametrize(
    "field,value",
    [
        ("headline", "<script>alert(1)</script>"),
        ("subtitle", "hello <img src=x onerror=alert(1)>"),
    ],
)
async def test_markup_in_text_is_refused(db_session, field, value):
    """Refused outright rather than escaped — a headline has no use for < or >."""
    with pytest.raises(BannerError):
        await _banner(db_session, **{field: value})


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/html;base64,PHN2Zz4=", "vbscript:msgbox"]
)
async def test_a_dangerous_image_url_is_refused(db_session, url):
    with pytest.raises(BannerError):
        await _banner(db_session, image_url=url)


async def test_an_uploaded_image_path_is_accepted(db_session):
    banner = await _banner(db_session, image_url="/api/movies/images/12")
    assert banner.image_url == "/api/movies/images/12"


async def test_an_unknown_label_is_refused(db_session):
    with pytest.raises(BannerError):
        await _banner(db_session, label_key="banner.label.made_up")
    for key in ALLOWED_LABEL_KEYS:
        assert (await _banner(db_session, label_key=key)).label_key == key


async def test_targeted_audiences_require_a_target(db_session):
    with pytest.raises(BannerError):
        await _banner(db_session, audience=BannerAudience.CONTENT_TYPE, target_value=None)


async def test_a_global_banner_stores_no_stray_target(db_session):
    banner = await _banner(db_session, audience=BannerAudience.GLOBAL, target_value="anime")
    assert banner.target_value is None


async def test_a_backwards_window_is_refused(db_session):
    now = datetime.now(timezone.utc)
    with pytest.raises(BannerError):
        await _banner(db_session, starts_at=now, ends_at=now - timedelta(days=1))


async def test_an_unknown_title_is_refused(db_session):
    with pytest.raises(BannerError):
        await _banner(db_session, title_id=999999)


# ---------- API: scoped to the caller, admin-gated ----------


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


async def test_the_endpoint_returns_the_callers_own_banners(db_session, as_user):
    title = await _title(db_session, "Featured")
    await _banner(db_session, headline="Anime", audience=BannerAudience.CONTENT_TYPE,
                  target_value=ContentType.ANIME.value, title_id=title.id)
    anime_fan = await make_user(db_session, 9940)
    other = await make_user(db_session, 9941)
    await _watch(db_session, anime_fan, ContentType.ANIME, 10)
    await db_session.commit()

    async with as_user(anime_fan) as client:
        mine = (await client.get("/api/movies/banners")).json()
    async with as_user(other) as client:
        theirs = (await client.get("/api/movies/banners")).json()

    assert [b["headline"] for b in mine] == ["Anime"]
    assert theirs == [], "another viewer must not receive a personalized slide"


async def test_the_slide_carries_its_movie_for_the_carousel(db_session, as_user):
    """The existing carousel plays/opens a Movie; the slide supplies one."""
    title = await _title(db_session, "Featured")
    await _banner(db_session, headline="Watch this", title_id=title.id)
    user = await make_user(db_session, 9942)
    await db_session.commit()

    async with as_user(user) as client:
        slide = (await client.get("/api/movies/banners")).json()[0]

    assert slide["movie"]["id"] == title.id
    assert slide["movie"]["episode_count"] == 1


async def test_no_banners_configured_returns_an_empty_list(db_session, as_user):
    """The Mini App falls back to its existing behaviour on an empty list."""
    user = await make_user(db_session, 9943)
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get("/api/movies/banners")).json() == []


async def test_an_ordinary_user_cannot_manage_banners(db_session, as_user):
    user = await make_user(db_session, 9944)
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get("/api/admin/banners")).status_code == 403
        assert (await client.post("/api/admin/banners", json={"headline": "x"})).status_code == 403


async def test_an_admin_without_the_permission_cannot_manage_banners(db_session, as_user):
    admin = await make_user(db_session, 9945)
    admin.role = UserRole.ADMIN
    await db_session.commit()

    async with as_user(admin) as client:
        assert (await client.get("/api/admin/banners")).status_code == 403


async def test_the_super_admin_can_manage_banners(db_session, as_user):
    admin = await make_user(db_session, 9946)
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async with as_user(admin) as client:
        created = await client.post(
            "/api/admin/banners",
            json={"headline": "Campaign", "audience": "global", "priority": 5},
        )
        assert created.status_code == 200
        assert (await client.get("/api/admin/banners")).status_code == 200

        banner_id = created.json()["id"]
        patched = await client.patch(
            f"/api/admin/banners/{banner_id}", json={"headline": "Renamed", "is_active": False}
        )
        assert patched.status_code == 200
        assert patched.json()["is_active"] is False

        assert (await client.delete(f"/api/admin/banners/{banner_id}")).status_code == 200


async def test_the_admin_api_refuses_markup(db_session, as_user):
    admin = await make_user(db_session, 9947)
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.post(
            "/api/admin/banners", json={"headline": "<script>alert(1)</script>"}
        )
        assert response.status_code == 422


async def test_the_label_allowlist_is_served_to_the_panel(db_session, as_user):
    """The panel offers exactly what the resolver accepts."""
    admin = await make_user(db_session, 9948)
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async with as_user(admin) as client:
        labels = (await client.get("/api/admin/banners/labels")).json()["labels"]

    assert set(labels) == ALLOWED_LABEL_KEYS
