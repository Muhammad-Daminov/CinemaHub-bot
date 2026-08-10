"""
Cross-user isolation for the appearance layer, end to end.

The earlier theme tests cover the resolver; these drive the **HTTP
surface** two users at a time, because that is the boundary where a leak
would actually reach somebody. Themes, shapes, decorations and banners
are all checked, plus the guarantee that an admin editing configuration
cannot alter what a *different* user resolves to.
"""
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.db.models.banner import BannerAudience
from app.db.models.content import (
    AudioLanguage,
    ContentType,
    Episode,
    MediaFile,
    Title,
    VideoQuality,
    WatchHistory,
)
from app.db.models.theme import ThemeScope
from app.db.session import get_db_session
from app.main import app
from app.services.banners import create_banner
from app.services.themes import assign_theme, create_theme, set_default_theme, set_tokens
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _watch(session, user, content_type: ContentType, count: int):
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


# ---------- shape and decoration are per-viewer ----------


async def test_two_users_receive_different_shapes_and_decorations(db_session, as_user):
    anime = await create_theme(
        db_session, key="anime-look", name="Anime", tokens={}, card_shape="square", decoration="anime"
    )
    base = await create_theme(
        db_session, key="base-look", name="Base", tokens={}, card_shape="rounded", decoration="none"
    )
    await assign_theme(
        db_session, anime.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value
    )
    await assign_theme(db_session, base.id, ThemeScope.GLOBAL)

    fan = await make_user(db_session, 9601)
    plain = await make_user(db_session, 9602)
    await _watch(db_session, fan, ContentType.ANIME, 12)
    await db_session.commit()

    async with as_user(fan) as client:
        mine = (await client.get("/api/auth/me/theme")).json()
    async with as_user(plain) as client:
        theirs = (await client.get("/api/auth/me/theme")).json()

    assert (mine["card_shape"], mine["decoration"]) == ("square", "anime")
    assert (theirs["card_shape"], theirs["decoration"]) == ("rounded", "none")


async def test_the_theme_payload_never_names_another_user(db_session, as_user):
    """Nothing identifying leaves the endpoint — it describes a palette."""
    user = await make_user(db_session, 9603)
    other = await make_user(db_session, 9604)
    theme = await create_theme(db_session, key="personal", name="Personal", tokens={})
    await assign_theme(db_session, theme.id, ThemeScope.USER, user_id=other.id)
    await db_session.commit()

    async with as_user(user) as client:
        payload = (await client.get("/api/auth/me/theme")).json()

    assert set(payload) == {"key", "name", "tokens", "card_shape", "decoration", "scope"}
    assert payload["key"] != "personal", "another user's personal theme must not resolve here"


# ---------- an admin edit does not disturb other viewers ----------


async def test_editing_one_theme_leaves_another_users_resolution_alone(db_session, as_user):
    anime = await create_theme(db_session, key="a-theme", name="A", tokens={"--color-bg": "#111111"})
    base = await create_theme(db_session, key="b-theme", name="B", tokens={"--color-bg": "#222222"})
    await assign_theme(
        db_session, anime.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value
    )
    await assign_theme(db_session, base.id, ThemeScope.GLOBAL)

    fan = await make_user(db_session, 9605)
    plain = await make_user(db_session, 9606)
    await _watch(db_session, fan, ContentType.ANIME, 12)
    await db_session.commit()

    # Recolour only the anime theme.
    await set_tokens(db_session, anime.id, {"--color-bg": "#999999"})
    await db_session.commit()

    async with as_user(fan) as client:
        assert (await client.get("/api/auth/me/theme")).json()["tokens"]["--color-bg"] == "#999999"
    async with as_user(plain) as client:
        assert (await client.get("/api/auth/me/theme")).json()["tokens"]["--color-bg"] == "#222222"


async def test_assigning_a_theme_to_one_user_does_not_move_another(db_session, as_user):
    special = await create_theme(db_session, key="special", name="Special", tokens={})
    fallback = await create_theme(db_session, key="fallback", name="Fallback", tokens={})
    await set_default_theme(db_session, fallback.id)

    chosen = await make_user(db_session, 9607)
    bystander = await make_user(db_session, 9608)
    await assign_theme(db_session, special.id, ThemeScope.USER, user_id=chosen.id)
    await db_session.commit()

    async with as_user(chosen) as client:
        assert (await client.get("/api/auth/me/theme")).json()["key"] == "special"
    async with as_user(bystander) as client:
        assert (await client.get("/api/auth/me/theme")).json()["key"] == "fallback"


