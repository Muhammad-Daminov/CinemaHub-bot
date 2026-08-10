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
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramAPIError
from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.system import (
    Broadcast,
    BroadcastAudience,
    BroadcastMedia,
    BroadcastMessage,
    BroadcastStatus,
    BroadcastTranslation,
    DeliveryStatus,
)
from app.db.models.personalization import UserInterestProfile
from app.db.models.user import Subscription, UILanguage, User
from app.services.personalization import (
    TargetError,
    badge_condition,
    interest_condition,
    refresh_profiles_for_targeting,
    validate_badge_target,
    validate_interest_target,
)

logger = logging.getLogger(__name__)

# ~20 messages/second, comfortably inside Telegram's limit for distinct chats.
SEND_INTERVAL_SECONDS = 0.05
# How often progress is written back. Every message would mean one UPDATE
# per send; never would mean an operator watching a long broadcast sees
# nothing until it ends.
PROGRESS_EVERY = 50

MAX_MESSAGE_LENGTH = 4096

# Telegram caps a media caption far lower than a plain message. Validated
# up front rather than discovered on the first recipient, when the row
# already says SENDING.
MAX_CAPTION_LENGTH = 1024

# Recipients are claimed and sent in bounded chunks so a broadcast to
# hundreds of thousands never loads the user table into memory.
BATCH_SIZE = 200

# A retryable failure is tried this many times in total before the row is
# given up as FAILED. Bounded on purpose: an unbounded retry against a
# permanently unreachable chat is an infinite loop wearing a hat.
MAX_ATTEMPTS = 3

# A broadcast whose worker has not touched it for this long is assumed
# dead and may be reclaimed. Comfortably longer than the slowest
# legitimate batch, so a healthy slow send is never stolen mid-flight.
STALE_AFTER = timedelta(minutes=30)


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


def _profiled_user_ids(condition) -> object:
    """
    Subquery of users whose materialised interest profile satisfies `condition`.

    A subquery rather than a join so the audience stays one row per user
    however the profile table grows, and so it composes with the banned
    filter exactly like the premium one does.
    """
    return select(UserInterestProfile.user_id).where(condition)


def validate_target(audience: BroadcastAudience, target_value: str | None) -> str | None:
    """
    Normalises the target for an audience, or refuses it.

    Both directions are errors: a targeted audience without a target would
    mean "everyone" — the single most dangerous silent reinterpretation in
    this module — and a stray target on ALL/PREMIUM/FREE would tell a
    later reader the send was narrower than it was.
    """
    cleaned = (target_value or "").strip() or None

    if not audience.needs_target:
        if cleaned:
            raise BroadcastError(f"{audience.value} targeting takes no target value")
        return None
    if not cleaned:
        raise BroadcastError(f"{audience.value} targeting needs a target value")

    try:
        if audience == BroadcastAudience.INTEREST:
            return validate_interest_target(cleaned)
        return validate_badge_target(cleaned)
    except TargetError as exc:
        raise BroadcastError(str(exc)) from exc


def _audience_filter(audience: BroadcastAudience, target_value: str | None = None):
    """
    Conditions selecting the audience — the **only** definition of who is
    eligible, shared by the estimate, the size counter and materialisation.

    One builder on purpose: an estimate computed by a second query would
    eventually disagree with the send, and an operator who is shown "137"
    and reaches 400 people has been actively misled.

    Banned users are excluded from every audience: they are blocked from
    using the platform, and messaging them anyway is both noise and a
    reason for them to report the bot.

    INTEREST and BADGE read the materialised profile and nothing else — no
    per-user computation, no client-supplied identity. Freshness is handled
    once up front by `refresh_profiles_for_targeting`.
    """
    # Validated here as well as at creation. A targeted audience with no
    # target must never widen to everyone, and the consequence of the
    # first gate being bypassed is a message to the entire user base — so
    # the query builder refuses to construct that filter at all.
    target = validate_target(audience, target_value)

    filters = [User.is_banned.is_(False)]
    if audience == BroadcastAudience.PREMIUM:
        filters.append(User.id.in_(_premium_user_ids()))
    elif audience == BroadcastAudience.FREE:
        filters.append(User.id.not_in(_premium_user_ids()))
    elif audience.needs_target:
        condition = (
            interest_condition(target)
            if audience == BroadcastAudience.INTEREST
            else badge_condition(target)
        )
        filters.append(User.id.in_(_profiled_user_ids(condition)))
    return filters


