"""
Payment receipt review — approve/reject, shared by the bot's inline
Approve/Reject buttons (app/bot/handlers/admin_payment.py) and the
admin REST API (app/api/admin.py). Both call the same two functions
so there is exactly one place that credits a balance, activates a
subscription, and notifies the user — not two copies that could drift.
"""
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.instance import bot
from app.core.config import settings
from app.core.i18n import t_for_user
from app.db.models.payment import PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.models.user import BalanceHistory, BalanceTxType, Subscription, User
from app.services.subscriptions import get_active_subscription


class ReceiptReviewError(Exception):
    """Raised when a receipt isn't in a reviewable state (already approved/rejected)."""


async def _credit_and_activate(session: AsyncSession, receipt: PaymentReceipt, user: User) -> None:
    """Applies the approved receipt's effect: balance credit and/or subscription extension."""
    session.add(
        BalanceHistory(
            user_id=user.id,
            amount=receipt.amount,
            tx_type=BalanceTxType.TOPUP,
            description=f"Payment receipt #{receipt.id} approved",
            reference_id=str(receipt.id),
        )
    )
    user.balance = user.balance + receipt.amount

    if receipt.purpose == PaymentPurpose.SUBSCRIPTION and receipt.subscription_plan:
        active = await get_active_subscription(session, user.id)
        base_time = active.expires_at if active else datetime.utcnow()
        session.add(
            Subscription(
                user_id=user.id,
                plan=receipt.subscription_plan,
                expires_at=base_time + timedelta(days=settings.PREMIUM_SUBSCRIPTION_DAYS),
            )
        )


async def approve_receipt(session: AsyncSession, receipt: PaymentReceipt, reviewer_user_id: int | None) -> None:
    if receipt.status != PaymentStatus.PENDING:
        raise ReceiptReviewError("Receipt has already been reviewed")

    user = await session.get(User, receipt.user_id)
    await _credit_and_activate(session, receipt, user)

    receipt.status = PaymentStatus.APPROVED
    receipt.reviewed_by_id = reviewer_user_id
    receipt.reviewed_at = datetime.utcnow()
    await session.flush()

    await bot.send_message(user.telegram_id, await t_for_user(session, user.id, "payment.approved"))


async def reject_receipt(
    session: AsyncSession, receipt: PaymentReceipt, reviewer_user_id: int | None, notes: str
) -> None:
    if receipt.status != PaymentStatus.PENDING:
        raise ReceiptReviewError("Receipt has already been reviewed")

    receipt.status = PaymentStatus.REJECTED
    receipt.admin_notes = notes
    receipt.reviewed_by_id = reviewer_user_id
    receipt.reviewed_at = datetime.utcnow()
    await session.flush()

    user = await session.get(User, receipt.user_id)
    await bot.send_message(
        user.telegram_id, await t_for_user(session, user.id, "payment.rejected", reason=notes)
    )
