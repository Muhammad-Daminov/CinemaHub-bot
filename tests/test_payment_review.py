"""
Receipt approval — the money path.

These exist because of a real production incident: a single receipt
produced five balance-ledger rows and five subscriptions, one of which
stacked an extra 30 days of premium. The status guard in
`approve_receipt` was not a guard at all, because concurrent approvals
all read PENDING before any of them wrote.

`test_concurrent_approvals_credit_exactly_once` is the regression test
for that incident and the reason `db_factory` hands out independent
sessions: two coroutines sharing one session share its transaction and
can never contend, so a shared-session version of this test would pass
against the broken code too.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.payment import PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.models.user import BalanceHistory, Subscription, SubscriptionPlan, User
from app.services.payment_review import (
    ReceiptNotFoundError,
    ReceiptReviewError,
    approve_receipt,
    reject_receipt,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]

AMOUNT = Decimal("50000.00")


async def _make_receipt(session, user: User, purpose: PaymentPurpose) -> PaymentReceipt:
    receipt = PaymentReceipt(
        user_id=user.id,
        purpose=purpose,
        subscription_plan=(
            SubscriptionPlan.PREMIUM if purpose == PaymentPurpose.SUBSCRIPTION else None
        ),
        amount=AMOUNT,
        receipt_photo_file_id="test-file-id",
        status=PaymentStatus.PENDING,
    )
    session.add(receipt)
    await session.flush()
    return receipt


# ---------- topup ----------


async def test_topup_credits_balance_once(db_session, silence_bot):
    user = await make_user(db_session, 5573610231)
    receipt = await _make_receipt(db_session, user, PaymentPurpose.TOPUP)
    await db_session.commit()

    await approve_receipt(db_session, receipt.id, reviewer_user_id=None)
    await db_session.commit()

    refreshed = await db_session.get(User, user.id, populate_existing=True)
    assert refreshed.balance == AMOUNT
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 1
    assert await count_rows(db_session, Subscription, user_id=user.id) == 0


async def test_topup_marks_receipt_approved(db_session, silence_bot):
    user = await make_user(db_session, 5573610231)
    receipt = await _make_receipt(db_session, user, PaymentPurpose.TOPUP)
    await db_session.commit()

    await approve_receipt(db_session, receipt.id, reviewer_user_id=None)
    await db_session.commit()

    stored = await db_session.get(PaymentReceipt, receipt.id, populate_existing=True)
    assert stored.status == PaymentStatus.APPROVED
    assert stored.reviewed_at is not None


# ---------- subscription ----------


async def test_subscription_activates_without_crediting_balance(db_session, silence_bot):
    """
    The behaviour change: paying for premium buys premium, not premium
    plus a full refund into spendable balance.
    """
    user = await make_user(db_session, 5573610231)
    receipt = await _make_receipt(db_session, user, PaymentPurpose.SUBSCRIPTION)
    await db_session.commit()

    await approve_receipt(db_session, receipt.id, reviewer_user_id=None)
    await db_session.commit()

    refreshed = await db_session.get(User, user.id, populate_existing=True)
    assert refreshed.balance == Decimal("0")
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 0
    assert await count_rows(db_session, Subscription, user_id=user.id) == 1


async def test_subscription_extends_an_active_one(db_session, silence_bot):
    now = datetime.now(timezone.utc)
    user = await make_user(db_session, 5573610231)
    db_session.add(
        Subscription(
            user_id=user.id,
            plan=SubscriptionPlan.PREMIUM,
            expires_at=now + timedelta(days=10),
        )
    )
    receipt = await _make_receipt(db_session, user, PaymentPurpose.SUBSCRIPTION)
    await db_session.commit()
    user_id = user.id

    await approve_receipt(db_session, receipt.id, reviewer_user_id=None)
    await db_session.commit()

    rows = (
        (await db_session.execute(select(Subscription).where(Subscription.user_id == user_id)))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    # The new term starts from the existing expiry, not from now — stacking,
    # not overwriting. 10 days remaining + 30 purchased must exceed 30.
    assert max(r.expires_at for r in rows) > now + timedelta(days=30)


# ---------- idempotency ----------


async def test_second_approval_is_refused(db_session, silence_bot):
    user = await make_user(db_session, 5573610231)
    receipt = await _make_receipt(db_session, user, PaymentPurpose.TOPUP)
    await db_session.commit()
    # Held as plain ints: the rollback below expires every instance in the
    # session, after which reading user.id would itself trigger a lazy load.
    user_id, receipt_id = user.id, receipt.id

    await approve_receipt(db_session, receipt_id, reviewer_user_id=None)
    await db_session.commit()

    with pytest.raises(ReceiptReviewError):
        await approve_receipt(db_session, receipt_id, reviewer_user_id=None)
    await db_session.rollback()

    balance = (
        await db_session.execute(select(User.balance).where(User.id == user_id))
    ).scalar_one()
    assert balance == AMOUNT
    assert await count_rows(db_session, BalanceHistory, user_id=user_id) == 1


async def test_rejecting_an_approved_receipt_is_refused(db_session, silence_bot):
    user = await make_user(db_session, 5573610231)
    receipt = await _make_receipt(db_session, user, PaymentPurpose.TOPUP)
    await db_session.commit()

    await approve_receipt(db_session, receipt.id, reviewer_user_id=None)
    await db_session.commit()

    with pytest.raises(ReceiptReviewError):
        await reject_receipt(db_session, receipt.id, None, "changed my mind")


async def test_approving_a_rejected_receipt_is_refused(db_session, silence_bot):
    """The inverse guard: rejection is as final as approval."""
    user = await make_user(db_session, 5573610231)
    receipt = await _make_receipt(db_session, user, PaymentPurpose.TOPUP)
    await db_session.commit()

    await reject_receipt(db_session, receipt.id, None, "blurry photo")
    await db_session.commit()

    with pytest.raises(ReceiptReviewError):
        await approve_receipt(db_session, receipt.id, reviewer_user_id=None)


async def test_unknown_receipt_raises_not_found(db_session, silence_bot):
    with pytest.raises(ReceiptNotFoundError):
        await approve_receipt(db_session, 999_999, reviewer_user_id=None)


async def test_rejecting_an_unknown_receipt_raises_not_found(db_session, silence_bot):
    with pytest.raises(ReceiptNotFoundError):
        await reject_receipt(db_session, 999_999, None, "nope")


async def test_rejection_credits_nothing(db_session, silence_bot):
    user = await make_user(db_session, 5573610231)
    receipt = await _make_receipt(db_session, user, PaymentPurpose.TOPUP)
    await db_session.commit()

    await reject_receipt(db_session, receipt.id, None, "blurry photo")
    await db_session.commit()

    refreshed = await db_session.get(User, user.id, populate_existing=True)
    assert refreshed.balance == Decimal("0")
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 0
    stored = await db_session.get(PaymentReceipt, receipt.id, populate_existing=True)
    assert stored.status == PaymentStatus.REJECTED


# ---------- the regression test ----------


@pytest.mark.parametrize("concurrency", [5])
async def test_concurrent_approvals_credit_exactly_once(
    db_factory, silence_bot, concurrency
):
    """
    Reproduces the production incident: N admins (or N taps) approving the
    same receipt at once. Against the pre-fix code this produced N ledger
    rows, N subscriptions and a single lost-update credit. It must now
    produce exactly one of each, with the losers refused.
    """
    import asyncio

    async with db_factory() as setup:
        user = await make_user(setup, 5573610231)
        receipt = await _make_receipt(setup, user, PaymentPurpose.TOPUP)
        await setup.commit()
        user_id, receipt_id = user.id, receipt.id

    async def attempt() -> str:
        async with db_factory() as session:
            try:
                await approve_receipt(session, receipt_id, reviewer_user_id=None)
                await session.commit()
                return "approved"
            except ReceiptReviewError:
                await session.rollback()
                return "refused"

    results = await asyncio.gather(*(attempt() for _ in range(concurrency)))

    assert results.count("approved") == 1, f"expected exactly one winner, got {results}"
    assert results.count("refused") == concurrency - 1

    async with db_factory() as check:
        credited = (
            await check.execute(select(User.balance).where(User.id == user_id))
        ).scalar_one()
        assert credited == AMOUNT, "balance credited more than once, or lost to a race"
        assert await count_rows(check, BalanceHistory, user_id=user_id) == 1
        assert await count_rows(check, Subscription, user_id=user_id) == 0
