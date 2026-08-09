"""
One user's money movements, for whichever surface is asking.

Extracted from `app/api/billing.py` so the bot's Orders screen and the
Mini App's history list read the same rows through the same query. The
bot's version was a "coming in a later phase" stub while the data had
existed since Phase 5; writing a second query for it would have been the
usual way two surfaces start disagreeing about someone's money.

Pending receipts are folded in alongside settled ledger entries because
they have not moved the balance yet — a user who has just submitted one
would otherwise see nothing and submit it again.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.payment import PaymentReceipt, PaymentStatus
from app.db.models.user import BalanceHistory

# The `kind` given to a receipt awaiting review. Not a BalanceTxType:
# nothing has moved yet, and giving it a ledger type would imply a row in
# a ledger that must stay the sum of the balance.
PENDING_RECEIPT = "pending_receipt"


@dataclass(frozen=True)
class HistoryEntry:
    """One line of history, settled or pending."""

    id: int
    amount: Decimal
    kind: str
    description: str | None
    created_at: datetime
    status: str | None

    @property
    def is_pending(self) -> bool:
        return self.kind == PENDING_RECEIPT


async def payment_history(
    session: AsyncSession, user_id: int, limit: int = 50
) -> list[HistoryEntry]:
    """
    Newest first: settled ledger entries plus receipts still under review.

    Amounts stay `Decimal` — this is money, and the float conversion
    belongs at the surface that serialises it, not in the shared query.
    Ledger amounts are signed (a purchase is negative); a pending receipt
    is the positive amount it would credit if approved.
    """
    ledger = (
        await session.execute(
            select(BalanceHistory)
            .where(BalanceHistory.user_id == user_id)
            .order_by(BalanceHistory.created_at.desc())
            .limit(limit)
        )
    ).scalars()

    entries = [
        HistoryEntry(
            id=entry.id,
            amount=entry.amount,
            kind=entry.tx_type.value,
            description=entry.description,
            created_at=entry.created_at,
            status=None,
        )
        for entry in ledger
    ]

    pending = (
        await session.execute(
            select(PaymentReceipt)
            .where(
                PaymentReceipt.user_id == user_id,
                PaymentReceipt.status == PaymentStatus.PENDING,
            )
            .order_by(PaymentReceipt.created_at.desc())
        )
    ).scalars()

    entries.extend(
        HistoryEntry(
            id=receipt.id,
            amount=receipt.amount,
            kind=PENDING_RECEIPT,
            description=None,
            created_at=receipt.created_at,
            status=receipt.status.value,
        )
        for receipt in pending
    )

    entries.sort(key=lambda entry: entry.created_at, reverse=True)
    return entries[:limit]
