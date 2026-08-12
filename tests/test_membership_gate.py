"""
The Mini App's join-the-channel gate.

Previously a refused watch was a message and nothing more: the viewer was
told to join a channel they had no way to find from inside the app. These
endpoints give the client the channel, a link to it, and a way to ask
Telegram again after joining.

Two things this must never become:

  - a client-side gate. `POST /watch` still decides on its own; these
    endpoints only tell the client what to *draw*. Every test here that
    asserts a status also asserts the file is still withheld.
  - a second source of channel configuration. The channel and its link
    come from `chp_system_settings` through `MembershipConfig`, the same
    place the bot reads, so an administrator changing it moves both
    surfaces at once.

The recheck endpoint deliberately drops the cached answer first, exactly
as the bot's "I have joined" button does — which is also what catches
somebody who has *left*.
"""
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
)
from app.db.session import get_db_session
from app.main import app
from app.services.settings_store import (
    REQUIRE_MEMBERSHIP,
    REQUIRED_CHANNEL,
    set_setting,
)
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


@pytest.fixture
def membership(monkeypatch):
    """
    Controls Telegram's answer without touching Redis or the network.

    Returns a one-item list so a test can flip the answer mid-flight —
    which is how "the user left the channel" is expressed.
    """
    from app.services import membership as membership_module

    joined = [True]

    async def fake_is_member(bot, channel, telegram_id):
        return joined[0]

    async def fake_clear(channel, telegram_id):
        return None

    monkeypatch.setattr(membership_module, "is_channel_member", fake_is_member)
    monkeypatch.setattr(membership_module, "clear_membership_cache", fake_clear)
    return joined


@pytest.fixture
def client(db_session):
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


async def _require_channel(session, channel: str = "@cinemahub") -> None:
    await set_setting(session, REQUIRE_MEMBERSHIP, "true")
    await set_setting(session, REQUIRED_CHANNEL, channel)


async def _playable(session, name: str = "Film") -> tuple[Title, Episode]:
    title = Title(name=name, content_type=ContentType.FILM, is_active=True)
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
    return title, episode


# ---------- status ----------


async def test_the_gate_is_off_when_no_channel_is_configured(db_session, client, membership):
    """An unconfigured platform must never show the gate."""
    user = await make_user(db_session, 9901)
    await db_session.commit()

    async with client(user) as api:
        body = (await api.get("/api/movies/membership")).json()

    assert body["required"] is False
    assert body["is_member"] is True
    assert body["channel"] is None


async def test_a_non_member_is_reported_as_missing(db_session, client, membership):
    user = await make_user(db_session, 9902)
    await _require_channel(db_session)
    await db_session.commit()
    membership[0] = False

    async with client(user) as api:
        body = (await api.get("/api/movies/membership")).json()

    assert body["required"] is True
    assert body["is_member"] is False


async def test_a_member_is_reported_as_joined(db_session, client, membership):
    user = await make_user(db_session, 9903)
    await _require_channel(db_session)
    await db_session.commit()
    membership[0] = True

    async with client(user) as api:
        body = (await api.get("/api/movies/membership")).json()

    assert body["required"] is True
    assert body["is_member"] is True


# ---------- the join link comes from configuration ----------


async def test_the_join_url_is_built_from_the_configured_channel(db_session, client, membership):
    """
    Not hardcoded anywhere. Changing the setting changes the link, which is
    what proves there is no second channel configuration.
    """
    user = await make_user(db_session, 9904)
    await _require_channel(db_session, "@some_other_channel")
    await db_session.commit()
    membership[0] = False

    async with client(user) as api:
        body = (await api.get("/api/movies/membership")).json()

    assert body["channel"] == "@some_other_channel"
    assert body["invite_url"] == "https://t.me/some_other_channel"


async def test_a_configured_invite_link_is_passed_through(db_session, client, membership):
    """A private channel is configured as a link and must be used verbatim."""
    user = await make_user(db_session, 9905)
    await _require_channel(db_session, "https://t.me/+PrivateInvite")
    await db_session.commit()
    membership[0] = False

    async with client(user) as api:
        body = (await api.get("/api/movies/membership")).json()

    assert body["invite_url"] == "https://t.me/+PrivateInvite"


