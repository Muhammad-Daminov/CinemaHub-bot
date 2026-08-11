"""
Required-channel membership.

Answers one question — "has this user joined the channel we require?" —
for both surfaces, so the bot and the Mini App cannot disagree about who
is let through.

Three decisions worth stating, because each one is the difference between
a gate and an outage:

**It fails open.** If Telegram errors, the channel does not exist, or the
bot is not an administrator there, the user is allowed through and the
problem is logged. A misconfigured channel would otherwise lock every
user out of a working catalog, and nobody would be able to tell the
difference from the app.

**Only membership is cached, never its absence.** A positive result is
stable and worth the ten minutes; a negative one changes the moment the
user presses "Join", and caching it would make the recheck button lie.

**Administrators are exempt.** They operate the platform and are gated on
their own permissions, not on being subscribers to it.
"""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.db.models.user import User, UserRole
from app.services.settings_store import MembershipConfig, get_membership_config

logger = logging.getLogger(__name__)

CACHE_PREFIX = "channel_member:"
CACHE_TTL_SECONDS = 600

# Telegram's ChatMember statuses that mean "in the chat". `restricted`
# counts only when is_member is set — a restricted non-member is someone
# who was removed, and treating them as present would let a banned
# channel member straight through.
_MEMBER_STATUSES = {"creator", "administrator", "member"}


def _cache_key(channel: str, telegram_id: int) -> str:
    return f"{CACHE_PREFIX}{channel}:{telegram_id}"


async def is_channel_member(bot: Bot, channel: str, telegram_id: int) -> bool:
    """Whether `telegram_id` is in `channel`. True on any Telegram failure — see module docstring."""
    redis = get_redis()
    key = _cache_key(channel, telegram_id)
    if await redis.get(key):
        return True

    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=telegram_id)
    except TelegramAPIError as exc:
        logger.warning("Membership check failed for channel %s: %s", channel, exc)
        return True

    status = getattr(member, "status", None)
    status = getattr(status, "value", status)
    joined = status in _MEMBER_STATUSES or (
        status == "restricted" and bool(getattr(member, "is_member", False))
    )

    if joined:
        await redis.setex(key, CACHE_TTL_SECONDS, "1")
    return joined


async def clear_membership_cache(channel: str, telegram_id: int) -> None:
    """Drops the cached yes, so a user who left is not carried by it after a change."""
    await get_redis().delete(_cache_key(channel, telegram_id))


async def check_access(
    session: AsyncSession, bot: Bot, user: User
) -> tuple[bool, MembershipConfig]:
    """
    Whether `user` may proceed, plus the configuration that decided it.

    The config comes back so the caller can build the join prompt without
    reading the settings a second time.
    """
    config = await get_membership_config(session)
    if not config.active:
        return True, config
    if user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        return True, config

    # A subscriber is not also asked to join the channel. The requirement
    # exists to grow an audience out of people who are not paying;
    # charging someone and then still gating them behind a join is how you
    # earn a refund request. Imported here rather than at module scope —
    # `subscriptions` reads settings that read this module.
    from app.services.subscriptions import is_user_premium

    if await is_user_premium(session, user.id):
        return True, config

    return await is_channel_member(bot, config.channel, user.telegram_id), config
