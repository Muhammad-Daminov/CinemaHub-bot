"""
Manual card payment review: mismatch, structured reasons, retry, and the
guarantees that stop money being created.

Three properties carry this feature, and each has a failure mode that
costs real money:

  **Nothing is credited before approval.** Submitting a receipt moves no
  balance, and a mismatch credits neither the declared nor the observed
  figure — deciding how much someone paid is exactly what manual review
  exists to avoid guessing at.

  **Approving twice credits once.** Not "usually" — the reviewer's finger
  on a slow connection produces five clicks in ten seconds, and the guard
  is a row lock, so the test drives independent sessions.

  **One payment's review never touches another's.** The lock is per
  receipt; approving user A must not block or affect user B.
"""
import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.payment import (
    PaymentPurpose,
    PaymentReceipt,
    PaymentStatus,
    RejectionReason,
)
from app.db.models.user import BalanceHistory, BalanceTxType, User
from app.services.payment_review import (
    ReceiptReviewError,
    approve_receipt,
    flag_amount_mismatch,
    reject_receipt,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]

DECLARED = Decimal("10000.00")


async def _receipt(session, user, amount=DECLARED, status=PaymentStatus.PENDING):
    receipt = PaymentReceipt(
        user_id=user.id,
        purpose=PaymentPurpose.TOPUP,
        amount=amount,
        receipt_photo_file_id="f",
        status=status,
    )
    session.add(receipt)
    await session.flush()
    return receipt


async def _reason(session, code="incorrect_amount"):
    reason = RejectionReason(code=code, sort_order=10)
    session.add(reason)
    await session.flush()
    return reason


async def _balance(session, user_id) -> Decimal:
    return (await session.execute(select(User.balance).where(User.id == user_id))).scalar_one()


# ---------- nothing is credited before approval ----------


async def test_submitting_credits_nothing(db_session):
    user = await make_user(db_session, 9101)
    await _receipt(db_session, user)

    assert await _balance(db_session, user.id) == Decimal("0.00")
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 0


async def test_approval_credits_the_declared_amount(db_session, silence_bot):
    user = await make_user(db_session, 9102)
    receipt = await _receipt(db_session, user)

    await approve_receipt(db_session, receipt.id, None)

    assert await _balance(db_session, user.id) == DECLARED
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 1


# ---------- amount mismatch ----------


@pytest.mark.parametrize(
    "actual,label",
    [(Decimal("10500.00"), "user under-declared"), (Decimal("9000.00"), "user over-declared")],
)
async def test_a_mismatch_credits_nothing_either_way(db_session, silence_bot, actual, label):
    """
    Neither figure is credited. Crediting the declared amount would pay out
    money that may not have arrived; crediting the observed one would be
    the platform deciding what a user paid.
    """
    user = await make_user(db_session, 9103 if actual > DECLARED else 9104)
    receipt = await _receipt(db_session, user)

    await flag_amount_mismatch(db_session, receipt.id, None, actual)

    assert await _balance(db_session, user.id) == Decimal("0.00"), label
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 0


async def test_a_mismatch_keeps_both_figures(db_session, silence_bot):
    """The user is told what they typed and what the receipt showed."""
    user = await make_user(db_session, 9105)
    receipt = await _receipt(db_session, user)

    await flag_amount_mismatch(db_session, receipt.id, None, Decimal("10500.00"))

    stored = await db_session.get(PaymentReceipt, receipt.id, populate_existing=True)
    assert stored.status == PaymentStatus.MISMATCH
    assert stored.amount == DECLARED
    assert stored.verified_amount == Decimal("10500.00")


async def test_a_mismatch_is_retryable_but_terminal_for_review(db_session, silence_bot):
    user = await make_user(db_session, 9106)
    receipt = await _receipt(db_session, user)
    await flag_amount_mismatch(db_session, receipt.id, None, Decimal("10500.00"))

    stored = await db_session.get(PaymentReceipt, receipt.id, populate_existing=True)
    assert stored.status.is_retryable is True
    assert stored.status.is_reviewable is False

    with pytest.raises(ReceiptReviewError):
        await approve_receipt(db_session, receipt.id, None)


