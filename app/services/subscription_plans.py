"""
Subscription plan and feature administration.

Everything that reads or mutates a plan goes through here. The rules
worth stating, because each has a failure mode behind it:

  * **Deleting a plan with subscribers is refused.** Their rows point at
    it, and cascading would silently revoke access people paid for.
    Deactivation is the intended alternative: it stops the plan being
    offered while every existing term runs to its end.
  * **A price change never touches an existing subscription.** Terms are
    fixed when bought. Repricing is for what is sold next, not for what
    was already sold.
  * **Exactly one plan may be the free plan.** Two would make "what does
    a user without a subscription get?" unanswerable.
  * **Codes are immutable.** They are what Phase 5 and the migration
    branch on; renaming one silently repoints history.
"""
import logging
import re

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.subscription import PlanFeature, SubscriptionFeature, SubscriptionPlanModel
from app.db.models.user import Subscription

logger = logging.getLogger(__name__)

CODE_PATTERN = re.compile(r"^[a-z0-9_]{2,32}$")


class PlanError(Exception):
    """Raised when a plan or feature operation is not allowed."""


class PlanNotFoundError(Exception):
    """Raised when the referenced plan or feature does not exist."""


def _validate_code(code: str) -> str:
    normalised = code.strip().lower()
    if not CODE_PATTERN.match(normalised):
        raise PlanError(
            "Code must be 2-32 characters of lowercase letters, digits or underscore"
        )
    return normalised


def _validate_terms(price: float | None, duration_days: int | None) -> None:
    if price is not None and price < 0:
        raise PlanError("Price cannot be negative")
    if duration_days is not None and duration_days < 1:
        raise PlanError("Duration must be at least one day")


# ---------- reading ----------


