"""
Buying a subscription: what happens when, and to what.

The rules are expressed entirely in terms of **relative priority**, never
in terms of a named plan. That is what makes a new tier a number rather
than a code change — adding "Ultra" at priority 4 needs no edit here.

Given the plan being bought and the one currently active:

  same priority     extend the current term by the new plan's duration
  higher priority   activate now; the old term's remaining days carry
                    over 1:1 on top of the new plan's duration
  lower priority    never interrupt what the user is already on. Queue
                    it to start when the current term (and anything
                    already queued) ends.

A subscription is "queued" purely by having a `started_at` in the future.
No status column, no second table: a queued row is an ordinary row whose
window has not opened yet, so it activates by the clock rather than by a
job that has to run. `get_active_subscription` filters on `started_at`,
which is the only thing that had to change for all of this to work.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.subscription import SubscriptionPlanModel
from app.db.models.user import (
    BalanceHistory,
    BalanceTxType,
    Subscription,
    SubscriptionPlan,
    User,
)

logger = logging.getLogger(__name__)

# The deprecated enum column is still NOT NULL during expand/contract.
# Every purchase here is a paid plan, so PREMIUM is the only filler that
# does not misreport anything to a rolled-back release.
_LEGACY_PLAN_VALUE = SubscriptionPlan.PREMIUM


class InsufficientBalanceError(Exception):
    """Raised when the balance cannot cover the plan. Carries the numbers the UI shows."""

    def __init__(self, balance: Decimal, price: Decimal) -> None:
        self.balance = balance
        self.price = price
        self.missing = price - balance
        super().__init__(f"Balance {balance} is short of {price} by {self.missing}")


class PlanUnavailableError(Exception):
    """Raised when the plan does not exist, is inactive, or is the free plan."""


async def active_subscription(session: AsyncSession, user_id: int) -> Subscription | None:
    """
    The subscription in force right now — started and not yet expired.

    Deliberately excludes rows whose `started_at` is in the future: those
    are queued purchases, and treating one as current would hand a user a
    tier they have not reached yet.
    """
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.started_at <= now,
            Subscription.expires_at > now,
        )
        .order_by(Subscription.expires_at.desc())
    )
    return result.scalars().first()


async def queued_subscriptions(session: AsyncSession, user_id: int) -> list[Subscription]:
    """Purchases waiting their turn, soonest first."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.started_at > now)
        .order_by(Subscription.started_at)
    )
    return list(result.scalars())


async def _timeline_end(session: AsyncSession, user_id: int) -> datetime | None:
    """
    When everything the user currently holds runs out — the active term
    plus every queued one.

    A second queued purchase must start after the first, not alongside it,
    so the queue is built from the far end of the timeline rather than
    from the active subscription.
    """
    result = await session.execute(
        select(Subscription.expires_at)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )
    latest = result.scalars().first()
    now = datetime.now(timezone.utc)
    return latest if latest and latest > now else None


async def _load_purchasable_plan(session: AsyncSession, plan_id: int) -> SubscriptionPlanModel:
    plan = await session.get(SubscriptionPlanModel, plan_id)
    if plan is None or not plan.is_active:
        raise PlanUnavailableError("This plan is not available")
    if plan.is_free:
        # The free plan is what everyone already has; selling it would take
        # money for nothing.
        raise PlanUnavailableError("The free plan cannot be purchased")
    return plan


async def preview_purchase(session: AsyncSession, user: User, plan_id: int) -> dict:
    """
    What buying this plan would do, without doing it.

    Lets the Mini App show "extends to…", "upgrades now" or "starts on…"
    before the user commits, using the same rules the purchase applies —
    a second implementation in the frontend would eventually disagree.
    """
    plan = await _load_purchasable_plan(session, plan_id)
    current = await active_subscription(session, user.id)
    balance = Decimal(str(user.balance))
    price = Decimal(str(plan.price))

    if current is None:
        outcome, starts_at = "activate", datetime.now(timezone.utc)
    else:
        current_plan = await session.get(SubscriptionPlanModel, current.plan_id)
        current_priority = current_plan.priority if current_plan else 0
        if plan.priority > current_priority:
            outcome, starts_at = "upgrade", datetime.now(timezone.utc)
        elif plan.priority == current_priority:
            outcome, starts_at = "extend", current.started_at
        else:
            outcome = "queued"
            starts_at = await _timeline_end(session, user.id) or datetime.now(timezone.utc)

    return {
        "outcome": outcome,
        "starts_at": starts_at,
        "price": price,
        "balance": balance,
        "missing": max(price - balance, Decimal("0")),
        "affordable": balance >= price,
    }


