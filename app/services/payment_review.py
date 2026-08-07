"""
Payment receipt review — approve/reject, shared by the bot's inline
Approve/Reject buttons (app/bot/handlers/admin_payment.py) and the
admin REST API (app/api/admin.py). Both call the same two functions
so there is exactly one place that credits a balance, activates a
subscription, and notifies the user — not two copies that could drift.

Both entry points take a receipt *id*, not a loaded row, and re-select
it FOR UPDATE themselves. That is deliberate: the row lock is the only
thing making the status guard below meaningful, and a caller handing in
an already-loaded row could not be trusted to have taken it. Owning the
fetch here means the lock cannot be forgotten at a call site.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.instance import bot
from app.core.i18n import t_for_user
from app.db.models.payment import PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.models.subscription import SubscriptionPlanModel
from app.db.models.user import (
    BalanceHistory,
    BalanceTxType,
    Subscription,
    SubscriptionPlan,
    User,
)
from app.services.subscription_plans import default_paid_plan
from app.services.subscriptions import get_active_subscription


logger = logging.getLogger(__name__)


class ReceiptReviewError(Exception):
    """Raised when a receipt isn't in a reviewable state (already approved/rejected)."""


class ReceiptNotFoundError(Exception):
    """Raised when no receipt exists for the given id."""


async def _lock_pending_receipt(session: AsyncSession, receipt_id: int) -> PaymentReceipt:
    """
    Fetches the receipt with a row lock and asserts it is still pending.

    The lock is the whole point. A bare `if receipt.status != PENDING`
    check is not a guard under concurrency: several approvals arriving
    together each read the row before any of them writes, so all of them
    see PENDING and all of them proceed. That is not hypothetical — it
    happened in production, where one receipt produced five ledger rows
    and five subscriptions, one of which stacked an extra 30 days of
    premium onto the user.

    SELECT ... FOR UPDATE makes the second arrival wait for the first to
    commit, by which time the status is APPROVED and it is turned away.
    Sessions are one-transaction-per-request/update (app/db/session.py,
    app/bot/middlewares/db.py), so the lock is held for exactly the right
    window and released on commit.
    """
    result = await session.execute(
        select(PaymentReceipt).where(PaymentReceipt.id == receipt_id).with_for_update()
    )
    receipt = result.scalar_one_or_none()
    if receipt is None:
        raise ReceiptNotFoundError(f"No receipt with id {receipt_id}")
    if receipt.status != PaymentStatus.PENDING:
        raise ReceiptReviewError("Receipt has already been reviewed")
    return receipt


async def _credit_and_activate(session: AsyncSession, receipt: PaymentReceipt, user: User) -> None:
    """
    Applies the approved receipt's effect, which depends on what was bought.

    A TOPUP buys balance. A SUBSCRIPTION buys subscription time and
    nothing else — it deliberately does *not* also credit the balance.
    Crediting both meant a user paying 50,000 for premium received
    premium plus 50,000 to spend, i.e. the platform gave the money back
    while keeping none of it. The receipt row itself records that a
    subscription was purchased; no balance moved, so no balance ledger
    entry is written for it.
    """
    if receipt.purpose == PaymentPurpose.TOPUP:
        session.add(
            BalanceHistory(
                user_id=user.id,
                amount=receipt.amount,
                tx_type=BalanceTxType.TOPUP,
                description=f"Payment receipt #{receipt.id} approved",
                reference_id=str(receipt.id),
            )
        )
        # Incremented in the database rather than read-modify-written in
        # Python. The old `user.balance = user.balance + amount` is a lost
        # update waiting to happen: concurrent writers all read the same
        # starting value and the last write wins, which is how five
        # credits of 50,000 previously collapsed into one.
        await session.execute(
            update(User).where(User.id == user.id).values(balance=User.balance + receipt.amount)
        )

    if receipt.purpose != PaymentPurpose.SUBSCRIPTION:
        return

    # The plan the receipt was raised against. A receipt from before the
    # plan table existed carries only the legacy enum, so fall back to the
    # cheapest active paid plan rather than refusing to honour a payment
    # someone already made.
    plan = None
    if receipt.plan_id is not None:
        plan = await session.get(SubscriptionPlanModel, receipt.plan_id)
    if plan is None:
        plan = await default_paid_plan(session)
    if plan is None:
        logger.error(
            "Receipt %s approved but no active paid plan exists — no subscription granted",
            receipt.id,
        )
        return

    active = await get_active_subscription(session, user.id)
    base_time = active.expires_at if active else datetime.now(timezone.utc)
    session.add(
        Subscription(
            user_id=user.id,
            plan_id=plan.id,
            # Legacy column, still written so a rollback to the previous
            # release finds what it reads. Dropped when the enum goes.
            plan=receipt.subscription_plan or SubscriptionPlan.PREMIUM,
            # Duration comes from the plan, not a global setting — that is
            # the whole point of plans being data.
            expires_at=base_time + timedelta(days=plan.duration_days),
        )
    )


async def approve_receipt(
    session: AsyncSession, receipt_id: int, reviewer_user_id: int | None
) -> PaymentReceipt:
    receipt = await _lock_pending_receipt(session, receipt_id)

    user = await session.get(User, receipt.user_id)
    await _credit_and_activate(session, receipt, user)

    receipt.status = PaymentStatus.APPROVED
    receipt.reviewed_by_id = reviewer_user_id
    receipt.reviewed_at = datetime.now(timezone.utc)
    await session.flush()

    await bot.send_message(user.telegram_id, await t_for_user(session, user.id, "payment.approved"))
    return receipt


async def reject_receipt(
    session: AsyncSession, receipt_id: int, reviewer_user_id: int | None, notes: str
) -> PaymentReceipt:
    receipt = await _lock_pending_receipt(session, receipt_id)

    receipt.status = PaymentStatus.REJECTED
    receipt.admin_notes = notes
    receipt.reviewed_by_id = reviewer_user_id
    receipt.reviewed_at = datetime.now(timezone.utc)
    await session.flush()

    user = await session.get(User, receipt.user_id)
    await bot.send_message(
        user.telegram_id, await t_for_user(session, user.id, "payment.rejected", reason=notes)
    )
    return receipt
