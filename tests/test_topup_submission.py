"""
Top-up submission: one receipt per payment.

`POST /billing/topup` had no duplicate protection at all — every call
created a `chp_payment_receipts` row. A double-tapped Submit, a retried
request or a flaky connection therefore produced *two* PENDING receipts
for a single real payment, and an administrator reviewing a queue has no
way to tell them apart: approving both credits the balance twice for
money that was paid once.

The client now holds a synchronous latch, but the client is not the
protection — a request that reaches the server must be refused there.
These tests drive the HTTP boundary, which is where the duplicate
actually arrives.
"""
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.db.models.payment import AdminCard, PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.session import get_db_session
from app.main import app
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    """
    Turns the limiter off for this file.

    `/billing/topup` is deliberately on the tighter bucket (10/minute) and
    the middleware keys on client IP, so every request in this module
    shares one counter — these tests would start 429-ing partway through
    and hide the behaviour they exist to check. `0` is the documented
    disable value, so this uses the real switch rather than bypassing the
    middleware.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "API_RATE_LIMIT_EXPENSIVE_PER_MINUTE", 0)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 0)

# A 1x1 PNG — the smallest thing `store_image` will accept as a real image.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415478da63f8cfc00000030101003c2b0a5b0000000049454e"
    "44ae426082"
)


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


async def _card(session, label: str = "Card") -> AdminCard:
    card = AdminCard(
        card_number="8600 0000 0000 0000", holder_name=label, bank_name=label, is_active=True
    )
    session.add(card)
    await session.flush()
    return card


def _payload(amount: int, card_id: int):
    return (
        {"amount": str(amount), "card_id": str(card_id)},
        {"receipt": ("receipt.png", PNG, "image/png")},
    )


def _kwargs(amount: int, card_id: int) -> dict:
    data, files = _payload(amount, card_id)
    return {"data": data, "files": files}


# ---------- the duplicate guard ----------


async def test_a_valid_topup_creates_one_pending_receipt(db_session, as_user):
    user = await make_user(db_session, 9701)
    card = await _card(db_session)
    await db_session.commit()

    data, files = _payload(50000, card.id)
    async with as_user(user) as client:
        response = await client.post("/api/billing/topup", data=data, files=files)

    assert response.status_code == 200
    assert response.json()["status"] == "submitted"
    assert await count_rows(db_session, PaymentReceipt, user_id=user.id) == 1


async def test_an_identical_second_submission_is_refused(db_session, as_user):
    """The double tap. Two requests, one receipt."""
    user = await make_user(db_session, 9702)
    card = await _card(db_session)
    await db_session.commit()

    async with as_user(user) as client:
        first = await client.post("/api/billing/topup", **_kwargs(50000, card.id))
        second = await client.post("/api/billing/topup", **_kwargs(50000, card.id))

    assert first.status_code == 200
    assert second.status_code == 409
    assert await count_rows(db_session, PaymentReceipt, user_id=user.id) == 1


async def test_a_different_amount_is_not_a_duplicate(db_session, as_user):
    """The guard suppresses repeats, not legitimate separate payments."""
    user = await make_user(db_session, 9703)
    card = await _card(db_session)
    await db_session.commit()

    async with as_user(user) as client:
        assert (
            await client.post("/api/billing/topup", **_kwargs(50000, card.id))
        ).status_code == 200
        assert (
            await client.post("/api/billing/topup", **_kwargs(75000, card.id))
        ).status_code == 200

    assert await count_rows(db_session, PaymentReceipt, user_id=user.id) == 2


async def test_a_different_card_is_not_a_duplicate(db_session, as_user):
    user = await make_user(db_session, 9704)
    first_card = await _card(db_session, "First")
    second_card = await _card(db_session, "Second")
    await db_session.commit()

    async with as_user(user) as client:
        assert (
            await client.post("/api/billing/topup", **_kwargs(50000, first_card.id))
        ).status_code == 200
        assert (
            await client.post("/api/billing/topup", **_kwargs(50000, second_card.id))
        ).status_code == 200

    assert await count_rows(db_session, PaymentReceipt, user_id=user.id) == 2


async def test_another_users_pending_receipt_does_not_block_mine(db_session, as_user):
    """The guard is per-user. One person paying must not stop another."""
    first = await make_user(db_session, 9705)
    second = await make_user(db_session, 9706)
    card = await _card(db_session)
    await db_session.commit()

    async with as_user(first) as client:
        assert (
            await client.post("/api/billing/topup", **_kwargs(50000, card.id))
        ).status_code == 200
    async with as_user(second) as client:
        assert (
            await client.post("/api/billing/topup", **_kwargs(50000, card.id))
        ).status_code == 200

    assert await count_rows(db_session, PaymentReceipt, user_id=first.id) == 1
    assert await count_rows(db_session, PaymentReceipt, user_id=second.id) == 1


async def test_once_reviewed_the_same_amount_can_be_paid_again(db_session, as_user):
    """
    Only *unreviewed* receipts block. A user who topped up 50 000 last week
    and wants to do it again must not be locked out by their own history.
    """
    user = await make_user(db_session, 9707)
    card = await _card(db_session)
    db_session.add(
        PaymentReceipt(
            user_id=user.id,
            admin_card_id=card.id,
            purpose=PaymentPurpose.TOPUP,
            amount=Decimal("50000"),
            receipt_photo_file_id="",
            status=PaymentStatus.APPROVED,
        )
    )
    await db_session.commit()

    async with as_user(user) as client:
        assert (
            await client.post("/api/billing/topup", **_kwargs(50000, card.id))
        ).status_code == 200


async def test_a_rejected_receipt_does_not_block_a_retry(db_session, as_user):
    """Retrying after a rejection is the documented recovery path."""
    user = await make_user(db_session, 9708)
    card = await _card(db_session)
    db_session.add(
        PaymentReceipt(
            user_id=user.id,
            admin_card_id=card.id,
            purpose=PaymentPurpose.TOPUP,
            amount=Decimal("50000"),
            receipt_photo_file_id="",
            status=PaymentStatus.REJECTED,
        )
    )
    await db_session.commit()

    async with as_user(user) as client:
        assert (
            await client.post("/api/billing/topup", **_kwargs(50000, card.id))
        ).status_code == 200


async def test_a_pending_subscription_receipt_does_not_block_a_topup(db_session, as_user):
    """Different purposes are different payments."""
    user = await make_user(db_session, 9709)
    card = await _card(db_session)
    db_session.add(
        PaymentReceipt(
            user_id=user.id,
            admin_card_id=card.id,
            purpose=PaymentPurpose.SUBSCRIPTION,
            amount=Decimal("50000"),
            receipt_photo_file_id="",
            status=PaymentStatus.PENDING,
        )
    )
    await db_session.commit()

    async with as_user(user) as client:
        assert (
            await client.post("/api/billing/topup", **_kwargs(50000, card.id))
        ).status_code == 200


# ---------- validation and authorization, unchanged ----------


async def test_a_non_positive_amount_is_refused(db_session, as_user):
    user = await make_user(db_session, 9710)
    card = await _card(db_session)
    await db_session.commit()

    async with as_user(user) as client:
        for amount in (0, -100):
            assert (
                await client.post("/api/billing/topup", **_kwargs(amount, card.id))
            ).status_code == 422

    assert await count_rows(db_session, PaymentReceipt, user_id=user.id) == 0


async def test_an_unknown_or_inactive_card_is_refused(db_session, as_user):
    user = await make_user(db_session, 9711)
    inactive = await _card(db_session, "Retired")
    inactive.is_active = False
    await db_session.commit()

    async with as_user(user) as client:
        assert (
            await client.post("/api/billing/topup", **_kwargs(50000, inactive.id))
        ).status_code == 404
        assert (
            await client.post("/api/billing/topup", **_kwargs(50000, 999999))
        ).status_code == 404

    assert await count_rows(db_session, PaymentReceipt, user_id=user.id) == 0


async def test_the_receipt_belongs_to_the_caller_not_the_request(db_session, as_user):
    """
    There is no field through which a submitter can name someone else —
    the owner comes from the verified session.
    """
    user = await make_user(db_session, 9712)
    victim = await make_user(db_session, 9713)
    card = await _card(db_session)
    await db_session.commit()

    data, files = _payload(50000, card.id)
    data["user_id"] = str(victim.id)  # ignored: not part of the form contract

    async with as_user(user) as client:
        assert (
            await client.post("/api/billing/topup", data=data, files=files)
        ).status_code == 200

    assert await count_rows(db_session, PaymentReceipt, user_id=user.id) == 1
    assert await count_rows(db_session, PaymentReceipt, user_id=victim.id) == 0


# ---------- the shared guard, both surfaces ----------
#
# The Mini App's Submit button and the bot's photo handler both create
# receipts. Phase 2 first guarded only the HTTP route, which left the bot
# able to add a second PENDING receipt for a payment already submitted.
# The decision now lives in one service; these pin that it is genuinely
# shared rather than duplicated.


async def test_the_guard_refuses_a_pending_duplicate_directly(db_session):
    from app.services.payment_submission import (
        DuplicateReceiptError,
        guard_against_duplicate,
    )

    user = await make_user(db_session, 9720)
    card = await _card(db_session)
    db_session.add(
        PaymentReceipt(
            user_id=user.id,
            admin_card_id=card.id,
            purpose=PaymentPurpose.TOPUP,
            amount=Decimal("50000"),
            receipt_photo_file_id="tg-file-id",
            status=PaymentStatus.PENDING,
        )
    )
    await db_session.flush()

    with pytest.raises(DuplicateReceiptError):
        await guard_against_duplicate(
            db_session,
            user_id=user.id,
            purpose=PaymentPurpose.TOPUP,
            card_id=card.id,
            amount=50000,
        )


async def test_a_receipt_submitted_in_the_bot_blocks_the_same_one_in_the_app(
    db_session, as_user
):
    """
    Cross-surface. The bot's receipt carries a Telegram file id rather than
    an uploaded image, but it is the same pending payment, and the app must
    not let the user submit it a second time.
    """
    user = await make_user(db_session, 9721)
    card = await _card(db_session)
    db_session.add(
        PaymentReceipt(
            user_id=user.id,
            admin_card_id=card.id,
            purpose=PaymentPurpose.TOPUP,
            amount=Decimal("50000"),
            receipt_photo_file_id="tg-file-id",
            status=PaymentStatus.PENDING,
        )
    )
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.post("/api/billing/topup", **_kwargs(50000, card.id))

    assert response.status_code == 409
    assert await count_rows(db_session, PaymentReceipt, user_id=user.id) == 1


async def test_both_surfaces_call_the_same_guard():
    """
    Asserted against the sources: a guard on one door is not a guard. If a
    third submission path ever appears, this is what should fail.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    creators = {
        "app/api/billing.py",
        "app/bot/handlers/payment.py",
    }
    for relative in creators:
        source = (root / relative).read_text(encoding="utf-8")
        assert "PaymentReceipt(" in source, f"{relative} no longer creates receipts"
        assert "guard_against_duplicate" in source, f"{relative} skips the duplicate guard"

    # And nothing else creates one.
    others = [
        path
        for path in root.glob("app/**/*.py")
        if "PaymentReceipt(" in path.read_text(encoding="utf-8")
        and path.relative_to(root).as_posix() not in creators
        and path.name != "payment.py"  # the model's own module
    ]
    assert others == [], f"unguarded receipt creation in: {others}"


