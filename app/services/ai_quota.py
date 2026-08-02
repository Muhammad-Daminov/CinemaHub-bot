"""
Daily AI-request quota for free-tier users.

Deliberately Redis-only, not the ai_requests_today/ai_limit_reset_at
columns on the User model — a per-day key (`ai_quota:<id>:<date>`)
with a TTL set to expire at the next UTC midnight resets itself for
free. No cron job, no "did we already reset today" bookkeeping, and
no extra write to chp_users on every single AI request. The columns
on User remain reserved for a future admin-facing usage report if
one's ever needed, but aren't the source of truth here.
"""
from datetime import datetime, timedelta, timezone

from app.core.redis import get_redis

QUOTA_KEY_PREFIX = "ai_quota:"


def _today_key(telegram_id: int) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    return f"{QUOTA_KEY_PREFIX}{telegram_id}:{today}"


def _seconds_until_next_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds())


async def increment_and_check(telegram_id: int, daily_limit: int) -> tuple[int, bool]:
    """Increments today's counter and returns (new_count, allowed)."""
    redis = get_redis()
    key = _today_key(telegram_id)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, _seconds_until_next_utc_midnight())
    return count, count <= daily_limit


async def get_usage_count(telegram_id: int) -> int:
    redis = get_redis()
    value = await redis.get(_today_key(telegram_id))
    return int(value) if value else 0


async def reset_quota(telegram_id: int) -> None:
    """Manual override — e.g. an admin comping a user extra requests for today."""
    redis = get_redis()
    await redis.delete(_today_key(telegram_id))
