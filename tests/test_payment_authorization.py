"""
Payment authorization and data isolation.

Section 16/17 of the Phase 9 brief: a user must never see another user's
payment, and a non-administrator must never review one. These are the
tests that would fail if an endpoint trusted an id from the client.

The receipt-status route answers **404, not 403**, for someone else's
payment. A 403 would confirm that the id exists — which is itself a leak,
since payment ids are sequential and enumerable.
"""
import io

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.api.auth import get_current_user
from app.db.models.payment import PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.models.user import UserRole
from app.db.session import get_db_session
from app.main import app
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), (20, 40, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


async def _receipt(session, user, amount="10000", status=PaymentStatus.PENDING):
    from decimal import Decimal

    receipt = PaymentReceipt(
        user_id=user.id,
        purpose=PaymentPurpose.TOPUP,
        amount=Decimal(amount),
        receipt_photo_file_id="f",
        status=status,
    )
    session.add(receipt)
    await session.flush()
    return receipt


@pytest.fixture
def as_user(db_session):
    """Runs the app as a given user against the test session."""

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


# ---------- one user cannot reach another's payment ----------


async def test_a_user_sees_their_own_payment(db_session, as_user):
    user = await make_user(db_session, 9601)
    receipt = await _receipt(db_session, user)
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.get(f"/api/billing/receipts/{receipt.id}")
        assert response.status_code == 200
        assert response.json()["id"] == receipt.id


async def test_another_users_payment_is_not_found(db_session, as_user):
    """
    404, deliberately — a 403 would confirm the id exists, and receipt ids
    are sequential, so that alone would map out other people's payments.
    """
    owner = await make_user(db_session, 9602)
    intruder = await make_user(db_session, 9603)
    receipt = await _receipt(db_session, owner)
    await db_session.commit()

    async with as_user(intruder) as client:
        assert (await client.get(f"/api/billing/receipts/{receipt.id}")).status_code == 404


async def test_an_admin_cannot_read_another_users_payment_through_the_user_route(
    db_session, as_user
):
    """
    Being an administrator does not make the *viewer* endpoint theirs. The
    admin surface is a separate, permission-gated route; this one is scoped
    to the caller whoever they are.
    """
    owner = await make_user(db_session, 9604)
    admin = await make_user(db_session, 9605)
    admin.role = UserRole.SUPER_ADMIN
    receipt = await _receipt(db_session, owner)
    await db_session.commit()

    async with as_user(admin) as client:
        assert (await client.get(f"/api/billing/receipts/{receipt.id}")).status_code == 404


async def test_a_receipt_image_belonging_to_someone_else_is_not_served(db_session, as_user):
    from app.services.images import store_image

    owner = await make_user(db_session, 9606)
    intruder = await make_user(db_session, 9607)
    image = await store_image(db_session, _jpeg(), "image/jpeg")
    receipt = await _receipt(db_session, owner)
    receipt.receipt_image_id = image.id
    await db_session.commit()

    async with as_user(owner) as client:
        assert (await client.get(f"/api/billing/receipts/{receipt.id}/image")).status_code == 200
    async with as_user(intruder) as client:
        assert (await client.get(f"/api/billing/receipts/{receipt.id}/image")).status_code == 404


async def test_payment_history_is_per_user(db_session, as_user):
    mine = await make_user(db_session, 9608)
    theirs = await make_user(db_session, 9609)
    await _receipt(db_session, theirs, amount="99999")
    await db_session.commit()

    async with as_user(mine) as client:
        assert (await client.get("/api/billing/history")).json() == []


# ---------- only administrators review ----------


async def test_an_ordinary_user_cannot_approve(db_session, as_user):
    user = await make_user(db_session, 9610)
    receipt = await _receipt(db_session, user)
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.post(f"/api/admin/receipts/{receipt.id}/approve")
        assert response.status_code == 403


async def test_a_user_cannot_approve_their_own_payment(db_session, as_user):
    """The obvious attack: self-approval to mint balance."""
    user = await make_user(db_session, 9611)
    receipt = await _receipt(db_session, user)
    await db_session.commit()

    async with as_user(user) as client:
        assert (
            await client.post(f"/api/admin/receipts/{receipt.id}/approve")
        ).status_code == 403

    stored = await db_session.get(PaymentReceipt, receipt.id, populate_existing=True)
    assert stored.status == PaymentStatus.PENDING
    from sqlalchemy import select
    from app.db.models.user import User

    balance = (
        await db_session.execute(select(User.balance).where(User.id == user.id))
    ).scalar_one()
    assert balance == 0


async def test_an_ordinary_user_cannot_flag_a_mismatch(db_session, as_user):
    user = await make_user(db_session, 9612)
    receipt = await _receipt(db_session, user)
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.post(
            f"/api/admin/receipts/{receipt.id}/mismatch", json={"verified_amount": 1}
        )
        assert response.status_code == 403


async def test_an_ordinary_user_cannot_list_rejection_reasons(db_session, as_user):
    user = await make_user(db_session, 9613)
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get("/api/admin/rejection-reasons")).status_code == 403


async def test_an_admin_with_the_permission_can_review(db_session, as_user, silence_bot):
    from app.db.models.user import AdminPermission

    admin = await make_user(db_session, 9614)
    admin.role = UserRole.ADMIN
    db_session.add(AdminPermission(user_id=admin.id, permission="manage_payments"))
    payer = await make_user(db_session, 9615)
    receipt = await _receipt(db_session, payer)
    await db_session.commit()

    async with as_user(admin) as client:
        assert (
            await client.post(f"/api/admin/receipts/{receipt.id}/approve")
        ).status_code == 200


async def test_an_admin_without_the_permission_cannot_review(db_session, as_user):
    """Being an administrator is not the same as holding manage_payments."""
    admin = await make_user(db_session, 9616)
    admin.role = UserRole.ADMIN
    payer = await make_user(db_session, 9617)
    receipt = await _receipt(db_session, payer)
    await db_session.commit()

    async with as_user(admin) as client:
        assert (
            await client.post(f"/api/admin/receipts/{receipt.id}/approve")
        ).status_code == 403


# ---------- input validation ----------


async def test_a_mismatch_amount_must_be_positive(db_session, as_user):
    admin = await make_user(db_session, 9618)
    admin.role = UserRole.SUPER_ADMIN
    payer = await make_user(db_session, 9619)
    receipt = await _receipt(db_session, payer)
    await db_session.commit()

    async with as_user(admin) as client:
        for bad in (0, -500):
            response = await client.post(
                f"/api/admin/receipts/{receipt.id}/mismatch", json={"verified_amount": bad}
            )
            assert response.status_code == 422


async def test_an_unknown_rejection_reason_is_refused(db_session, as_user):
    """A client-supplied id is validated, never written through blindly."""
    admin = await make_user(db_session, 9620)
    admin.role = UserRole.SUPER_ADMIN
    payer = await make_user(db_session, 9621)
    receipt = await _receipt(db_session, payer)
    await db_session.commit()

    async with as_user(admin) as client:
        response = await client.post(
            f"/api/admin/receipts/{receipt.id}/reject", json={"reason_id": 999999}
        )
        assert response.status_code == 422

    stored = await db_session.get(PaymentReceipt, receipt.id, populate_existing=True)
    assert stored.status == PaymentStatus.PENDING, "a refused request must change nothing"