async def audience_size(
    session: AsyncSession, audience: BroadcastAudience, target_value: str | None = None
) -> int:
    """
    How many users this audience currently reaches.

    Aggregate only — it counts the same rows materialisation would insert
    and returns nothing that identifies any of them.
    """
    result = await session.execute(
        select(func.count(User.id)).where(*_audience_filter(audience, target_value))
    )
    return result.scalar_one()


async def estimate_recipients(
    session: AsyncSession, audience: BroadcastAudience, target_value: str | None = None
) -> int:
    """
    The pre-send estimate, refreshing profiles first exactly as the send will.

    Deliberately the same call path as `run_broadcast`: refresh, then count
    through `_audience_filter`. A preview that skipped the refresh would
    differ from the send for no reason the operator could see.
    """
    validate_target(audience, target_value)
    if audience.needs_target:
        await refresh_profiles_for_targeting(session)
    return await audience_size(session, audience, target_value)


async def create_broadcast(
    session: AsyncSession,
    actor: User,
    message: str,
    audience: BroadcastAudience,
    media_type: BroadcastMedia | None = None,
    media_file_id: str | None = None,
    target_value: str | None = None,
) -> Broadcast:
    """
    Records the broadcast as PENDING. Nothing is sent until `run_broadcast`
    picks it up.

    Media is a Telegram `file_id` captured through the trusted admin
    forward flow — we never download, store or serve the bytes.

    The audience is an enum plus an allowlisted target, and that is the
    whole vocabulary. There is no parameter here through which a caller
    can name a user, supply a list of users, or contribute a filter: who
    receives this is derived from database state at send time and from
    nothing else.
    """
    text = message.strip()
    if not text:
        raise BroadcastError("A broadcast needs a message")
    if len(text) > MAX_MESSAGE_LENGTH:
        # Telegram would reject it on the first recipient, after the row
        # already said SENDING.
        raise BroadcastError(f"Message is longer than {MAX_MESSAGE_LENGTH} characters")

    media = media_type or BroadcastMedia.NONE
    file_id = (media_file_id or "").strip() or None

    # The pairing is validated here, not at send time: a mismatch
    # discovered on the first recipient leaves the broadcast half-claimed
    # and the operator guessing.
    if media.needs_file and not file_id:
        raise BroadcastError(f"A {media.value} broadcast needs a {media.value} file")
    if not media.needs_file and file_id:
        raise BroadcastError("Media was supplied but the broadcast is text-only")
    if media.needs_file and len(text) > MAX_CAPTION_LENGTH:
        raise BroadcastError(
            f"A caption cannot be longer than {MAX_CAPTION_LENGTH} characters"
        )

    target = validate_target(audience, target_value)

    # A double-tapped Send is two identical requests, and two identical
    # broadcasts means every recipient gets the message twice. The row lock
    # protects one broadcast from two workers; nothing protected the
    # platform from two broadcasts, so this does.
    #
    # Scoped as tightly as it can be while still catching that: the same
    # admin, the same everything, still unsent. It is a duplicate
    # *suppressor*, not an idempotency key — an admin who genuinely wants
    # to repeat a send can, once the first has finished. Deliberately not a
    # client-generated token, which would be a guarantee the browser makes
    # to itself.
    duplicate = (
        await session.execute(
            select(Broadcast.id).where(
                Broadcast.created_by_id == actor.id,
                Broadcast.message == text,
                Broadcast.audience == audience,
                Broadcast.target_value.is_(target) if target is None
                else Broadcast.target_value == target,
                Broadcast.media_type == media,
                Broadcast.status.in_([BroadcastStatus.PENDING, BroadcastStatus.SENDING]),
            )
        )
    ).scalars().first()
    if duplicate is not None:
        raise BroadcastError(
            "An identical broadcast is already queued or sending"
        )

    broadcast = Broadcast(
        created_by_id=actor.id,
        message=text,
        audience=audience,
        target_value=target,
        media_type=media,
        media_file_id=file_id,
    )
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
    await session.flush()
    return broadcast


