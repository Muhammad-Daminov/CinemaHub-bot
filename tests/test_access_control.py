"""
The two gates a request passes before it is served: is this account
banned, and does it satisfy the required-channel rule.

Both are asserted at the HTTP boundary rather than at the service, for
the same reason the /watch guarantee is: a rule enforced only in the UI
is not a rule. The negative cases matter as much as the positive ones —
a ban that also silenced /auth/me would leave the Mini App showing an
empty catalog with no explanation, and a membership check that failed
closed would lock every user out the moment the bot lost its
administrator rights in the channel.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.db.models.user import User, UserRole
from app.db.session import get_db_session
from app.main import app
from app.services import membership as membership_module
from app.services.settings_store import get_membership_config, set_membership_config
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


@pytest.fixture
def as_user(db_session):
    """Runs the app as a given user, with the test session behind it."""
    def _install(user: User) -> AsyncClient:
        async def override_session():
            yield db_session

        async def override_user():
            return user

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _install
    app.dependency_overrides.clear()


# ---------- bans ----------


async def test_a_banned_user_is_refused_by_the_catalog(db_session, as_user):
    user = await make_user(db_session, 9901)
    user.is_banned = True
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get("/api/movies")).status_code == 403
        assert (await client.get("/api/billing/overview")).status_code == 403


async def test_a_banned_user_can_still_read_their_profile(db_session, as_user):
    """
    Deliberate. /auth/me is what tells the app to render "your account is
    blocked"; refusing it would show an empty catalog instead of a reason.
    """
    user = await make_user(db_session, 9902)
    user.is_banned = True
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.get("/api/auth/me")
        assert response.status_code == 200
        assert response.json()["is_banned"] is True


async def test_an_unbanned_user_is_served_normally(db_session, as_user):
    user = await make_user(db_session, 9903)
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get("/api/movies")).status_code == 200
        assert (await client.get("/api/auth/me")).json()["is_banned"] is False


async def test_unbanning_restores_access(db_session, as_user):
    user = await make_user(db_session, 9904)
    user.is_banned = True
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get("/api/movies")).status_code == 403
        user.is_banned = False
        await db_session.commit()
        assert (await client.get("/api/movies")).status_code == 200


async def test_a_banned_super_admin_is_still_served(db_session, as_user):
    """The owner cannot be locked out of their own platform by a ban."""
    user = await make_user(db_session, 9905)
    user.role = UserRole.SUPER_ADMIN
    user.is_banned = True
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get("/api/movies")).status_code == 200


async def test_a_banned_admin_loses_the_admin_api(db_session, as_user):
    """They keep the role — the ban stops them using it, and is reversible."""
    user = await make_user(db_session, 9906)
    user.role = UserRole.ADMIN
    user.is_banned = True
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get("/api/admin/stats")).status_code == 403
    assert user.role == UserRole.ADMIN


# ---------- ban administration ----------


@pytest.fixture
def as_admin(db_session, as_user):
    async def _make(role: UserRole, telegram_id: int) -> tuple[AsyncClient, User]:
        actor = await make_user(db_session, telegram_id)
        actor.role = role
        await db_session.commit()
        return as_user(actor), actor

    return _make


async def test_an_admin_can_ban_an_ordinary_user(db_session, as_admin):
    client, _ = await as_admin(UserRole.SUPER_ADMIN, 9910)
    target = await make_user(db_session, 9911)
    await db_session.commit()

    async with client as http:
        response = await http.patch(f"/api/admin/users/{target.id}/ban", json={"banned": True})
        assert response.status_code == 200
        assert response.json()["is_banned"] is True


async def test_the_super_admin_cannot_be_banned(db_session, as_admin):
    client, _ = await as_admin(UserRole.SUPER_ADMIN, 9912)
    owner = await make_user(db_session, 9913)
    owner.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async with client as http:
        assert (
            await http.patch(f"/api/admin/users/{owner.id}/ban", json={"banned": True})
        ).status_code == 403


async def test_an_admin_cannot_ban_another_admin(db_session, as_admin):
    """Otherwise two admins with MANAGE_USERS settle it by clicking first."""
    from app.db.models.user import AdminPermission

    client, actor = await as_admin(UserRole.ADMIN, 9914)
    db_session.add(AdminPermission(user_id=actor.id, permission="manage_users"))
    peer = await make_user(db_session, 9915)
    peer.role = UserRole.ADMIN
    await db_session.commit()

    async with client as http:
        assert (
            await http.patch(f"/api/admin/users/{peer.id}/ban", json={"banned": True})
        ).status_code == 403


async def test_nobody_bans_themselves(db_session, as_admin):
    client, actor = await as_admin(UserRole.SUPER_ADMIN, 9916)
    async with client as http:
        assert (
            await http.patch(f"/api/admin/users/{actor.id}/ban", json={"banned": True})
        ).status_code == 422


# ---------- required channel ----------


async def test_membership_is_off_until_configured(db_session):
    config = await get_membership_config(db_session)
    assert config.active is False


async def test_enabling_without_a_channel_stays_off(db_session):
    """An incomplete configuration must read as "off", never as "deny everyone"."""
    config = await set_membership_config(db_session, enabled=True, channel=None)
    assert config.enabled is True
    assert config.active is False


async def test_a_configured_channel_gets_an_invite_url(db_session):
    config = await set_membership_config(db_session, enabled=True, channel="@cinemahub")
    assert config.active is True
    assert config.invite_url == "https://t.me/cinemahub"


async def test_a_numeric_chat_id_has_no_invite_url(db_session):
    config = await set_membership_config(db_session, enabled=True, channel="-1001234567890")
    assert config.active is True
    assert config.invite_url is None


async def _playable_film(session):
    from app.db.models.content import (
        AudioLanguage,
        ContentType,
        Episode,
        MediaFile,
        Title,
        VideoQuality,
    )

    title = Title(content_type=ContentType.FILM, name="Gated", is_active=True)
    session.add(title)
    await session.flush()
    episode = Episode(title_id=title.id, season=1, number=1)
    session.add(episode)
    await session.flush()
    session.add(
        MediaFile(
            episode_id=episode.id,
            file_id="f1",
            language=AudioLanguage.UZ_DUB,
            quality=VideoQuality.HD_720,
        )
    )
    await session.flush()
    return title


async def test_a_non_member_cannot_watch(db_session, as_user, monkeypatch):
    user = await make_user(db_session, 9920)
    film = await _playable_film(db_session)
    await set_membership_config(db_session, enabled=True, channel="@cinemahub")
    await db_session.commit()

    async def not_a_member(*args, **kwargs):
        return False

    monkeypatch.setattr(membership_module, "is_channel_member", not_a_member)

    async with as_user(user) as client:
        response = await client.post(f"/api/movies/{film.id}/watch")
        assert response.status_code == 403


async def test_a_member_can_watch(db_session, as_user, monkeypatch):
    """The gate must open, not merely close — otherwise it is an outage."""
    user = await make_user(db_session, 9924)
    film = await _playable_film(db_session)
    await set_membership_config(db_session, enabled=True, channel="@cinemahub")
    await db_session.commit()

    async def a_member(*args, **kwargs):
        return True

    async def fake_deliver(**kwargs):
        return None

    from app.services import streaming as streaming_module

    monkeypatch.setattr(membership_module, "is_channel_member", a_member)
    monkeypatch.setattr(streaming_module.streaming_service, "deliver_episode", fake_deliver)

    async with as_user(user) as client:
        assert (await client.post(f"/api/movies/{film.id}/watch")).status_code == 200


async def test_browsing_stays_open_to_a_non_member(db_session, as_user, monkeypatch):
    """The catalog is the shop window — gating it would hide what the channel is for."""
    user = await make_user(db_session, 9921)
    await set_membership_config(db_session, enabled=True, channel="@cinemahub")
    await db_session.commit()

    async def not_a_member(*args, **kwargs):
        return False

    monkeypatch.setattr(membership_module, "is_channel_member", not_a_member)

    async with as_user(user) as client:
        assert (await client.get("/api/movies")).status_code == 200


async def test_an_administrator_is_exempt(db_session, monkeypatch):
    user = await make_user(db_session, 9922)
    user.role = UserRole.ADMIN
    await set_membership_config(db_session, enabled=True, channel="@cinemahub")
    await db_session.flush()

    async def never_called(*args, **kwargs):
        raise AssertionError("an administrator must not be checked against the channel")

    monkeypatch.setattr(membership_module, "is_channel_member", never_called)

    allowed, _ = await membership_module.check_access(db_session, object(), user)
    assert allowed is True


async def test_a_telegram_failure_fails_open(db_session, monkeypatch):
    """
    A channel the bot cannot see must not lock the platform. The
    alternative is one misconfiguration taking every user offline with no
    visible cause.
    """
    from aiogram.exceptions import TelegramBadRequest

    user = await make_user(db_session, 9923)
    await set_membership_config(db_session, enabled=True, channel="@missing")
    await db_session.flush()

    class FailingBot:
        async def get_chat_member(self, **kwargs):
            raise TelegramBadRequest(method=None, message="chat not found")

    async def no_cache(*args, **kwargs):
        return None

    # Redis is not part of this suite; the cache lookup is stubbed so the
    # test exercises the Telegram failure path, which is the point.
    monkeypatch.setattr(membership_module, "get_redis", lambda: _NullRedis())

    allowed, _ = await membership_module.check_access(db_session, FailingBot(), user)
    assert allowed is True


class _NullRedis:
    """Minimal stand-in: nothing cached, and writes go nowhere."""

    async def get(self, key):
        return None

    async def setex(self, key, ttl, value):
        return None

    async def delete(self, key):
        return None


# ---------- what the bot gate lets through ----------


def _callback(data: str):
    from aiogram.types import CallbackQuery, User as TelegramUser

    return CallbackQuery(
        id="1",
        from_user=TelegramUser(id=1, is_bot=False, first_name="x"),
        chat_instance="c",
        data=data,
    )


def test_start_is_never_gated_by_membership():
    """
    Registration has to complete: a user with no row has no language, no
    referral capture, and no way to be told anything.
    """
    from types import SimpleNamespace

    from app.bot.middlewares.access import _is_exempt

    assert _is_exempt(SimpleNamespace(text="/start REF_ABC")) is True
    assert _is_exempt(SimpleNamespace(text="Kinolar")) is False


def test_the_recheck_and_language_buttons_are_exempt():
    """
    The recheck exists to be pressed by someone not yet through the gate,
    and the language picker is the second half of /start.
    """
    from app.bot.middlewares.access import MEMBERSHIP_CHECK_CALLBACK, _is_exempt

    assert _is_exempt(_callback(MEMBERSHIP_CHECK_CALLBACK)) is True
    assert _is_exempt(_callback("setlang:ru")) is True
    assert _is_exempt(_callback("browse:page:2")) is False
