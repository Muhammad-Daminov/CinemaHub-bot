"""
Permission checks for bot handlers.

A thin adapter, not a second implementation. The bot knows a Telegram id
and a session; everything it needs to decide is behind
`app.services.permissions.has_permission`, which the REST API calls too.
The only thing that lives here is the lookup from Telegram id to User —
the authorization question itself is asked in exactly one place, so the
panel and the bot cannot come to different answers.

Replaces the old `ADMIN_IDS` membership test, under which every
administrator could do everything.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Permission
from app.db.models.user import User
from app.services.permissions import has_permission


async def actor_with_permission(
    session: AsyncSession, telegram_id: int, permission: Permission
) -> User | None:
    """
    The User behind `telegram_id` if they hold `permission`, otherwise None.

    Returns None for both "not an administrator" and "lacks this specific
    capability" on purpose: the bot's reply is the same either way, and
    distinguishing them out loud tells an unauthorised person which
    permissions exist.
    """
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        return None
    return user if await has_permission(session, user, permission) else None


async def admins_with_permission(
    session: AsyncSession, permission: Permission
) -> list[User]:
    """
    Administrators who hold `permission` — for notifications.

    Used instead of broadcasting to every configured admin id, so that a
    payment receipt reaches the people who can actually act on it rather
    than everyone with any admin access.
    """
    from app.db.models.user import UserRole  # local: avoids an import cycle at module load

    result = await session.execute(
        select(User).where(User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN]))
    )
    admins = list(result.scalars())
    return [a for a in admins if await has_permission(session, a, permission)]
