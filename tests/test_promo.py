"""
Promo redemption — the second money path.

Same failure class as receipt approval: values read in Python, then
written back. Two of them here, and the concurrency tests are the point
of the file, since every single-threaded assertion below passed against
the broken code too.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.promo import PromoCode, PromoDiscountType, PromoUsage
from app.db.models.user import BalanceHistory, Subscription, User
from app.services.promo import PromoError, promo_service
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]

CREDIT = Decimal("20000.00")


async def _promo(session, code="TEST20", *, max_uses=None, value=CREDIT, kind=None) -> PromoCode:
    promo = PromoCode(
        code=code,
        discount_type=kind or PromoDiscountType.FIXED_AMOUNT_BALANCE,
        value=value,
        max_uses=max_uses,
        current_uses=0,
        is_active=True,
    )
    session.add(promo)
    await session.flush()
    return promo


# ---------- single-threaded behaviour is unchanged ----------


async def test_balance_promo_credits_once(db_session):
    user = await make_user(db_session, 111)
    promo = await _promo(db_session)
    await db_session.commit()

    await promo_service.redeem(db_session, user, "TEST20")
    await db_session.commit()

    refreshed = await db_session.get(User, user.id, populate_existing=True)
    assert refreshed.balance == CREDIT
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 1
    stored = await db_session.get(PromoCode, promo.id, populate_existing=True)
    assert stored.current_uses == 1


async def test_code_is_case_insensitive_and_trimmed(db_session):
    user = await make_user(db_session, 112)
    await _promo(db_session)
    await db_session.commit()

    await promo_service.redeem(db_session, user, "  test20  ")
    await db_session.commit()
    assert (await db_session.get(User, user.id, populate_existing=True)).balance == CREDIT


async def test_same_user_cannot_redeem_twice(db_session):
    user = await make_user(db_session, 113)
    await _promo(db_session)
    await db_session.commit()

    await promo_service.redeem(db_session, user, "TEST20")
    await db_session.commit()

    with pytest.raises(PromoError, match="already_used"):
        await promo_service.redeem(db_session, user, "TEST20")


async def test_exhausted_code_is_refused(db_session):
    user_a = await make_user(db_session, 114)
    user_b = await make_user(db_session, 115)
    await _promo(db_session, max_uses=1)
    await db_session.commit()

    await promo_service.redeem(db_session, user_a, "TEST20")
    await db_session.commit()

    with pytest.raises(PromoError, match="limit_reached"):
        await promo_service.redeem(db_session, user_b, "TEST20")


async def test_promo_with_a_future_expiry_is_redeemable(db_session):
    """
    Regression: `valid_until` is a timezone-aware column, and _validate
    compared it against a naive datetime.utcnow(), so *every* promo code
    carrying an expiry date raised TypeError on redemption. Codes without
    one worked, which is why it went unnoticed.
    """
    user = await make_user(db_session, 117)
    promo = await _promo(db_session)
    promo.valid_until = datetime.now(timezone.utc) + timedelta(days=5)
    await db_session.commit()

    await promo_service.redeem(db_session, user, "TEST20")
    await db_session.commit()

    assert (await db_session.get(User, user.id, populate_existing=True)).balance == CREDIT


async def test_expired_promo_is_refused(db_session):
    user = await make_user(db_session, 118)
    promo = await _promo(db_session)
    promo.valid_until = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    with pytest.raises(PromoError, match="expired"):
        await promo_service.redeem(db_session, user, "TEST20")


async def test_premium_days_promo_grants_a_subscription(db_session):
    user = await make_user(db_session, 116)
    await _promo(db_session, value=Decimal("7"), kind=PromoDiscountType.PREMIUM_DAYS)
    await db_session.commit()

    await promo_service.redeem(db_session, user, "TEST20")
    await db_session.commit()

    assert await count_rows(db_session, Subscription, user_id=user.id) == 1
    # Premium days move no balance.
    assert await count_rows(db_session, BalanceHistory, user_id=user.id) == 0


# ---------- the races ----------


async def test_concurrent_redemptions_cannot_exceed_max_uses(db_factory):
    """
    Regression: `promo.current_uses += 1` was a read-modify-write, so
    several users redeeming the last slot at once each saw
    current_uses < max_uses and each proceeded. A code capped at 2 could
    be redeemed 5 times.
    """
    async with db_factory() as setup:
        users = [await make_user(setup, 200 + i) for i in range(5)]
        await _promo(setup, max_uses=2)
        await setup.commit()
        user_ids = [u.id for u in users]

    async def attempt(user_id: int) -> str:
        async with db_factory() as session:
            user = await session.get(User, user_id)
            try:
                await promo_service.redeem(session, user, "TEST20")
                await session.commit()
                return "ok"
            except Exception:
                await session.rollback()
                return "refused"

    results = await asyncio.gather(*(attempt(uid) for uid in user_ids))

    assert results.count("ok") == 2, f"max_uses=2 breached: {results}"

    async with db_factory() as check:
        promo = (
            await check.execute(select(PromoCode).where(PromoCode.code == "TEST20"))
        ).scalar_one()
        assert promo.current_uses == 2
        assert await count_rows(check, PromoUsage, promo_code_id=promo.id) == 2


async def test_concurrent_credits_to_one_user_are_not_lost(db_factory):
    """
    Regression for the balance read-modify-write: three distinct codes
    credited to the same user concurrently must sum, not overwrite.
    """
    async with db_factory() as setup:
        user = await make_user(setup, 300)
        for i in range(3):
            await _promo(setup, code=f"CODE{i}")
        await setup.commit()
        user_id = user.id

    async def redeem(code: str) -> None:
        async with db_factory() as session:
            u = await session.get(User, user_id)
            await promo_service.redeem(session, u, code)
            await session.commit()

    await asyncio.gather(*(redeem(f"CODE{i}") for i in range(3)))

    async with db_factory() as check:
        balance = (
            await check.execute(select(User.balance).where(User.id == user_id))
        ).scalar_one()
        assert balance == CREDIT * 3, "a concurrent credit was lost"
        assert await count_rows(check, BalanceHistory, user_id=user_id) == 3