async def test_repeated_requests_do_not_drift(db_session, as_user):
    """Two users, interleaved, several times — the answers stay put."""
    anime = await create_theme(db_session, key="x-anime", name="A", tokens={})
    base = await create_theme(db_session, key="x-base", name="B", tokens={})
    await assign_theme(
        db_session, anime.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value
    )
    await assign_theme(db_session, base.id, ThemeScope.GLOBAL)

    fan = await make_user(db_session, 9609)
    plain = await make_user(db_session, 9610)
    await _watch(db_session, fan, ContentType.ANIME, 12)
    await db_session.commit()

    for _ in range(3):
        async with as_user(fan) as client:
            assert (await client.get("/api/auth/me/theme")).json()["key"] == "x-anime"
        async with as_user(plain) as client:
            assert (await client.get("/api/auth/me/theme")).json()["key"] == "x-base"


# ---------- banners stay per-viewer alongside themes ----------


async def test_themes_and_banners_resolve_independently_per_user(db_session, as_user):
    anime_theme = await create_theme(db_session, key="t-anime", name="A", tokens={})
    base_theme = await create_theme(db_session, key="t-base", name="B", tokens={})
    await assign_theme(
        db_session, anime_theme.id, ThemeScope.INTEREST, target_value=ContentType.ANIME.value
    )
    await assign_theme(db_session, base_theme.id, ThemeScope.GLOBAL)

    await create_banner(
        db_session,
        headline="Anime campaign",
        audience=BannerAudience.CONTENT_TYPE,
        target_value=ContentType.ANIME.value,
    )

    fan = await make_user(db_session, 9611)
    plain = await make_user(db_session, 9612)
    await _watch(db_session, fan, ContentType.ANIME, 12)
    await db_session.commit()

    async with as_user(fan) as client:
        assert (await client.get("/api/auth/me/theme")).json()["key"] == "t-anime"
        assert [b["headline"] for b in (await client.get("/api/movies/banners")).json()] == [
            "Anime campaign"
        ]
    async with as_user(plain) as client:
        assert (await client.get("/api/auth/me/theme")).json()["key"] == "t-base"
        assert (await client.get("/api/movies/banners")).json() == []


# ---------- a broken theme cannot break the app ----------


async def test_a_disabled_default_chain_still_serves_a_palette(db_session, as_user):
    """
    Every assignment points at a disabled theme and there is no default.
    The viewer must still receive a complete, usable palette.
    """
    from app.services.themes import DEFAULT_TOKENS, set_theme_active

    broken = await create_theme(db_session, key="broken", name="Broken", tokens={})
    user = await make_user(db_session, 9613)
    await assign_theme(db_session, broken.id, ThemeScope.USER, user_id=user.id)
    await set_theme_active(db_session, broken.id, False)
    await db_session.commit()

    async with as_user(user) as client:
        payload = (await client.get("/api/auth/me/theme")).json()

    assert payload["tokens"] == DEFAULT_TOKENS
    assert payload["card_shape"] == "rounded"
    assert payload["decoration"] == "none"


async def test_an_ordinary_user_cannot_reach_any_appearance_admin_route(db_session, as_user):
    user = await make_user(db_session, 9614)
    await db_session.commit()

    async with as_user(user) as client:
        for path in (
            "/api/admin/themes",
            "/api/admin/themes/tokens",
            "/api/admin/theme-assignments",
            "/api/admin/banners",
            "/api/admin/banners/labels",
        ):
            assert (await client.get(path)).status_code == 403, path


# ---------- "apply to my own panel" ----------


