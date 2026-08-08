"""
Admin broadcasts.

Sending one message to every user is the single most destructive button
in an admin panel: it cannot be recalled, it is visible to everyone at
once, and Telegram will rate-limit or ban a bot that gets it wrong. So
the mechanics here are conservative by design.

**A broadcast cannot be sent twice.** The row moves PENDING → SENDING
under `SELECT … FOR UPDATE`, and a worker that finds it already SENDING
stops. A double-clicked button, a retried request, or two web processes
picking up the same row all collapse to one send — the same guard that
protects payment approval, for the same reason.

**Pacing is deliberate.** Telegram allows roughly 30 messages a second to
distinct chats; this sends at ~20 and obeys `RetryAfter` when told to.
Going faster does not deliver sooner, it earns a 429 for the whole bot.

**Blocked is not failed.** Users who blocked the bot or deleted their
account are counted separately, because that number is churn to know
about rather than an error to fix.

**No recipient data leaves this module.** The API reports counts; who
received the message is derivable from the audience filter and is never
materialised or returned.
"""
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramAPIError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.system import Broadcast, BroadcastAudience, BroadcastStatus
from app.db.models.user import Subscription, User

logger = logging.getLogger(__name__)

# ~20 messages/second, comfortably inside Telegram's limit for distinct chats.
SEND_INTERVAL_SECONDS = 0.05
# How often progress is written back. Every message would mean one UPDATE
# per send; never would mean an operator watching a long broadcast sees
# nothing until it ends.
PROGRESS_EVERY = 50

MAX_MESSAGE_LENGTH = 4096


class BroadcastError(Exception):
    """Raised when a broadcast cannot be created or started."""


def _premium_user_ids():
    """Subquery of users holding a subscription that is active right now."""
    now = datetime.now(timezone.utc)
    return (
        select(Subscription.user_id)
        .where(Subscription.started_at <= now, Subscription.expires_at > now)
        .distinct()
    )


def _audience_filter(audience: BroadcastAudience):
    """
    Conditions selecting the audience.

    Banned users are excluded from every audience: they are blocked from
    using the platform, and messaging them anyway is both noise and a
    reason for them to report the bot.
    """
    filters = [User.is_banned.is_(False)]
    if audience == BroadcastAudience.PREMIUM:
        filters.append(User.id.in_(_premium_user_ids()))
    elif audience == BroadcastAudience.FREE:
        filters.append(User.id.not_in(_premium_user_ids()))
    return filters


async def audience_size(session: AsyncSession, audience: BroadcastAudience) -> int:
    result = await session.execute(
        select(func.count(User.id)).where(*_audience_filter(audience))
    )
    return result.scalar_one()


async def create_broadcast(
    session: AsyncSession, actor: User, message: str, audience: BroadcastAudience
) -> Broadcast:
    """Records the broadcast as PENDING. Nothing is sent until `run_broadcast` picks it up."""
    text = message.strip()
    if not text:
        raise BroadcastError("A broadcast needs a message")
    if len(text) > MAX_MESSAGE_LENGTH:
        # Telegram would reject it on the first recipient, after the row
        # already said SENDING.
        raise BroadcastError(f"Message is longer than {MAX_MESSAGE_LENGTH} characters")

    broadcast = Broadcast(created_by_id=actor.id, message=text, audience=audience)
    session.add(broadcast)
    await session.flush()
    return broadcast


async def list_broadcasts(session: AsyncSession, limit: int = 50) -> list[Broadcast]:
    result = await session.execute(
        select(Broadcast).order_by(Broadcast.created_at.desc()).limit(limit)
    )
    return list(result.scalars())


async def _claim(session: AsyncSession, broadcast_id: int) -> Broadcast | None:
    """
    Takes ownership of a pending broadcast, or returns None.

    The lock is the duplicate-send guard: a plain status check would let
    two callers both read PENDING before either wrote SENDING, and every
    user would receive the message twice.
    """
    broadcast = (
        await session.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id).with_for_update()
        )
    ).scalar_one_or_none()

    if broadcast is None or broadcast.status != BroadcastStatus.PENDING:
        return None

    broadcast.status = BroadcastStatus.SENDING
    broadcast.started_at = datetime.now(timezone.utc)
    broadcast.total_recipients = await audience_size(session, broadcast.audience)
    await session.flush()
    return broadcast


async def _recipients(session: AsyncSession, audience: BroadcastAudience) -> list[int]:
    result = await session.execute(
        select(User.telegram_id).where(*_audience_filter(audience)).order_by(User.id)
    )
    return list(result.scalars())


async def run_broadcast(
    session_factory: async_sessionmaker[AsyncSession], bot: Bot, broadcast_id: int
) -> None:
    """
    Sends a pending broadcast to its audience.

    Owns its own sessions rather than borrowing the request's: a send of
    several thousand messages takes minutes, and holding the HTTP
    request's transaction open for that would pin a connection and block
    every writer touching the same rows.
    """
    async with session_factory() as session:
        broadcast = await _claim(session, broadcast_id)
        if broadcast is None:
            logger.info("Broadcast %s is not pending — skipping", broadcast_id)
            return
        audience, message = broadcast.audience, broadcast.message
        recipients = await _recipients(session, audience)
        await session.commit()

    sent = failed = blocked = 0
    error: str | None = None

    async def persist(status: BroadcastStatus | None = None) -> None:
        values = {"sent_count": sent, "failed_count": failed, "blocked_count": blocked}
        if status is not None:
            values["status"] = status
            values["completed_at"] = datetime.now(timezone.utc)
            values["error"] = error
        async with session_factory() as progress:
            await progress.execute(
                update(Broadcast).where(Broadcast.id == broadcast_id).values(**values)
            )
            await progress.commit()

    try:
        for index, telegram_id in enumerate(recipients, start=1):
            try:
                await bot.send_message(telegram_id, message)
                sent += 1
            except TelegramRetryAfter as exc:
                # Told exactly how long to wait — waiting and retrying once
                # is the difference between a paused broadcast and a bot
                # that keeps hammering a closed door.
                await asyncio.sleep(exc.retry_after)
                try:
                    await bot.send_message(telegram_id, message)
                    sent += 1
                except TelegramAPIError:
                    failed += 1
            except TelegramForbiddenError:
                blocked += 1
            except TelegramAPIError as exc:
                failed += 1
                logger.debug("Broadcast %s could not reach a user: %s", broadcast_id, exc)

            if index % PROGRESS_EVERY == 0:
                await persist()
            await asyncio.sleep(SEND_INTERVAL_SECONDS)
    except Exception as exc:  # noqa: BLE001 — the row must never be left SENDING
        error = str(exc)[:500]
        logger.exception("Broadcast %s aborted", broadcast_id)
        await persist(BroadcastStatus.FAILED)
        return

    await persist(BroadcastStatus.COMPLETED)
    logger.info(
        "Broadcast %s finished: sent=%d blocked=%d failed=%d", broadcast_id, sent, blocked, failed
    )
