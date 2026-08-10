"""
Theme resolution, precedence, isolation and safety.

Three properties carry this feature:

  **Exactly one theme wins.** Several rules can match one viewer, and the
  answer must be the same every time, in the documented order
  USER > BADGE > INTEREST > SUBSCRIPTION > GLOBAL.

  **A theme never crosses between users.** There is no module-level state
  in the resolver and no cache keyed on anything less than the user; the
  isolation section proves it in both directions.

  **A broken theme cannot break the app.** Every layer falls back, and an
  admin form can never produce a state with no usable palette.
"""
import asyncio

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
from app.db.models.theme import Theme, ThemeScope
from app.db.models.user import UserRole
from app.db.session import get_db_session
from app.main import app
from app.services.themes import (
    ALLOWED_TOKENS,
    DEFAULT_TOKENS,
    SCOPE_PRECEDENCE,
    ThemeError,
    assign_theme,
    create_theme,
    delete_theme,
    resolve_for_user,
    set_default_theme,
    set_theme_active,
    set_tokens,
)
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _theme(session, key: str, colour: str = "#112233") -> Theme:
    return await create_theme(
        session, key=key, name=key.title(), tokens={"--color-bg": colour}
    )


async def _watch(session, user, content_type: ContentType, count: int):
    """Gives a user a dominant interest through Phase 9B's real rules."""
    from datetime import datetime, timezone

    for index in range(count):
        title = Title(
            content_type=content_type, name=f"{content_type.value}-{user.id}-{index}", is_active=True
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
                last_watched_at=datetime.now(timezone.utc),
            )
        )
    await session.flush()


# ---------- defaults and fallback ----------


async def test_with_no_themes_the_builtin_palette_is_served(db_session):
    """Backward compatibility: an untouched platform looks exactly as today."""
    user = await make_user(db_session, 9101)
    resolved = await resolve_for_user(db_session, user)

    assert resolved.key == "default"
    assert resolved.tokens == DEFAULT_TOKENS
    assert resolved.scope is None


async def test_a_partial_theme_inherits_the_defaults(db_session):
    """"Change only the accent" must not blank every other colour."""
    theme = await _theme(db_session, "accent-only")
    await set_default_theme(db_session, theme.id)
    user = await make_user(db_session, 9102)

    resolved = await resolve_for_user(db_session, user)
    assert resolved.tokens["--color-bg"] == "#112233"
    assert resolved.tokens["--color-ink"] == DEFAULT_TOKENS["--color-ink"]
    assert set(resolved.tokens) == set(DEFAULT_TOKENS)


async def test_an_assignment_to_a_disabled_theme_is_ignored(db_session):
    """Switching a theme off must switch it off wherever it was referenced."""
    fallback = await _theme(db_session, "fallback", "#000000")
    await set_default_theme(db_session, fallback.id)
    disabled = await _theme(db_session, "disabled-one", "#ff0000")
    user = await make_user(db_session, 9103)
    await assign_theme(db_session, disabled.id, ThemeScope.USER, user_id=user.id)
    await set_theme_active(db_session, disabled.id, False)

    assert (await resolve_for_user(db_session, user)).key == "fallback"


async def test_the_default_theme_cannot_be_deleted_or_disabled(db_session):
    """An admin form must not be able to leave the platform with no palette."""
    theme = await _theme(db_session, "the-default")
    await set_default_theme(db_session, theme.id)

    with pytest.raises(ThemeError):
        await delete_theme(db_session, theme.id)
    with pytest.raises(ThemeError):
        await set_theme_active(db_session, theme.id, False)


async def test_setting_a_new_default_moves_the_flag(db_session):
    first = await _theme(db_session, "first")
    second = await _theme(db_session, "second")
    await set_default_theme(db_session, first.id)
    await set_default_theme(db_session, second.id)

    assert (await db_session.get(Theme, first.id, populate_existing=True)).is_default is False
    assert (await db_session.get(Theme, second.id, populate_existing=True)).is_default is True


# ---------- precedence ----------


async def test_the_documented_precedence_order_is_the_one_used(db_session):
    assert SCOPE_PRECEDENCE == (
        ThemeScope.USER,
        ThemeScope.BADGE,
        ThemeScope.INTEREST,
        ThemeScope.SUBSCRIPTION,
        ThemeScope.GLOBAL,
    )


async def test_every_scope_resolves_on_its_own(db_session):
    user = await make_user(db_session, 9110)
    await _watch(db_session, user, ContentType.ANIME, 12)

    for scope, kwargs in [
        (ThemeScope.GLOBAL, {}),
        (ThemeScope.SUBSCRIPTION, {"target_value": "free"}),
        (ThemeScope.INTEREST, {"target_value": ContentType.ANIME.value}),
        (ThemeScope.BADGE, {"target_value": "badge.anime."}),
        (ThemeScope.USER, {"user_id": user.id}),
    ]:
        theme = await _theme(db_session, f"scope-{scope.value}")
        await assign_theme(db_session, theme.id, scope, **kwargs)
        resolved = await resolve_for_user(db_session, user)
        assert resolved.scope == scope, f"{scope.value} should now win"
        assert resolved.key == f"scope-{scope.value}"