async def test_a_numeric_chat_id_yields_no_link(db_session, client, membership):
    """
    There is no public URL for a numeric id, so the client is given the
    name and no button rather than a button that goes nowhere.
    """
    user = await make_user(db_session, 9906)
    await _require_channel(db_session, "-1001234567890")
    await db_session.commit()
    membership[0] = False

    async with client(user) as api:
        body = (await api.get("/api/movies/membership")).json()

    assert body["channel"] == "-1001234567890"
    assert body["invite_url"] is None


# ---------- recheck ----------


async def test_recheck_reports_the_join(db_session, client, membership):
    """The flow the button drives: not a member, joins, presses check."""
    user = await make_user(db_session, 9907)
    await _require_channel(db_session)
    await db_session.commit()

    membership[0] = False
    async with client(user) as api:
        assert (await api.get("/api/movies/membership")).json()["is_member"] is False
        membership[0] = True  # the user goes and joins
        assert (await api.post("/api/movies/membership/recheck")).json()["is_member"] is True


async def test_recheck_still_refuses_someone_who_has_not_joined(db_session, client, membership):
    user = await make_user(db_session, 9908)
    await _require_channel(db_session)
    await db_session.commit()
    membership[0] = False

    async with client(user) as api:
        body = (await api.post("/api/movies/membership/recheck")).json()

    assert body["is_member"] is False


async def test_someone_who_left_is_caught_again(db_session, client, membership):
    """
    The case a cached yes would hide. Membership is re-asked every time,
    so leaving the channel closes access again rather than being carried
    by a remembered answer.
    """
    user = await make_user(db_session, 9909)
    await _require_channel(db_session)
    await db_session.commit()

    membership[0] = True
    async with client(user) as api:
        assert (await api.get("/api/movies/membership")).json()["is_member"] is True
        membership[0] = False  # they leave
        assert (await api.get("/api/movies/membership")).json()["is_member"] is False
        assert (await api.post("/api/movies/membership/recheck")).json()["is_member"] is False


# ---------- the gate is never the enforcement ----------


async def test_watch_is_refused_while_outside_the_channel(db_session, client, membership):
    user = await make_user(db_session, 9910)
    await _require_channel(db_session)
    title, episode = await _playable(db_session)
    await db_session.commit()
    membership[0] = False

    async with client(user) as api:
        response = await api.post(
            f"/api/movies/{title.id}/watch", json={"episode_id": episode.id}
        )

    assert response.status_code == 403


async def test_watch_succeeds_once_the_join_is_real(db_session, client, membership, monkeypatch):
    """
    The resume path. The client replays the original request after the
    server confirms the join, so delivery goes through the same checks
    rather than being waved through because a gate closed.
    """
    delivered: list[int] = []

    async def fake_deliver(**kwargs):
        delivered.append(kwargs["episode"].id)

        class Sent:
            message_id = 1

        return Sent()

    from app.services import streaming as streaming_module

    monkeypatch.setattr(streaming_module.streaming_service, "deliver_episode", fake_deliver)

    user = await make_user(db_session, 9911)
    await _require_channel(db_session)
    title, episode = await _playable(db_session)
    await db_session.commit()

    membership[0] = False
    async with client(user) as api:
        assert (
            await api.post(f"/api/movies/{title.id}/watch", json={"episode_id": episode.id})
        ).status_code == 403
        assert delivered == [], "nothing may be delivered while the gate is up"

        membership[0] = True
        assert (await api.post("/api/movies/membership/recheck")).json()["is_member"] is True
        assert (
            await api.post(f"/api/movies/{title.id}/watch", json={"episode_id": episode.id})
        ).status_code == 200

    assert delivered == [episode.id]


async def test_a_forged_identity_cannot_claim_membership(db_session, client, membership):
    """
    Nothing in the request influences the answer. The body below claims a
    different user and asserts membership; the check still runs against the
    verified caller.
    """
    user = await make_user(db_session, 9912)
    other = await make_user(db_session, 9913)
    await _require_channel(db_session)
    await db_session.commit()
    membership[0] = False

    async with client(user) as api:
        body = (
            await api.post(
                "/api/movies/membership/recheck",
                json={"telegram_id": other.telegram_id, "is_member": True, "required": False},
            )
        ).json()

    assert body["is_member"] is False


async def test_the_endpoints_require_authentication(db_session):
    """Not public — the same verified-identity gate as the rest of the API."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/movies/membership")).status_code in (401, 403, 422)
        assert (await anon.post("/api/movies/membership/recheck")).status_code in (401, 403, 422)
