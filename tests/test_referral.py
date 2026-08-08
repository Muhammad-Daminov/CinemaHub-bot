"""
Referral payouts — the rule, and the guarantee that it never pays twice.

Paying twice is the failure this file exists for. Approvals are retried,
arrive concurrently from the bot and the panel, and a user can top up
more than once; each of those is a chance to credit the same referral
again. The guard is a database index, not a Python check, so the
concurrency test uses independent sessions — two coroutines on one
session share its transaction and could never contend.
"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models.payment import PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.models.user import BalanceHistory, BalanceTxType, User
from app.services.payment_review import approve_receipt
from app.services.referral import pay_referral_bonus
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]

BONUS = Decimal(str(settings.REFERRAL_BONUS_AMOUNT))
TOPUP = Decimal("50000.00")


async def _pair(session, referrer_tg: int, referee_tg: int):
    referrer = await make_user(session, referrer_tg)
    referee = await make_user(session, referee_tg)
    referee.referred_by_id = referrer.id
    await session.flush()
    return referrer, referee


async def _topup_receipt(session, user: User) -> PaymentReceipt:
    receipt = PaymentReceipt(
        user_id=user.id,
        purpose=PaymentPurpose.TOPUP,
        amount=TOPUP,
        receipt_photo_file_id="f",
        status=PaymentStatus.PENDING,
    )
    session.add(receipt)
    await session.flush()
    return receipt


async def _balance(session, user_id: int) -> Decimal:
    return (await session.execute(select(User.balance).where(User.id == user_id))).scalar_one()


# ---------- the rule ----------


async def test_both_sides_are_credited(db_session):
    referrer, referee = await _pair(db_session, 9701, 9702)

    credited = await pay_referral_bonus(db_session, referee)

    assert set(credited) == {referrer.id, referee.id}
    assert await _balance(db_session, referrer.id) == BONUS
    assert await _balance(db_session, referee.id) == BONUS


async def test_the_ledger_matches_the_balance(db_session):
    """The signed ledger must sum to the balance — a credit without its row breaks that."""
    referrer, referee = await _pair(db_session, 9703, 9704)
    await pay_referral_bonus(db_session, referee)

    entries = (
        await db_session.execute(
            select(BalanceHistory).where(BalanceHistory.user_id == referrer.id)
        )
    ).scalars().all()
    assert len(entries) == 1
    assert entries[0].tx_type == BalanceTxType.REFERRAL_BONUS
    assert entries[0].amount == BONUS


async def test_a_user_with_no_referrer_is_paid_nothing(db_session):
    user = await make_user(db_session, 9705)
    assert await pay_referral_bonus(db_session, user) == []
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 0


async def test_self_referral_pays_nothing(db_session):
    """The first thing anyone tries against a referral scheme."""
    user = await make_user(db_session, 9706)
    user.referred_by_id = user.id
    await db_session.flush()

    assert await pay_referral_bonus(db_session, user) == []
    assert await _balance(db_session, user.id) == Decimal("0.00")


async def test_a_zero_bonus_disables_payouts(db_session, monkeypatch):
    """0 is the documented "not decided yet" setting and must move no money."""
    monkeypatch.setattr(settings, "REFERRAL_BONUS_AMOUNT", 0)
    referrer, referee = await _pair(db_session, 9707, 9708)

    assert await pay_referral_bonus(db_session, referee) == []
    assert await _balance(db_session, referrer.id) == Decimal("0.00")


# ---------- paying exactly once ----------


async def test_a_repeated_payout_is_a_no_op(db_session):
    referrer, referee = await _pair(db_session, 9710, 9711)

    await pay_referral_bonus(db_session, referee)
    assert await pay_referral_bonus(db_session, referee) == [], "the second call must pay nothing"

    assert await _balance(db_session, referrer.id) == BONUS
    assert await count_rows(db_session, BalanceHistory, user_id=referrer.id) == 1


async def test_a_second_topup_does_not_pay_again(db_session, silence_bot):
    """
    The rule is *first* approved payment. The reference is scoped to the
    referred user, so every later top-up collides with the same index
    entry — no counting query, and therefore no race in the counting.
    """
    referrer, referee = await _pair(db_session, 9712, 9713)

    for _ in range(3):
        receipt = await _topup_receipt(db_session, referee)
        await approve_receipt(db_session, receipt.id, None)

    assert await _balance(db_session, referrer.id) == BONUS
    # Three top-ups plus one bonus.
    assert await count_rows(db_session, BalanceHistory, user_id=referee.id) == 4
    assert await _balance(db_session, referee.id) == TOPUP * 3 + BONUS


async def test_approving_a_subscription_receipt_pays_no_referral(db_session, silence_bot):
    """The rule names a top-up. A subscription receipt moves no balance at all."""
    referrer, referee = await _pair(db_session, 9714, 9715)
    from tests.conftest import make_paid_plan

    plan = await make_paid_plan(db_session)
    receipt = PaymentReceipt(
        user_id=referee.id,
        plan_id=plan.id,
        purpose=PaymentPurpose.SUBSCRIPTION,
        amount=TOPUP,
        receipt_photo_file_id="f",
        status=PaymentStatus.PENDING,
    )
    db_session.add(receipt)
    await db_session.flush()

    await approve_receipt(db_session, receipt.id, None)
    assert await _balance(db_session, referrer.id) == Decimal("0.00")


async def test_concurrent_payouts_credit_exactly_once(db_factory, silence_bot):
    """
    Two approvals landing together. Without the unique index both read
    "not paid yet" and both credit — the same lost-update shape that once
    turned one receipt into five.
    """
    import asyncio

    async with db_factory() as setup:
        referrer = await make_user(setup, 9720)
        referee = await make_user(setup, 9721)
        referee.referred_by_id = referrer.id
        await setup.flush()
        receipts = [(await _topup_receipt(setup, referee)).id for _ in range(4)]
        await setup.commit()
        referrer_id, referee_id = referrer.id, referee.id

    async def approve(receipt_id: int) -> None:
        async with db_factory() as session:
            try:
                await approve_receipt(session, receipt_id, None)
                await session.commit()
            except Exception:
                await session.rollback()

    await asyncio.gather(*(approve(receipt_id) for receipt_id in receipts))

    async with db_factory() as check:
        assert await _balance(check, referrer_id) == BONUS, "the bonus must be paid exactly once"
        bonuses = (
            await check.execute(
                select(BalanceHistory).where(
                    BalanceHistory.user_id == referee_id,
                    BalanceHistory.tx_type == BalanceTxType.REFERRAL_BONUS,
                )
            )
        ).scalars().all()
        assert len(bonuses) == 1
