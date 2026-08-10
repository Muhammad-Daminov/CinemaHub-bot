"""
Whether a top-up submission is a duplicate — decided in exactly one place.

The counterpart to `payment_review.py`, which owns the other half of the
money path. Crediting a balance happens in one place so it cannot happen
twice; deciding that a receipt has *already been submitted* has to work
the same way, because there are two surfaces that submit one.

The failure this prevents: one real payment, two PENDING receipts. An
administrator reviewing a queue cannot tell them apart — the amount, the
card and the payer are identical — so approving both credits the balance
twice for money that was paid once. The Mini App's Submit button and the
bot's photo handler are two doors into the same room, and a guard on one
door is not a guard.

Deliberately not a database constraint. A unique index would forbid two
identical payments *forever*, and a user who tops up 50 000 twice in a
month is doing something legitimate. The rule is about what is still
awaiting review, which is a question about current state, not a fact
about the data.
"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.payment import PaymentPurpose, PaymentReceipt, PaymentStatus
from app.db.models.user import User


class DuplicateReceiptError(Exception):
    """Raised when an identical receipt is already awaiting review."""


async def guard_against_duplicate(
    session: AsyncSession,
    *,
    user_id: int,
    purpose: PaymentPurpose,
    card_id: int | None,
    amount: Decimal | float | str,
) -> None:
    """
    Refuses a submission identical to one still awaiting review.

    "Identical" is the narrowest thing that is certainly a duplicate: the
    same payer, the same purpose, the same card, the same amount, still
    PENDING. Anything already approved or rejected is history and does not
    block — retrying after a rejection is the documented recovery path.

    Two genuine payments of the same size to the same card are
    indistinguishable from a double submission until the first is
    reviewed. Refusing the second is the safe side of that ambiguity:
    nothing is lost, and the user submits again once the first is settled.

    **The payer's row is locked first, and that is the whole guarantee.** A
    bare SELECT is not a guard under concurrency: two requests arriving
    together each read "no duplicate" before either has inserted, and both
    proceed — which is exactly what happened when this was written without
    the lock, reproducibly, two receipts for one payment. Locking the user
    serialises that payer's submissions, so the second waits for the first
    to commit and then sees it. The same shape as `purchase_plan`, which
    locks the same row for the same reason.

    Per-payer, so one person submitting never blocks anybody else.
    """
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())

    existing = (
        await session.execute(
            select(PaymentReceipt.id).where(
                PaymentReceipt.user_id == user_id,
                PaymentReceipt.purpose == purpose,
                PaymentReceipt.admin_card_id == card_id,
                PaymentReceipt.amount == Decimal(str(amount)),
                PaymentReceipt.status == PaymentStatus.PENDING,
            )
        )
    ).scalars().first()

    if existing is not None:
        raise DuplicateReceiptError(
            f"receipt {existing} for the same amount is already awaiting review"
        )
