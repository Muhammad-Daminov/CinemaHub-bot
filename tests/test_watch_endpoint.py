"""
POST /api/movies/{id}/watch — the episode guarantee, tested at the route.

Deliberately an endpoint test rather than a service one. The rule being
protected is *"the backend must never silently start episode 1 of a
serial"*, and a rule that only holds in the UI is not the rule. Testing
the service layer would prove the pieces work while leaving the actual
guarantee — what the HTTP boundary does with a missing `episode_id` —
unverified.

Auth and the database session are overridden rather than faked at the
HTTP layer: `initData` verification has its own tests, and repeating it
here would test Telegram's algorithm instead of this route's logic.

Driven through httpx's ASGITransport rather than TestClient. TestClient
runs the app on its own event loop, and the asyncpg connection behind
`db_session` belongs to the test's loop — sharing one across the two
fails with "attached to a different loop". ASGITransport also skips the
lifespan, which is what we want: startup would reach for the production
database and register a webhook.
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
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _playable_title(session, *, episodes: int, content_type=ContentType.SERIAL) -> Title:
    """A title with `episodes` episodes, each carrying a file so it is deliverable."""
    title = Title(content_type=content_type, name=f"T{episodes}", is_active=True)
    session.add(title)
    await session.flush()
    for number in range(1, episodes + 1):
        episode = Episode(title_id=title.id, season=1, number=number)
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


@pytest.fixture
async def client(db_session, monkeypatch):
    """
    An HTTP client wired to the test session and a stub viewer.

    `deliver_episode` is stubbed so the suite never calls Telegram, and so
    the assertions are about *which episode was selected* rather than
    about delivery, which has its own coverage.
    """
    user = await make_user(db_session, 5150)
    await db_session.commit()

    delivered: list[Episode] = []

    async def fake_deliver(*, episode, **kwargs):
        delivered.append(episode)
        return None

    from app.services import streaming as streaming_module

    monkeypatch.setattr(streaming_module.streaming_service, "deliver_episode", fake_deliver)

    async def override_session():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, delivered
    app.dependency_overrides.clear()


# ---------- films: unchanged behaviour ----------


async def test_film_plays_without_an_episode_id(client, db_session):
    """The existing movie flow must keep working exactly as before."""
    test_client, delivered = client
    film = await _playable_title(db_session, episodes=1, content_type=ContentType.FILM)
    await db_session.commit()

    response = await test_client.post(f"/api/movies/{film.id}/watch")
    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert len(delivered) == 1


async def test_single_episode_serial_still_plays_without_an_id(db_session, client):
    """The rule keys off episode count, not content type — a one-part serial is unambiguous."""
    test_client, delivered = client
    title = await _playable_title(db_session, episodes=1)
    await db_session.commit()

    assert (await test_client.post(f"/api/movies/{title.id}/watch")).status_code == 200
    assert len(delivered) == 1


# ---------- series: the guarantee ----------


async def test_series_without_an_episode_id_is_refused(db_session, client):
    """
    The point of this file. Previously this silently delivered episode 1,
    which is wrong for a viewer resuming at episode 40.
    """
    test_client, delivered = client
    series = await _playable_title(db_session, episodes=5)
    await db_session.commit()

    response = await test_client.post(f"/api/movies/{series.id}/watch")
    assert response.status_code == 422
    assert delivered == [], "nothing may be delivered when the episode is ambiguous"


async def test_the_refusal_is_translated(db_session, client):
    """This detail is rendered verbatim into a toast, so it cannot be English-only."""
    test_client, _ = client
    series = await _playable_title(db_session, episodes=3)
    await db_session.commit()

    detail = (await test_client.post(f"/api/movies/{series.id}/watch")).json()["detail"]
    assert detail and not detail.startswith("app."), "should be a resolved string, not a key"


async def test_series_plays_the_requested_episode(db_session, client):
    """Series playback through the Mini App must continue to work."""
    test_client, delivered = client
    series = await _playable_title(db_session, episodes=5)
    await db_session.commit()

    episodes = (await content_episodes(db_session, series.id))
    target = episodes[3]

    response = await test_client.post(f"/api/movies/{series.id}/watch?episode_id={target.id}")
    assert response.status_code == 200
    assert [e.id for e in delivered] == [target.id], "must deliver the episode that was asked for"


async def test_episode_from_another_title_is_refused(db_session, client):
    """The ownership boundary, asserted at the HTTP layer this time."""
    test_client, delivered = client
    mine = await _playable_title(db_session, episodes=2)
    theirs = await _playable_title(db_session, episodes=2)
    await db_session.commit()

    foreign = (await content_episodes(db_session, theirs.id))[0]
    response = await test_client.post(f"/api/movies/{mine.id}/watch?episode_id={foreign.id}")
    assert response.status_code == 404
    assert delivered == []


async def test_unknown_title_is_refused(client):
    test_client, delivered = client
    assert (await test_client.post("/api/movies/999999/watch")).status_code == 404
    assert delivered == []


async def content_episodes(session, title_id: int) -> list[Episode]:
    from sqlalchemy import select

    result = await session.execute(
        select(Episode).where(Episode.title_id == title_id).order_by(Episode.number)
    )
    return list(result.scalars())
