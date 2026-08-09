"""
Payment history, shared by the bot and the Mini App.

The bot's Orders screen was a "coming in a later phase" stub while the
Mini App had rendered this data since Phase 5. Rather than write the
query a second time, both surfaces now read
`app.services.payment_history` — because two copies of "what has this
user paid" is how two surfaces start disagreeing about someone's money.

The test that matters is the parity one: the same user must produce the
same history whichever surface asks.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import get_current_user
from app.db.models.payment import PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.models.user import BalanceHistory, BalanceTxType
from app.db.session import get_db_session
from app.main import app
from app.services.payment_history import PENDING_RECEIPT, payment_history
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _ledger(session, user, amount, tx_type=BalanceTxType.TOPUP, description="x", ago_days=0):
    entry = BalanceHistory(
        user_id=user.id,
        amount=Decimal(amount),
        tx_type=tx_type,
        description=description,
        created_at=datetime.now(timezone.utc) - timedelta(days=ago_days),
    )
    session.add(entry)
    await session.flush()
    return entry


async def _pending_receipt(session, user, amount="25000"):
    receipt = PaymentReceipt(
        user_id=user.id,
        purpose=PaymentPurpose.TOPUP,
        amount=Decimal(amount),
        receipt_photo_file_id="f",
        status=PaymentStatus.PENDING,
    )
    session.add(receipt)
    await session.flush()
    return receipt


# ---------- the shared service ----------


async def test_history_is_newest_first(db_session):
    user = await make_user(db_session, 9201)
    await _ledger(db_session, user, "100", ago_days=5, description="older")
    await _ledger(db_session, user, "200", ago_days=1, description="newer")

    entries = await payment_history(db_session, user.id)
    assert [e.description for e in entries] == ["newer", "older"]


async def test_pending_receipts_appear_alongside_settled_entries(db_session):
    """
    A user who has just submitted a receipt would otherwise see nothing
    and submit it again.
    """
    user = await make_user(db_session, 9202)
    await _ledger(db_session, user, "100")
    await _pending_receipt(db_session, user)

    kinds = {e.kind for e in await payment_history(db_session, user.id)}
    assert kinds == {BalanceTxType.TOPUP.value, PENDING_RECEIPT}


async def test_a_pending_entry_is_marked_as_such(db_session):
    user = await make_user(db_session, 9203)
    await _pending_receipt(db_session, user)

    entry = (await payment_history(db_session, user.id))[0]
    assert entry.is_pending is True
    assert entry.status == PaymentStatus.PENDING.value


async def test_amounts_stay_decimal(db_session):
    """This is money — the float conversion belongs at the serialising surface."""
    user = await make_user(db_session, 9204)
    await _ledger(db_session, user, "-10000.50", tx_type=BalanceTxType.DEDUCTION)

    entry = (await payment_history(db_session, user.id))[0]
    assert isinstance(entry.amount, Decimal)
    assert entry.amount == Decimal("-10000.50")


async def test_history_is_per_user(db_session):
    mine = await make_user(db_session, 9205)
    theirs = await make_user(db_session, 9206)
    await _ledger(db_session, theirs, "999")

    assert await payment_history(db_session, mine.id) == []


async def test_the_limit_is_respected(db_session):
    user = await make_user(db_session, 9207)
    for day in range(5):
        await _ledger(db_session, user, "10", ago_days=day)

    assert len(await payment_history(db_session, user.id, limit=3)) == 3


async def test_an_approved_receipt_is_not_double_counted(db_session):
    """
    Only *pending* receipts are added. An approved one already has its
    ledger row, and listing both would show the same payment twice.
    """
    user = await make_user(db_session, 9208)
    receipt = await _pending_receipt(db_session, user)
    receipt.status = PaymentStatus.APPROVED
    await db_session.flush()
    await _ledger(db_session, user, "25000", description=f"Payment receipt #{receipt.id} approved")

    entries = await payment_history(db_session, user.id)
    assert len(entries) == 1
    assert entries[0].kind == BalanceTxType.TOPUP.value


# ---------- both surfaces agree ----------


@pytest.fixture
async def client(db_session):
    user = await make_user(db_session, 9210)
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


async def test_the_api_returns_what_the_service_returns(client, db_session):
    """
    Parity, asserted directly. The bot renders the service's output and
    the Mini App renders this endpoint's; if these two ever diverge, the
    two surfaces are showing different money.
    """
    test_client, user = client
    await _ledger(db_session, user, "50000", description="top-up")
    await _ledger(db_session, user, "-10000", tx_type=BalanceTxType.DEDUCTION, description="plan")
    await _pending_receipt(db_session, user, "7000")
    await db_session.commit()

    from_service = await payment_history(db_session, user.id)
    from_api = (await test_client.get("/api/billing/history")).json()

    assert len(from_api) == len(from_service)
    for api_row, service_row in zip(from_api, from_service):
        assert api_row["id"] == service_row.id
        assert api_row["kind"] == service_row.kind
        assert Decimal(str(api_row["amount"])) == service_row.amount
        assert api_row["status"] == service_row.status


async def test_an_empty_history_is_empty_on_both_surfaces(client, db_session):
    test_client, user = client
    assert await payment_history(db_session, user.id) == []
    assert (await test_client.get("/api/billing/history")).json() == []


async def test_the_bot_renders_the_same_entries(db_session):
    """
    The bot's Orders handler formats exactly what the service returns —
    proven by driving the handler and checking the rendered lines against
    the service's own rows rather than re-deriving them.
    """
    from app.bot.handlers.base import handle_orders_entry

    user = await make_user(db_session, 9211)
    await _ledger(db_session, user, "50000", description="top-up")
    await _pending_receipt(db_session, user, "7000")

    sent: list[str] = []

    class FakeMessage:
        from_user = type("U", (), {"id": 9211})()

        async def answer(self, text, **kwargs):
            sent.append(text)

    await handle_orders_entry(FakeMessage(), db_session, lambda key, **kw: f"{key}:{kw}")

    assert len(sent) == 1
    rendered = sent[0]
    entries = await payment_history(db_session, user.id)
    assert rendered.count("orders.line") == len(entries)
    assert "orders.line_pending" in rendered, "the pending receipt must be shown as pending"


async def test_the_bot_says_so_when_there_is_no_history(db_session):
    from app.bot.handlers.base import handle_orders_entry

    await make_user(db_session, 9212)
    sent: list[str] = []

    class FakeMessage:
        from_user = type("U", (), {"id": 9212})()

        async def answer(self, text, **kwargs):
            sent.append(text)

    await handle_orders_entry(FakeMessage(), db_session, lambda key, **kw: key)
    assert sent == ["orders.empty"], "the 'coming soon' stub must be gone"