# ---------- concurrency ----------
#
# The guard began life as a plain SELECT, and a plain SELECT is not a
# guard: two requests arriving together each read "no duplicate" before
# either has inserted, and both proceed. That was reproducible — two
# receipts for one payment — and is why the payer's row is locked first.
# These drive independent sessions, because a shared one serialises the
# work and would hide the very thing being asserted.


async def _seed(db_factory, telegram_id: int):
    async with db_factory() as setup:
        user = await make_user(setup, telegram_id)
        card = AdminCard(
            card_number="8600 0000 0000 0000", holder_name="H", bank_name="B", is_active=True
        )
        setup.add(card)
        await setup.flush()
        ids = (user.id, card.id)
        await setup.commit()
    return ids


async def _submit(db_factory, user_id: int, card_id: int, amount: int = 50000) -> str:
    """One submission through the shared guard, in a transaction of its own."""
    from app.services.payment_submission import (
        DuplicateReceiptError,
        guard_against_duplicate,
    )

    async with db_factory() as session:
        try:
            await guard_against_duplicate(
                session,
                user_id=user_id,
                purpose=PaymentPurpose.TOPUP,
                card_id=card_id,
                amount=amount,
            )
        except DuplicateReceiptError:
            return "refused"
        session.add(
            PaymentReceipt(
                user_id=user_id,
                admin_card_id=card_id,
                purpose=PaymentPurpose.TOPUP,
                amount=Decimal(str(amount)),
                receipt_photo_file_id="",
                status=PaymentStatus.PENDING,
            )
        )
        await session.commit()
        return "created"


