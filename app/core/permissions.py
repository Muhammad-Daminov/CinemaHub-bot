"""
The permission vocabulary — the single place a capability is named.

Every enforcement point in the system resolves through this module: the
REST API's dependencies, the bot's admin handlers, and the Mini App's
panel gating all ask the same question of the same enum. There is no
second list of permission strings anywhere, because two lists drift the
moment one is edited and the drift is invisible until someone is either
locked out or let in.

Stored as VARCHAR rather than a PostgreSQL enum on purpose. Adding a
capability then costs a row, not an `ALTER TYPE` migration against a
production database — and this vocabulary is expected to grow as the
admin surface does.
"""
import enum


class Permission(str, enum.Enum):
    """
    One capability an administrator may hold.

    Values are stable identifiers written to `chp_admin_permissions` and
    sent to the Mini App. Renaming one is a data migration, so treat them
    as the wire format they are.
    """

    MANAGE_USERS = "manage_users"
    MANAGE_ADMINS = "manage_admins"
    MANAGE_ROLES = "manage_roles"
    MANAGE_MOVIES = "manage_movies"
    MANAGE_SERIES = "manage_series"
    MANAGE_CATEGORIES = "manage_categories"
    MANAGE_GENRES = "manage_genres"
    MANAGE_LANGUAGES = "manage_languages"
    MANAGE_AUDIO_TRACKS = "manage_audio_tracks"
    MANAGE_SUBSCRIPTIONS = "manage_subscriptions"
    MANAGE_SUBSCRIPTION_FEATURES = "manage_subscription_features"
    MANAGE_PAYMENTS = "manage_payments"
    MANAGE_PROMO_CODES = "manage_promo_codes"
    MANAGE_BALANCE = "manage_balance"
    MANAGE_NOTIFICATIONS = "manage_notifications"
    MANAGE_AI_SETTINGS = "manage_ai_settings"
    VIEW_ANALYTICS = "view_analytics"
    VIEW_LOGS = "view_logs"
    MANAGE_SYSTEM_SETTINGS = "manage_system_settings"


ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)

# Presentation order for the admin panel. Kept here rather than in the
# frontend so the panel cannot show a stale or reordered list, and so a
# newly added permission appears without a frontend change.
PERMISSION_GROUPS: dict[str, tuple[Permission, ...]] = {
    "access": (
        Permission.MANAGE_USERS,
        Permission.MANAGE_ADMINS,
        Permission.MANAGE_ROLES,
    ),
    "catalog": (
        Permission.MANAGE_MOVIES,
        Permission.MANAGE_SERIES,
        Permission.MANAGE_CATEGORIES,
        Permission.MANAGE_GENRES,
        Permission.MANAGE_LANGUAGES,
        Permission.MANAGE_AUDIO_TRACKS,
    ),
    "commerce": (
        Permission.MANAGE_SUBSCRIPTIONS,
        Permission.MANAGE_SUBSCRIPTION_FEATURES,
        Permission.MANAGE_PAYMENTS,
        Permission.MANAGE_PROMO_CODES,
        Permission.MANAGE_BALANCE,
    ),
    "platform": (
        Permission.MANAGE_NOTIFICATIONS,
        Permission.MANAGE_AI_SETTINGS,
        Permission.VIEW_ANALYTICS,
        Permission.VIEW_LOGS,
        Permission.MANAGE_SYSTEM_SETTINGS,
    ),
}


def parse_permission(raw: str) -> Permission | None:
    """Permission for a stored/submitted string, or None if it is not one of ours."""
    try:
        return Permission(raw)
    except ValueError:
        return None
