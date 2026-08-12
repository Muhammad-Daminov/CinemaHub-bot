"""
Which poster URLs are private, and which are not.

The Mini App renders `movie.poster_url` in an `<img>`, and that field is
two different kinds of thing: a public TMDB link, or a path to our own
authenticated image endpoint when an administrator uploaded artwork. An
`<img>` cannot send the init-data header, so the second kind never
rendered — every uploaded poster was a broken image on every card.

The client-side fix is `useAuthedImage`, which fetches the private ones
with the header and leaves public ones alone. This file pins the two
facts that fix depends on, because both are server-side and both would
silently invalidate it if they changed:

  1. the image endpoint really does require authentication — if it ever
     became public, the blob fetch would be pointless complexity;
  2. a title with no upload still yields the plain external URL, so the
     common case never touches the authenticated path at all.

The hook's own behaviour — revoking object URLs, ignoring a stale
response — is not covered here. This repository has no frontend test
runner (no vitest, jest or testing-library, and no `test` script in
webapp/package.json), so there is nothing that could execute it.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.api.movies import _poster_for
from app.db.models.content import ContentType, Title
from app.db.models.subscription import UploadedImage
from app.db.session import get_db_session
from app.main import app
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _image(session) -> UploadedImage:
    image = UploadedImage(data=b"bytes", content_type="image/jpeg", byte_size=5)
    session.add(image)
    await session.flush()
    return image


@pytest.fixture
def client(db_session):
    def _install(user):
        async def override_session():
            yield db_session

        async def override_user():
            return user

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _install
    app.dependency_overrides.clear()


# ---------- which URL a title yields ----------


async def test_an_uploaded_poster_resolves_to_the_private_endpoint(db_session):
    image = await _image(db_session)
    title = Title(name="Uploaded", content_type=ContentType.FILM, is_active=True)
    title.poster_url = "https://image.tmdb.org/t/p/w500/tmdb.jpg"
    title.poster_image_id = image.id
    db_session.add(title)
    await db_session.flush()

    # The upload wins over TMDB, and it is a path into our own API — which
    # is exactly the case an <img> cannot fetch unaided.
    assert _poster_for(title) == f"/api/movies/images/{image.id}"


async def test_a_tmdb_poster_is_returned_untouched(db_session):
    """
    The common case must not change. `useAuthedImage` returns any non-/api
    URL as-is, so this is the path that does no fetch and creates no blob.
    """
    title = Title(name="Tmdb", content_type=ContentType.FILM, is_active=True)
    title.poster_url = "https://image.tmdb.org/t/p/w500/tmdb.jpg"
    db_session.add(title)
    await db_session.flush()

    resolved = _poster_for(title)
    assert resolved == "https://image.tmdb.org/t/p/w500/tmdb.jpg"
    assert not resolved.startswith("/api/")


async def test_a_title_with_no_artwork_yields_nothing(db_session):
    title = Title(name="Bare", content_type=ContentType.FILM, is_active=True)
    db_session.add(title)
    await db_session.flush()

    assert _poster_for(title) is None


# ---------- the endpoint is genuinely private ----------


async def test_the_image_endpoint_refuses_an_unauthenticated_request(db_session):
    """
    The premise of the whole fix.

    If this ever answers 200 without a header, the endpoint has been made
    public and `useAuthedImage` should be deleted rather than left as
    unexplained indirection. It failing is also the reason a plain
    `<img src="/api/movies/images/1">` shows nothing.
    """
    image = await _image(db_session)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        response = await anon.get(f"/api/movies/images/{image.id}")

    assert response.status_code in (401, 403, 422)
    assert response.status_code != 200, "poster bytes must not be served without authentication"


async def test_the_image_endpoint_serves_bytes_to_an_authenticated_caller(db_session, client):
    """The other half: with the header the fetch succeeds, which is what the hook does."""
    user = await make_user(db_session, 9701)
    image = await _image(db_session)
    await db_session.commit()

    async with client(user) as authed:
        response = await authed.get(f"/api/movies/images/{image.id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"bytes"


# ---------- the frontend contracts ----------
#
# Source assertions, following the pattern already used for
# DecorationLayer.tsx in tests/test_theme_presentation.py. They are not a
# substitute for running the components — this repository has no frontend
# test runner — but they do pin the two values that silently broke the
# feature, and both are one-token edits that would otherwise regress
# unnoticed.


def _frontend_source(name: str) -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    return (root / "webapp" / "src" / name).read_text(encoding="utf-8")


def test_both_pickers_accept_any_image_type():
    """
    A Telegram WebView gallery reports HEIC photos and many ordinary
    entries as `application/octet-stream`, so a picker filtered to
    jpeg/png/webp made them unselectable — the file never reached the
    upload and there was no error to read.

    Widening this is safe because the accept attribute was never the
    check: `store_image` decodes the bytes with Pillow and re-encodes
    them, which is asserted separately below.
    """
    for path in ("admin/PosterPicker.tsx", "components/TopUpSheet.tsx"):
        source = _frontend_source(path)
        assert 'accept="image/*"' in source or 'const ACCEPT = "image/*"' in source, (
            f"{path} must not narrow gallery selection by MIME type"
        )
        assert "image/jpeg,image/png,image/webp" not in source, (
            f"{path} still carries the narrow MIME list"
        )


def test_the_backend_still_validates_the_bytes():
    """
    The other half of the trade: the frontend stopped filtering, so the
    decode must still be the gate. If this ever becomes a content-type
    check again, widening `accept` would have loosened validation.
    """
    from app.services.images import ImageError, _optimise

    with pytest.raises(ImageError):
        _optimise(b"this is not an image")


def test_poster_rendering_uses_one_shared_helper():
    """
    Every component that can render a private poster goes through
    `useAuthedImage`, so the fetch/blob/revoke logic exists once. A second
    copy is how one of them ends up leaking object URLs.
    """
    for path in (
        "components/MovieCard.tsx",
        "components/HeroBanner.tsx",
        "admin/PosterPicker.tsx",
    ):
        assert "useAuthedImage" in _frontend_source(path), f"{path} bypasses the shared helper"

    hook = _frontend_source("lib/useAuthedImage.ts")
    # The three things that make it correct rather than merely working.
    assert "revokeObjectURL" in hook, "object URLs must be revoked"
    assert "cancelled" in hook, "a stale response must not overwrite a newer one"
    assert 'startsWith("/api/")' in hook, "only private URLs may be fetched"


def test_public_urls_are_not_fetched_through_the_api():
    """
    A TMDB poster must render as a plain <img src>. Blob-ing it would add
    a request and a copy of every image in the catalog for no benefit.
    """
    hook = _frontend_source("lib/useAuthedImage.ts")
    body = hook.split("export function useAuthedImage", 1)[1]
    # The public branch returns before any fetch is reached.
    assert body.index("setResolved(src)") < body.index("fetchImageObjectUrl")


async def test_a_card_carries_the_private_url_for_an_uploaded_poster(db_session, client):
    """
    End to end through the API the Mini App actually calls, so the shape
    the client must handle is pinned rather than assumed.
    """
    user = await make_user(db_session, 9702)
    image = await _image(db_session)
    title = Title(name="Uploaded", content_type=ContentType.FILM, is_active=True)
    title.poster_image_id = image.id
    db_session.add(title)
    await db_session.flush()
    await db_session.commit()

    async with client(user) as authed:
        body = (await authed.get(f"/api/movies/{title.id}")).json()

    assert body["poster_url"] == f"/api/movies/images/{image.id}"