async def test_two_simultaneous_submissions_create_one_receipt(db_factory):
    import asyncio

    user_id, card_id = await _seed(db_factory, 9800)

    results = await asyncio.gather(
        _submit(db_factory, user_id, card_id), _submit(db_factory, user_id, card_id)
    )

    assert sorted(results) == ["created", "refused"]
    async with db_factory() as check:
        assert await count_rows(check, PaymentReceipt, user_id=user_id) == 1


async def test_a_burst_of_submissions_still_creates_one_receipt(db_factory):
    """Five at once — a retry storm, not just a double tap."""
    import asyncio

    user_id, card_id = await _seed(db_factory, 9801)

    results = await asyncio.gather(*(_submit(db_factory, user_id, card_id) for _ in range(5)))

    assert results.count("created") == 1, results
    async with db_factory() as check:
        assert await count_rows(check, PaymentReceipt, user_id=user_id) == 1


async def test_concurrent_submissions_of_different_amounts_both_land(db_factory):
    """
    The lock must serialise, not reject. Two genuinely different top-ups
    sent together are both legitimate and must both be recorded.
    """
    import asyncio

    user_id, card_id = await _seed(db_factory, 9802)

    results = await asyncio.gather(
        _submit(db_factory, user_id, card_id, 50000),
        _submit(db_factory, user_id, card_id, 75000),
    )

    assert results == ["created", "created"]
    async with db_factory() as check:
        assert await count_rows(check, PaymentReceipt, user_id=user_id) == 2


async def test_one_payers_lock_does_not_block_another(db_factory):
    """Per-payer. A busy user must not serialise the whole platform."""
    import asyncio

    first_user, first_card = await _seed(db_factory, 9803)
    second_user, second_card = await _seed(db_factory, 9804)

    results = await asyncio.gather(
        _submit(db_factory, first_user, first_card),
        _submit(db_factory, second_user, second_card),
    )

    assert results == ["created", "created"]