async def set_translations(
    session: AsyncSession,
    broadcast_id: int,
    bodies: dict[UILanguage, str],
    with_media: bool = False,
) -> None:
    """
    Stores the per-language bodies for a broadcast.

    Upserted so re-saving a draft replaces rather than accumulates. The
    broadcast's own `message` remains the fallback for any language not
    supplied — a broadcast is never undeliverable because a translation
    is missing.
    """
    for language, body in bodies.items():
        text = (body or "").strip()
        if not text:
            continue
        limit = MAX_CAPTION_LENGTH if with_media else MAX_MESSAGE_LENGTH
        if len(text) > limit:
            raise BroadcastError(
                f"{language.value} text is longer than {limit} characters"
            )
        await session.execute(
            pg_insert(BroadcastTranslation)
            .values(broadcast_id=broadcast_id, language=language, body=text)
            .on_conflict_do_update(
                constraint="uq_broadcast_translation_language", set_={"body": text}
            )
        )
    await session.flush()


async def _bodies_by_language(session: AsyncSession, broadcast: Broadcast) -> dict[str, str]:
    """
    Every language's text, keyed by language value, with the default filled in.

    Loaded once per run rather than per recipient — the alternative is a
    query per user, which is the N+1 a broadcast can least afford.
    """
    rows = await session.execute(
        select(BroadcastTranslation).where(BroadcastTranslation.broadcast_id == broadcast.id)
    )
    bodies = {row.language.value: row.body for row in rows.scalars()}
    for language in UILanguage:
        # The admin's own text is the fallback for any language they did
        # not translate. Never the *admin's language* as a concept — just
        # the one body we are certain exists.
        bodies.setdefault(language.value, broadcast.message)
    return bodies


