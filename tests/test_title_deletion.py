"""
Deleting a title, and everything that hangs off one.

The bug this pins: `delete_title` removed episodes, media files and
translations, but left `chp_watch_history`, `chp_favorites` and
`chp_title_collections` behind. Neither of the first two foreign keys
cascades, so the moment anyone had watched or saved the film, deleting it
raised ForeignKeyViolationError. The request 500'd, the admin panel's
`.catch(() => undefined)` swallowed it, the list refreshed, and the title
was still there.

A title nobody had touched deleted perfectly — which is exactly why it
survived. It only failed for films that had an audience, so the fixture
below deliberately gives the doomed title one.

There is no soft-delete convention here to preserve: `is_active` exists
and is what the Faol/Yashirin toggle flips, but the delete route has
always been a real row delete, and the admin list is not filtered by
`is_active` unless asked. So this fixes the hard delete rather than
inventing a soft one.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.auth import get_current_user
from app.db.models.content import (
    AudioLanguage,
    Collection,
    ContentType,
    Episode,
    Favorite,
    MediaFile,
    Title,
    VideoQuality,
    WatchHistory,
    title_collections,
)
from app.db.models.user import UserRole
from app.db.session import get_db_session
from app.main import app
from app.services.admin_content import admin_content_service
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _watched_title(session, user, name: str) -> tuple[Title, Collection]:
    """A title with the full set of dependents a real, watched film has."""
    title = Title(name=name, content_type=ContentType.FILM, is_active=True)
    session.add(title)
    await session.flush()

    episode = Episode(title_id=title.id, season=1, number=1)
    collection = Collection(name=f"C{name}", slug=f"c-{name.lower()}")
    session.add_all([episode, collection])
    await session.flush()

    session.add_all(
        [
            MediaFile(
                episode_id=episode.id,
                file_id=f"f{episode.id}",
                language=AudioLanguage.UZ_DUB,
                quality=VideoQuality.HD_720,
            ),
            Favorite(user_id=user.id, title_id=title.id),
            WatchHistory(user_id=user.id, title_id=title.id, episode_id=episode.id),
        ]
    )
    await session.execute(
        title_collections.insert().values(title_id=title.id, collection_id=collection.id)
    )
    await session.flush()
    return title, collection


async def _count(session, model, **filters) -> int:
    stmt = select(func.count()).select_from(model)
    for column, value in filters.items():
        stmt = stmt.where(getattr(model.c if hasattr(model, "c") else model, column) == value)
    return (await session.execute(stmt)).scalar_one()


# ---------- the service ----------


async def test_a_watched_title_can_be_deleted(db_session):
    """
    The regression. Before the fix this raised ForeignKeyViolationError
    and the title survived.
    """
    user = await make_user(db_session, 9801)
    title, _ = await _watched_title(db_session, user, "Doomed")
    title_id = title.id

    assert await admin_content_service.delete_title(db_session, title_id) is True
    await db_session.flush()

    assert (
        await db_session.execute(select(Title.id).where(Title.id == title_id))
    ).scalar_one_or_none() is None


async def test_deleting_a_title_leaves_no_orphans(db_session):
    user = await make_user(db_session, 9802)
    title, _ = await _watched_title(db_session, user, "Orphans")
    title_id = title.id

    await admin_content_service.delete_title(db_session, title_id)
    await db_session.flush()

    assert await _count(db_session, Favorite, title_id=title_id) == 0
    assert await _count(db_session, WatchHistory, title_id=title_id) == 0
    links = (
        await db_session.execute(
            select(func.count())
            .select_from(title_collections)
            .where(title_collections.c.title_id == title_id)
        )
    ).scalar_one()
    assert links == 0


async def test_deleting_one_title_does_not_touch_another(db_session):
    """The blast radius has to stop at the title being removed."""
    user = await make_user(db_session, 9803)
    doomed, _ = await _watched_title(db_session, user, "Doomed")
    keeper, keeper_collection = await _watched_title(db_session, user, "Keeper")
    doomed_id, keeper_id, collection_id = doomed.id, keeper.id, keeper_collection.id

    await admin_content_service.delete_title(db_session, doomed_id)
    await db_session.flush()

    assert (
        await db_session.execute(select(Title.id).where(Title.id == keeper_id))
    ).scalar_one_or_none() is not None
    assert await _count(db_session, Favorite, title_id=keeper_id) == 1
    assert await _count(db_session, WatchHistory, title_id=keeper_id) == 1
    # A collection outlives the titles in it — deleting a film must not
    # take the "Marvel" rail with it.
    assert (
        await db_session.execute(select(Collection.id).where(Collection.id == collection_id))
    ).scalar_one_or_none() is not None


async def test_deleting_a_title_that_does_not_exist_reports_false(db_session):
    assert await admin_content_service.delete_title(db_session, 10_000_001) is False


# ---------- the route ----------


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


async def test_an_admin_can_delete_and_the_title_stays_deleted(db_session, as_user):
    """
    Through the real route, then re-queried — a delete that "worked" but
    reappeared on the next list load is the bug being fixed.
    """
    admin = await make_user(db_session, 9804)
    admin.role = UserRole.SUPER_ADMIN
    viewer = await make_user(db_session, 9805)
    title, _ = await _watched_title(db_session, viewer, "Doomed")
    title_id = title.id
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.delete(f"/api/admin/titles/{title_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        # A fresh listing, as the panel does after deleting.
        listing = (await client.get("/api/admin/titles", params={"q": "Doomed"})).json()

    assert [item["id"] for item in listing["items"]] == []
    assert (
        await db_session.execute(select(Title.id).where(Title.id == title_id))
    ).scalar_one_or_none() is None


async def test_deleting_a_missing_title_is_a_404(db_session, as_user):
    admin = await make_user(db_session, 9806)
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.delete("/api/admin/titles/10000002")

    assert response.status_code == 404


# ---------- authorization ----------


async def test_an_ordinary_user_cannot_delete_a_title(db_session, as_user):
    user = await make_user(db_session, 9807)
    title, _ = await _watched_title(db_session, user, "Safe")
    title_id = title.id
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.delete(f"/api/admin/titles/{title_id}")

    assert response.status_code in (401, 403)
    assert (
        await db_session.execute(select(Title.id).where(Title.id == title_id))
    ).scalar_one_or_none() is not None, "the title must survive a refused delete"


async def test_a_forged_identity_in_the_body_cannot_delete(db_session, as_user):
    """
    Authorization comes from verified initData, never from the request.
    The body below claims to be a super admin; the caller is not one.
    """
    admin = await make_user(db_session, 9808)
    admin.role = UserRole.SUPER_ADMIN
    attacker = await make_user(db_session, 9809)
    title, _ = await _watched_title(db_session, attacker, "Safe")
    title_id = title.id
    await db_session.commit()

    async with as_user(attacker) as client:
        response = await client.request(
            "DELETE",
            f"/api/admin/titles/{title_id}",
            json={"user_id": admin.id, "telegram_id": admin.telegram_id, "role": "SUPER_ADMIN"},
        )

    assert response.status_code in (401, 403)
    assert (
        await db_session.execute(select(Title.id).where(Title.id == title_id))
    ).scalar_one_or_none() is not None


# ---------- the frontend contract ----------


def test_the_admin_panel_surfaces_a_failed_delete():
    """
    Source assertion, following the DecorationLayer precedent — this
    repository has no frontend test runner.

    The delete handler must not swallow its error. It used to end in
    `.catch(() => undefined)` and then reload, so a 500 looked exactly
    like success and the title silently came back.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    source = (root / "webapp" / "src" / "admin" / "ContentPanel.tsx").read_text(encoding="utf-8")
    handler = source.split("const handleDelete", 1)[1].split("const handle", 1)[0]
    # Comments explain the old bug and quote the code being asserted
    # against, so they have to come out before matching on it.
    code = "\n".join(
        line for line in handler.splitlines() if not line.strip().startswith("//")
    )

    assert "catch (err)" in code, "a failed delete must be caught and reported"
    assert "setError" in code, "a failed delete must be shown to the operator"
    assert ".catch(() => undefined)" not in code, "the delete error must not be swallowed"
    assert "deleteTitle" in code, "delete must go through the existing admin API"