async def test_retrying_after_a_mismatch_is_a_fresh_receipt(db_session, silence_bot):
    """
    The corrected submission is its own row. Editing the rejected one would
    lose the record of what was originally declared — which is the evidence
    the mismatch decision rests on.
    """
    user = await make_user(db_session, 9107)
    first = await _receipt(db_session, user)
    await flag_amount_mismatch(db_session, first.id, None, Decimal("10500.00"))

    second = await _receipt(db_session, user, amount=Decimal("10500.00"))
    await approve_receipt(db_session, second.id, None)

    assert await _balance(db_session, user.id) == Decimal("10500.00")
    original = await db_session.get(PaymentReceipt, first.id, populate_existing=True)
    assert original.status == PaymentStatus.MISMATCH, "the original record survives"


# ---------- rejection reasons ----------


async def test_rejection_records_the_structured_reason(db_session, silence_bot):
    user = await make_user(db_session, 9110)
    receipt = await _receipt(db_session, user)
    reason = await _reason(db_session)

    await reject_receipt(db_session, receipt.id, None, None, reason.id)

    stored = await db_session.get(PaymentReceipt, receipt.id, populate_existing=True)
    assert stored.status == PaymentStatus.REJECTED
    assert stored.rejection_reason_id == reason.id
    assert await _balance(db_session, user.id) == Decimal("0.00")


async def test_a_built_in_reason_reaches_the_user_translated(db_session, silence_bot):
    """
    The stored value is a code; what the user receives is their language.
    Storing the sentence would show one language to everyone.
    """
    user = await make_user(db_session, 9111)
    from app.db.models.user import UILanguage

    user.language = UILanguage.RU
    receipt = await _receipt(db_session, user)
    reason = await _reason(db_session)

    await reject_receipt(db_session, receipt.id, None, None, reason.id)

    _, message = silence_bot[-1]
    assert "Неверная сумма" in message


async def test_free_text_notes_are_appended(db_session, silence_bot):
    user = await make_user(db_session, 9112)
    receipt = await _receipt(db_session, user)
    reason = await _reason(db_session)

    await reject_receipt(db_session, receipt.id, None, "chek 2019-yilga tegishli", reason.id)

    _, message = silence_bot[-1]
    assert "chek 2019-yilga tegishli" in message


async def test_rejection_without_any_reason_still_says_something(db_session, silence_bot):
    """A user must never receive an empty explanation."""
    user = await make_user(db_session, 9113)
    receipt = await _receipt(db_session, user)

    await reject_receipt(db_session, receipt.id, None, None, None)

    _, message = silence_bot[-1]
    assert message.strip()


# ---------- repeated approval ----------


async def test_a_second_approval_is_refused(db_session, silence_bot):
    user = await make_user(db_session, 9120)
    receipt = await _receipt(db_session, user)

    await approve_receipt(db_session, receipt.id, None)
    with pytest.raises(ReceiptReviewError):
        await approve_receipt(db_session, receipt.id, None)

    assert await _balance(db_session, user.id) == DECLARED


async def test_ten_rapid_approvals_credit_once(db_factory, silence_bot):
    """
    The reported scenario: an administrator taps Approve five to ten times
    in a few seconds. Independent sessions, because a shared one shares its
    transaction and could never contend.
    """
    async with db_factory() as setup:
        user = await make_user(setup, 9121)
        receipt = await _receipt(setup, user)
        await setup.commit()
        user_id, receipt_id = user.id, receipt.id

    async def approve() -> str:
        async with db_factory() as session:
            try:
                await approve_receipt(session, receipt_id, None)
                await session.commit()
                return "credited"
            except Exception:
                await session.rollback()
                return "refused"

    results = await asyncio.gather(*(approve() for _ in range(10)))

    assert results.count("credited") == 1, f"exactly one credit: {results}"
    async with db_factory() as check:
        assert await _balance(check, user_id) == DECLARED
        entries = await check.execute(
            select(BalanceHistory).where(BalanceHistory.user_id == user_id)
        )
        assert len(entries.scalars().all()) == 1, "one ledger row"


