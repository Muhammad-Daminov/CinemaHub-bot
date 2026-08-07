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
    """
    Increments today's counter and returns (new_count, allowed).

    A refused attempt is rolled back rather than left counted. INCR has to
    come first — checking then incrementing is a race two concurrent
    requests slip through — but leaving the increment in place meant a
    blocked user who kept trying drove the counter arbitrarily above their
    real usage, so anything reporting on it (see TASKS.md P3-1) would be
    reading a number of retries, not of requests.
    """
    redis = get_redis()
    key = _today_key(telegram_id)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, _seconds_until_next_utc_midnight())

    if count > daily_limit:
        await redis.decr(key)
        return daily_limit, False
    return count, True


async def refund(telegram_id: int) -> None:
    """
    Gives back one request after a call that never produced an answer.

    The middleware has to increment *before* the handler runs, otherwise
    the cap isn't a gate at all — a user could fire several requests at
    once and every one of them would check against the same stale count.
    So the counter is optimistic, and the handler reverses it on a known
    failure rather than the middleware guessing at the outcome.

    Dropping the key once it reaches zero is both the floor (it can never
    go negative) and the cleanup: DECR resurrects an already-expired key
    at -1 with no TTL, and a zero-valued key is indistinguishable from a
    missing one to every reader here. A SET would have reset the TTL and
    handed the user a fresh day.
    """
    redis = get_redis()
    key = _today_key(telegram_id)
    if await redis.decr(key) <= 0:
        await redis.delete(key)


