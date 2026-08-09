"""
Manual gallery poster upload, and its precedence over TMDB.

The reported bug: an admin picked a poster from the gallery, and the
editor reverted to showing the TMDB poster as if the upload had failed.
The upload had in fact succeeded — the image was stored and
`poster_image_id` was set. What failed was the *refresh*: the editor
re-read the title it was editing by scanning the first page of the
paginated admin list, and production holds 102 titles against a 100-row
page cap. Every title outside the newest 100 was therefore invisible to
that refresh, so the editor never learned the new `poster_image_id` and
carried on rendering TMDB's URL.

`test_a_title_outside_the_first_page_is_still_reachable` is the
regression test for that specific failure; the rest pin the precedence
rules the request asks for.
"""
import io

import pytest
from datetime import datetime, timedelta, timezone
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.api.auth import get_current_user
from app.db.models.content import ContentType, Title
from app.db.models.user import UserRole
from app.db.session import get_db_session
from app.main import app
from app.services.admin_content import admin_content_service
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]

PAGE_CAP = 100  # the admin list's maximum page_size


def _jpeg(colour=(10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 60), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


async def _title(session, name="Qum sayyorasi", *, tmdb_poster="https://image.tmdb.org/p.jpg", age_days=0):
    title = Title(
        content_type=ContentType.FILM,
        name=name,
        is_active=True,
        poster_url=tmdb_poster,
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    session.add(title)
    await session.flush()
    return title


@pytest.fixture
async def admin_client(db_session):
    admin = await make_user(db_session, 9301)
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async def override_session():
        yield db_session

    async def override_user():
        return admin

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------- the upload itself ----------


async def test_an_uploaded_poster_is_stored_and_returned(admin_client, db_session):
    title = await _title(db_session)
    await db_session.commit()

    response = await admin_client.post(
        f"/api/admin/titles/{title.id}/poster",
        files={"poster": ("poster.jpg", _jpeg(), "image/jpeg")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "uploaded"
    # The response carries the stored id, so the picker can switch to the
    # new poster without depending on a later refresh succeeding.
    assert body["image_id"] > 0

    await db_session.refresh(title)
    assert title.poster_image_id == body["image_id"]


async def test_the_uploaded_poster_survives_a_re_read(admin_client, db_session):
    """What the editor does after uploading — and what used to come back stale."""
    title = await _title(db_session)
    await db_session.commit()

    uploaded = (
        await admin_client.post(
            f"/api/admin/titles/{title.id}/poster",
            files={"poster": ("poster.jpg", _jpeg(), "image/jpeg")},
        )
    ).json()

    reread = (await admin_client.get(f"/api/admin/titles/{title.id}")).json()
    assert reread["poster_image_id"] == uploaded["image_id"], (
        "the refresh must report the poster that was just uploaded"
    )


async def test_a_failed_upload_reports_the_real_error(admin_client, db_session):
    """
    A rejected file must say why. Silently doing nothing is what made the
    original bug look like an unexplained revert to TMDB.
    """
    title = await _title(db_session)
    await db_session.commit()

    response = await admin_client.post(
        f"/api/admin/titles/{title.id}/poster",
        files={"poster": ("notes.txt", b"this is not an image", "text/plain")},
    )
    assert response.status_code == 422
    assert response.json()["detail"], "the upload error must reach the client"

    await db_session.refresh(title)
    assert title.poster_image_id is None, "a rejected upload must not half-apply"


async def test_uploading_to_an_unknown_title_is_refused(admin_client):
    response = await admin_client.post(
        "/api/admin/titles/999999/poster",
        files={"poster": ("poster.jpg", _jpeg(), "image/jpeg")},
    )
    assert response.status_code == 404


# ---------- the regression: reachable regardless of catalog size ----------


async def test_a_title_outside_the_first_page_is_still_reachable(admin_client, db_session):
    """
    The root cause, pinned. With more titles than one page holds, the
    editor's refresh must still find the row it is editing.
    """
    target = await _title(db_session, "Eng eski", age_days=500)
    for index in range(PAGE_CAP + 5):
        await _title(db_session, f"Yangi {index}", age_days=0)
    await db_session.commit()

    uploaded = (
        await admin_client.post(
            f"/api/admin/titles/{target.id}/poster",
            files={"poster": ("poster.jpg", _jpeg(), "image/jpeg")},
        )
    ).json()

    # The old refresh path: scan the newest page and hope.
    page = (await admin_client.get(f"/api/admin/titles?page_size={PAGE_CAP}")).json()
    assert not any(item["id"] == target.id for item in page["items"]), (
        "precondition: this title is outside the first page, as in production"
    )

    # The fixed refresh path: ask for it by id.
    fetched = (await admin_client.get(f"/api/admin/titles/{target.id}")).json()
    assert fetched["poster_image_id"] == uploaded["image_id"]


async def test_fetching_an_unknown_title_by_id_is_a_clean_404(admin_client):
    assert (await admin_client.get("/api/admin/titles/999999")).status_code == 404


async def test_the_similar_route_is_not_swallowed_by_the_id_route(admin_client, db_session):
    """
    `/titles/similar` is a literal path declared before `/titles/{id}`.
    Reversing that order would make it parse as a title id and 422.
    """
    await _title(db_session, "Qum sayyorasi")
    await db_session.commit()

    response = await admin_client.get("/api/admin/titles/similar?name=Qum")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# ---------- precedence over TMDB ----------


async def test_a_manual_poster_wins_over_the_tmdb_url(admin_client, db_session):
    """The viewer-facing rule: an uploaded poster is served, TMDB is fallback."""
    from app.api.movies import _poster_for

    title = await _title(db_session, tmdb_poster="https://image.tmdb.org/tmdb.jpg")
    await db_session.commit()

    uploaded = (
        await admin_client.post(
            f"/api/admin/titles/{title.id}/poster",
            files={"poster": ("poster.jpg", _jpeg(), "image/jpeg")},
        )
    ).json()
    await db_session.refresh(title)

    assert _poster_for(title) == f"/api/movies/images/{uploaded['image_id']}"
    assert title.poster_url == "https://image.tmdb.org/tmdb.jpg", "TMDB's URL is kept as fallback"


async def test_tmdb_enrichment_does_not_overwrite_a_manual_poster(admin_client, db_session, monkeypatch):
    """
    Enrichment refreshes the *fallback*, never the upload. An admin who
    chose a poster must not have it replaced by a background enrichment.
    """
    from app.services import admin_content as module

    class FakeTMDB:
        async def search_movie(self, *args, **kwargs):
            return [{"id": 42}]

        async def get_movie_details(self, tmdb_id, language=None):
            return {
                "id": tmdb_id,
                "title": "Dune",
                "overview": "…",
                "poster_path": "/from-tmdb.jpg",
                "vote_average": 8.1,
                "genres": [],
                "release_date": "2021-01-01",
            }

        @staticmethod
        def build_poster_url(path, size="w500"):
            return f"https://image.tmdb.org/t/p/{size}{path}"

    monkeypatch.setattr(module, "tmdb_service", FakeTMDB())

    title = await _title(db_session)
    await db_session.commit()
    uploaded = (
        await admin_client.post(
            f"/api/admin/titles/{title.id}/poster",
            files={"poster": ("poster.jpg", _jpeg(), "image/jpeg")},
        )
    ).json()

    await admin_content_service.enrich_from_tmdb(db_session, title.id)
    await db_session.refresh(title)

    assert title.poster_image_id == uploaded["image_id"], (
        "enrichment must never clear a manually uploaded poster"
    )
    assert "from-tmdb.jpg" in title.poster_url, "only the fallback is refreshed"


async def test_applying_a_tmdb_match_does_not_overwrite_a_manual_poster(admin_client, db_session, monkeypatch):
    """The same guarantee on the explicit 'apply this TMDB record' path."""
    from app.services import admin_content as module

    class FakeTMDB:
        async def get_movie_details(self, tmdb_id, language=None):
            return {
                "id": tmdb_id,
                "title": "Dune",
                "overview": "…",
                "poster_path": "/picked.jpg",
                "vote_average": 8.1,
                "genres": [],
            }

        @staticmethod
        def build_poster_url(path, size="w500"):
            return f"https://image.tmdb.org/t/p/{size}{path}"

    monkeypatch.setattr(module, "tmdb_service", FakeTMDB())

    title = await _title(db_session)
    await db_session.commit()
    uploaded = (
        await admin_client.post(
            f"/api/admin/titles/{title.id}/poster",
            files={"poster": ("poster.jpg", _jpeg(), "image/jpeg")},
        )
    ).json()

    await admin_content_service.apply_tmdb_match(db_session, title.id, 42)
    await db_session.refresh(title)

    assert title.poster_image_id == uploaded["image_id"]


async def test_clearing_the_upload_reverts_to_tmdb(admin_client, db_session):
    """Explicit removal is the only way back to TMDB — and it must work."""
    from app.api.movies import _poster_for

    title = await _title(db_session, tmdb_poster="https://image.tmdb.org/tmdb.jpg")
    await db_session.commit()
    await admin_client.post(
        f"/api/admin/titles/{title.id}/poster",
        files={"poster": ("poster.jpg", _jpeg(), "image/jpeg")},
    )

    assert (await admin_client.delete(f"/api/admin/titles/{title.id}/poster")).status_code == 200
    await db_session.refresh(title)

    assert title.poster_image_id is None
    assert _poster_for(title) == "https://image.tmdb.org/tmdb.jpg"


async def test_replacing_a_poster_keeps_the_newest(admin_client, db_session):
    title = await _title(db_session)
    await db_session.commit()

    first = (
        await admin_client.post(
            f"/api/admin/titles/{title.id}/poster",
            files={"poster": ("a.jpg", _jpeg((200, 10, 10)), "image/jpeg")},
        )
    ).json()
    second = (
        await admin_client.post(
            f"/api/admin/titles/{title.id}/poster",
            files={"poster": ("b.jpg", _jpeg((10, 200, 10)), "image/jpeg")},
        )
    ).json()

    assert second["image_id"] != first["image_id"]
    await db_session.refresh(title)
    assert title.poster_image_id == second["image_id"]


async def test_saving_the_title_afterwards_keeps_the_uploaded_poster(admin_client, db_session):
    """
    The editor saves the text form separately from the poster. An ordinary
    save must not drop the upload.
    """
    title = await _title(db_session)
    await db_session.commit()
    uploaded = (
        await admin_client.post(
            f"/api/admin/titles/{title.id}/poster",
            files={"poster": ("poster.jpg", _jpeg(), "image/jpeg")},
        )
    ).json()

    response = await admin_client.patch(
        f"/api/admin/titles/{title.id}",
        json={"name": "Qum sayyorasi 2", "poster_url": "https://image.tmdb.org/other.jpg"},
    )
    assert response.status_code == 200
    assert response.json()["poster_image_id"] == uploaded["image_id"]


# ---------- a title with no TMDB match ----------


async def test_a_title_with_no_tmdb_match_accepts_a_gallery_poster(admin_client, db_session):
    """
    The case the request is really about: much of this catalog is regional
    content TMDB has never heard of. Such a title must still get a poster,
    and nothing in the upload path may require a TMDB match.
    """
    title = await _title(db_session, "Mahalliy kino", tmdb_poster=None)
    await db_session.commit()
    assert title.tmdb_id is None and title.poster_url is None

    response = await admin_client.post(
        f"/api/admin/titles/{title.id}/poster",
        files={"poster": ("gallery.jpg", _jpeg(), "image/jpeg")},
    )
    assert response.status_code == 200

    fetched = (await admin_client.get(f"/api/admin/titles/{title.id}")).json()
    assert fetched["poster_image_id"] == response.json()["image_id"]
    assert fetched["tmdb_id"] is None, "no TMDB match was needed at any point"


async def test_the_uploaded_poster_is_what_the_viewer_api_serves(admin_client, db_session):
    """End of the chain: a TMDB-less title shows its uploaded poster to viewers."""
    from app.api.movies import _poster_for

    title = await _title(db_session, "Mahalliy kino", tmdb_poster=None)
    await db_session.commit()
    uploaded = (
        await admin_client.post(
            f"/api/admin/titles/{title.id}/poster",
            files={"poster": ("gallery.jpg", _jpeg(), "image/jpeg")},
        )
    ).json()
    await db_session.refresh(title)

    assert _poster_for(title) == f"/api/movies/images/{uploaded['image_id']}"


# ---------- what a real device sends ----------


@pytest.mark.parametrize(
    "content_type",
    ["image/jpeg", "image/jpg", "application/octet-stream"],
    ids=["standard", "non-standard", "webview gallery pick"],
)
async def test_a_gallery_pick_is_accepted_however_the_browser_labels_it(
    admin_client, db_session, content_type
):
    """
    A Telegram Mini App is a mobile WebView, and those routinely send
    `application/octet-stream` or `image/jpg` for a photo from the gallery.
    Enforcing the label rejected real photos.
    """
    title = await _title(db_session)
    await db_session.commit()

    response = await admin_client.post(
        f"/api/admin/titles/{title.id}/poster",
        files={"poster": ("gallery", _jpeg(), content_type)},
    )
    assert response.status_code == 200, response.text


async def test_an_oversized_upload_is_refused(admin_client, db_session):
    from app.services.images import MAX_UPLOAD_BYTES

    title = await _title(db_session)
    await db_session.commit()

    response = await admin_client.post(
        f"/api/admin/titles/{title.id}/poster",
        files={"poster": ("huge.jpg", b"\x00" * (MAX_UPLOAD_BYTES + 1), "image/jpeg")},
    )
    assert response.status_code == 422
    assert "larger than" in response.json()["detail"]


# ---------- association ----------


async def test_an_upload_attaches_to_the_right_title_only(admin_client, db_session):
    """Two titles edited in one session must not share a poster."""
    first = await _title(db_session, "Birinchi")
    second = await _title(db_session, "Ikkinchi")
    await db_session.commit()

    a = (
        await admin_client.post(
            f"/api/admin/titles/{first.id}/poster",
            files={"poster": ("a.jpg", _jpeg((200, 10, 10)), "image/jpeg")},
        )
    ).json()
    b = (
        await admin_client.post(
            f"/api/admin/titles/{second.id}/poster",
            files={"poster": ("b.jpg", _jpeg((10, 200, 10)), "image/jpeg")},
        )
    ).json()

    await db_session.refresh(first)
    await db_session.refresh(second)
    assert first.poster_image_id == a["image_id"]
    assert second.poster_image_id == b["image_id"]
    assert first.poster_image_id != second.poster_image_id


async def test_a_series_poster_uses_the_same_path_as_a_film(admin_client, db_session):
    """Films and serials are both Titles — one upload path, not two."""
    series = Title(content_type=ContentType.SERIAL, name="Serial", is_active=True)
    db_session.add(series)
    await db_session.flush()
    await db_session.commit()

    response = await admin_client.post(
        f"/api/admin/titles/{series.id}/poster",
        files={"poster": ("poster.jpg", _jpeg(), "image/jpeg")},
    )
    assert response.status_code == 200
    await db_session.refresh(series)
    assert series.poster_image_id == response.json()["image_id"]


async def test_a_collection_poster_takes_the_same_fix(admin_client, db_session):
    """Collections share `store_image`, so the content-type fix reaches them too."""
    from app.db.models.content import Collection

    collection = Collection(name="Marvel", slug="marvel", is_active=True)
    db_session.add(collection)
    await db_session.flush()
    await db_session.commit()

    response = await admin_client.post(
        f"/api/admin/collections/{collection.id}/poster",
        files={"poster": ("gallery", _jpeg(), "application/octet-stream")},
    )
    assert response.status_code == 200
    await db_session.refresh(collection)
    assert collection.poster_image_id == response.json()["image_id"]
