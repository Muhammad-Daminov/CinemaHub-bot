"""Shared Redis client, single connection pool for the whole app."""
from redis.asyncio import Redis, ConnectionPool

from app.core.config import settings

_pool = ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)


def get_redis() -> Redis:
    """Returns a Redis client bound to the shared pool — cheap to call repeatedly."""
    return Redis(connection_pool=_pool)
