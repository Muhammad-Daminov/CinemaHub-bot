"""
Authorization: roles, permissions, and administrator management.

The whole point of this phase is that there is exactly one place the
question "may this user do X" is answered, so these tests exercise that
function directly — the API dependency and the bot adapter are both thin
wrappers over it.
"""
import pytest

from app.core.permissions import ALL_PERMISSIONS, Permission, parse_permission
from app.db.models.user import AdminPermission, UserRole
from app.services.permissions import (
    AdminNotFoundError,
    PermissionError_,
    create_admin,
    ensure_super_admin,
    has_permission,
    is_super_admin,
    list_admins,
    load_permissions,
    remove_admin,
    set_permissions,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


async def _super_admin(session, telegram_id=6427415448):
    user = await make_user(session, telegram_id)
    user.role = UserRole.SUPER_ADMIN
    await session.flush()
    return user


async def _admin(session, telegram_id, permissions=()):
    user = await make_user(session, telegram_id)
    user.role = UserRole.ADMIN
    await session.flush()
    for permission in permissions:
        session.add(AdminPermission(user_id=user.id, permission=permission.value))
    await session.flush()
    return user


# ---------- the vocabulary ----------


def test_permission_values_are_unique_and_stable():
    values = [p.value for p in Permission]
    assert len(values) == len(set(values))
    assert len(values) == 19, "the owner specified 19 permissions"


def test_unknown_permission_names_are_rejected():
    assert parse_permission("manage_users") is Permission.MANAGE_USERS
    assert parse_permission("not_a_permission") is None


# ---------- reading authority ----------


async def test_ordinary_user_has_nothing(db_session):
    user = await make_user(db_session, 9001)
    assert await load_permissions(db_session, user) == set()
    assert not await has_permission(db_session, user, Permission.VIEW_ANALYTICS)


async def test_super_admin_holds_every_permission(db_session):
    """
    Implicit, not seeded. Seeded rows can be revoked, and a Super Admin who
    revoked their own MANAGE_ADMINS would lock the platform out of itself.
    """
    boss = await _super_admin(db_session)
    assert await load_permissions(db_session, boss) == set(ALL_PERMISSIONS)
    assert await count_rows(db_session, AdminPermission, user_id=boss.id) == 0
    for permission in Permission:
        assert await has_permission(db_session, boss, permission)


async def test_admin_holds_only_what_was_granted(db_session):
    admin = await _admin(db_session, 9002, [Permission.MANAGE_PAYMENTS])
    assert await load_permissions(db_session, admin) == {Permission.MANAGE_PAYMENTS}
    assert await has_permission(db_session, admin, Permission.MANAGE_PAYMENTS)
    assert not await has_permission(db_session, admin, Permission.MANAGE_MOVIES)


async def test_permission_rows_on_a_demoted_user_grant_nothing(db_session):
    """Role is checked first, so stale rows cannot resurrect access."""
    admin = await _admin(db_session, 9003, [Permission.MANAGE_USERS])
    admin.role = UserRole.USER
    await db_session.flush()
    assert await load_permissions(db_session, admin) == set()


async def test_retired_permission_names_are_ignored_not_fatal(db_session):
    """A capability dropped from the vocabulary must not 500 every request."""
    admin = await _admin(db_session, 9004, [Permission.MANAGE_USERS])
    db_session.add(AdminPermission(user_id=admin.id, permission="manage_teleporters"))
    await db_session.flush()
    assert await load_permissions(db_session, admin) == {Permission.MANAGE_USERS}


# ---------- bootstrap and transfer ----------


async def test_configured_account_is_promoted(db_session, monkeypatch):
    from app.core.config import settings

    user = await make_user(db_session, 6427415448)
    monkeypatch.setattr(settings, "SUPER_ADMIN_TELEGRAM_ID", 6427415448)

    await ensure_super_admin(db_session)
    assert user.role == UserRole.SUPER_ADMIN


async def test_transfer_demotes_the_previous_holder(db_session, monkeypatch):
    """
    Ownership transfers by changing configuration. The previous holder is
    demoted to ADMIN rather than stripped entirely — they were running the
    platform, and a config change should not silently remove all access.
    """
    from app.core.config import settings

    old = await _super_admin(db_session, 1111)
    new = await make_user(db_session, 2222)
    monkeypatch.setattr(settings, "SUPER_ADMIN_TELEGRAM_ID", 2222)

    await ensure_super_admin(db_session)
    assert new.role == UserRole.SUPER_ADMIN
    assert old.role == UserRole.ADMIN
    assert not is_super_admin(old)


async def test_bootstrap_is_idempotent(db_session, monkeypatch):
    from app.core.config import settings

    user = await make_user(db_session, 3333)
    monkeypatch.setattr(settings, "SUPER_ADMIN_TELEGRAM_ID", 3333)
    await ensure_super_admin(db_session)
    await ensure_super_admin(db_session)
    assert user.role == UserRole.SUPER_ADMIN


async def test_bootstrap_tolerates_an_absent_account(db_session, monkeypatch):
    """The owner may not have contacted the bot yet on a fresh deploy."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SUPER_ADMIN_TELEGRAM_ID", 999_999_999)
    assert await ensure_super_admin(db_session) is None


# ---------- administration ----------


async def test_super_admin_can_appoint_an_admin(db_session):
    boss = await _super_admin(db_session)
    await make_user(db_session, 9101)

    appointed = await create_admin(
        db_session, boss, 9101, {Permission.MANAGE_MOVIES, Permission.VIEW_ANALYTICS}
    )
    assert appointed.role == UserRole.ADMIN
    assert await load_permissions(db_session, appointed) == {
        Permission.MANAGE_MOVIES,
        Permission.VIEW_ANALYTICS,
    }


async def test_an_admin_cannot_appoint_another_admin(db_session):
    """
    Appointment is Super-Admin-only and is deliberately not a grantable
    capability: an admin who could grant it could grant themselves the rest.
    """
    admin = await _admin(db_session, 9102, list(Permission))
    await make_user(db_session, 9103)

    with pytest.raises(PermissionError_):
        await create_admin(db_session, admin, 9103, set())


async def test_appointing_an_unknown_telegram_id_fails(db_session):
    boss = await _super_admin(db_session)
    with pytest.raises(AdminNotFoundError):
        await create_admin(db_session, boss, 424_242_424, set())


async def test_permissions_can_be_replaced_wholesale(db_session):
    boss = await _super_admin(db_session)
    admin = await _admin(db_session, 9104, [Permission.MANAGE_MOVIES])

    await set_permissions(db_session, boss, admin.id, {Permission.MANAGE_PAYMENTS})
    assert await load_permissions(db_session, admin) == {Permission.MANAGE_PAYMENTS}


async def test_untouched_grants_keep_their_audit_trail(db_session):
    """
    Permissions are diffed rather than deleted and reinserted, so a grant
    that survives an edit keeps its original granted_at/granted_by.
    """
    boss = await _super_admin(db_session)
    admin = await _admin(db_session, 9105, [Permission.MANAGE_MOVIES])

    from sqlalchemy import select

    original = (
        await db_session.execute(
            select(AdminPermission).where(AdminPermission.user_id == admin.id)
        )
    ).scalar_one()
    original_id, original_at = original.id, original.granted_at

    await set_permissions(
        db_session, boss, admin.id, {Permission.MANAGE_MOVIES, Permission.VIEW_LOGS}
    )
    survivor = (
        await db_session.execute(
            select(AdminPermission).where(
                AdminPermission.user_id == admin.id,
                AdminPermission.permission == Permission.MANAGE_MOVIES.value,
            )
        )
    ).scalar_one()
    assert survivor.id == original_id
    assert survivor.granted_at == original_at


async def test_removing_an_admin_clears_their_permissions(db_session):
    boss = await _super_admin(db_session)
    admin = await _admin(db_session, 9106, [Permission.MANAGE_MOVIES, Permission.VIEW_LOGS])

    await remove_admin(db_session, boss, admin.id)
    assert admin.role == UserRole.USER
    assert await count_rows(db_session, AdminPermission, user_id=admin.id) == 0


async def test_the_super_admin_cannot_be_removed(db_session):
    boss = await _super_admin(db_session)
    with pytest.raises(PermissionError_):
        await remove_admin(db_session, boss, boss.id)


async def test_the_super_admin_cannot_be_given_explicit_permissions(db_session):
    """Their authority is the role; a second source of truth could contradict it."""
    boss = await _super_admin(db_session)
    with pytest.raises(PermissionError_):
        await set_permissions(db_session, boss, boss.id, {Permission.MANAGE_MOVIES})


async def test_listing_admins_includes_permissions(db_session):
    boss = await _super_admin(db_session)
    await _admin(db_session, 9107, [Permission.MANAGE_PAYMENTS])

    listed = await list_admins(db_session)
    by_id = {user.telegram_id: perms for user, perms in listed}
    assert by_id[boss.telegram_id] == set(ALL_PERMISSIONS)
    assert by_id[9107] == {Permission.MANAGE_PAYMENTS}


async def test_ordinary_users_are_not_listed_as_admins(db_session):
    await _super_admin(db_session)
    await make_user(db_session, 9108)
    assert 9108 not in {user.telegram_id for user, _ in await list_admins(db_session)}
