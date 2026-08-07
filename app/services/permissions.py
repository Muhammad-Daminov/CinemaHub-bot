"""
Authorization: who may do what, and the administration of that.

This is the *only* module that answers "may this user do X". The REST
API's dependencies, the bot's admin handlers, and the Mini App's panel
gating all call `has_permission` / `load_permissions` here. Nothing
re-derives authority from `ADMIN_IDS`, a role comparison, or a second
permission list — a system with two authorization paths eventually
disagrees with itself, and the disagreement is only noticed when someone
is wrongly let in.

Role model:

  SUPER_ADMIN  exactly one account; implicitly holds every permission and
               is alone able to appoint, remove, or re-permission an
               administrator. Deliberately not expressible as a grant, so
               it cannot be handed out by accident — only transferred.
  ADMIN        holds exactly the permissions granted to it, one row each.
  everyone else has none.
"""
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.permissions import ALL_PERMISSIONS, Permission, parse_permission
from app.db.models.user import AdminPermission, User, UserRole

logger = logging.getLogger(__name__)


class PermissionError_(Exception):
    """Raised when an actor may not perform an administration action."""


class AdminNotFoundError(Exception):
    """Raised when the target of an administration action is not an administrator."""


# ---------- reading ----------


def is_super_admin(user: User) -> bool:
    return user.role == UserRole.SUPER_ADMIN


async def load_permissions(session: AsyncSession, user: User) -> set[Permission]:
    """
    Everything `user` may do.

    The Super Admin short-circuits to the full set rather than being
    seeded with 19 rows: seeded rows can be revoked, and a Super Admin who
    has revoked their own MANAGE_ADMINS is a locked-out platform.
    """
    if is_super_admin(user):
        return set(ALL_PERMISSIONS)
    if user.role != UserRole.ADMIN:
        return set()

    result = await session.execute(
        select(AdminPermission.permission).where(AdminPermission.user_id == user.id)
    )
    # Unknown strings are dropped rather than raising: a permission
    # retired from the vocabulary should not break every request made by
    # an admin who still has its row.
    return {p for p in (parse_permission(raw) for raw in result.scalars()) if p is not None}


async def has_permission(session: AsyncSession, user: User, permission: Permission) -> bool:
    """The one authorization question. Every enforcement point calls this."""
    return permission in await load_permissions(session, user)


async def is_admin_user(user: User) -> bool:
    """Administrator of any kind — used only for 'may this person see the panel at all'."""
    return user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)


async def list_admins(session: AsyncSession) -> list[tuple[User, set[Permission]]]:
    """Every administrator with their permissions, Super Admin first."""
    result = await session.execute(
        select(User)
        .where(User.role.in_([UserRole.ADMIN, UserRole.SUPER_ADMIN]))
        .order_by(User.role.desc(), User.id)
    )
    admins = list(result.scalars())
    return [(admin, await load_permissions(session, admin)) for admin in admins]


# ---------- bootstrap ----------


async def ensure_super_admin(session: AsyncSession) -> User | None:
    """
    Promotes the configured Telegram id to Super Admin, and demotes anyone
    else still holding the role.

    Runs on startup and again whenever that account contacts the bot,
    because the account may not exist as a row yet on the first boot. The
    demotion half is what makes `SUPER_ADMIN_TELEGRAM_ID` authoritative:
    the role is transferred by changing configuration, and the previous
    holder stops being Super Admin rather than silently becoming a second
    one.
    """
    target_id = settings.SUPER_ADMIN_TELEGRAM_ID
    if not target_id:
        return None

    result = await session.execute(
        select(User).where(
            (User.telegram_id == target_id) | (User.role == UserRole.SUPER_ADMIN)
        )
    )
    rows = list(result.scalars())

    target = next((u for u in rows if u.telegram_id == target_id), None)
    for user in rows:
        if user is target:
            continue
        # Demoted to ADMIN, not USER: whoever held the role was running the
        # platform, and silently stripping all their access on a config
        # change is a worse failure than leaving them an administrator.
        logger.warning("Demoting previous super admin %s to admin", user.telegram_id)
        user.role = UserRole.ADMIN

    if target is None:
        # Not a user yet. They become Super Admin the moment they /start.
        return None

    if target.role != UserRole.SUPER_ADMIN:
        logger.info("Promoting %s to super admin", target.telegram_id)
        target.role = UserRole.SUPER_ADMIN

    await session.flush()
    return target


# ---------- administration ----------


async def _require_super_admin(actor: User) -> None:
    if not is_super_admin(actor):
        raise PermissionError_("Only the Super Admin may manage administrators")


async def _load_admin(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None or user.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        raise AdminNotFoundError(f"User {user_id} is not an administrator")
    return user


async def create_admin(
    session: AsyncSession,
    actor: User,
    telegram_id: int,
    permissions: set[Permission] | None = None,
) -> User:
    """
    Promotes an existing user to administrator.

    Deliberately requires the person to exist already — an administrator
    is appointed by Telegram id, and a row that has never contacted the
    bot has no verified identity behind it.
    """
    await _require_super_admin(actor)

    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise AdminNotFoundError(f"No user with Telegram id {telegram_id} — ask them to /start first")

    if user.role == UserRole.SUPER_ADMIN:
        raise PermissionError_("The Super Admin is already an administrator")

    user.role = UserRole.ADMIN
    await session.flush()

    if permissions:
        await set_permissions(session, actor, user.id, permissions)
    return user


async def remove_admin(session: AsyncSession, actor: User, user_id: int) -> User:
    """Revokes administrator status and every permission with it."""
    await _require_super_admin(actor)
    target = await _load_admin(session, user_id)

    if is_super_admin(target):
        raise PermissionError_("The Super Admin cannot be removed — transfer the role instead")

    await session.execute(delete(AdminPermission).where(AdminPermission.user_id == target.id))
    target.role = UserRole.USER
    await session.flush()
    return target


async def set_permissions(
    session: AsyncSession, actor: User, user_id: int, permissions: set[Permission]
) -> set[Permission]:
    """
    Replaces an administrator's permissions with exactly `permissions`.

    Computed as a diff rather than delete-then-insert so that untouched
    grants keep their original `granted_at` and `granted_by_id` — the
    audit trail is the point of recording them, and rewriting every row on
    every edit would erase it.
    """
    await _require_super_admin(actor)
    target = await _load_admin(session, user_id)

    if is_super_admin(target):
        raise PermissionError_("The Super Admin implicitly holds every permission")

    current = await load_permissions(session, target)
    to_add = permissions - current
    to_remove = current - permissions

    if to_remove:
        await session.execute(
            delete(AdminPermission).where(
                AdminPermission.user_id == target.id,
                AdminPermission.permission.in_([p.value for p in to_remove]),
            )
        )
    for permission in to_add:
        session.add(
            AdminPermission(
                user_id=target.id, permission=permission.value, granted_by_id=actor.id
            )
        )

    await session.flush()
    return permissions


async def grant_permission(
    session: AsyncSession, actor: User, user_id: int, permission: Permission
) -> set[Permission]:
    current = await load_permissions(session, await _load_admin(session, user_id))
    return await set_permissions(session, actor, user_id, current | {permission})


async def revoke_permission(
    session: AsyncSession, actor: User, user_id: int, permission: Permission
) -> set[Permission]:
    current = await load_permissions(session, await _load_admin(session, user_id))
    return await set_permissions(session, actor, user_id, current - {permission})
