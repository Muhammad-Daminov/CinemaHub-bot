"""
The daily AI quota.

Written to verify the Phase 5 fix rather than to change it: a refused
request must not consume quota, and the counter must be incremented
before it is checked so two requests arriving together cannot both pass
against the same stale count.

Runs against an in-memory stand-in for Redis. That is not a shortcut —
the properties being asserted are about the *order* of INCR, the check
and the compensating DECR, which are identical whichever server executes
them, and the suite has no Redis to point at.
"""
import asyncio

import pytest

from app.services import ai_quota
from app.services.ai_quota import increment_and_check, refund

LIMIT = 3


class FakeRedis:
    """INCR/DECR/EXPIRE/DELETE with the semantics the quota code relies on."""

    def __init__(self):
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def decr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ai_quota, "get_redis", lambda: fake)
    return fake


async def test_requests_within_the_limit_are_allowed(redis):
    for expected in range(1, LIMIT + 1):
        count, allowed = await increment_and_check(1, LIMIT)
        assert (count, allowed) == (expected, True)


async def test_the_first_request_sets_an_expiry(redis):
    """Without the TTL the counter would never reset and the quota would be a lifetime cap."""
    await increment_and_check(2, LIMIT)
    assert len(redis.ttls) == 1
    assert next(iter(redis.ttls.values())) > 0


async def test_the_request_past_the_limit_is_refused(redis):
    for _ in range(LIMIT):
        await increment_and_check(3, LIMIT)
    count, allowed = await increment_and_check(3, LIMIT)
    assert allowed is False
    assert count == LIMIT


async def test_a_refused_request_does_not_inflate_the_counter(redis):
    """
    The Phase 5 fix. A blocked user who keeps trying used to drive the
    counter arbitrarily above their real usage, so anything reporting on
    it was counting retries rather than requests.
    """
    for _ in range(LIMIT):
        await increment_and_check(4, LIMIT)
    for _ in range(10):
        await increment_and_check(4, LIMIT)

    stored = next(iter(redis.values.values()))
    assert stored == LIMIT, f"refused attempts must be rolled back, got {stored}"


async def test_concurrent_requests_cannot_exceed_the_limit(redis):
    """
    INCR before the check is what makes this hold: check-then-increment
    lets every concurrent request read the same count and pass.
    """
    results = await asyncio.gather(*(increment_and_check(5, LIMIT) for _ in range(10)))
    assert sum(1 for _, allowed in results if allowed) == LIMIT


async def test_a_refund_gives_back_exactly_one(redis):
    await increment_and_check(6, LIMIT)
    await increment_and_check(6, LIMIT)
    await refund(6)

    count, allowed = await increment_and_check(6, LIMIT)
    assert (count, allowed) == (2, True)


async def test_refunding_to_zero_drops_the_key(redis):
    """
    A zero-valued key is indistinguishable from a missing one to every
    reader here, and DECR on an expired key resurrects it at -1 with no
    TTL — deleting is both the floor and the cleanup.
    """
    await increment_and_check(7, LIMIT)
    await refund(7)
    assert redis.values == {}


async def test_a_refund_on_an_expired_key_does_not_go_negative(redis):
    await refund(8)
    assert redis.values == {}


async def test_quotas_are_per_user(redis):
    for _ in range(LIMIT):
        await increment_and_check(9, LIMIT)
    _, allowed = await increment_and_check(10, LIMIT)
    assert allowed is True
