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

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.system import SystemSetting

REQUIRED_CHANNEL = "required_channel"
REQUIRE_MEMBERSHIP = "require_membership"


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
