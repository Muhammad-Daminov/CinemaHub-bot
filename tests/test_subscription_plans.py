"""
Database-driven subscription plans.

The invariants here exist because each has a way of losing money or
access: deleting a plan someone paid for, repricing a term already sold,
or two plans both claiming to be the free one.
"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models.subscription import PlanFeature, SubscriptionFeature, SubscriptionPlanModel
from app.db.models.user import Subscription
from app.services.subscription_plans import (
    PlanError,
    PlanNotFoundError,
    create_feature,
    create_plan,
    default_paid_plan,
    delete_feature,
    delete_plan,
    get_plan_by_code,
    list_features,
    list_plans,
    plan_features,
    reorder_plans,
    set_plan_features,
    subscriber_count,
    update_plan,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _plan(session, code="premium", **kwargs):
    defaults = dict(name=code.title(), price=Decimal("50000"), duration_days=30)
    return await create_plan(session, code=code, **{**defaults, **kwargs})


# ---------- creation & validation ----------


async def test_a_plan_can_be_created_and_found_by_code(db_session):
    plan = await _plan(db_session)
    assert plan.id
    found = await get_plan_by_code(db_session, "premium")
    assert found is not None and found.id == plan.id


async def test_codes_are_normalised_and_unique(db_session):
    await _plan(db_session, code="Premium")
    assert (await get_plan_by_code(db_session, "premium")) is not None
    with pytest.raises(PlanError, match="already exists"):
        await _plan(db_session, code="PREMIUM")


@pytest.mark.parametrize("bad", ["", "a", "has spaces", "UPPER-DASH", "x" * 33])
async def test_invalid_codes_are_rejected(db_session, bad):
    with pytest.raises(PlanError):
        await _plan(db_session, code=bad)


async def test_negative_price_and_zero_duration_are_rejected(db_session):
    with pytest.raises(PlanError, match="negative"):
        await _plan(db_session, code="bad1", price=Decimal("-1"))
    with pytest.raises(PlanError, match="at least one day"):
        await _plan(db_session, code="bad2", duration_days=0)


async def test_unlimited_plans_are_supported(db_session):
    """The enum allowed exactly two; the table must not care how many there are."""
    for index in range(12):
        await _plan(db_session, code=f"tier_{index}", price=Decimal(index * 1000))
    assert len(await list_plans(db_session, include_inactive=True)) == 12


# ---------- editing ----------


async def test_price_and_duration_are_editable(db_session):
    plan = await _plan(db_session)
    await update_plan(db_session, plan.id, price=Decimal("75000"), duration_days=90)
    assert plan.price == Decimal("75000")
    assert plan.duration_days == 90


async def test_benefits_are_editable_as_a_block(db_session):
    plan = await _plan(db_session, benefits=["one"])
    await update_plan(db_session, plan.id, benefits=["a", "b", "c"])
    assert plan.benefits == ["a", "b", "c"]


async def test_code_cannot_be_changed(db_session):
    """Codes are what the migration and Phase 5 branch on; renaming repoints history."""
    plan = await _plan(db_session)
    with pytest.raises(PlanError, match="cannot be changed"):
        await update_plan(db_session, plan.id, code="something_else")


async def test_repricing_does_not_touch_an_existing_subscription(db_session):
    """
    Terms are fixed when bought. A price change is for what is sold next —
    silently repricing a term someone already paid for would be theft in
    one direction or a giveaway in the other.
    """
    from datetime import datetime, timedelta, timezone

    user = await make_user(db_session, 8001)
    plan = await _plan(db_session)
    subscription = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(subscription)
    await db_session.flush()
    original_expiry = subscription.expires_at

    await update_plan(db_session, plan.id, price=Decimal("999999"), duration_days=365)
    await db_session.refresh(subscription)
    assert subscription.expires_at == original_expiry
    assert subscription.plan_id == plan.id


async def test_toggling_active_does_not_affect_subscribers(db_session):
    from datetime import datetime, timedelta, timezone

    user = await make_user(db_session, 8002)
    plan = await _plan(db_session)
    db_session.add(
        Subscription(
            user_id=user.id, plan_id=plan.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
    )
    await db_session.flush()

    await update_plan(db_session, plan.id, is_active=False)
    assert await subscriber_count(db_session, plan.id) == 1


# ---------- the free plan invariant ----------


async def test_only_one_plan_can_be_free(db_session):
    first = await _plan(db_session, code="free_a", price=Decimal("0"), is_free=True)
    second = await _plan(db_session, code="free_b", price=Decimal("0"), is_free=True)
    await db_session.refresh(first)
    assert second.is_free is True
    assert first.is_free is False, "the earlier free plan must be demoted, not co-exist"


async def test_marking_free_on_update_demotes_the_other(db_session):
    first = await _plan(db_session, code="free_a", is_free=True)
    second = await _plan(db_session, code="paid")
    await update_plan(db_session, second.id, is_free=True)
    await db_session.refresh(first)
    assert (first.is_free, second.is_free) == (False, True)


# ---------- deletion ----------


async def test_a_plan_with_subscribers_cannot_be_deleted(db_session):
    """Deleting would either orphan the reference or silently revoke paid access."""
    from datetime import datetime, timedelta, timezone

    user = await make_user(db_session, 8003)
    plan = await _plan(db_session)
    db_session.add(
        Subscription(
            user_id=user.id, plan_id=plan.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=5),
        )
    )
    await db_session.flush()

    with pytest.raises(PlanError, match="deactivate it instead"):
        await delete_plan(db_session, plan.id)
    assert await get_plan_by_code(db_session, "premium") is not None


async def test_an_unused_plan_can_be_deleted(db_session):
    plan = await _plan(db_session, code="obsolete")
    await delete_plan(db_session, plan.id)
    assert await get_plan_by_code(db_session, "obsolete") is None


async def test_deleting_a_plan_removes_its_feature_grants(db_session):
    """Otherwise the join table keeps rows pointing at a plan that is gone."""
    plan = await _plan(db_session, code="doomed")
    feature = await create_feature(db_session, code="hd", name="HD")
    await set_plan_features(db_session, plan.id, {feature.id: None})
    assert await count_rows(db_session, PlanFeature, plan_id=plan.id) == 1

    await delete_plan(db_session, plan.id)
    assert await count_rows(db_session, PlanFeature, plan_id=plan.id) == 0


async def test_deleting_an_unknown_plan_raises(db_session):
    with pytest.raises(PlanNotFoundError):
        await delete_plan(db_session, 999_999)


# ---------- ordering ----------


async def test_new_plans_are_appended_not_inserted(db_session):
    a = await _plan(db_session, code="pa")
    b = await _plan(db_session, code="pb")
    assert b.sort_order > a.sort_order


async def test_plans_can_be_reordered(db_session):
    a = await _plan(db_session, code="pa")
    b = await _plan(db_session, code="pb")
    c = await _plan(db_session, code="pc")

    await reorder_plans(db_session, [c.id, a.id, b.id])
    ordered = await list_plans(db_session, include_inactive=True)
    assert [p.code for p in ordered] == ["pc", "pa", "pb"]


async def test_reordering_rejects_unknown_ids(db_session):
    plan = await _plan(db_session)
    with pytest.raises(PlanNotFoundError):
        await reorder_plans(db_session, [plan.id, 999_999])


async def test_inactive_plans_are_hidden_from_the_default_listing(db_session):
    await _plan(db_session, code="live")
    await _plan(db_session, code="retired", is_active=False)
    assert [p.code for p in await list_plans(db_session)] == ["live"]
    assert len(await list_plans(db_session, include_inactive=True)) == 2


# ---------- default paid plan (what Phase 5 and legacy receipts resolve to) ----------


async def test_default_paid_plan_is_the_cheapest_active_paid_one(db_session):
    await _plan(db_session, code="free", price=Decimal("0"), is_free=True)
    await _plan(db_session, code="gold", price=Decimal("90000"))
    cheap = await _plan(db_session, code="silver", price=Decimal("30000"))

    chosen = await default_paid_plan(db_session)
    assert chosen is not None and chosen.id == cheap.id


async def test_default_paid_plan_ignores_inactive_plans(db_session):
    await _plan(db_session, code="cheap_but_off", price=Decimal("1"), is_active=False)
    live = await _plan(db_session, code="live", price=Decimal("50000"))
    chosen = await default_paid_plan(db_session)
    assert chosen is not None and chosen.id == live.id


async def test_default_paid_plan_is_none_when_only_free_exists(db_session):
    await _plan(db_session, code="free", price=Decimal("0"), is_free=True)
    assert await default_paid_plan(db_session) is None


# ---------- features ----------


async def test_features_can_be_created_and_granted_with_a_value(db_session):
    plan = await _plan(db_session)
    quality = await create_feature(db_session, code="max_quality", name="Max quality")
    devices = await create_feature(db_session, code="devices", name="Devices")

    await set_plan_features(db_session, plan.id, {quality.id: "1080", devices.id: "5"})
    granted = {f.code: v for f, v in await plan_features(db_session, plan.id)}
    assert granted == {"max_quality": "1080", "devices": "5"}


async def test_the_same_feature_can_differ_per_plan(db_session):
    """The usual shape of a tiered offering — same capability, different level."""
    basic = await _plan(db_session, code="basic")
    pro = await _plan(db_session, code="pro")
    quality = await create_feature(db_session, code="max_quality", name="Max quality")

    await set_plan_features(db_session, basic.id, {quality.id: "720"})
    await set_plan_features(db_session, pro.id, {quality.id: "2160"})

    assert dict((f.code, v) for f, v in await plan_features(db_session, basic.id)) == {"max_quality": "720"}
    assert dict((f.code, v) for f, v in await plan_features(db_session, pro.id)) == {"max_quality": "2160"}


async def test_setting_features_replaces_the_whole_set(db_session):
    plan = await _plan(db_session)
    a = await create_feature(db_session, code="fa", name="A")
    b = await create_feature(db_session, code="fb", name="B")

    await set_plan_features(db_session, plan.id, {a.id: None, b.id: None})
    await set_plan_features(db_session, plan.id, {b.id: "x"})

    granted = {f.code: v for f, v in await plan_features(db_session, plan.id)}
    assert granted == {"fb": "x"}


async def test_untouched_grants_keep_their_row(db_session):
    """Diffed, not deleted-and-reinserted — so created_at survives an edit."""
    plan = await _plan(db_session)
    a = await create_feature(db_session, code="fa", name="A")
    b = await create_feature(db_session, code="fb", name="B")

    await set_plan_features(db_session, plan.id, {a.id: None})
    original = (
        await db_session.execute(select(PlanFeature).where(PlanFeature.plan_id == plan.id))
    ).scalar_one()
    original_id = original.id

    await set_plan_features(db_session, plan.id, {a.id: None, b.id: None})
    survivor = (
        await db_session.execute(
            select(PlanFeature).where(
                PlanFeature.plan_id == plan.id, PlanFeature.feature_id == a.id
            )
        )
    ).scalar_one()
    assert survivor.id == original_id


async def test_granting_an_unknown_feature_is_rejected_before_anything_changes(db_session):
    plan = await _plan(db_session)
    keeper = await create_feature(db_session, code="keep", name="Keep")
    await set_plan_features(db_session, plan.id, {keeper.id: None})

    with pytest.raises(PlanNotFoundError):
        await set_plan_features(db_session, plan.id, {999_999: None})
    # The existing grant must survive a rejected edit.
    assert len(await plan_features(db_session, plan.id)) == 1


async def test_feature_codes_are_unique(db_session):
    await create_feature(db_session, code="hd", name="HD")
    with pytest.raises(PlanError, match="already exists"):
        await create_feature(db_session, code="hd", name="HD again")


async def test_deleting_a_feature_removes_it_from_every_plan(db_session):
    one = await _plan(db_session, code="one")
    two = await _plan(db_session, code="two")
    feature = await create_feature(db_session, code="doomed", name="Doomed")
    await set_plan_features(db_session, one.id, {feature.id: None})
    await set_plan_features(db_session, two.id, {feature.id: None})

    await delete_feature(db_session, feature.id)
    assert await count_rows(db_session, PlanFeature, feature_id=feature.id) == 0
    assert await count_rows(db_session, SubscriptionFeature, id=feature.id) == 0


# ---------- integrity ----------


async def test_no_orphaned_plan_features_after_churn(db_session):
    """Every join row must point at a live plan and a live feature."""
    plan = await _plan(db_session, code="churn")
    keep = await create_feature(db_session, code="keep", name="Keep")
    drop = await create_feature(db_session, code="drop", name="Drop")
    await set_plan_features(db_session, plan.id, {keep.id: None, drop.id: None})
    await delete_feature(db_session, drop.id)

    rows = (
        await db_session.execute(
            select(PlanFeature)
            .outerjoin(SubscriptionPlanModel, SubscriptionPlanModel.id == PlanFeature.plan_id)
            .outerjoin(SubscriptionFeature, SubscriptionFeature.id == PlanFeature.feature_id)
            .where(
                (SubscriptionPlanModel.id.is_(None)) | (SubscriptionFeature.id.is_(None))
            )
        )
    ).scalars().all()
    assert rows == []


async def test_listing_features_is_ordered_and_stable(db_session):
    a = await create_feature(db_session, code="fa", name="A")
    b = await create_feature(db_session, code="fb", name="B")
    assert [f.id for f in await list_features(db_session)] == [a.id, b.id]
