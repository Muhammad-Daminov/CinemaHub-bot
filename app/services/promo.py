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
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.promo import PromoCode, PromoDiscountType, PromoUsage
from app.db.models.user import BalanceHistory, BalanceTxType, Subscription, SubscriptionPlan, User
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

        effect_key, effect_params = await self._apply_effect(session, promo, user)

        promo.current_uses += 1
        session.add(PromoUsage(promo_code_id=promo.id, user_id=user.id))
        await session.flush()

        return promo, effect_key, effect_params

    def _validate(self, promo: PromoCode | None) -> None:
        if promo is None:
            raise PromoError("promo.not_found")
        if not promo.is_active:
            raise PromoError("promo.inactive")
        if promo.valid_until and promo.valid_until < datetime.utcnow():
            raise PromoError("promo.expired")
        if promo.max_uses is not None and promo.current_uses >= promo.max_uses:
            raise PromoError("promo.limit_reached")

    async def _apply_effect(
        self, session: AsyncSession, promo: PromoCode, user: User
    ) -> tuple[str, dict]:
        if promo.discount_type == PromoDiscountType.FIXED_AMOUNT_BALANCE:
            user.balance = user.balance + promo.value
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
            now = datetime.utcnow()
            active = await get_active_subscription(session, user.id)
            base_time = active.expires_at if active else now
            days = int(promo.value)
            session.add(
                Subscription(
                    user_id=user.id,
                    plan=SubscriptionPlan.PREMIUM,
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
