"""
Admin-editable platform settings.

The only reader and writer of `chp_system_settings`. Keys are declared
here as constants with a typed accessor each, so a setting is never
addressed by a string literal scattered through call sites — a typo in
one of those reads as "unset", which for a gate means silently open.

Deliberately uncached. These are read once per bot update at most, on a
session that already exists, and a cache would need invalidating across
every process the moment an administrator changed a value — which is
exactly when being wrong is most visible.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.system import SystemSetting

REQUIRED_CHANNEL = "required_channel"
REQUIRE_MEMBERSHIP = "require_membership"
# When app/tasks/cron.py last completed. The 30-day receipt-image promise
# depends on that script running, and nothing in this repository invokes
# it — so the only way to know whether it runs is to have it say so.
LAST_MAINTENANCE_RUN = "last_maintenance_run"

# New-user trial. Two keys rather than one encoded value, so an operator
# can turn the offer off without losing the duration they had chosen.
TRIAL_ENABLED = "trial_enabled"
TRIAL_DAYS = "trial_days"

# Used when nothing has been configured. Off by default: a platform that
# started handing out free subscriptions because a row was missing would
# be giving away inventory on a default nobody chose.
DEFAULT_TRIAL_DAYS = 3
MAX_TRIAL_DAYS = 365

# A daily job unheard from for two days has missed at least one run. Wide
# enough that a late or skipped single run is not alarming, narrow enough
# that a silently unscheduled job is noticed within a day.
MAINTENANCE_STALE_AFTER = timedelta(hours=48)


@dataclass(frozen=True)
class MembershipConfig:
    """Whether joining a channel is required before watching, and which one."""

    enabled: bool
    channel: str | None

    @property
    def active(self) -> bool:
        """
        Enforcement is only on when both halves are set.

        A flag switched on with no channel named would block every user
        from a channel that does not exist — so an incomplete
        configuration deliberately reads as "off", not as "deny".
        """
        return self.enabled and bool(self.channel)

    @property
    def invite_url(self) -> str | None:
        """A joinable link for the configured channel, if one can be built."""
        if not self.channel:
            return None
        if self.channel.startswith("http"):
            return self.channel
        if self.channel.startswith("@"):
            return f"https://t.me/{self.channel.lstrip('@')}"
        # A numeric -100… id has no public link; the panel warns about this.
        return None


async def get_setting(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(select(SystemSetting.value).where(SystemSetting.key == key))
    return result.scalar_one_or_none()


async def set_setting(
    session: AsyncSession, key: str, value: str | None, actor_id: int | None = None
) -> None:
    """Upsert — settings are addressed by key, and creating one is the same act as editing it."""
    await session.execute(
        pg_insert(SystemSetting)
        .values(key=key, value=value, updated_by_id=actor_id)
        .on_conflict_do_update(
            index_elements=["key"], set_={"value": value, "updated_by_id": actor_id}
        )
    )
    await session.flush()


async def get_all_settings(session: AsyncSession) -> dict[str, str | None]:
    result = await session.execute(select(SystemSetting.key, SystemSetting.value))
    return {key: value for key, value in result.all()}


async def get_membership_config(session: AsyncSession) -> MembershipConfig:
    stored = await get_all_settings(session)
    channel = (stored.get(REQUIRED_CHANNEL) or "").strip() or None
    return MembershipConfig(
        enabled=(stored.get(REQUIRE_MEMBERSHIP) or "").lower() == "true",
        channel=channel,
    )


async def set_membership_config(
    session: AsyncSession, enabled: bool, channel: str | None, actor_id: int | None = None
) -> MembershipConfig:
    await set_setting(session, REQUIRE_MEMBERSHIP, "true" if enabled else "false", actor_id)
    await set_setting(session, REQUIRED_CHANNEL, (channel or "").strip() or None, actor_id)
    return await get_membership_config(session)


# ---------- scheduled maintenance ----------


async def record_maintenance_run(
    session: AsyncSession, when: datetime | None = None
) -> datetime:
    """
    Stamps a completed maintenance run.

    Idempotent by construction: the row is keyed by name and upserted, so
    running the cron twice in a day leaves one row holding the later time
    rather than accumulating history. This is a liveness signal, not an
    audit log — "did it run recently" is the only question it answers.
    """
    moment = when or datetime.now(timezone.utc)
    await set_setting(session, LAST_MAINTENANCE_RUN, moment.isoformat())
    return moment


async def last_maintenance_run(session: AsyncSession) -> datetime | None:
    """When maintenance last completed, or None if it never has (or the value is unreadable)."""
    raw = await get_setting(session, LAST_MAINTENANCE_RUN)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        # A corrupt value reads as "never ran", which is the safe direction:
        # it warns rather than silently reporting health it cannot prove.
        return None
    # Older rows could have been written without a timezone; treat a naive
    # value as UTC rather than raising when it is compared below.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def maintenance_is_stale(
    session: AsyncSession, max_age: timedelta = MAINTENANCE_STALE_AFTER
) -> tuple[bool, datetime | None]:
    """
    Whether maintenance is overdue, with the last run time for the message.

    Never having run counts as stale. That is the case this exists for:
    the receipt-retention promise has always depended on a Render Cron Job
    nobody could confirm exists, and "no evidence it ever ran" is exactly
    what that looks like from inside the application.
    """
    last = await last_maintenance_run(session)
    if last is None:
        return True, None
    return datetime.now(timezone.utc) - last > max_age, last


# ---------- new-user trial ----------


@dataclass(frozen=True)
class TrialConfig:
    """Whether new users are given a subscription, and for how long."""

    enabled: bool
    days: int


async def get_trial_config(session: AsyncSession) -> TrialConfig:
    """
    The trial offer as configured, falling back to "off".

    An unreadable or nonsensical duration falls back to the default rather
    than raising: a typo in the admin panel must not stop people signing
    up, and it must certainly not grant an accidental thousand-day
    subscription.
    """
    raw_enabled = await get_setting(session, TRIAL_ENABLED)
    raw_days = await get_setting(session, TRIAL_DAYS)

    try:
        days = int(raw_days) if raw_days else DEFAULT_TRIAL_DAYS
    except (TypeError, ValueError):
        days = DEFAULT_TRIAL_DAYS
    days = max(1, min(days, MAX_TRIAL_DAYS))

    return TrialConfig(enabled=(raw_enabled or "").lower() == "true", days=days)


async def set_trial_config(
    session: AsyncSession, enabled: bool, days: int, updated_by_id: int | None = None
) -> TrialConfig:
    """Stores the trial offer. The duration is clamped, never rejected."""
    days = max(1, min(int(days), MAX_TRIAL_DAYS))
    await set_setting(session, TRIAL_ENABLED, "true" if enabled else "false", updated_by_id)
    await set_setting(session, TRIAL_DAYS, str(days), updated_by_id)
    return TrialConfig(enabled=enabled, days=days)
