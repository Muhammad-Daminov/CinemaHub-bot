"""
Promo code redemption.

Validation (expiry/limit/already-used) happens in Python first for a
fast, clear error message — but the real guarantee against a double
redemption race (two concurrent /promo submissions) is the DB-level
UniqueConstraint on (promo_code_id, user_id) in chp_promo_usages: if
two requests slip past the Python check simultaneously, only one
INSERT succeeds and the other raises IntegrityError, which the caller
must treat as "already used" (session middleware rolls back that
update's transaction either way).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.promo import PromoCode, PromoDiscountType, PromoUsage
from app.db.models.user import BalanceHistory, BalanceTxType, Subscription, SubscriptionPlan, User
from app.services.subscription_plans import default_paid_plan
from app.services.subscriptions import get_active_subscription


class PromoError(Exception):
    """
    Raised for any invalid redemption attempt.

    Carries a *translation key*, not a sentence — this service has no
    language context and shouldn't acquire one. The handler that catches
    it renders the key with its own injected translator.
    """


class PromoService:
    async def redeem(
        self, session: AsyncSession, user: User, raw_code: str
    ) -> tuple[PromoCode, str, dict]:
        """
        Validates and applies a promo code for `user`.

        Returns (promo, effect_key, effect_params) — the caller translates
        effect_key, for the same reason PromoError carries a key.
        """
        code = raw_code.strip().upper()
        result = await session.execute(select(PromoCode).where(PromoCode.code == code))
        promo = result.scalar_one_or_none()

        self._validate(promo)

        usage_check = await session.execute(
            select(PromoUsage).where(PromoUsage.promo_code_id == promo.id, PromoUsage.user_id == user.id)
        )
        if usage_check.scalar_one_or_none() is not None:
            raise PromoError("promo.already_used")

        # Claimed before the effect is applied: if the code turns out to be
        # exhausted there is nothing to undo, and the balance/subscription
        # work is never done only to be rolled back.
        await self._claim_use(session, promo)

        effect_key, effect_params = await self._apply_effect(session, promo, user)

        session.add(PromoUsage(promo_code_id=promo.id, user_id=user.id))
        await session.flush()

        return promo, effect_key, effect_params

    async def _claim_use(self, session: AsyncSession, promo: PromoCode) -> None:
        """
        Atomically takes one of the code's remaining uses.

        _validate already rejects an exhausted code, but it reads a value
        another transaction may be about to change: two users redeeming
        the last use at once both see current_uses < max_uses and both
        proceed, so a code capped at 10 is redeemed 11 times. Folding the
        check into the UPDATE's WHERE clause makes testing and taking the
        slot one operation — exactly one writer matches a row.

        `promo.current_uses` in memory is stale afterwards. The only
        caller discards the returned promo, and re-reading it here would
        cost a query to populate a field nobody consults.
        """
        result = await session.execute(
            update(PromoCode)
            .where(
                PromoCode.id == promo.id,
                or_(
                    PromoCode.max_uses.is_(None),
                    PromoCode.current_uses < PromoCode.max_uses,
                ),
            )
            .values(current_uses=PromoCode.current_uses + 1)
        )
        if result.rowcount == 0:
            raise PromoError("promo.limit_reached")

    def _validate(self, promo: PromoCode | None) -> None:
        if promo is None:
            raise PromoError("promo.not_found")
        if not promo.is_active:
            raise PromoError("promo.inactive")
        if promo.valid_until and promo.valid_until < datetime.now(timezone.utc):
            raise PromoError("promo.expired")
        if promo.max_uses is not None and promo.current_uses >= promo.max_uses:
            raise PromoError("promo.limit_reached")

    async def _apply_effect(
        self, session: AsyncSession, promo: PromoCode, user: User
    ) -> tuple[str, dict]:
        if promo.discount_type == PromoDiscountType.FIXED_AMOUNT_BALANCE:
            # Incremented in the database, not read-modify-written in
            # Python: concurrent credits to the same user would otherwise
            # all read the same starting balance and the last write would
            # win, silently swallowing the others. Same failure that cost
            # four of five payment credits in app/services/payment_review.py.
            await session.execute(
                update(User).where(User.id == user.id).values(balance=User.balance + promo.value)
            )
            session.add(
                BalanceHistory(
                    user_id=user.id,
                    amount=promo.value,
                    tx_type=BalanceTxType.PROMO_CREDIT,
                    description=f"Promo code {promo.code}",
                    reference_id=promo.code,
                )
            )
            return "promo.effect_balance", {"amount": f"{promo.value:,}"}

        if promo.discount_type == PromoDiscountType.PREMIUM_DAYS:
            now = datetime.now(timezone.utc)
            active = await get_active_subscription(session, user.id)
            base_time = active.expires_at if active else now
            days = int(promo.value)
            # The promo names a number of days, not a plan, so it grants
            # the cheapest active paid plan for that long.
            plan = await default_paid_plan(session)
            session.add(
                Subscription(
                    user_id=user.id,
                    plan_id=plan.id if plan else None,
                    plan=SubscriptionPlan.PREMIUM,  # legacy column
                    expires_at=base_time + timedelta(days=days),
                )
            )
            return "promo.effect_premium", {"days": days}

        # PERCENTAGE_DISCOUNT: no immediate balance/subscription effect — it needs to be
        # consumed against a specific future purchase, which lives in the payment flow
        # (Phase 6), not here. Recording usage still prevents reuse; wiring the discount
        # into checkout is a follow-up once that flow needs it.
        return "promo.effect_percent", {"percent": f"{promo.value:.0f}"}


    async def create_promo_code(
        self,
        session: AsyncSession,
        discount_type: PromoDiscountType,
        value: float,
        code: str,
        max_uses: int | None = None,
        valid_until: datetime | None = None,
        campaign_name: str | None = None,
        created_by_id: int | None = None,
    ) -> PromoCode:
        promo = PromoCode(
            code=code.strip().upper(),
            discount_type=discount_type,
            value=value,
            max_uses=max_uses,
            valid_until=valid_until,
            campaign_name=campaign_name,
            created_by_id=created_by_id,
        )
        session.add(promo)
        await session.flush()
        return promo


promo_service = PromoService()