async def materialise_recipients(session: AsyncSession, broadcast: Broadcast) -> int:
    """
    Creates the per-recipient rows for this broadcast, once.

    INSERT … SELECT so the audience never crosses into Python, and
    ON CONFLICT DO NOTHING so running it again — on resume, on a retried
    request, from cron — adds nothing and removes nothing. Returns how
    many rows were newly created.

    **The recipient set is frozen at the first call.** Anyone who signs up,
    acquires the targeted interest or badge, or starts a subscription
    afterwards does not join a send already addressed; anyone who loses it
    still receives what was addressed to them.

    ON CONFLICT alone does not give that guarantee — it stops a *duplicate*
    row, not a *new* one, so a second call after the audience changed would
    quietly enlarge the send. The freeze is the emptiness check below,
    taken under the broadcast's own row lock so two workers cannot both
    find it empty. Delivery reads these rows and never re-evaluates the
    audience, which is what makes `total_recipients` mean something.
    """
    # The lock is what makes "has this been materialised?" answerable. Held
    # by `_claim` already in the normal path; taken here too so the
    # function is safe called on its own — by resume, by cron, by a test.
    locked = (
        await session.execute(
            select(Broadcast.id).where(Broadcast.id == broadcast.id).with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        return 0

    already = (
        await session.execute(
            select(func.count())
            .select_from(BroadcastMessage)
            .where(BroadcastMessage.broadcast_id == broadcast.id)
        )
    ).scalar_one()
    if already:
        return 0

    # Literals must be explicit in a SELECT feeding an INSERT — a bare
    # Python value is read as a column name.
    source = select(
        literal(broadcast.id),
        User.id,
        # `.name`, not `.value`: SQLAlchemy stores enums by member name,
        # so the Postgres labels are PENDING/SENT/…, and the literal must
        # be cast into the enum type rather than passed as varchar.
        literal(DeliveryStatus.PENDING.name).cast(
            BroadcastMessage.__table__.c.status.type
        ),
    ).where(*_audience_filter(broadcast.audience, broadcast.target_value))

    result = await session.execute(
        pg_insert(BroadcastMessage)
        .from_select(["broadcast_id", "user_id", "status"], source)
        .on_conflict_do_nothing(constraint="uq_broadcast_recipient")
    )
    await session.flush()
    return result.rowcount or 0


async def _claim_batch(
    session: AsyncSession, broadcast_id: int, limit: int = BATCH_SIZE
) -> list[tuple[int, int, str]]:
    """
    Takes the next batch of recipients, returning (message_id, telegram_id, language).

    Each row moves PENDING → SENDING with `attempts` incremented **before**
    the send, and the transaction commits before Telegram is called. That
    ordering is what bounds the crash window: a row found in SENDING on
    resume already carries its attempt, so it can be retried a fixed
    number of times rather than forever.

    SKIP LOCKED so two workers can share a broadcast without either
    blocking or handing the same recipient to both.
    """
    rows = (
        await session.execute(
            select(BroadcastMessage.id, User.telegram_id, User.language)
            .join(User, User.id == BroadcastMessage.user_id)
            .where(
                BroadcastMessage.broadcast_id == broadcast_id,
                BroadcastMessage.status == DeliveryStatus.PENDING,
                BroadcastMessage.attempts < MAX_ATTEMPTS,
            )
            .order_by(BroadcastMessage.id)
            .limit(limit)
            .with_for_update(skip_locked=True, of=BroadcastMessage)
        )
    ).all()

    if not rows:
        return []

    await session.execute(
        update(BroadcastMessage)
        .where(BroadcastMessage.id.in_([row[0] for row in rows]))
        .values(status=DeliveryStatus.SENDING, attempts=BroadcastMessage.attempts + 1)
    )
    await session.flush()
    return [(row[0], row[1], row[2].value) for row in rows]


async def _finish(
    session: AsyncSession,
    message_id: int,
    status: DeliveryStatus,
    error: str | None = None,
) -> None:
    """Records one recipient's outcome. Errors are truncated, never formatted with secrets."""
    values: dict = {"status": status, "error": (error or None) and error[:300]}
    if status == DeliveryStatus.SENT:
        values["sent_at"] = datetime.now(timezone.utc)
    await session.execute(
        update(BroadcastMessage).where(BroadcastMessage.id == message_id).values(**values)
    )


async def _sync_counters(session: AsyncSession, broadcast_id: int) -> dict[str, int]:
    """
    Recomputes the broadcast's counters from the recipient rows.

    Derived rather than incremented: after a crash and resume, an
    in-memory tally is wrong, while a COUNT over the rows is right by
    construction. The existing counter columns keep their meaning, so old
    broadcasts and the existing admin screen are unaffected.
    """
    rows = (
        await session.execute(
            select(BroadcastMessage.status, func.count())
            .where(BroadcastMessage.broadcast_id == broadcast_id)
            .group_by(BroadcastMessage.status)
        )
    ).all()
    counts = {status.value: total for status, total in rows}

    sent = counts.get(DeliveryStatus.SENT.value, 0)
    failed = counts.get(DeliveryStatus.FAILED.value, 0)
    skipped = counts.get(DeliveryStatus.SKIPPED.value, 0)
    outstanding = counts.get(DeliveryStatus.PENDING.value, 0) + counts.get(
        DeliveryStatus.SENDING.value, 0
    )

    await session.execute(
        update(Broadcast)
        .where(Broadcast.id == broadcast_id)
        .values(sent_count=sent, failed_count=failed, blocked_count=skipped)
    )
    return {"sent": sent, "failed": failed, "skipped": skipped, "outstanding": outstanding}


def _classify(exc: TelegramAPIError) -> DeliveryStatus:
    """
    Permanent or retryable.

    A blocked or deleted account will never accept this message however
    many times we ask, so it is terminal immediately; anything else is
    assumed transient and left to the bounded retry.
    """
    if isinstance(exc, TelegramForbiddenError):
        return DeliveryStatus.SKIPPED
    text = str(exc).lower()
    if "chat not found" in text or "user is deactivated" in text or "bot was blocked" in text:
        return DeliveryStatus.SKIPPED
    # A rejected file_id or unparseable caption fails identically for every
    # recipient. Retrying it would mean MAX_ATTEMPTS × the whole audience
    # of calls that cannot possibly succeed, so it is terminal — and the
    # error text tells the operator what to fix.
    if (
        "wrong file identifier" in text
        or "wrong remote file id" in text
        or "failed to get http url content" in text
        or "can't parse entities" in text
        or "media_caption_too_long" in text
    ):
        return DeliveryStatus.FAILED
    return DeliveryStatus.PENDING


async def run_broadcast(
    session_factory: async_sessionmaker[AsyncSession], bot: Bot, broadcast_id: int
) -> None:
    """
    Sends a broadcast, resumably.

    Owns its own sessions rather than borrowing the request's: a send of
    several thousand messages takes minutes, and holding the HTTP
    request's transaction open for that would pin a connection and block
    every writer touching the same rows.

    **Delivery is at-least-once, not exactly-once**, and the boundary is
    worth stating plainly: Telegram accepting a message and this process
    committing that fact are two operations that cannot be made atomic. A
    crash in the gap between them leaves the row in SENDING with its
    attempt already counted, and the resume path retries it — so that
    recipient may see the message twice. The alternative, marking SENT
    before sending, loses messages instead, which is worse for a
    notification. Everything else — duplicate requests, concurrent
    workers, repeated resumes — is made exactly-once by the unique
    constraint on (broadcast_id, user_id).
    """
    async with session_factory() as session:
        broadcast = await _claim(session, broadcast_id)
        if broadcast is None:
            logger.info("Broadcast %s is not claimable — skipping", broadcast_id)
            return

        # Freshness is a single bulk pass before the audience is resolved,
        # never a recomputation per recipient. Only targeted audiences pay
        # for it — an ALL/PREMIUM/FREE send costs exactly what it did
        # before this existed.
        if broadcast.audience.needs_target:
            await refresh_profiles_for_targeting(session)

        await materialise_recipients(session, broadcast)
        # Counted from the rows that were actually created rather than from
        # a second query over the audience. The two could differ — a user
        # signing up between them would be enough — and the number an
        # operator watches must be the number being delivered to.
        broadcast.total_recipients = (
            await session.execute(
                select(func.count())
                .select_from(BroadcastMessage)
                .where(BroadcastMessage.broadcast_id == broadcast.id)
            )
        ).scalar_one()

        bodies = await _bodies_by_language(session, broadcast)
        media = (broadcast.media_type, broadcast.media_file_id)
        await session.commit()

    await _deliver(session_factory, bot, broadcast_id, bodies, media)


async def _deliver(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    broadcast_id: int,
    bodies: dict[str, str],
    media: tuple[BroadcastMedia, str | None] = (BroadcastMedia.NONE, None),
) -> None:
    """The batch loop. Separated so resume can re-enter it without re-claiming."""
    media_type, file_id = media
    error: str | None = None

    try:
        while True:
            async with session_factory() as session:
                batch = await _claim_batch(session, broadcast_id)
                await session.commit()

            if not batch:
                break

            for message_id, telegram_id, language in batch:
                # Each recipient reads in their own language; the admin's
                # is never consulted.
                text = bodies.get(language) or bodies[UILanguage.UZ.value]
                status, failure = await _send_one(bot, telegram_id, text, media_type, file_id)

                async with session_factory() as session:
                    await _finish(session, message_id, status, failure)
                    await session.commit()

                await asyncio.sleep(SEND_INTERVAL_SECONDS)

            async with session_factory() as session:
                await _sync_counters(session, broadcast_id)
                await session.commit()

    except Exception as exc:  # noqa: BLE001 — the row must never be left SENDING
        error = str(exc)[:500]
        logger.exception("Broadcast %s aborted", broadcast_id)
        async with session_factory() as session:
            await _sync_counters(session, broadcast_id)
            await session.execute(
                update(Broadcast)
                .where(Broadcast.id == broadcast_id)
                .values(
                    status=BroadcastStatus.FAILED,
                    error=error,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        return

    async with session_factory() as session:
        counts = await _sync_counters(session, broadcast_id)
        # Rows still outstanding mean every remaining recipient exhausted
        # its attempts; the run is over either way, and the counters say
        # what actually happened.
        await session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(status=BroadcastStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
        )
        await session.commit()

    logger.info(
        "Broadcast %s finished: sent=%d skipped=%d failed=%d",
        broadcast_id, counts["sent"], counts["skipped"], counts["failed"],
    )


async def _dispatch(
    bot: Bot, telegram_id: int, text: str, media_type: BroadcastMedia, file_id: str | None
) -> None:
    """
    The single place a broadcast reaches Telegram.

    One call per media type, using the same primitives the rest of the app
    already uses. The file_id is passed straight through — no download, no
    re-upload, no bytes in our process.
    """
    if media_type == BroadcastMedia.PHOTO and file_id:
        await bot.send_photo(telegram_id, file_id, caption=text or None)
    elif media_type == BroadcastMedia.VIDEO and file_id:
        await bot.send_video(telegram_id, file_id, caption=text or None)
    else:
        await bot.send_message(telegram_id, text)


async def _send_one(
    bot: Bot,
    telegram_id: int,
    text: str,
    media_type: BroadcastMedia = BroadcastMedia.NONE,
    file_id: str | None = None,
) -> tuple[DeliveryStatus, str | None]:
    """
    One delivery attempt. Returns the outcome and a safe error string.

    A RetryAfter is honoured once inline — Telegram has told us exactly
    how long to wait, and obeying is the difference between a paused
    broadcast and a bot hammering a closed door. Anything still failing is
    left PENDING for the bounded retry rather than being given up on.

    Identical for text, photo and video: media changes which call is made,
    not how failure is handled, so a photo broadcast retries, resumes and
    recovers exactly like a text one.
    """
    try:
        await _dispatch(bot, telegram_id, text, media_type, file_id)
        return DeliveryStatus.SENT, None
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await _dispatch(bot, telegram_id, text, media_type, file_id)
            return DeliveryStatus.SENT, None
        except TelegramAPIError as retry_exc:
            return _classify(retry_exc), str(retry_exc)[:300]
    except TelegramAPIError as exc:
        return _classify(exc), str(exc)[:300]


async def delivery_breakdown(session: AsyncSession, broadcast_id: int) -> dict[str, int]:
    """
    Live per-status counts for one broadcast, from the recipient rows.

    The progress screen reads this rather than deriving anything from the
    pre-send estimate: an estimate is a guess about the future, while
    these rows are what is actually happening. `outstanding` counts only
    rows that can still be claimed — a recipient that exhausted its
    attempts is finished, however PENDING it looks.
    """
    rows = (
        await session.execute(
            select(BroadcastMessage.status, func.count())
            .where(BroadcastMessage.broadcast_id == broadcast_id)
            .group_by(BroadcastMessage.status)
        )
    ).all()
    counts = {status.value: total for status, total in rows}

    outstanding = (
        await session.execute(
            select(func.count())
            .select_from(BroadcastMessage)
            .where(
                BroadcastMessage.broadcast_id == broadcast_id,
                BroadcastMessage.status.in_(
                    [DeliveryStatus.PENDING, DeliveryStatus.SENDING]
                ),
                BroadcastMessage.attempts < MAX_ATTEMPTS,
            )
        )
    ).scalar_one()

    return {
        "pending": counts.get(DeliveryStatus.PENDING.value, 0),
        "sending": counts.get(DeliveryStatus.SENDING.value, 0),
        "sent": counts.get(DeliveryStatus.SENT.value, 0),
        "failed": counts.get(DeliveryStatus.FAILED.value, 0),
        "skipped": counts.get(DeliveryStatus.SKIPPED.value, 0),
        "outstanding": outstanding,
    }


def _is_resumable(broadcast: Broadcast, outstanding: int, now: datetime) -> bool:
    """
    Whether an operator may restart this broadcast — decided here, on the
    server, and never inferred by the panel.

    Two recoverable shapes: a run that raised and was recorded FAILED, and
    one left SENDING by a worker that died. The staleness window is the
    same one cron uses, because "still being worked on" must mean the same
    thing to both — a Resume button offered during a healthy slow send
    would invite an operator to fight their own worker.

    Nothing to do means not resumable, whatever the status says.
    """
    if outstanding <= 0:
        return False
    if broadcast.status == BroadcastStatus.FAILED:
        return True
    return (
        broadcast.status == BroadcastStatus.SENDING
        and broadcast.started_at is not None
        and broadcast.started_at < now - STALE_AFTER
    )


async def resumability(session: AsyncSession, broadcast: Broadcast) -> tuple[bool, dict[str, int]]:
    """The Resume button's authority, and the counters it is decided from."""
    breakdown = await delivery_breakdown(session, broadcast.id)
    resumable = _is_resumable(broadcast, breakdown["outstanding"], datetime.now(timezone.utc))
    return resumable, breakdown


async def _resume_one(
    session_factory: async_sessionmaker[AsyncSession], bot: Bot, broadcast_id: int
) -> None:
    """
    Restarts delivery for one broadcast that already has its recipients.

    **Never re-materialises.** The frozen set from the original send is
    what gets delivered — the audience is not re-evaluated, so a resume
    cannot reach someone the broadcast was not addressed to, and the
    target is not consulted at all.

    Rows left SENDING by the dead worker go back to PENDING with their
    attempt already counted, so they cannot loop forever. `started_at` is
    re-stamped so the staleness window restarts and a cron run does not
    immediately steal a broadcast an operator just picked up.
    """
    async with session_factory() as session:
        await session.execute(
            update(BroadcastMessage)
            .where(
                BroadcastMessage.broadcast_id == broadcast_id,
                BroadcastMessage.status == DeliveryStatus.SENDING,
            )
            .values(status=DeliveryStatus.PENDING)
        )
        broadcast = await session.get(Broadcast, broadcast_id)
        await session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status=BroadcastStatus.SENDING,
                started_at=datetime.now(timezone.utc),
                error=None,
                completed_at=None,
            )
        )
        bodies = await _bodies_by_language(session, broadcast)
        media = (broadcast.media_type, broadcast.media_file_id)
        await session.commit()

    logger.warning("Resuming broadcast %s", broadcast_id)
    await _deliver(session_factory, bot, broadcast_id, bodies, media)


async def claim_for_resume(session: AsyncSession, broadcast_id: int) -> Broadcast | None:
    """
    Checks resumability under the broadcast's row lock, or refuses.

    The lock is why two operators tapping Resume at the same moment cannot
    both start a worker: the second finds the row already re-stamped
    SENDING and inside its staleness window, and stops. The caller commits
    before the delivery task runs.
    """
    broadcast = (
        await session.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id).with_for_update()
        )
    ).scalar_one_or_none()
    if broadcast is None:
        return None

    resumable, _breakdown = await resumability(session, broadcast)
    if not resumable:
        return None

    broadcast.status = BroadcastStatus.SENDING
    broadcast.started_at = datetime.now(timezone.utc)
    await session.flush()
    return broadcast


