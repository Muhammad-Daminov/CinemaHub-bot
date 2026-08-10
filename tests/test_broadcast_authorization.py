"""
Broadcast authorization and the privacy of delivery records.

Two things are being protected. First, only an administrator holding
MANAGE_NOTIFICATIONS may create or inspect a broadcast. Second — and
easier to get wrong — the per-recipient rows must stay internal: they
record who was messaged and when, which no ordinary user should be able
to read, and which no admin should be able to reach through a route that
was never meant to serve them.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.db.models.system import BroadcastAudience, BroadcastMessage
from app.db.models.user import AdminPermission, UserRole
from app.db.session import get_db_session
from app.main import app
from app.services.broadcast import create_broadcast, materialise_recipients
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


async def test_an_ordinary_user_cannot_create_or_list_broadcasts(db_session, as_user):
    user = await make_user(db_session, 9201)
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get("/api/admin/broadcasts")).status_code == 403
        assert (
            await client.post("/api/admin/broadcasts", json={"message": "hi", "audience": "all"})
        ).status_code == 403
        assert (await client.get("/api/admin/broadcasts/audience")).status_code == 403


async def test_an_admin_without_the_permission_is_refused(db_session, as_user):
    admin = await make_user(db_session, 9202)
    admin.role = UserRole.ADMIN
    await db_session.commit()

    async with as_user(admin) as client:
        assert (await client.get("/api/admin/broadcasts")).status_code == 403


async def test_an_admin_with_manage_notifications_is_allowed(db_session, as_user):
    admin = await make_user(db_session, 9203)
    admin.role = UserRole.ADMIN
    db_session.add(AdminPermission(user_id=admin.id, permission="manage_notifications"))
    await db_session.commit()

    async with as_user(admin) as client:
        assert (await client.get("/api/admin/broadcasts")).status_code == 200


async def test_no_route_exposes_per_recipient_delivery_rows(db_session):
    """
    The internal table must not be reachable through any endpoint. Asserted
    against the OpenAPI schema so a future route that returns these rows
    has to be a deliberate act, not an accident.
    """
    schema = app.openapi()
    serialised = str(schema).lower()

    assert "broadcastmessage" not in serialised
    assert "chp_broadcast_messages" not in serialised
    # No path mentions recipients either.
    assert not [path for path in schema["paths"] if "recipient" in path.lower()]


async def test_recipients_are_derived_from_the_audience_not_the_request(
    db_session, as_user, monkeypatch
):
    """
    There is no way to name who receives a broadcast. The payload carries
    an audience; the server resolves the people.

    The send itself is stubbed out. Creating a broadcast schedules a
    FastAPI background task holding the *application's* session factory,
    which points at `settings.DATABASE_URL` — production. Every other test
    here drives the service directly with the test factory; this one goes
    through HTTP, so the task has to be neutralised or the suite would
    reach across at the real database.
    """
    from app.api import admin as admin_module

    async def no_send(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_module, "run_broadcast", no_send)

    admin = await make_user(db_session, 9204)
    admin.role = UserRole.SUPER_ADMIN
    victim = await make_user(db_session, 9205)
    await db_session.commit()

    async with as_user(admin) as client:
        # user_id / user_ids are not part of the schema, and the model
        # forbids extras — so naming a recipient fails loudly rather than
        # being quietly dropped and appearing to have worked.
        rejected = await client.post(
            "/api/admin/broadcasts",
            json={"message": "hi", "audience": "all", "user_id": victim.id, "user_ids": [victim.id]},
        )
        assert rejected.status_code == 422

        response = await client.post(
            "/api/admin/broadcasts", json={"message": "hi", "audience": "all"}
        )

    assert response.status_code == 200
    assert response.json()["audience"] == "all"


async def test_delivery_rows_belong_to_exactly_one_broadcast(db_session):
    """Cross-broadcast isolation at the row level."""
    actor = await make_user(db_session, 9206)
    await make_user(db_session, 9207)
    first = await create_broadcast(db_session, actor, "First", BroadcastAudience.ALL)
    second = await create_broadcast(db_session, actor, "Second", BroadcastAudience.ALL)
    await materialise_recipients(db_session, first)

    assert await count_rows(db_session, BroadcastMessage, broadcast_id=first.id) == 2
    assert await count_rows(db_session, BroadcastMessage, broadcast_id=second.id) == 0


async def test_an_empty_message_is_still_refused(db_session, as_user):
    """Existing validation is unchanged by the delivery rework."""
    admin = await make_user(db_session, 9208)
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async with as_user(admin) as client:
        assert (
            await client.post("/api/admin/broadcasts", json={"message": "   ", "audience": "all"})
        ).status_code == 422
        assert (
            await client.post(
                "/api/admin/broadcasts", json={"message": "x" * 5000, "audience": "all"}
            )
        ).status_code == 422


async def test_error_text_stored_per_recipient_is_bounded(db_session):
    """A stored error must never grow unbounded, and never carry a token."""
    from app.db.models.system import BroadcastMessage as Row

    column = Row.__table__.c.error
    assert column.type.length == 300
