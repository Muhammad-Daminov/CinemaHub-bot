"""
What a user's subscription actually entitles them to.

This is the **one** place a capability is decided, in the same sense that
`app.services.permissions.has_permission` is the one place authority is.
Two entitlement paths would eventually disagree, and the disagreement
would show up as a paying user being refused something they bought.

Until now the plan/feature tables were decorative: `chp_plan_features`
was read in exactly one place, to *render* the comparison matrix in the
Mini App. Nothing branched on a grant, so tiers differed only in price.
The single behaviour that actually varied — the daily AI limit — was
hardcoded as "premium means unlimited".

**Behaviour is unchanged on day one, with no data seeding.** A platform
that has granted no features keeps exactly the old rule: a subscriber is
unlimited, everyone else gets `AI_DAILY_LIMIT_FREE`. The moment an
administrator grants `ai_daily_limit` to a plan, that grant wins — a
commercial change made from the panel, with no deploy. That fallback is
the whole reason this can ship without touching production data.

Values are text (`PlanFeature.value`), because a feature is either a
flag (no value) or a quantity ("5" devices, "1080" quality). Each reader
parses its own — there is no schema to guess at.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.subscription import PlanFeature, SubscriptionFeature, SubscriptionPlanModel
from app.services.subscriptions import get_active_subscription

logger = logging.getLogger(__name__)

# Feature codes this codebase branches on. A code named here is a
# contract; one that exists only in the database is data an admin can
# define but nothing reads yet, which is fine and deliberate.
AI_DAILY_LIMIT = "ai_daily_limit"

# Value meaning "no cap". Recognised case-insensitively so an admin typing
# "Unlimited" in the panel gets what they meant.
UNLIMITED = "unlimited"


async def features_for_user(session: AsyncSession, user_id: int) -> dict[str, str | None]:
    """
    Every feature this user holds, as `{code: value}`; value is None for a
    plain on/off grant.

    Resolved from the plan behind their *active* subscription, falling back
    to the free plan for a user without one — so "what does a user with no
    subscription get?" is answered by data an administrator controls, not
    by a constant. One query for the grants; membership of the dict is the
    only question callers should ask.
    """
    subscription = await get_active_subscription(session, user_id)

    plan_id = subscription.plan_id if subscription else None
    if plan_id is None:
        # No subscription, or a legacy row predating plan_id: fall back to
        # the free plan, which is what such a user effectively holds.
        plan_id = (
            await session.execute(
                select(SubscriptionPlanModel.id).where(
                    SubscriptionPlanModel.is_free.is_(True),
                    SubscriptionPlanModel.is_active.is_(True),
                )
            )
        ).scalars().first()

    if plan_id is None:
        return {}

    rows = await session.execute(
        select(SubscriptionFeature.code, PlanFeature.value)
        .join(PlanFeature, PlanFeature.feature_id == SubscriptionFeature.id)
        .where(
            PlanFeature.plan_id == plan_id,
            SubscriptionFeature.is_active.is_(True),
        )
    )
    return {code: value for code, value in rows.all()}


async def has_feature(session: AsyncSession, user_id: int, code: str) -> bool:
    """Whether the user's plan grants `code` at all, regardless of its value."""
    return code in await features_for_user(session, user_id)


async def ai_daily_limit(session: AsyncSession, user_id: int) -> int | None:
    """
    How many AI requests this user gets today. `None` means unlimited.

    Order of authority:

      1. An explicit `ai_daily_limit` grant on their plan — the value, or
         unlimited when it says so or carries no value at all. A grant with
         no value reads as "this plan removes the cap", which is what
         ticking a feature without filling in a number means to an admin.
      2. Otherwise the pre-Phase-8 rule, preserved exactly: a subscriber is
         unlimited, everyone else gets the configured free allowance.

    An unparseable value falls through to (2) rather than guessing a
    number — a typo in the panel must not silently cut off paying users.
    """
    features = await features_for_user(session, user_id)

    if AI_DAILY_LIMIT in features:
        raw = features[AI_DAILY_LIMIT]
        if raw is None or raw.strip().lower() == UNLIMITED:
            return None
        try:
            return max(int(raw.strip()), 0)
        except ValueError:
            logger.warning(
                "Plan feature %s has a non-numeric value %r — falling back to the default",
                AI_DAILY_LIMIT,
                raw,
            )

    if await get_active_subscription(session, user_id) is not None:
        return None
    return settings.AI_DAILY_LIMIT_FREE