async def test_reviewing_one_payment_does_not_block_another(db_factory, silence_bot):
    """
    The lock is per receipt. Approving user A's payment while user B's sits
    underneath must leave B's fully reviewable — a global lock would make
    the queue unusable.
    """
    async with db_factory() as setup:
        first = await make_user(setup, 9122)
        second = await make_user(setup, 9123)
        a = await _receipt(setup, first)
        b = await _receipt(setup, second)
        await setup.commit()
        ids = (first.id, second.id, a.id, b.id)

    first_id, second_id, a_id, b_id = ids

    async def approve(receipt_id: int) -> None:
        async with db_factory() as session:
            await approve_receipt(session, receipt_id, None)
            await session.commit()

    await asyncio.gather(approve(a_id), approve(b_id))

    async with db_factory() as check:
        assert await _balance(check, first_id) == DECLARED
        assert await _balance(check, second_id) == DECLARED


async def test_a_mismatch_and_an_approval_cannot_both_land(db_factory, silence_bot):
    """Two reviewers deciding differently at the same moment — one wins."""
    async with db_factory() as setup:
        user = await make_user(setup, 9124)
        receipt = await _receipt(setup, user)
        await setup.commit()
        user_id, receipt_id = user.id, receipt.id

    async def approve() -> str:
        async with db_factory() as session:
            try:
                await approve_receipt(session, receipt_id, None)
                await session.commit()
                return "approved"
            except Exception:
                await session.rollback()
                return "refused"

    async def mismatch() -> str:
        async with db_factory() as session:
            try:
                await flag_amount_mismatch(session, receipt_id, None, Decimal("10500.00"))
                await session.commit()
                return "mismatch"
            except Exception:
                await session.rollback()
                return "refused"

    results = await asyncio.gather(approve(), mismatch())
    assert results.count("refused") == 1, f"exactly one decision stands: {results}"

    async with db_factory() as check:
        stored = await check.get(PaymentReceipt, receipt_id)
        balance = await _balance(check, user_id)
        if stored.status == PaymentStatus.APPROVED:
            assert balance == DECLARED
        else:
            assert balance == Decimal("0.00"), "a mismatch must not credit"


# ---------- the ledger stays consistent ----------


async def test_the_ledger_sums_to_the_balance(db_session, silence_bot):
    user = await make_user(db_session, 9130)
    for _ in range(3):
        receipt = await _receipt(db_session, user)
        await approve_receipt(db_session, receipt.id, None)

    ledger = (
        await db_session.execute(
            select(BalanceHistory.amount).where(BalanceHistory.user_id == user.id)
        )
    ).scalars().all()
    assert sum(ledger) == await _balance(db_session, user.id)
    assert all(entry == DECLARED for entry in ledger)
    assert len(ledger) == 3


async def test_only_topups_touch_the_balance(db_session, silence_bot):
    """A subscription receipt buys time, not spendable balance."""
    from tests.conftest import make_paid_plan

    user = await make_user(db_session, 9131)
    plan = await make_paid_plan(db_session)
    receipt = PaymentReceipt(
        user_id=user.id,
        plan_id=plan.id,
        purpose=PaymentPurpose.SUBSCRIPTION,
        amount=DECLARED,
        receipt_photo_file_id="f",
    )
    db_session.add(receipt)
    await db_session.flush()

    await approve_receipt(db_session, receipt.id, None)

    assert await _balance(db_session, user.id) == Decimal("0.00")
    assert await count_rows(db_session, BalanceHistory, user_id=user.id, tx_type=BalanceTxType.TOPUP) == 0