async def purchase_plan(session: AsyncSession, user: User, plan_id: int) -> Subscription:
    """
    Buys `plan_id` from the user's balance.

    The debit and the subscription are written in one transaction, and the
    balance is decremented in the database rather than read-modify-written
    in Python — the same lost-update that once collapsed five payment
    credits into one applies just as well to a debit.
    """
    plan = await _load_purchasable_plan(session, plan_id)
    price = Decimal(str(plan.price))

    # Re-read the row under a lock: two taps on Buy must not both see the
    # same balance and both succeed.
    #
    # populate_existing is what makes this work. Without it SQLAlchemy takes
    # the lock but hands back the instance already in the session's identity
    # map — carrying the balance read *before* the lock — so every concurrent
    # caller sees the same stale figure and all of them pass the check.
    locked = (
        await session.execute(
            select(User)
            .where(User.id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    balance = Decimal(str(locked.balance))
    if balance < price:
        raise InsufficientBalanceError(balance, price)

    current = await active_subscription(session, user.id)
    now = datetime.now(timezone.utc)
    duration = timedelta(days=plan.duration_days)

    if current is None:
        started_at, expires_at = now, now + duration
    else:
        current_plan = await session.get(SubscriptionPlanModel, current.plan_id)
        current_priority = current_plan.priority if current_plan else 0

        if plan.priority > current_priority:
            # Upgrade: starts now, and the days left on the old tier are
            # carried over 1:1 rather than forfeited.
            remaining = max(current.expires_at - now, timedelta(0))
            started_at, expires_at = now, now + duration + remaining
            # The old term ends here; without this the user would briefly
            # hold two active subscriptions and `active_subscription`
            # would pick whichever expires later.
            current.expires_at = now

        elif plan.priority == current_priority:
            # Same tier: push the existing expiry out. Extending the row
            # in place keeps one term rather than accumulating a row per
            # renewal.
            started_at = current.started_at
            expires_at = current.expires_at + duration
            current.expires_at = expires_at
            await _record_debit(session, locked, plan, price)
            await session.flush()
            return current

        else:
            # Lower tier: queued behind everything already held.
            started_at = await _timeline_end(session, user.id) or now
            expires_at = started_at + duration

    subscription = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        plan=_LEGACY_PLAN_VALUE,
        started_at=started_at,
        expires_at=expires_at,
    )
    session.add(subscription)
    await _record_debit(session, locked, plan, price)
    await session.flush()
    return subscription


async def _record_debit(
    session: AsyncSession, user: User, plan: SubscriptionPlanModel, price: Decimal
) -> None:
    """
    Takes the money and writes the ledger row.

    Decremented in the database, never read-modify-written — see the
    module docstring. The ledger amount is negative because
    `chp_balance_history` is a signed ledger and the sum of it must equal
    the balance; a positive number here would break that invariant.
    """
    from sqlalchemy import update

    await session.execute(
        update(User).where(User.id == user.id).values(balance=User.balance - price)
    )
    session.add(
        BalanceHistory(
            user_id=user.id,
            amount=-price,
            tx_type=BalanceTxType.DEDUCTION,
            description=f"Subscription: {plan.name}",
            # Unique per purchase. chp_balance_history carries a unique
            # index on (user_id, tx_type, reference_id) to stop one event
            # being credited twice; a plan-derived key would make a
            # legitimate second purchase of the same plan collide with it.
            reference_id=f"purchase:{uuid4().hex}",
        )
    )
