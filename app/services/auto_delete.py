"""
Auto-delete engine for copyright safety.

Uses a Redis sorted set as a delay queue instead of raw
`asyncio.create_task(asyncio.sleep(...))` timers: a bare in-memory
timer is lost on every redeploy/restart (Render redeploys on every
push), silently leaving copyrighted content undeleted. The ZSET
(score = due unix timestamp) survives that; a single background
worker polls it and deletes whatever is due.
"""
import asyncio
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from app.core.config import settings
from app.core.redis import get_redis

QUEUE_KEY = "auto_delete:queue"
POLL_INTERVAL_SECONDS = 15.0


def _member(chat_id: int, message_id: int) -> str:
    return f"{chat_id}:{message_id}"


async def schedule_deletion(
    chat_id: int, message_id: int, delay_seconds: int = settings.AUTO_DELETE_SECONDS
) -> None:
    """Queues a sent message for deletion `delay_seconds` from now."""
    redis = get_redis()
    due_at = time.time() + delay_seconds
    await redis.zadd(QUEUE_KEY, {_member(chat_id, message_id): due_at})


async def cancel_deletion(chat_id: int, message_id: int) -> None:
    """Removes a pending deletion (e.g. if the message was already deleted some other way)."""
    redis = get_redis()
    await redis.zrem(QUEUE_KEY, _member(chat_id, message_id))


async def _process_due_deletions(bot: Bot) -> None:
    redis = get_redis()
    now = time.time()
    due_members: list[str] = await redis.zrangebyscore(QUEUE_KEY, min=0, max=now)

    for member in due_members:
        chat_id_str, message_id_str = member.split(":", 1)
        try:
            await bot.delete_message(chat_id=int(chat_id_str), message_id=int(message_id_str))
        except TelegramBadRequest:
            pass  # already deleted by the user, or too old — nothing to do
        finally:
            await redis.zrem(QUEUE_KEY, member)


async def run_auto_delete_worker(bot: Bot) -> None:
    """Long-running background loop — launch once via asyncio.create_task at app startup."""
    while True:
        await _process_due_deletions(bot)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
