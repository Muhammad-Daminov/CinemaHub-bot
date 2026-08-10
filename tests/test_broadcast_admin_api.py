"""
The API surface the broadcast admin UI depends on.

Phase 9E-D added no new capability — it exposed what 9E-A/B/C already
did: per-language bodies on create, a live delivery breakdown, and an
operator-triggered resume. So these tests are mostly about what the API
still refuses to do once a screen is driving it: name a recipient, hand
back a `file_id`, resume something that is not recoverable, or send the
same broadcast twice because a button was tapped twice.

Every test that goes through HTTP stubs the background task. Creating or
resuming a broadcast schedules work holding the *application's* session
factory — `settings.DATABASE_URL`, production. That is not hypothetical:
Phase 9E-B hit it.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.auth import get_current_user
from app.db.models.system import (
    Broadcast,
    BroadcastAudience,
    BroadcastMedia,
    BroadcastMessage,
    BroadcastStatus,
    BroadcastTranslation,
    DeliveryStatus,
)
from app.db.models.user import AdminPermission, UILanguage, UserRole
from app.db.session import get_db_session
from app.main import app
from app.services.broadcast import (
    MAX_ATTEMPTS,
    STALE_AFTER,
    BroadcastError,
    create_broadcast,
    delivery_breakdown,
    materialise_recipients,
    resumability,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


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


@pytest.fixture
def no_background_send(monkeypatch):
    """Neutralises both background paths — the send and the resume."""
    from app.api import admin as admin_module

    async def no_send(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_module, "run_broadcast", no_send)
    monkeypatch.setattr(admin_module, "resume_broadcast", no_send)


async def _admin(session, telegram_id: int):
    admin = await make_user(session, telegram_id)
    admin.role = UserRole.ADMIN
    session.add(AdminPermission(user_id=admin.id, permission="manage_notifications"))
    await session.commit()
    return admin


# ---------- translations on create ----------


async def test_creating_a_broadcast_stores_its_per_language_bodies(
    db_session, as_user, no_background_send
):
    admin = await _admin(db_session, 9801)

    async with as_user(admin) as client:
        response = await client.post(
            "/api/admin/broadcasts",
            json={
                "message": "Salom",
                "translations": {"ru": "Привет", "en": "Hello"},
                "audience": "all",
            },
        )

    assert response.status_code == 200
    rows = (
        await db_session.execute(
            select(BroadcastTranslation).where(
                BroadcastTranslation.broadcast_id == response.json()["id"]
            )
        )
    ).scalars().all()
    assert {row.language: row.body for row in rows} == {
        UILanguage.RU: "Привет",
        UILanguage.EN: "Hello",
    }


async def test_an_overlong_translation_is_refused_server_side(
    db_session, as_user, no_background_send
):
    """
    The composer counts characters for the operator's benefit; the refusal
    happens here. A caption limit applies as soon as media is attached.
    """
    admin = await _admin(db_session, 9802)

    async with as_user(admin) as client:
        assert (
            await client.post(
                "/api/admin/broadcasts",
                json={"message": "Salom", "translations": {"ru": "x" * 5000}, "audience": "all"},
            )
        ).status_code == 422

        assert (
            await client.post(
                "/api/admin/broadcasts",
                json={
                    "message": "Salom",
                    "translations": {"ru": "x" * 2000},
                    "audience": "all",
                    "media_type": "photo",
                    "media_file_id": "some-file-id",
                },
            )
        ).status_code == 422


async def test_an_unknown_language_key_is_refused(db_session, as_user, no_background_send):
    admin = await _admin(db_session, 9803)

    async with as_user(admin) as client:
        assert (
            await client.post(
                "/api/admin/broadcasts",
                json={"message": "Salom", "translations": {"fr": "Bonjour"}, "audience": "all"},
            )
        ).status_code == 422


# ---------- double submit ----------


async def test_an_identical_queued_broadcast_is_refused(db_session, as_user, no_background_send):
    """
    A double-tapped Send is two identical requests, and two broadcasts
    means every recipient gets the message twice. The button guard is the
    fast path; this is the one that actually holds.
    """
    admin = await _admin(db_session, 9810)
    payload = {"message": "Salom", "audience": "all"}

    async with as_user(admin) as client:
        first = await client.post("/api/admin/broadcasts", json=payload)
        second = await client.post("/api/admin/broadcasts", json=payload)

    assert first.status_code == 200
    assert second.status_code == 422
    assert await count_rows(db_session, Broadcast, created_by_id=admin.id) == 1


async def test_a_different_broadcast_is_not_mistaken_for_a_duplicate(
    db_session, as_user, no_background_send
):
    """The guard suppresses repeats, not legitimate distinct sends."""
    admin = await _admin(db_session, 9811)

    async with as_user(admin) as client:
        assert (
            await client.post("/api/admin/broadcasts", json={"message": "A", "audience": "all"})
        ).status_code == 200
        assert (
            await client.post("/api/admin/broadcasts", json={"message": "B", "audience": "all"})
        ).status_code == 200
        assert (
            await client.post("/api/admin/broadcasts", json={"message": "A", "audience": "free"})
        ).status_code == 200

    assert await count_rows(db_session, Broadcast, created_by_id=admin.id) == 3


async def test_a_finished_broadcast_can_be_repeated(db_session, as_user, no_background_send):
    """Suppression is scoped to unsent broadcasts, not to history."""
    admin = await _admin(db_session, 9812)
    sent = await create_broadcast(db_session, admin, "Salom", BroadcastAudience.ALL)
    sent.status = BroadcastStatus.COMPLETED
    await db_session.commit()

    async with as_user(admin) as client:
        assert (
            await client.post("/api/admin/broadcasts", json={"message": "Salom", "audience": "all"})
        ).status_code == 200


async def test_two_admins_are_not_each_others_duplicates(db_session):
    """The guard is per-actor; two people sending the same words is not a bug."""
    first = await make_user(db_session, 9813)
    second = await make_user(db_session, 9814)
    await create_broadcast(db_session, first, "Salom", BroadcastAudience.ALL)
    await create_broadcast(db_session, second, "Salom", BroadcastAudience.ALL)
    assert await count_rows(db_session, Broadcast) == 2


async def test_the_duplicate_guard_distinguishes_targets(db_session):
    admin = await make_user(db_session, 9815)
    await create_broadcast(
        db_session, admin, "Salom", BroadcastAudience.INTEREST, target_value="anime"
    )
    await create_broadcast(
        db_session, admin, "Salom", BroadcastAudience.INTEREST, target_value="film"
    )
    with pytest.raises(BroadcastError):
        await create_broadcast(
            db_session, admin, "Salom", BroadcastAudience.INTEREST, target_value="anime"
        )


# ---------- detail ----------


async def test_the_detail_route_reports_the_live_breakdown(db_session, as_user):
    admin = await _admin(db_session, 9820)
    for index in range(4):
        await make_user(db_session, 9830 + index)
    broadcast = await create_broadcast(db_session, admin, "Salom", BroadcastAudience.ALL)
    await materialise_recipients(db_session, broadcast)

    rows = (
        await db_session.execute(
            select(BroadcastMessage).where(BroadcastMessage.broadcast_id == broadcast.id)
        )
    ).scalars().all()
    rows[0].status = DeliveryStatus.SENT
    rows[1].status = DeliveryStatus.FAILED
    rows[2].status = DeliveryStatus.SKIPPED
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.get(f"/api/admin/broadcasts/{broadcast.id}")

    assert response.status_code == 200
    body = response.json()
    assert (body["sent"], body["failed"], body["skipped"], body["pending"]) == (1, 1, 1, 2)
    assert body["can_resume"] is False  # still PENDING — nothing has died


async def test_the_detail_route_never_returns_a_file_id(db_session, as_user):
    """
    The media reference is input, never output. An admin screen needs to
    know a broadcast carries a photo, not which one.
    """
    admin = await _admin(db_session, 9840)
    broadcast = await create_broadcast(
        db_session,
        admin,
        "Salom",
        BroadcastAudience.ALL,
        media_type=BroadcastMedia.PHOTO,
        media_file_id="super-secret-file-id",
    )
    await db_session.commit()

    async with as_user(admin) as client:
        detail = await client.get(f"/api/admin/broadcasts/{broadcast.id}")
        listing = await client.get("/api/admin/broadcasts")

    assert "super-secret-file-id" not in detail.text
    assert "super-secret-file-id" not in listing.text
    assert detail.json()["media_type"] == "photo"
    assert "media_file_id" not in detail.json()


async def test_the_detail_route_reports_which_languages_were_written(db_session, as_user):
    from app.services.broadcast import set_translations

    admin = await _admin(db_session, 9841)
    broadcast = await create_broadcast(db_session, admin, "Salom", BroadcastAudience.ALL)
    await set_translations(db_session, broadcast.id, {UILanguage.RU: "Привет"})
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.get(f"/api/admin/broadcasts/{broadcast.id}")

    assert response.json()["languages"] == ["ru"]


async def test_a_missing_broadcast_is_a_404(db_session, as_user):
    admin = await _admin(db_session, 9842)
    async with as_user(admin) as client:
        assert (await client.get("/api/admin/broadcasts/999999")).status_code == 404


# ---------- resumability ----------


async def _stalled(session, admin, *, status: BroadcastStatus, minutes_ago: int):
    """A broadcast with recipients, left in `status` by a worker that died."""
    broadcast = await create_broadcast(
        session, admin, f"Salom {status.value} {minutes_ago}", BroadcastAudience.ALL
    )
    await materialise_recipients(session, broadcast)
    broadcast.status = status
    broadcast.started_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    await session.flush()
    return broadcast


async def test_a_healthy_send_in_progress_is_not_resumable(db_session):
    """
    A Resume button during a live send would invite an operator to fight
    their own worker. The staleness window is the same one cron uses.
    """
    admin = await make_user(db_session, 9850)
    await make_user(db_session, 9851)
    broadcast = await _stalled(db_session, admin, status=BroadcastStatus.SENDING, minutes_ago=1)
    resumable, _ = await resumability(db_session, broadcast)
    assert resumable is False


async def test_a_stale_sending_broadcast_is_resumable(db_session):
    admin = await make_user(db_session, 9852)
    await make_user(db_session, 9853)
    minutes = int(STALE_AFTER.total_seconds() // 60) + 5
    broadcast = await _stalled(
        db_session, admin, status=BroadcastStatus.SENDING, minutes_ago=minutes
    )
    resumable, _ = await resumability(db_session, broadcast)
    assert resumable is True


async def test_a_failed_broadcast_with_work_left_is_resumable(db_session):
    admin = await make_user(db_session, 9854)
    await make_user(db_session, 9855)
    broadcast = await _stalled(db_session, admin, status=BroadcastStatus.FAILED, minutes_ago=1)
    resumable, _ = await resumability(db_session, broadcast)
    assert resumable is True


async def test_nothing_left_to_do_is_not_resumable(db_session):
    """Whatever the status says. A resume with no work is a no-op with a button."""
    admin = await make_user(db_session, 9856)
    await make_user(db_session, 9857)
    broadcast = await _stalled(db_session, admin, status=BroadcastStatus.FAILED, minutes_ago=1)

    for row in (
        await db_session.execute(
            select(BroadcastMessage).where(BroadcastMessage.broadcast_id == broadcast.id)
        )
    ).scalars():
        row.status = DeliveryStatus.SENT
    await db_session.flush()

    resumable, breakdown = await resumability(db_session, broadcast)
    assert resumable is False
    assert breakdown["outstanding"] == 0


async def test_recipients_that_exhausted_their_attempts_are_not_outstanding(db_session):
    admin = await make_user(db_session, 9858)
    await make_user(db_session, 9859)
    broadcast = await _stalled(db_session, admin, status=BroadcastStatus.FAILED, minutes_ago=1)

    for row in (
        await db_session.execute(
            select(BroadcastMessage).where(BroadcastMessage.broadcast_id == broadcast.id)
        )
    ).scalars():
        row.attempts = MAX_ATTEMPTS
    await db_session.flush()

    breakdown = await delivery_breakdown(db_session, broadcast.id)
    assert breakdown["pending"] == 2 and breakdown["outstanding"] == 0
    resumable, _ = await resumability(db_session, broadcast)
    assert resumable is False


# ---------- the resume route ----------


async def test_resuming_a_recoverable_broadcast_reclaims_it(
    db_session, as_user, no_background_send
):
    admin = await _admin(db_session, 9860)
    await make_user(db_session, 9861)
    await db_session.commit()
    broadcast = await _stalled(db_session, admin, status=BroadcastStatus.FAILED, minutes_ago=1)
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.post(f"/api/admin/broadcasts/{broadcast.id}/resume")

    assert response.status_code == 200
    assert response.json()["status"] == "sending"


async def test_resuming_something_unrecoverable_is_a_409(db_session, as_user, no_background_send):
    """
    The panel may have been open for an hour. 409 is its cue to refresh
    rather than retry — repeating the request would not help.
    """
    admin = await _admin(db_session, 9862)
    await make_user(db_session, 9863)
    await db_session.commit()
    broadcast = await create_broadcast(db_session, admin, "Salom", BroadcastAudience.ALL)
    broadcast.status = BroadcastStatus.COMPLETED
    await db_session.commit()

    async with as_user(admin) as client:
        assert (
            await client.post(f"/api/admin/broadcasts/{broadcast.id}/resume")
        ).status_code == 409


async def test_a_second_resume_is_refused_while_the_first_is_running(
    db_session, as_user, no_background_send
):
    """The claim re-stamps `started_at`, so the second tap finds it fresh."""
    admin = await _admin(db_session, 9864)
    await make_user(db_session, 9865)
    await db_session.commit()
    broadcast = await _stalled(db_session, admin, status=BroadcastStatus.FAILED, minutes_ago=1)
    await db_session.commit()

    async with as_user(admin) as client:
        assert (
            await client.post(f"/api/admin/broadcasts/{broadcast.id}/resume")
        ).status_code == 200
        assert (
            await client.post(f"/api/admin/broadcasts/{broadcast.id}/resume")
        ).status_code == 409


async def test_resuming_does_not_rematerialise_or_retarget(db_session, db_factory, monkeypatch):
    """
    The frozen recipient set is what gets delivered. Someone who acquires
    the targeted interest afterwards must not be swept in by a resume.
    """
    from app.db.models.personalization import UserInterestProfile
    from app.services.broadcast import resume_broadcast

    class FakeBot:
        def __init__(self):
            self.sent: list[int] = []

        async def send_message(self, chat_id, text, *args, **kwargs):
            self.sent.append(chat_id)

    monkeypatch.setattr("app.services.broadcast.SEND_INTERVAL_SECONDS", 0)

    async with db_factory() as setup:
        admin = await make_user(setup, 9870)
        inside = await make_user(setup, 9871)
        latecomer = await make_user(setup, 9872)
        setup.add(
            UserInterestProfile(
                user_id=inside.id, dominant_type="anime", dominant_count=12, total_titles=12
            )
        )
        await setup.flush()

        broadcast = await create_broadcast(
            setup, admin, "Salom", BroadcastAudience.INTEREST, target_value="anime"
        )
        await materialise_recipients(setup, broadcast)
        broadcast.status = BroadcastStatus.FAILED
        broadcast_id = broadcast.id

        # The audience changes after materialisation.
        setup.add(
            UserInterestProfile(
                user_id=latecomer.id, dominant_type="anime", dominant_count=40, total_titles=40
            )
        )
        await setup.commit()

    bot = FakeBot()
    await resume_broadcast(db_factory, bot, broadcast_id)

    assert bot.sent == [inside.telegram_id]
    async with db_factory() as check:
        assert await count_rows(check, BroadcastMessage, broadcast_id=broadcast_id) == 1


# ---------- authorization ----------


async def test_the_new_routes_are_permission_gated(db_session, as_user):
    ordinary = await make_user(db_session, 9880)
    admin_without = await make_user(db_session, 9881)
    admin_without.role = UserRole.ADMIN
    owner = await _admin(db_session, 9882)
    broadcast = await create_broadcast(db_session, owner, "Salom", BroadcastAudience.ALL)
    await db_session.commit()

    for user in (ordinary, admin_without):
        async with as_user(user) as client:
            assert (
                await client.get(f"/api/admin/broadcasts/{broadcast.id}")
            ).status_code == 403
            assert (
                await client.post(f"/api/admin/broadcasts/{broadcast.id}/resume")
            ).status_code == 403


async def test_no_user_facing_route_reaches_broadcast_data(db_session):
    """The delivery rows and the media reference stay out of the schema."""
    schema = app.openapi()
    serialised = str(schema).lower()

    assert "broadcastmessage" not in serialised
    assert "media_file_id" not in str(
        schema["components"]["schemas"].get("BroadcastDetailOut", {})
    ).lower()
    assert not [path for path in schema["paths"] if "recipient" in path.lower()]
    # Broadcast routes live under the admin router exclusively.
    assert all(
        path.startswith("/api/admin/")
        for path in schema["paths"]
        if "broadcast" in path.lower()
    )