async def test_a_user_assignment_without_an_id_targets_the_caller(db_session, as_user):
    """
    The panel's shortcut sends no user id at all — the server resolves it
    from the verified session. That is what makes the easy path also the
    safe one: there is no id in the request to tamper with.
    """
    from app.db.models.theme import ThemeAssignment, ThemeScope
    from app.db.models.user import AdminPermission, UserRole
    from app.services.themes import create_theme

    admin = await make_user(db_session, 9930)
    admin.role = UserRole.ADMIN
    db_session.add(AdminPermission(user_id=admin.id, permission="manage_system_settings"))
    theme = await create_theme(
        db_session, key="mine", name="Mine", tokens={"--color-bg": "#101010"}
    )
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.post(
            "/api/admin/theme-assignments",
            json={"theme_id": theme.id, "scope": "user"},
        )

    assert response.status_code == 200
    assert response.json()["user_id"] == admin.id

    row = await db_session.get(ThemeAssignment, response.json()["id"])
    assert row.scope == ThemeScope.USER and row.user_id == admin.id


async def test_the_caller_now_resolves_to_that_theme(db_session, as_user):
    """End to end: the shortcut actually changes what the admin renders."""
    from app.db.models.user import AdminPermission, UserRole
    from app.services.themes import create_theme

    admin = await make_user(db_session, 9931)
    admin.role = UserRole.ADMIN
    db_session.add(AdminPermission(user_id=admin.id, permission="manage_system_settings"))
    theme = await create_theme(
        db_session, key="panel", name="Panel", tokens={"--color-bg": "#202020"}
    )
    await db_session.commit()

    async with as_user(admin) as client:
        assert (
            await client.post(
                "/api/admin/theme-assignments", json={"theme_id": theme.id, "scope": "user"}
            )
        ).status_code == 200
        resolved = (await client.get("/api/auth/me/theme")).json()

    # A real scope, so the client applies it — unlike the built-in default.
    assert resolved["scope"] == "user"
    assert resolved["tokens"]["--color-bg"] == "#202020"


async def test_the_shortcut_does_not_touch_anybody_else(db_session, as_user):
    """The other user keeps resolving to the built-in default, scope null."""
    from app.db.models.user import AdminPermission, UserRole
    from app.services.themes import create_theme

    admin = await make_user(db_session, 9932)
    admin.role = UserRole.ADMIN
    db_session.add(AdminPermission(user_id=admin.id, permission="manage_system_settings"))
    bystander = await make_user(db_session, 9933)
    theme = await create_theme(
        db_session, key="onlymine", name="Only Mine", tokens={"--color-bg": "#303030"}
    )
    await db_session.commit()

    async with as_user(admin) as client:
        await client.post(
            "/api/admin/theme-assignments", json={"theme_id": theme.id, "scope": "user"}
        )

    async with as_user(bystander) as client:
        resolved = (await client.get("/api/auth/me/theme")).json()

    assert resolved["scope"] is None
    assert resolved["tokens"]["--color-bg"] != "#303030"


async def test_an_explicit_user_id_is_still_honoured(db_session, as_user):
    """
    Assigning a theme to someone else remains a legitimate admin action —
    the shortcut adds a default, it does not remove a capability.
    """
    from app.db.models.user import AdminPermission, UserRole
    from app.services.themes import create_theme

    admin = await make_user(db_session, 9934)
    admin.role = UserRole.ADMIN
    db_session.add(AdminPermission(user_id=admin.id, permission="manage_system_settings"))
    other = await make_user(db_session, 9935)
    theme = await create_theme(
        db_session, key="forthem", name="For Them", tokens={"--color-bg": "#404040"}
    )
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.post(
            "/api/admin/theme-assignments",
            json={"theme_id": theme.id, "scope": "user", "user_id": other.id},
        )

    assert response.status_code == 200
    assert response.json()["user_id"] == other.id


async def test_an_ordinary_user_cannot_assign_a_theme_to_themselves(db_session, as_user):
    """The shortcut is convenience for an admin, not a new capability."""
    from app.services.themes import create_theme

    plain = await make_user(db_session, 9936)
    theme = await create_theme(
        db_session, key="nope", name="Nope", tokens={"--color-bg": "#505050"}
    )
    await db_session.commit()

    async with as_user(plain) as client:
        assert (
            await client.post(
                "/api/admin/theme-assignments", json={"theme_id": theme.id, "scope": "user"}
            )
        ).status_code == 403