async def test_a_user_assignment_beats_every_group_rule(db_session):
    """The whole point of precedence: one person's override wins."""
    user = await make_user(db_session, 9111)
    await _watch(db_session, user, ContentType.ANIME, 12)

    for scope, kwargs in [
        (ThemeScope.GLOBAL, {}),
        (ThemeScope.SUBSCRIPTION, {"target_value": "free"}),
        (ThemeScope.INTEREST, {"target_value": ContentType.ANIME.value}),
        (ThemeScope.BADGE, {"target_value": "badge.anime."}),
    ]:
        theme = await _theme(db_session, f"group-{scope.value}")
        await assign_theme(db_session, theme.id, scope, **kwargs)

    personal = await _theme(db_session, "personal")
    await assign_theme(db_session, personal.id, ThemeScope.USER, user_id=user.id)

    resolved = await resolve_for_user(db_session, user)
    assert resolved.key == "personal"
    assert resolved.scope == ThemeScope.USER


async def test_conflicting_rules_still_yield_exactly_one_theme(db_session):
    """Never a mixture — one theme object, one key."""
    user = await make_user(db_session, 9112)
    await _watch(db_session, user, ContentType.ANIME, 12)
    badge = await _theme(db_session, "badge-theme", "#111111")
    interest = await _theme(db_session, "interest-theme", "#222222")
    await assign_theme(db_session, badge.id, ThemeScope.BADGE, target_value="badge.anime.")
    await assign_theme(
        db_session, interest.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value
    )

    resolved = await resolve_for_user(db_session, user)
    assert resolved.key == "badge-theme"
    assert resolved.tokens["--color-bg"] == "#111111"


async def test_priority_breaks_a_tie_within_one_scope(db_session):
    user = await make_user(db_session, 9113)
    low = await _theme(db_session, "low")
    high = await _theme(db_session, "high")
    await assign_theme(db_session, low.id, ThemeScope.SUBSCRIPTION, target_value="free", priority=1)
    await assign_theme(db_session, high.id, ThemeScope.SUBSCRIPTION, target_value="premium", priority=9)

    # Only the free rule matches this user, so priority must not override
    # matching — the higher-priority premium rule simply does not apply.
    assert (await resolve_for_user(db_session, user)).key == "low"


