"""
Collection listing, at the service layer and through the REST API.

Written because `list_collections` was once left with a docstring for a
body — another method had been inserted directly after it, swallowing its
code — and *every other gate stayed green*. The module imported, the
schema was unchanged, and nothing in the suite called it, so a method
that silently returned `None` reached a commit-ready diff. The three live
callers (the bot's Collections menu, `/collections`, `/search/all`) would
all have crashed on the first request.

So the assertion these tests exist to make is the dull one: the method
returns a populated list of `CollectionSummary`, and the endpoints that
render it answer 200. The title counts and filtering are checked in the
same pass because a count of zero everywhere would be the same kind of
quiet wrong.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.db.models.content import (
    AudioLanguage,
    Collection,
    ContentType,
    Episode,
    MediaFile,
    Title,
    VideoQuality,
    title_collections,
)
from app.db.models.subscription import UploadedImage
from app.db.session import get_db_session
from app.main import app
from app.services.content import CollectionSummary, content_service
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _playable_title(session, name: str) -> Title:
    """A title that passes the playable-file gate the count relies on."""
    title = Title(content_type=ContentType.FILM, name=name, is_active=True)
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


async def _collection(session, name: str, slug: str, is_active: bool = True) -> Collection:
    collection = Collection(name=name, slug=slug, is_active=is_active, sort_order=0)
    session.add(collection)
    await session.flush()
    return collection


async def _add_to(session, title: Title, collection: Collection) -> None:
    await session.execute(
        title_collections.insert().values(title_id=title.id, collection_id=collection.id)
    )
    await session.flush()


@pytest.fixture
async def client(db_session):
    user = await make_user(db_session, 9871)
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


async def test_list_collections_returns_summaries(db_session):
    """
    The regression guard: a list of CollectionSummary, never None.

    Deliberately asserts the type rather than only truthiness — the broken
    version returned None, which `assert not result` would have accepted
    just as happily as an empty catalog does.
    """
    collection = await _collection(db_session, "Marvel", "marvel")
    title = await _playable_title(db_session, "Iron Man")
    await _add_to(db_session, title, collection)

    result = await content_service.list_collections(db_session)

    assert isinstance(result, list)
    assert all(isinstance(item, CollectionSummary) for item in result)
    assert [item.collection.slug for item in result] == ["marvel"]


async def test_list_collections_counts_only_playable_titles(db_session):
    """A title with no media file is not advertised by the count."""
    collection = await _collection(db_session, "Action", "action")
    playable = await _playable_title(db_session, "Die Hard")
    await _add_to(db_session, playable, collection)

    unplayable = Title(content_type=ContentType.FILM, name="Unreleased", is_active=True)
    db_session.add(unplayable)
    await db_session.flush()
    await _add_to(db_session, unplayable, collection)

    result = await content_service.list_collections(db_session)

    assert [(item.collection.slug, item.title_count) for item in result] == [("action", 1)]


async def test_list_collections_excludes_inactive(db_session):
    """Inactive collections belong to the admin panel, not the catalog."""
    await _collection(db_session, "Hidden", "hidden", is_active=False)
    await _collection(db_session, "Shown", "shown")

    result = await content_service.list_collections(db_session)

    assert [item.collection.slug for item in result] == ["shown"]


# ---------- REST ----------
#
# These two uncovered a second, older defect: `_collection_out` reads
# `collection.poster_image_id`, which migration f6b2d94ae713 had added to
# the table while the model declaration was missed. Both endpoints
# answered 500 on every request. The column is declared now, and these
# tests are the standing check that they answer at all.


async def test_collections_endpoint_returns_the_collection(client, db_session):
    """`GET /collections` — one of the two routes the broken version broke."""
    ac, _user = client
    collection = await _collection(db_session, "Marvel", "marvel")
    title = await _playable_title(db_session, "Thor")
    await _add_to(db_session, title, collection)

    response = await ac.get("/api/movies/collections")

    assert response.status_code == 200
    body = response.json()
    assert [item["slug"] for item in body] == ["marvel"]


async def test_uploaded_poster_overrides_the_url(client, db_session):
    """
    An uploaded poster wins over poster_url and is served by image id.

    The behaviour the missing column actually cost: the admin panel's
    upload wrote `poster_image_id` to an ORM instance that had no such
    attribute, so it never reached the database and the collection kept
    rendering its old poster_url with no error anywhere.
    """
    ac, _user = client
    image = UploadedImage(data=b"bytes", content_type="image/jpeg", byte_size=5)
    db_session.add(image)
    await db_session.flush()

    collection = await _collection(db_session, "Marvel", "marvel")
    collection.poster_url = "https://tmdb.example/old.jpg"
    collection.poster_image_id = image.id
    title = await _playable_title(db_session, "Thor")
    await _add_to(db_session, title, collection)
    await db_session.flush()

    response = await ac.get("/api/movies/collections")

    assert response.status_code == 200
    assert response.json()[0]["poster_url"] == f"/api/movies/images/{image.id}"


async def test_search_all_returns_matching_collections(client, db_session):
    """`GET /search/all` — the other caller, which renders collections too."""
    ac, _user = client
    collection = await _collection(db_session, "Marvel", "marvel")
    title = await _playable_title(db_session, "Thor")
    await _add_to(db_session, title, collection)

    response = await ac.get("/api/movies/search/all", params={"q": "marv"})

    assert response.status_code == 200
    assert [item["slug"] for item in response.json()["collections"]] == ["marvel"]
