"""
Subscription feature enforcement.

Before this, `chp_plan_features` was decorative — read once to render the
comparison matrix and never to decide anything. Tiers differed in price
and in nothing else, while the only behaviour that actually varied (the
daily AI limit) was hardcoded as "premium means unlimited".

Two properties carry the change:

  **Nothing changes on day one.** A platform that has granted no features
  keeps the old rule exactly. That is what makes this shippable without
  seeding production data.

  **A grant wins, and takes effect without a deploy.** Changing a plan's
  feature in the admin panel changes what its subscribers get — which is
  the entire point of plans being data.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models.subscription import PlanFeature, SubscriptionFeature, SubscriptionPlanModel
from app.db.models.user import Subscription
from app.services.plan_features import (
    AI_DAILY_LIMIT,
    ai_daily_limit,
    features_for_user,
    has_feature,
)
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _plan(session, code, *, is_free=False, price="10000") -> SubscriptionPlanModel:
    plan = SubscriptionPlanModel(
        code=code,
        name=code.title(),
        price=Decimal(price),
        duration_days=30,
        is_active=True,
        is_free=is_free,
    )
    session.add(plan)
    await session.flush()
    return plan


async def _grant(session, plan, code, value=None) -> None:
    feature = SubscriptionFeature(code=code, name=code, is_active=True)
    session.add(feature)
    await session.flush()
    session.add(PlanFeature(plan_id=plan.id, feature_id=feature.id, value=value))
    await session.flush()


async def _subscribe(session, user, plan) -> Subscription:
    now = datetime.now(timezone.utc)
    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        started_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=29),
    )
    session.add(sub)
    await session.flush()
    return sub


# ---------- resolution ----------


async def test_a_user_with_no_subscription_gets_the_free_plans_features(db_session):
    """"What does a user without a subscription get?" is answered by data, not a constant."""
    user = await make_user(db_session, 9101)
    free = await _plan(db_session, "free", is_free=True, price="0")
    await _grant(db_session, free, "basic_catalog")

    assert await features_for_user(db_session, user.id) == {"basic_catalog": None}


async def test_a_subscriber_gets_their_own_plans_features(db_session):
    user = await make_user(db_session, 9102)
    free = await _plan(db_session, "free", is_free=True, price="0")
    await _grant(db_session, free, "basic_catalog")
    paid = await _plan(db_session, "pro")
    await _grant(db_session, paid, "hd_quality")
    await _subscribe(db_session, user, paid)

    features = await features_for_user(db_session, user.id)
    assert features == {"hd_quality": None}, "the paid plan replaces the free one, not adds to it"


async def test_a_valued_feature_carries_its_value(db_session):
    user = await make_user(db_session, 9103)
    plan = await _plan(db_session, "pro")
    await _grant(db_session, plan, "devices", value="5")
    await _subscribe(db_session, user, plan)

    assert (await features_for_user(db_session, user.id))["devices"] == "5"


async def test_has_feature_ignores_the_value(db_session):
    user = await make_user(db_session, 9104)
    plan = await _plan(db_session, "pro")
    await _grant(db_session, plan, "devices", value="5")
    await _subscribe(db_session, user, plan)

    assert await has_feature(db_session, user.id, "devices") is True
    assert await has_feature(db_session, user.id, "nothing_like_this") is False


async def test_an_expired_subscription_falls_back_to_free(db_session):
    user = await make_user(db_session, 9105)
    free = await _plan(db_session, "free", is_free=True, price="0")
    await _grant(db_session, free, "basic_catalog")
    paid = await _plan(db_session, "pro")
    await _grant(db_session, paid, "hd_quality")

    now = datetime.now(timezone.utc)
    db_session.add(
        Subscription(
            user_id=user.id,
            plan_id=paid.id,
            started_at=now - timedelta(days=60),
            expires_at=now - timedelta(days=1),
        )
    )
    await db_session.flush()

    assert await features_for_user(db_session, user.id) == {"basic_catalog": None}


async def test_a_queued_subscription_grants_nothing_yet(db_session):
    """Phase 5's queue: a row whose window has not opened is not active."""
    user = await make_user(db_session, 9106)
    paid = await _plan(db_session, "pro")
    await _grant(db_session, paid, "hd_quality")

    now = datetime.now(timezone.utc)
    db_session.add(
        Subscription(
            user_id=user.id,
            plan_id=paid.id,
            started_at=now + timedelta(days=10),
            expires_at=now + timedelta(days=40),
        )
    )
    await db_session.flush()

    assert await features_for_user(db_session, user.id) == {}


