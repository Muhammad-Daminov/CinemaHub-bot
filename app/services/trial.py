"""
The new-user trial subscription.

Uses the existing subscription machinery rather than a parallel notion of
"trial user": a trial is simply a `chp_subscriptions` row with an expiry,
so every question already answered about subscriptions — is it active, has
it lapsed, does it unlock premium titles — is answered for trials too,
by the same code, with no second lifecycle to keep in step.

**Granted at most once per person, ever.** The guard is the presence of
*any* subscription row, not a flag: someone who has already had a trial,
bought a plan, or had one expire is not a new user, and re-issuing on
/start would turn the offer into an infinite renewal for anyone who
worked out the trick.

The insert is guarded by a row lock on the user, because /start twice in
quick succession is two concurrent updates and a bare existence check is
not a guard — the same shape as the payment path, for the same reason.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import Subscription, SubscriptionPlan, User
from app.services.settings_store import get_trial_config

logger = logging.getLogger(__name__)


async def grant_trial_if_eligible(
    session: AsyncSession, user: User
) -> Subscription | None:
    """
    Gives `user` the configured trial, or returns None.

    Returns None — never raises — when the offer is off or the person has
    had a subscription before. Signing up must not fail because a trial
    could not be issued.
    """
    config = await get_trial_config(session)
    if not config.enabled:
        return None

    # Serialises two simultaneous /start updates from the same person, so
    # the second waits and then sees the first one's row.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())

    already = (
        await session.execute(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.user_id == user.id)
        )
    ).scalar_one()
    if already:
        return None

    now = datetime.now(timezone.utc)
    trial = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.PREMIUM,
        # Deliberately no plan_id: a trial is not a purchase of any
        # sellable plan, and pointing it at one would make it appear in
        # revenue reporting as a sale that never happened.
        plan_id=None,
        started_at=now,
        expires_at=now + timedelta(days=config.days),
        auto_renew=False,
    )
    session.add(trial)
    await session.flush()

    logger.info("Granted a %d-day trial to user %s", config.days, user.id)
    return trial
