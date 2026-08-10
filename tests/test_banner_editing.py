"""
Editing a banner campaign in place.

The admin panel now edits an existing campaign rather than forcing a
recreate, which introduces two risks worth testing directly: an edit
touching the wrong row, and an edit slipping past the validation a
creation would have failed. Both are covered here.

Repeated admin actions are also exercised — a panel button pressed twice
must not duplicate a campaign or corrupt one.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models.banner import Banner, BannerAudience
from app.db.models.content import ContentType, Title
from app.db.models.user import UserRole
from app.services.banners import BannerError, create_banner, update_banner
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _banner(session, **fields) -> Banner:
    defaults = {"headline": "Original", "audience": BannerAudience.GLOBAL, "priority": 0}
    return await create_banner(session, **{**defaults, **fields})


# ---------- editing applies, and only where intended ----------


async def test_editing_updates_the_named_campaign(db_session):
    banner = await _banner(db_session, headline="Before")
    await update_banner(db_session, banner.id, headline="After", priority=7)

    stored = await db_session.get(Banner, banner.id, populate_existing=True)
    assert stored.headline == "After"
    assert stored.priority == 7


async def test_editing_one_campaign_leaves_the_others_alone(db_session):
    """The failure that would be worst and quietest."""
    first = await _banner(db_session, headline="First", priority=1)
    second = await _banner(db_session, headline="Second", priority=2)
    third = await _banner(db_session, headline="Third", priority=3)

    await update_banner(db_session, second.id, headline="Second edited", priority=9)

    assert (await db_session.get(Banner, first.id, populate_existing=True)).headline == "First"
    assert (await db_session.get(Banner, third.id, populate_existing=True)).headline == "Third"
    assert (await db_session.get(Banner, first.id, populate_existing=True)).priority == 1
    assert (await db_session.get(Banner, third.id, populate_existing=True)).priority == 3


async def test_editing_does_not_create_a_second_row(db_session):
    banner = await _banner(db_session)
    for index in range(5):
        await update_banner(db_session, banner.id, headline=f"Edit {index}")

    assert await count_rows(db_session, Banner) == 1
    assert (await db_session.get(Banner, banner.id, populate_existing=True)).headline == "Edit 4"


async def test_editing_an_unknown_campaign_returns_none(db_session):
    assert await update_banner(db_session, 999999, headline="x") is None


async def test_repeated_identical_edits_are_stable(db_session):
    """A double-tapped save must leave one campaign in one state."""
    banner = await _banner(db_session, headline="Stable")
    for _ in range(3):
        await update_banner(db_session, banner.id, headline="Stable", priority=4)

    assert await count_rows(db_session, Banner) == 1
    stored = await db_session.get(Banner, banner.id, populate_existing=True)
    assert (stored.headline, stored.priority) == ("Stable", 4)


# ---------- an edit is validated exactly like a creation ----------


@pytest.mark.parametrize(
    "field,value",
    [("headline", "<script>alert(1)</script>"), ("subtitle", "<img src=x onerror=alert(1)>")],
)
async def test_markup_is_refused_on_edit_too(db_session, field, value):
    banner = await _banner(db_session, headline="Clean")
    with pytest.raises(BannerError):
        await update_banner(db_session, banner.id, **{field: value})

    assert (await db_session.get(Banner, banner.id, populate_existing=True)).headline == "Clean"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html;base64,PHN2Zz4=", "vbscript:x"])
async def test_a_dangerous_image_url_is_refused_on_edit(db_session, url):
    banner = await _banner(db_session)
    with pytest.raises(BannerError):
        await update_banner(db_session, banner.id, image_url=url)


async def test_an_unknown_label_is_refused_on_edit(db_session):
    banner = await _banner(db_session)
    with pytest.raises(BannerError):
        await update_banner(db_session, banner.id, label_key="banner.label.invented")


async def test_a_backwards_window_is_refused_on_edit(db_session):
    banner = await _banner(db_session)
    now = datetime.now(timezone.utc)
    with pytest.raises(BannerError):
        await update_banner(db_session, banner.id, starts_at=now, ends_at=now - timedelta(days=1))


async def test_an_unknown_title_is_refused_on_edit(db_session):
    banner = await _banner(db_session)
    with pytest.raises(BannerError):
        await update_banner(db_session, banner.id, title_id=999999)


async def test_a_targeted_audience_still_needs_its_target_on_edit(db_session):
    banner = await _banner(db_session)
    with pytest.raises(BannerError):
        await update_banner(
            db_session, banner.id, audience=BannerAudience.CONTENT_TYPE, target_value=None
        )


async def test_a_rejected_edit_changes_nothing(db_session):
    """Validation failure must not half-apply."""
    banner = await _banner(db_session, headline="Untouched", priority=3)
    with pytest.raises(BannerError):
        await update_banner(db_session, banner.id, headline="<b>bad</b>", priority=99)

    stored = await db_session.get(Banner, banner.id, populate_existing=True)
    assert stored.headline == "Untouched"
    assert stored.priority == 3


# ---------- a campaign can still lose its title ----------


async def test_a_campaign_can_be_edited_into_an_announcement(db_session):
    """Clearing the title turns a promotion into a "coming soon" notice."""
    title = Title(content_type=ContentType.FILM, name="Existing", is_active=True)
    db_session.add(title)
    await db_session.flush()

    banner = await _banner(db_session, title_id=title.id)
    await update_banner(db_session, banner.id, title_id=None, label_key="banner.label.coming_soon")

    stored = await db_session.get(Banner, banner.id, populate_existing=True)
    assert stored.title_id is None
    assert stored.label_key == "banner.label.coming_soon"


# ---------- authorization on the edit route ----------


async def test_an_ordinary_user_cannot_edit_a_campaign(db_session):
    from httpx import ASGITransport, AsyncClient

    from app.api.auth import get_current_user
    from app.db.session import get_db_session
    from app.main import app

    banner = await _banner(db_session)
    user = await make_user(db_session, 9501)
    await db_session.commit()

    async def override_session():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/admin/banners/{banner.id}", json={"headline": "hacked"})
    app.dependency_overrides.clear()

    assert response.status_code == 403
    assert (await db_session.get(Banner, banner.id, populate_existing=True)).headline == "Original"


async def test_an_authorized_admin_can_edit(db_session):
    from httpx import ASGITransport, AsyncClient

    from app.api.auth import get_current_user
    from app.db.session import get_db_session
    from app.main import app

    banner = await _banner(db_session)
    admin = await make_user(db_session, 9502)
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async def override_session():
        yield db_session

    async def override_user():
        return admin

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.patch(f"/api/admin/banners/{banner.id}", json={"headline": "Edited"})
        rejected = await client.patch(
            f"/api/admin/banners/{banner.id}", json={"headline": "<script>x</script>"}
        )
    app.dependency_overrides.clear()

    assert ok.status_code == 200
    assert rejected.status_code == 422, "the edit route enforces the same rules as creation"