async def test_no_plans_at_all_grants_nothing(db_session):
    user = await make_user(db_session, 9107)
    assert await features_for_user(db_session, user.id) == {}


async def test_an_inactive_feature_is_not_granted(db_session):
    user = await make_user(db_session, 9108)
    plan = await _plan(db_session, "pro")
    feature = SubscriptionFeature(code="retired", name="Retired", is_active=False)
    db_session.add(feature)
    await db_session.flush()
    db_session.add(PlanFeature(plan_id=plan.id, feature_id=feature.id, value=None))
    await db_session.flush()
    await _subscribe(db_session, user, plan)

    assert await features_for_user(db_session, user.id) == {}


# ---------- the AI limit: the first enforced feature ----------


async def test_a_free_user_keeps_the_configured_default(db_session):
    """Day-one behaviour with no grants anywhere — nothing may change."""
    user = await make_user(db_session, 9110)
    assert await ai_daily_limit(db_session, user.id) == settings.AI_DAILY_LIMIT_FREE


async def test_a_subscriber_is_unlimited_without_any_grant(db_session):
    """The pre-Phase-8 rule, preserved so no production seeding is needed."""
    user = await make_user(db_session, 9111)
    plan = await _plan(db_session, "pro")
    await _subscribe(db_session, user, plan)

    assert await ai_daily_limit(db_session, user.id) is None


async def test_a_granted_limit_overrides_the_default(db_session):
    """A commercial change made from the panel, with no deploy."""
    user = await make_user(db_session, 9112)
    free = await _plan(db_session, "free", is_free=True, price="0")
    await _grant(db_session, free, AI_DAILY_LIMIT, value="7")

    assert await ai_daily_limit(db_session, user.id) == 7


async def test_changing_the_grant_changes_behaviour(db_session):
    """The property that makes plans data rather than code."""
    user = await make_user(db_session, 9113)
    plan = await _plan(db_session, "pro")
    await _grant(db_session, plan, AI_DAILY_LIMIT, value="2")
    await _subscribe(db_session, user, plan)
    assert await ai_daily_limit(db_session, user.id) == 2

    grant = (
        await db_session.execute(select(PlanFeature).where(PlanFeature.plan_id == plan.id))
    ).scalar_one()
    grant.value = "9"
    await db_session.flush()

    assert await ai_daily_limit(db_session, user.id) == 9, (
        "an admin edit must take effect without a code change"
    )


async def test_an_explicit_unlimited_grant_removes_the_cap(db_session):
    user = await make_user(db_session, 9114)
    free = await _plan(db_session, "free", is_free=True, price="0")
    await _grant(db_session, free, AI_DAILY_LIMIT, value="Unlimited")

    assert await ai_daily_limit(db_session, user.id) is None, "matched case-insensitively"


async def test_a_valueless_grant_reads_as_unlimited(db_session):
    """Ticking the feature without filling in a number means "no cap" to an admin."""
    user = await make_user(db_session, 9115)
    free = await _plan(db_session, "free", is_free=True, price="0")
    await _grant(db_session, free, AI_DAILY_LIMIT, value=None)

    assert await ai_daily_limit(db_session, user.id) is None


async def test_a_nonsense_value_falls_back_rather_than_guessing(db_session):
    """A typo in the panel must not silently cut off a paying user."""
    user = await make_user(db_session, 9116)
    plan = await _plan(db_session, "pro")
    await _grant(db_session, plan, AI_DAILY_LIMIT, value="lots")
    await _subscribe(db_session, user, plan)

    assert await ai_daily_limit(db_session, user.id) is None, "falls back to the subscriber rule"


async def test_a_zero_grant_is_honoured(db_session):
    """Zero is a real business choice — no AI on this tier — not an error."""
    user = await make_user(db_session, 9117)
    free = await _plan(db_session, "free", is_free=True, price="0")
    await _grant(db_session, free, AI_DAILY_LIMIT, value="0")

    assert await ai_daily_limit(db_session, user.id) == 0
