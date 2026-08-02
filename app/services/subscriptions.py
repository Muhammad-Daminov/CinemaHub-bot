"""
Shared subscription queries.

Kept out of app/db/models/user.py deliberately: User.active_subscription
would need to lazily load the .subscriptions relationship, which throws
under async SQLAlchemy unless it's already been eager-loaded. An
explicit query is the safe, correct way to answer "is this user
premium right now" anywhere in async handler/service code.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import Subscription


async def get_active_subscription(session: AsyncSession, user_id: int) -> Subscription | None:
    """Most-recently-expiring subscription that is still valid right now, if any."""
    result = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.expires_at > datetime.now(timezone.utc))
        .order_by(Subscription.expires_at.desc())
    )
    return result.scalars().first()


async def is_user_premium(session: AsyncSession, user_id: int) -> bool:
    return await get_active_subscription(session, user_id) is not None
