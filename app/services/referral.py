"""
Referral payouts.

Capture has existed since the first release — `/start REF_ABC123` records
`referred_by_id` — but nothing was ever paid out, so the Mini App was
promoting a code that did nothing (TASKS.md P2-3).

**The rule**, taken from IDEAS.md I-2 rather than invented here: both
sides are credited when the referred user's *first top-up is approved*.
Rewarding signup instead would pay for accounts, which cost nothing to
manufacture; an approved payment has a human reviewing a bank receipt
behind it.

**The amount** is genuinely undefined in the repository. It is therefore
configuration (`REFERRAL_BONUS_AMOUNT`, default 5000, 0 disables) with
the decision recorded in TASKS.md — not a number chosen silently in code.

**Paying twice is the failure mode that matters.** Approvals are retried
by admins, arrive concurrently from the bot and the panel, and Telegram
redelivers updates. So the payout is not guarded by a "have we paid
already?" read — that is a race, and this codebase has already lost
money to exactly that shape once. The guard is the partial unique index
`uq_balance_history_event` on (user_id, tx_type, reference_id): the
ledger row is inserted ON CONFLICT DO NOTHING and the balance moves only
if that insert actually created a row. The database decides, once.
"""
import logging
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.user import BalanceHistory, BalanceTxType, User

logger = logging.getLogger(__name__)


def _reference(referee_id: int) -> str:
    """
    One reference per referred user, shared by both ledger rows.

    Scoping it to the referee (not the receipt) is what makes the bonus
    once-per-referral rather than once-per-payment: a second approved
    top-up by the same user reuses the reference and conflicts.
    """
    return f"referral:{referee_id}"


async def _credit_once(
    session: AsyncSession, user_id: int, amount: Decimal, reference: str, description: str
) -> bool:
    """
    Writes one bonus ledger row and moves the balance, or does nothing.

    Returns whether it paid. The balance update is deliberately inside the
    same `if`: a credited balance without its ledger row would break the
    invariant that the signed ledger sums to the balance.
    """
    result = await session.execute(
        pg_insert(BalanceHistory)
        .values(
            user_id=user_id,
            amount=amount,
            tx_type=BalanceTxType.REFERRAL_BONUS,
            description=description,
            reference_id=reference,
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "tx_type", "reference_id"],
            index_where=text("reference_id IS NOT NULL"),
        )
    )
    if not result.rowcount:
        return False

    # Incremented in the database, never read-modify-written in Python —
    # the same lost-update guard the receipt credit path uses.
    await session.execute(
        update(User).where(User.id == user_id).values(balance=User.balance + amount)
    )
    return True


async def pay_referral_bonus(session: AsyncSession, referee: User) -> list[int]:
    """
    Pays both sides of `referee`'s referral, if it has not been paid.

    Returns the ids of the users actually credited — empty when there is
    no referrer, payouts are disabled, or the bonus was already paid.
    Never raises for an ordinary "nothing to do"; approving a payment must
    not fail because a bonus could not be granted.
    """
    amount = Decimal(str(settings.REFERRAL_BONUS_AMOUNT))
    if amount <= 0 or referee.referred_by_id is None:
        return []

    if referee.referred_by_id == referee.id:
        # Not reachable through /start (the payload names someone else's
        # code) but cheap to refuse, and self-referral is the first thing
        # anyone tries against a scheme like this.
        logger.warning("User %s is recorded as their own referrer — no bonus paid", referee.id)
        return []

    referrer = (
        await session.execute(select(User).where(User.id == referee.referred_by_id))
    ).scalar_one_or_none()
    if referrer is None:
        return []

    reference = _reference(referee.id)
    credited: list[int] = []

    if await _credit_once(
        session, referrer.id, amount, reference, f"Referral bonus for user #{referee.id}"
    ):
        credited.append(referrer.id)
    if await _credit_once(
        session, referee.id, amount, reference, "Referral welcome bonus"
    ):
        credited.append(referee.id)

    if credited:
        await session.flush()
        logger.info("Referral bonus of %s paid to %s", amount, credited)
    return credited