async def list_plans(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[SubscriptionPlanModel]:
    """
    Plans in display order.

    `sort_order` first, then id, so a plan created without an explicit
    order still lands somewhere stable rather than jumping around between
    requests.
    """
    stmt = select(SubscriptionPlanModel)
    if not include_inactive:
        stmt = stmt.where(SubscriptionPlanModel.is_active.is_(True))
    result = await session.execute(
        stmt.order_by(SubscriptionPlanModel.sort_order, SubscriptionPlanModel.id)
    )
    return list(result.scalars())


async def get_plan(session: AsyncSession, plan_id: int) -> SubscriptionPlanModel:
    plan = await session.get(SubscriptionPlanModel, plan_id)
    if plan is None:
        raise PlanNotFoundError(f"No plan with id {plan_id}")
    return plan


async def get_plan_by_code(session: AsyncSession, code: str) -> SubscriptionPlanModel | None:
    result = await session.execute(
        select(SubscriptionPlanModel).where(SubscriptionPlanModel.code == code.strip().lower())
    )
    return result.scalar_one_or_none()


async def default_paid_plan(session: AsyncSession) -> SubscriptionPlanModel | None:
    """
    The plan to grant when something awards "a subscription" without
    naming one — an approved receipt from an older client, or a
    premium-days promo.

    Cheapest active non-free plan, so a platform that later adds a
    premium tier does not start handing out the expensive one by accident.
    """
    result = await session.execute(
        select(SubscriptionPlanModel)
        .where(
            SubscriptionPlanModel.is_active.is_(True),
            SubscriptionPlanModel.is_free.is_(False),
        )
        .order_by(SubscriptionPlanModel.price, SubscriptionPlanModel.sort_order)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def subscriber_count(session: AsyncSession, plan_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(Subscription).where(Subscription.plan_id == plan_id)
    )
    return result.scalar_one()


# ---------- writing ----------


async def create_plan(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    price: float,
    duration_days: int,
    description: str | None = None,
    benefits: list[str] | None = None,
    is_active: bool = True,
    is_free: bool = False,
    sort_order: int | None = None,
) -> SubscriptionPlanModel:
    code = _validate_code(code)
    _validate_terms(price, duration_days)

    if await get_plan_by_code(session, code) is not None:
        raise PlanError(f"A plan with code '{code}' already exists")
    if is_free:
        await _clear_free_flag(session)

    if sort_order is None:
        # Appended rather than inserted, so creating a plan never silently
        # reorders the ones already on screen.
        highest = (
            await session.execute(select(func.max(SubscriptionPlanModel.sort_order)))
        ).scalar_one()
        sort_order = (highest or 0) + 1

    plan = SubscriptionPlanModel(
        code=code,
        name=name.strip(),
        description=description,
        price=price,
        duration_days=duration_days,
        benefits=benefits or [],
        is_active=is_active,
        is_free=is_free,
        sort_order=sort_order,
    )
    session.add(plan)
    await session.flush()
    return plan


async def update_plan(session: AsyncSession, plan_id: int, **fields) -> SubscriptionPlanModel:
    """
    Edits a plan in place.

    `code` is deliberately not editable — see the module docstring. A
    price or duration change applies to future purchases only; nothing
    here touches `chp_subscriptions`.
    """
    plan = await get_plan(session, plan_id)

    if "code" in fields and fields["code"] is not None:
        if _validate_code(fields["code"]) != plan.code:
            raise PlanError("A plan's code cannot be changed once created")
        fields.pop("code")

    _validate_terms(fields.get("price"), fields.get("duration_days"))

    if fields.get("is_free"):
        await _clear_free_flag(session, keep_id=plan.id)

    for key, value in fields.items():
        if value is not None and hasattr(plan, key):
            setattr(plan, key, value.strip() if key == "name" else value)

    await session.flush()
    return plan


async def delete_plan(session: AsyncSession, plan_id: int) -> None:
    """
    Removes a plan that nobody holds.

    Refused while subscriptions reference it: those rows are the record of
    what someone paid for, and deleting the plan out from under them would
    either orphan the reference or revoke access silently. Deactivate
    instead — the plan stops being offered and existing terms run out.
    """
    plan = await get_plan(session, plan_id)

    holders = await subscriber_count(session, plan_id)
    if holders:
        raise PlanError(
            f"{holders} subscription(s) still reference this plan — deactivate it instead"
        )

    await session.execute(delete(PlanFeature).where(PlanFeature.plan_id == plan_id))
    await session.delete(plan)
    await session.flush()


async def reorder_plans(session: AsyncSession, ordered_ids: list[int]) -> list[SubscriptionPlanModel]:
    """Applies an explicit display order. Ids not listed keep their position after the listed ones."""
    known = {plan.id for plan in await list_plans(session, include_inactive=True)}
    unknown = [pid for pid in ordered_ids if pid not in known]
    if unknown:
        raise PlanNotFoundError(f"Unknown plan id(s): {unknown}")

    for position, plan_id in enumerate(ordered_ids):
        await session.execute(
            update(SubscriptionPlanModel)
            .where(SubscriptionPlanModel.id == plan_id)
            .values(sort_order=position)
        )
    await session.flush()
    return await list_plans(session, include_inactive=True)


async def _clear_free_flag(session: AsyncSession, keep_id: int | None = None) -> None:
    stmt = update(SubscriptionPlanModel).values(is_free=False).where(
        SubscriptionPlanModel.is_free.is_(True)
    )
    if keep_id is not None:
        stmt = stmt.where(SubscriptionPlanModel.id != keep_id)
    await session.execute(stmt)


# ---------- features ----------


async def list_features(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[SubscriptionFeature]:
    stmt = select(SubscriptionFeature)
    if not include_inactive:
        stmt = stmt.where(SubscriptionFeature.is_active.is_(True))
    result = await session.execute(
        stmt.order_by(SubscriptionFeature.sort_order, SubscriptionFeature.id)
    )
    return list(result.scalars())


async def create_feature(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    description: str | None = None,
    sort_order: int | None = None,
) -> SubscriptionFeature:
    code = _validate_code(code)
    existing = await session.execute(
        select(SubscriptionFeature).where(SubscriptionFeature.code == code)
    )
    if existing.scalar_one_or_none() is not None:
        raise PlanError(f"A feature with code '{code}' already exists")

    if sort_order is None:
        highest = (await session.execute(select(func.max(SubscriptionFeature.sort_order)))).scalar_one()
        sort_order = (highest or 0) + 1

    feature = SubscriptionFeature(
        code=code, name=name.strip(), description=description, sort_order=sort_order
    )
    session.add(feature)
    await session.flush()
    return feature


async def delete_feature(session: AsyncSession, feature_id: int) -> None:
    """Removes a feature and every plan's grant of it — the capability simply stops existing."""
    feature = await session.get(SubscriptionFeature, feature_id)
    if feature is None:
        raise PlanNotFoundError(f"No feature with id {feature_id}")

    await session.execute(delete(PlanFeature).where(PlanFeature.feature_id == feature_id))
    await session.delete(feature)
    await session.flush()


async def plan_features(session: AsyncSession, plan_id: int) -> list[tuple[SubscriptionFeature, str | None]]:
    """(feature, value) pairs granted by one plan, in feature display order."""
    result = await session.execute(
        select(SubscriptionFeature, PlanFeature.value)
        .join(PlanFeature, PlanFeature.feature_id == SubscriptionFeature.id)
        .where(PlanFeature.plan_id == plan_id)
        .order_by(SubscriptionFeature.sort_order, SubscriptionFeature.id)
    )
    return [(feature, value) for feature, value in result.all()]


async def set_plan_features(
    session: AsyncSession, plan_id: int, grants: dict[int, str | None]
) -> list[tuple[SubscriptionFeature, str | None]]:
    """
    Replaces a plan's feature grants with exactly `grants` ({feature_id: value}).

    Diffed rather than deleted-and-reinserted so an untouched grant keeps
    its `created_at`, and so a feature id that does not exist is caught
    before anything is removed.
    """
    await get_plan(session, plan_id)

    known = {feature.id for feature in await list_features(session, include_inactive=True)}
    unknown = [fid for fid in grants if fid not in known]
    if unknown:
        raise PlanNotFoundError(f"Unknown feature id(s): {unknown}")

    current = {
        row.feature_id: row
        for row in (
            await session.execute(select(PlanFeature).where(PlanFeature.plan_id == plan_id))
        ).scalars()
    }

    for feature_id in set(current) - set(grants):
        await session.delete(current[feature_id])
    for feature_id, value in grants.items():
        if feature_id in current:
            current[feature_id].value = value
        else:
            session.add(PlanFeature(plan_id=plan_id, feature_id=feature_id, value=value))

    await session.flush()
    return await plan_features(session, plan_id)
