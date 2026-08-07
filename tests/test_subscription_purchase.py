"""
Buying a subscription: tier rules, balance, and the queue.

Every rule is asserted through *relative priority*, never a plan name —
if these tests referenced "premium" they would pass while the promise
that a new tier needs no code change quietly broke.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.user import BalanceHistory, BalanceTxType, Subscription, User
from app.services.subscription_purchase import (
    InsufficientBalanceError,
    PlanUnavailableError,
    active_subscription,
    preview_purchase,
    purchase_plan,
    queued_subscriptions,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _plan(session, code, price, priority, days=30, **kwargs):
    from app.db.models.subscription import SubscriptionPlanModel

    plan = SubscriptionPlanModel(
        code=code,
        name=code.title(),
        price=Decimal(str(price)),
        duration_days=days,
        priority=priority,
        is_active=True,
        **kwargs,
    )
    session.add(plan)
    await session.flush()
    return plan


async def _tiers(session):
    """light=1, pro=2, premium=3 — the example hierarchy from the request."""
    return (
        await _plan(session, "light", 10000, 1),
        await _plan(session, "pro", 20000, 2),
        await _plan(session, "premium", 30000, 3),
    )


async def _rich(session, telegram_id, amount="500000"):
    return await make_user(session, telegram_id, balance=amount)


# ---------- first purchase ----------


async def test_first_purchase_activates_immediately(db_session):
    user = await _rich(db_session, 9501)
    light, _, _ = await _tiers(db_session)

    sub = await purchase_plan(db_session, user, light.id)
    assert sub.plan_id == light.id
    assert sub.started_at <= datetime.now(timezone.utc)
    assert sub.expires_at > datetime.now(timezone.utc)


async def test_purchase_debits_the_balance_and_writes_a_ledger_row(db_session):
    user = await _rich(db_session, 9502, "100000")
    light, _, _ = await _tiers(db_session)

    await purchase_plan(db_session, user, light.id)
    refreshed = await db_session.get(User, user.id, populate_existing=True)
    assert refreshed.balance == Decimal("90000.00")

    entry = (
        await db_session.execute(
            select(BalanceHistory).where(BalanceHistory.user_id == user.id)
        )
    ).scalar_one()
    assert entry.tx_type == BalanceTxType.DEDUCTION
    # Signed ledger: the sum of it must equal the balance, so a purchase
    # has to be negative.
    assert entry.amount == Decimal("-10000.00")


async def test_insufficient_balance_carries_the_numbers_the_dialog_shows(db_session):
    user = await make_user(db_session, 9503, balance="5000")
    light, _, _ = await _tiers(db_session)

    with pytest.raises(InsufficientBalanceError) as caught:
        await purchase_plan(db_session, user, light.id)
    assert caught.value.balance == Decimal("5000")
    assert caught.value.price == Decimal("10000")
    assert caught.value.missing == Decimal("5000")


async def test_a_failed_purchase_moves_no_money(db_session):
    user = await make_user(db_session, 9504, balance="1")
    light, _, _ = await _tiers(db_session)

    with pytest.raises(InsufficientBalanceError):
        await purchase_plan(db_session, user, light.id)
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 0
    assert await count_rows(db_session, Subscription, user_id=user.id) == 0


async def test_the_free_plan_cannot_be_purchased(db_session):
    """Selling what everyone already has would take money for nothing."""
    user = await _rich(db_session, 9505)
    free = await _plan(db_session, "free", 0, 0, is_free=True)

    with pytest.raises(PlanUnavailableError):
        await purchase_plan(db_session, user, free.id)


async def test_an_inactive_plan_cannot_be_purchased(db_session):
    user = await _rich(db_session, 9506)
    retired = await _plan(db_session, "retired", 10000, 1)
    retired.is_active = False
    await db_session.flush()

    with pytest.raises(PlanUnavailableError):
        await purchase_plan(db_session, user, retired.id)


# ---------- same tier: extend ----------


async def test_same_plan_extends_rather_than_stacking_rows(db_session):
    user = await _rich(db_session, 9510)
    light, _, _ = await _tiers(db_session)

    first = await purchase_plan(db_session, user, light.id)
    original_expiry = first.expires_at

    await purchase_plan(db_session, user, light.id)
    assert await count_rows(db_session, Subscription, user_id=user.id) == 1, (
        "a renewal must extend the term, not accumulate a row each time"
    )
    refreshed = await db_session.get(Subscription, first.id, populate_existing=True)
    assert refreshed.expires_at == original_expiry + timedelta(days=30)


async def test_a_different_plan_at_the_same_priority_also_extends(db_session):
    """The rule is about rank, not identity."""
    user = await _rich(db_session, 9511)
    a = await _plan(db_session, "tier_a", 10000, 1)
    b = await _plan(db_session, "tier_b", 12000, 1)

    first = await purchase_plan(db_session, user, a.id)
    expiry = first.expires_at
    await purchase_plan(db_session, user, b.id)

    assert await count_rows(db_session, Subscription, user_id=user.id) == 1
    refreshed = await db_session.get(Subscription, first.id, populate_existing=True)
    assert refreshed.expires_at == expiry + timedelta(days=30)


# ---------- higher tier: upgrade now, carry remaining days ----------


async def test_upgrading_activates_immediately_and_carries_remaining_days(db_session):
    """
    The 1:1 carry-over. Twenty days left on light plus a thirty-day pro
    term must give fifty — forfeiting the remainder would be charging
    twice for the same days.
    """
    user = await _rich(db_session, 9520)
    light, pro, _ = await _tiers(db_session)

    old = await purchase_plan(db_session, user, light.id)
    # Wind the existing term back so exactly 20 days remain.
    now = datetime.now(timezone.utc)
    old.expires_at = now + timedelta(days=20)
    await db_session.flush()

    new = await purchase_plan(db_session, user, pro.id)
    assert new.plan_id == pro.id
    assert new.started_at <= datetime.now(timezone.utc)

    expected = timedelta(days=50)
    actual = new.expires_at - now
    assert abs(actual - expected) < timedelta(minutes=1)


async def test_upgrading_ends_the_old_term(db_session):
    """Two overlapping active subscriptions would make 'which tier?' ambiguous."""
    user = await _rich(db_session, 9521)
    light, pro, _ = await _tiers(db_session)

    old = await purchase_plan(db_session, user, light.id)
    await purchase_plan(db_session, user, pro.id)

    refreshed = await db_session.get(Subscription, old.id, populate_existing=True)
    assert refreshed.expires_at <= datetime.now(timezone.utc)

    current = await active_subscription(db_session, user.id)
    assert current is not None and current.plan_id == pro.id


async def test_upgrading_two_tiers_at_once_works(db_session):
    """Nothing may assume upgrades move one step."""
    user = await _rich(db_session, 9522)
    light, _, premium = await _tiers(db_session)

    await purchase_plan(db_session, user, light.id)
    await purchase_plan(db_session, user, premium.id)

    current = await active_subscription(db_session, user.id)
    assert current is not None and current.plan_id == premium.id


# ---------- lower tier: queue ----------


async def test_a_lower_tier_is_queued_and_does_not_downgrade(db_session):
    user = await _rich(db_session, 9530)
    light, _, premium = await _tiers(db_session)

    current = await purchase_plan(db_session, user, premium.id)
    queued = await purchase_plan(db_session, user, light.id)

    assert queued.started_at >= current.expires_at, "must wait for the better tier to end"
    still_current = await active_subscription(db_session, user.id)
    assert still_current is not None and still_current.plan_id == premium.id, (
        "buying a cheaper plan must never demote the user"
    )


async def test_queued_subscriptions_do_not_read_as_active(db_session):
    """
    The whole queue mechanism rests on this: a queued row is an ordinary
    row whose window has not opened, so every premium check must respect
    started_at.
    """
    user = await _rich(db_session, 9531)
    light, _, premium = await _tiers(db_session)
    await purchase_plan(db_session, user, premium.id)
    await purchase_plan(db_session, user, light.id)

    from app.services.subscriptions import get_active_subscription

    active = await get_active_subscription(db_session, user.id)
    assert active is not None and active.plan_id == premium.id


async def test_multiple_queued_purchases_chain_rather_than_overlap(db_session):
    user = await _rich(db_session, 9532)
    light, _, premium = await _tiers(db_session)

    current = await purchase_plan(db_session, user, premium.id)
    first = await purchase_plan(db_session, user, light.id)
    second = await purchase_plan(db_session, user, light.id)

    queued = await queued_subscriptions(db_session, user.id)
    assert len(queued) == 2
    assert first.started_at >= current.expires_at
    assert second.started_at >= first.expires_at, "the second must follow the first, not overlap it"


async def test_there_is_no_expiration_cap(db_session):
    """Explicitly required: queue as far into the future as the user pays for."""
    user = await _rich(db_session, 9533, "10000000")
    light, _, premium = await _tiers(db_session)

    await purchase_plan(db_session, user, premium.id)
    for _ in range(10):
        await purchase_plan(db_session, user, light.id)

    queued = await queued_subscriptions(db_session, user.id)
    assert len(queued) == 10
    assert queued[-1].expires_at > datetime.now(timezone.utc) + timedelta(days=300)


# ---------- preview agrees with the purchase ----------


@pytest.mark.parametrize(
    "held_priority,bought_priority,expected",
    [(None, 1, "activate"), (1, 1, "extend"), (1, 2, "upgrade"), (3, 1, "queued")],
)
async def test_preview_reports_the_outcome_the_purchase_produces(
    db_session, held_priority, bought_priority, expected
):
    user = await _rich(db_session, 9540 + bought_priority + (held_priority or 0) * 10)
    plans = {p.priority: p for p in await _tiers(db_session)}

    if held_priority is not None:
        await purchase_plan(db_session, user, plans[held_priority].id)

    result = await preview_purchase(db_session, user, plans[bought_priority].id)
    assert result["outcome"] == expected


async def test_preview_reports_the_shortfall_without_charging(db_session):
    user = await make_user(db_session, 9550, balance="4000")
    light, _, _ = await _tiers(db_session)

    result = await preview_purchase(db_session, user, light.id)
    assert result["affordable"] is False
    assert result["missing"] == Decimal("6000")
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 0


# ---------- concurrency ----------


async def test_two_simultaneous_purchases_cannot_overspend(db_factory):
    """
    The balance is locked and decremented in the database. Without that,
    both requests read the same balance and both succeed — the same
    lost-update that once collapsed five payment credits into one.
    """
    import asyncio

    async with db_factory() as setup:
        user = await make_user(setup, 9560, balance="10000")
        plan = await _plan(setup, "light", 10000, 1)
        await setup.commit()
        user_id, plan_id = user.id, plan.id

    async def attempt() -> str:
        async with db_factory() as session:
            u = await session.get(User, user_id)
            try:
                await purchase_plan(session, u, plan_id)
                await session.commit()
                return "bought"
            except Exception:
                await session.rollback()
                return "refused"

    results = await asyncio.gather(*(attempt() for _ in range(4)))
    assert results.count("bought") == 1, f"only one purchase is affordable: {results}"

    async with db_factory() as check:
        balance = (
            await check.execute(select(User.balance).where(User.id == user_id))
        ).scalar_one()
        assert balance == Decimal("0.00"), "balance must never go negative"
