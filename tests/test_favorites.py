"""
Favourites, at the service layer and through the REST API.

The Mini App is the new caller; the bot has used the same service since
the first release. Both surfaces are asserted against the same rules —
one row per (user, title) however many times the heart is tapped, films
and serials treated alike, and a title that has become unwatchable
disappearing from the saved list rather than sitting there unplayable.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.db.models.content import (
    AudioLanguage,
    ContentType,
    Episode,
    Favorite,
    MediaFile,
    Title,
    VideoQuality,
)
from app.db.session import get_db_session
from app.main import app
from app.services.content import content_service
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _playable_title(session, name: str, episodes: int = 1) -> Title:
    content_type = ContentType.FILM if episodes == 1 else ContentType.SERIAL
    title = Title(content_type=content_type, name=name, is_active=True)
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
async def client(db_session):
    user = await make_user(db_session, 9801)
    await db_session.commit()

    async def override_session():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, user
    app.dependency_overrides.clear()


# ---------- service ----------


async def test_toggling_saves_then_unsaves(db_session):
    user = await make_user(db_session, 9810)
    title = await _playable_title(db_session, "A")

    assert await content_service.toggle_favorite(db_session, user.id, title.id) is True
    assert await content_service.toggle_favorite(db_session, user.id, title.id) is False
    assert await count_rows(db_session, Favorite, user_id=user.id) == 0


async def test_saving_twice_leaves_one_row(db_session):
    """
    The unique constraint is the guarantee; the ON CONFLICT insert is what
    keeps a double-tap from raising instead of settling on a state.
    """
    user = await make_user(db_session, 9811)
    title = await _playable_title(db_session, "B")

    await content_service.toggle_favorite(db_session, user.id, title.id)
    await content_service.remove_favorite(db_session, user.id, title.id)
    await content_service.toggle_favorite(db_session, user.id, title.id)

    assert await count_rows(db_session, Favorite, user_id=user.id, title_id=title.id) == 1


async def test_removing_is_idempotent(db_session):
    """A stale saved-list must not re-add the title it is removing."""
    user = await make_user(db_session, 9812)
    title = await _playable_title(db_session, "C")

    await content_service.toggle_favorite(db_session, user.id, title.id)
    assert await content_service.remove_favorite(db_session, user.id, title.id) is True
    assert await content_service.remove_favorite(db_session, user.id, title.id) is False
    assert await count_rows(db_session, Favorite, user_id=user.id) == 0


async def test_favourites_are_per_user(db_session):
    first = await make_user(db_session, 9813)
    second = await make_user(db_session, 9814)
    title = await _playable_title(db_session, "D")

    await content_service.toggle_favorite(db_session, first.id, title.id)

    assert await content_service.is_favorite(db_session, second.id, title.id) is False
    assert (await content_service.list_favorites(db_session, second.id)).titles == []


async def test_a_serial_is_saved_once_not_per_episode(db_session):
    user = await make_user(db_session, 9815)
    series = await _playable_title(db_session, "S", episodes=8)

    await content_service.toggle_favorite(db_session, user.id, series.id)
    saved = await content_service.list_favorites(db_session, user.id)

    assert [t.id for t in saved.titles] == [series.id]


async def test_an_unplayable_title_drops_out_of_the_list(db_session):
    """
    Same playable-file gate as browsing. A saved title whose files were
    removed would otherwise sit in the list and fail on play.
    """
    user = await make_user(db_session, 9816)
    empty = Title(content_type=ContentType.FILM, name="No files", is_active=True)
    db_session.add(empty)
    await db_session.flush()

    await content_service.toggle_favorite(db_session, user.id, empty.id)
    assert (await content_service.list_favorites(db_session, user.id)).titles == []


# ---------- REST API ----------


async def test_the_api_toggles_and_reports_state(client, db_session):
    test_client, _ = client
    title = await _playable_title(db_session, "E")
    await db_session.commit()

    saved = await test_client.post(f"/api/movies/{title.id}/favorite")
    assert saved.status_code == 200
    assert saved.json() == {"title_id": title.id, "is_favorite": True}

    unsaved = await test_client.post(f"/api/movies/{title.id}/favorite")
    assert unsaved.json()["is_favorite"] is False


async def test_the_delete_route_never_re_adds(client, db_session):
    test_client, user = client
    title = await _playable_title(db_session, "F")
    await db_session.commit()

    await test_client.post(f"/api/movies/{title.id}/favorite")
    for _ in range(2):
        response = await test_client.delete(f"/api/movies/{title.id}/favorite")
        assert response.json()["is_favorite"] is False
    assert await count_rows(db_session, Favorite, user_id=user.id) == 0


async def test_saving_an_unknown_title_is_refused(client):
    test_client, _ = client
    response = await test_client.post("/api/movies/99999/favorite")
    assert response.status_code == 404


async def test_the_saved_list_endpoint_returns_saved_titles(client, db_session):
    test_client, _ = client
    title = await _playable_title(db_session, "G")
    await db_session.commit()

    await test_client.post(f"/api/movies/{title.id}/favorite")
    listed = (await test_client.get("/api/movies/favorites")).json()

    assert [item["id"] for item in listed] == [title.id]
    assert listed[0]["is_favorite"] is True


async def test_catalog_responses_carry_the_saved_flag(client, db_session):
    """
    The flag rides on the card so a row of results renders with the right
    hearts from one response, rather than one request per card.
    """
    test_client, _ = client
    saved_title = await _playable_title(db_session, "H")
    other = await _playable_title(db_session, "I")
    await db_session.commit()

    await test_client.post(f"/api/movies/{saved_title.id}/favorite")
    listing = {item["id"]: item["is_favorite"] for item in (await test_client.get("/api/movies")).json()}

    assert listing[saved_title.id] is True
    assert listing[other.id] is False


async def test_an_unauthenticated_caller_is_refused(db_session):
    """No initData header, no identity — favourites are per user by definition."""
    async def override_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        assert (await ac.get("/api/movies/favorites")).status_code == 422
        assert (await ac.post("/api/movies/1/favorite")).status_code == 422
    app.dependency_overrides.clear()