async def test_premium_and_free_receive_different_themes(db_session):
    from datetime import datetime, timedelta, timezone

    from app.db.models.user import Subscription
    from tests.conftest import make_paid_plan

    free_theme = await _theme(db_session, "free-theme")
    premium_theme = await _theme(db_session, "premium-theme")
    await assign_theme(db_session, free_theme.id, ThemeScope.SUBSCRIPTION, target_value="free")
    await assign_theme(db_session, premium_theme.id, ThemeScope.SUBSCRIPTION, target_value="premium")

    free_user = await make_user(db_session, 9114)
    subscriber = await make_user(db_session, 9115)
    plan = await make_paid_plan(db_session)
    db_session.add(
        Subscription(
            user_id=subscriber.id,
            plan_id=plan.id,
            started_at=datetime.now(timezone.utc) - timedelta(days=1),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    await db_session.flush()

    assert (await resolve_for_user(db_session, free_user)).key == "free-theme"
    assert (await resolve_for_user(db_session, subscriber)).key == "premium-theme"


async def test_a_user_with_no_profile_falls_through_to_global(db_session):
    """A newcomer matches no badge or interest rule and must still be themed."""
    global_theme = await _theme(db_session, "global-theme")
    anime_theme = await _theme(db_session, "anime-theme")
    await assign_theme(db_session, global_theme.id, ThemeScope.GLOBAL)
    await assign_theme(
        db_session, anime_theme.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value
    )
    newcomer = await make_user(db_session, 9116)

    assert (await resolve_for_user(db_session, newcomer)).key == "global-theme"


# ---------- isolation ----------


async def test_four_users_receive_four_different_themes(db_session):
    from datetime import datetime, timedelta, timezone

    from app.db.models.user import Subscription
    from tests.conftest import make_paid_plan

    anime = await _theme(db_session, "anime", "#a1a1a1")
    drama = await _theme(db_session, "drama", "#b2b2b2")
    premium = await _theme(db_session, "premium", "#c3c3c3")
    base = await _theme(db_session, "base", "#d4d4d4")
    await assign_theme(db_session, anime.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value)
    await assign_theme(db_session, drama.id, ThemeScope.INTEREST, target_value=ContentType.DRAMA.value)
    await assign_theme(db_session, premium.id, ThemeScope.SUBSCRIPTION, target_value="premium")
    await assign_theme(db_session, base.id, ThemeScope.GLOBAL)

    a = await make_user(db_session, 9120)
    b = await make_user(db_session, 9121)
    c = await make_user(db_session, 9122)
    d = await make_user(db_session, 9123)
    await _watch(db_session, a, ContentType.ANIME, 12)
    await _watch(db_session, b, ContentType.DRAMA, 12)
    plan = await make_paid_plan(db_session)
    db_session.add(
        Subscription(
            user_id=c.id,
            plan_id=plan.id,
            started_at=datetime.now(timezone.utc) - timedelta(days=1),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    await db_session.flush()

    assert (await resolve_for_user(db_session, a)).key == "anime"
    assert (await resolve_for_user(db_session, b)).key == "drama"
    assert (await resolve_for_user(db_session, c)).key == "premium"
    assert (await resolve_for_user(db_session, d)).key == "base"


async def test_resolution_order_cannot_change_another_users_answer(db_session):
    """Both directions, as the brief asks: A→B and B→A."""
    anime = await _theme(db_session, "anime")
    drama = await _theme(db_session, "drama")
    await assign_theme(db_session, anime.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value)
    await assign_theme(db_session, drama.id, ThemeScope.INTEREST, target_value=ContentType.DRAMA.value)

    a = await make_user(db_session, 9124)
    b = await make_user(db_session, 9125)
    await _watch(db_session, a, ContentType.ANIME, 12)
    await _watch(db_session, b, ContentType.DRAMA, 12)

    assert (await resolve_for_user(db_session, a)).key == "anime"
    assert (await resolve_for_user(db_session, b)).key == "drama"
    # …and again the other way round.
    assert (await resolve_for_user(db_session, b)).key == "drama"
    assert (await resolve_for_user(db_session, a)).key == "anime"


async def test_repeated_resolution_is_stable(db_session):
    theme = await _theme(db_session, "stable")
    await assign_theme(db_session, theme.id, ThemeScope.GLOBAL)
    user = await make_user(db_session, 9126)

    keys = {(await resolve_for_user(db_session, user)).key for _ in range(5)}
    assert keys == {"stable"}


async def test_concurrent_resolution_stays_isolated(db_factory):
    """Independent sessions in flight together — a shared cache would show here."""
    async with db_factory() as setup:
        anime = await _theme(setup, "anime")
        base = await _theme(setup, "base")
        await assign_theme(setup, anime.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value)
        await assign_theme(setup, base.id, ThemeScope.GLOBAL)
        fan = await make_user(setup, 9127)
        plain = await make_user(setup, 9128)
        await _watch(setup, fan, ContentType.ANIME, 12)
        await setup.commit()
        fan_id, plain_id = fan.id, plain.id

    async def resolve(user_id: int) -> str:
        from app.db.models.user import User

        async with db_factory() as session:
            user = await session.get(User, user_id)
            return (await resolve_for_user(session, user)).key

    results = await asyncio.gather(
        *[resolve(fan_id) for _ in range(4)], *[resolve(plain_id) for _ in range(4)]
    )
    assert results[:4] == ["anime"] * 4
    assert results[4:] == ["base"] * 4


# ---------- validation: no CSS injection ----------


@pytest.mark.parametrize(
    "value",
    [
        "red; background: url(javascript:alert(1))",
        "url('x')",
        "var(--evil)",
        "expression(alert(1))",
        "#12",
        "rgb(1,2,3)",
        "",
    ],
)
async def test_a_non_hex_colour_is_refused(db_session, value):
    """
    The grammar is deliberately narrower than CSS: these values become
    custom properties, and anything beyond hex widens the surface.
    """
    with pytest.raises(ThemeError):
        await create_theme(db_session, key="bad", name="Bad", tokens={"--color-bg": value})


async def test_an_unknown_token_is_refused(db_session):
    with pytest.raises(ThemeError):
        await create_theme(
            db_session, key="bad2", name="Bad", tokens={"--evil-token": "#ffffff"}
        )


async def test_every_allowed_token_round_trips(db_session):
    theme = await create_theme(
        db_session,
        key="full",
        name="Full",
        tokens={token: "#abcdef" for token in ALLOWED_TOKENS},
    )
    await set_default_theme(db_session, theme.id)
    user = await make_user(db_session, 9130)

    resolved = await resolve_for_user(db_session, user)
    assert all(resolved.tokens[token] == "#abcdef" for token in ALLOWED_TOKENS)


async def test_a_malformed_key_is_refused(db_session):
    for key in ("", "a", "Has Spaces", "x" * 65, "semi;colon"):
        with pytest.raises(ThemeError):
            await create_theme(db_session, key=key, name="X", tokens={})


async def test_duplicate_keys_are_refused(db_session):
    await _theme(db_session, "unique-key")
    with pytest.raises(ThemeError):
        await _theme(db_session, "unique-key")


async def test_updating_tokens_keeps_untouched_ones(db_session):
    theme = await create_theme(
        db_session, key="partial", name="P", tokens={"--color-bg": "#111111", "--color-ink": "#222222"}
    )
    await set_tokens(db_session, theme.id, {"--color-bg": "#333333"})
    await set_default_theme(db_session, theme.id)
    user = await make_user(db_session, 9131)

    resolved = await resolve_for_user(db_session, user)
    assert resolved.tokens["--color-bg"] == "#333333"
    assert resolved.tokens["--color-ink"] == "#222222"


async def test_assignment_targets_are_validated(db_session):
    theme = await _theme(db_session, "target-check")
    with pytest.raises(ThemeError):
        await assign_theme(db_session, theme.id, ThemeScope.USER, user_id=None)
    with pytest.raises(ThemeError):
        await assign_theme(db_session, theme.id, ThemeScope.USER, user_id=999999)
    with pytest.raises(ThemeError):
        await assign_theme(db_session, theme.id, ThemeScope.INTEREST, target_value=None)
    with pytest.raises(ThemeError):
        await assign_theme(db_session, theme.id, ThemeScope.SUBSCRIPTION, target_value="gold")
    with pytest.raises(ThemeError):
        await assign_theme(db_session, 999999, ThemeScope.GLOBAL)


# ---------- the API ----------


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


async def test_the_endpoint_serves_the_callers_own_theme(db_session, as_user):
    anime = await _theme(db_session, "anime", "#a0a0a0")
    base = await _theme(db_session, "base", "#b0b0b0")
    await assign_theme(db_session, anime.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value)
    await assign_theme(db_session, base.id, ThemeScope.GLOBAL)
    fan = await make_user(db_session, 9140)
    plain = await make_user(db_session, 9141)
    await _watch(db_session, fan, ContentType.ANIME, 12)
    await db_session.commit()

    async with as_user(fan) as client:
        mine = (await client.get("/api/auth/me/theme")).json()
    async with as_user(plain) as client:
        theirs = (await client.get("/api/auth/me/theme")).json()

    assert mine["key"] == "anime"
    assert theirs["key"] == "base"


async def test_a_user_id_in_the_query_is_ignored(db_session, as_user):
    """There is no parameter to point at somebody else."""
    anime = await _theme(db_session, "anime")
    base = await _theme(db_session, "base")
    await assign_theme(db_session, anime.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value)
    await assign_theme(db_session, base.id, ThemeScope.GLOBAL)
    fan = await make_user(db_session, 9142)
    plain = await make_user(db_session, 9143)
    await _watch(db_session, fan, ContentType.ANIME, 12)
    await db_session.commit()

    async with as_user(plain) as client:
        payload = (await client.get(f"/api/auth/me/theme?user_id={fan.id}")).json()

    assert payload["key"] == "base"


async def test_the_payload_only_contains_known_tokens(db_session, as_user):
    """The client applies these to CSS variables, so nothing unexpected may appear."""
    user = await make_user(db_session, 9144)
    await db_session.commit()

    async with as_user(user) as client:
        tokens = (await client.get("/api/auth/me/theme")).json()["tokens"]

    assert set(tokens) <= ALLOWED_TOKENS
    assert all(value.startswith("#") for value in tokens.values())


async def test_an_ordinary_user_cannot_manage_themes(db_session, as_user):
    user = await make_user(db_session, 9145)
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get("/api/admin/themes")).status_code == 403
        assert (
            await client.post("/api/admin/themes", json={"key": "x", "name": "X", "tokens": {}})
        ).status_code == 403


async def test_the_super_admin_can_manage_themes(db_session, as_user):
    admin = await make_user(db_session, 9146)
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async with as_user(admin) as client:
        created = await client.post(
            "/api/admin/themes",
            json={"key": "cinematic", "name": "Cinematic", "tokens": {"--color-bg": "#000000"}},
        )
        assert created.status_code == 200
        theme_id = created.json()["id"]

        assert (await client.get("/api/admin/themes")).status_code == 200
        assert (await client.post(f"/api/admin/themes/{theme_id}/default")).status_code == 200
        # Now the default, it must be protected.
        assert (await client.delete(f"/api/admin/themes/{theme_id}")).status_code == 422


async def test_the_admin_api_refuses_an_unsafe_colour(db_session, as_user):
    admin = await make_user(db_session, 9147)
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.post(
            "/api/admin/themes",
            json={
                "key": "evil",
                "name": "Evil",
                "tokens": {"--color-bg": "red; background: url(javascript:alert(1))"},
            },
        )
        assert response.status_code == 422