async def resume_broadcast(
    session_factory: async_sessionmaker[AsyncSession], bot: Bot, broadcast_id: int
) -> None:
    """Operator-triggered resume. Same body as the scheduled one, deliberately."""
    await _resume_one(session_factory, bot, broadcast_id)


async def resume_stale_broadcasts(
    session_factory: async_sessionmaker[AsyncSession], bot: Bot, limit: int = 3
) -> int:
    """
    Reclaims broadcasts whose worker died, and finishes them.

    Called by the scheduled maintenance run. Idempotent: a broadcast with
    no outstanding recipients is simply completed, and one already being
    worked on is not stale yet, so a second cron run does nothing.

    Rows left SENDING by a crash are returned to PENDING here — their
    attempt is already counted, so they cannot loop forever.
    """
    cutoff = datetime.now(timezone.utc) - STALE_AFTER
    async with session_factory() as session:
        stale = (
            await session.execute(
                select(Broadcast.id)
                .where(
                    Broadcast.status == BroadcastStatus.SENDING,
                    Broadcast.started_at.is_not(None),
                    Broadcast.started_at < cutoff,
                )
                .order_by(Broadcast.started_at)
                .limit(limit)
            )
        ).scalars().all()

    for broadcast_id in stale:
        # The same body an operator's Resume runs. One implementation, so
        # the scheduled recovery and the button cannot drift apart.
        await _resume_one(session_factory, bot, broadcast_id)

    return len(stale)
