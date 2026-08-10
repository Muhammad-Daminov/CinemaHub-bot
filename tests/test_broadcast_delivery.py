"""
Broadcast delivery state: idempotency, resume and crash safety.

The property this file exists for is that **a user never receives the
same broadcast twice** because of a duplicate request, two workers, a
resume, or a cron recovery. That guarantee is a database constraint —
unique (broadcast_id, user_id) — not an application check, so the tests
attack it at that level.

The one case where duplication *is* possible is stated openly in
`test_the_crash_window_between_telegram_and_commit_is_documented`:
Telegram accepting a message and this process recording it cannot be made
atomic, so delivery is at-least-once. Everything else is exactly-once.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select, update

from app.db.models.system import (
    Broadcast,
    BroadcastAudience,
    BroadcastMessage,
    BroadcastStatus,
    BroadcastTranslation,
    DeliveryStatus,
)
from app.db.models.user import UILanguage
from app.services.broadcast import (
    MAX_ATTEMPTS,
    create_broadcast,
    materialise_recipients,
    resume_stale_broadcasts,
    run_broadcast,
    set_translations,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


class FakeBot:
    """Records deliveries; can be told to fail in specific ways."""

    def __init__(self, blocked=None, retry_once=None, always_fail=None, crash_after=None):
        self.sent: list[tuple[int, str]] = []
        self.blocked = blocked or set()
        self.retry_once = set(retry_once or [])
        self.always_fail = always_fail or set()
        self.crash_after = crash_after
        self._retried: set[int] = set()

    async def send_message(self, chat_id, text, *args, **kwargs):
        if self.crash_after is not None and len(self.sent) >= self.crash_after:
            raise RuntimeError("worker died")
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        if chat_id in self.always_fail:
            raise TelegramRetryAfter(method=None, message="flood", retry_after=0)
        if chat_id in self.retry_once and chat_id not in self._retried:
            self._retried.add(chat_id)
            raise TelegramRetryAfter(method=None, message="flood", retry_after=0)
        self.sent.append((chat_id, text))


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    from app.services import broadcast as module

    monkeypatch.setattr(module, "SEND_INTERVAL_SECONDS", 0)


async def _broadcast(session, actor, message="Hello", audience=BroadcastAudience.ALL):
    return await create_broadcast(session, actor, message, audience)


# ---------- the constraint ----------


async def test_one_row_per_recipient(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9001)
        await make_user(s, 9002)
        broadcast = await _broadcast(s, actor)
        await s.commit()
        broadcast_id = broadcast.id

    async with db_factory() as s:
        broadcast = await s.get(Broadcast, broadcast_id)
        created = await materialise_recipients(s, broadcast)
        await s.commit()
        assert created == 2

    async with db_factory() as s:
        assert await count_rows(s, BroadcastMessage, broadcast_id=broadcast_id) == 2


async def test_materialising_twice_adds_nothing(db_factory):
    """Resume, retry and cron all re-enter this — it must be a no-op."""
    async with db_factory() as s:
        actor = await make_user(s, 9003)
        await make_user(s, 9004)
        broadcast = await _broadcast(s, actor)
        await s.commit()
        broadcast_id = broadcast.id

    for expected in (2, 0, 0):
        async with db_factory() as s:
            broadcast = await s.get(Broadcast, broadcast_id)
            assert await materialise_recipients(s, broadcast) == expected
            await s.commit()

    async with db_factory() as s:
        assert await count_rows(s, BroadcastMessage, broadcast_id=broadcast_id) == 2


async def test_a_duplicate_recipient_row_is_rejected(db_factory):
    """The guarantee stated at the database level."""
    from sqlalchemy.exc import IntegrityError

    async with db_factory() as s:
        actor = await make_user(s, 9005)
        broadcast = await _broadcast(s, actor)
        await s.flush()
        s.add(BroadcastMessage(broadcast_id=broadcast.id, user_id=actor.id))
        await s.flush()
        s.add(BroadcastMessage(broadcast_id=broadcast.id, user_id=actor.id))
        with pytest.raises(IntegrityError):
            await s.flush()


# ---------- delivery ----------


async def test_a_broadcast_reaches_its_audience_once(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9010)
        await make_user(s, 9011)
        broadcast = await _broadcast(s, actor, "Hi")
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)

    assert sorted(chat for chat, _ in bot.sent) == [9010, 9011]
    async with db_factory() as s:
        row = await s.get(Broadcast, broadcast_id)
        assert row.status == BroadcastStatus.COMPLETED
        assert row.sent_count == 2
        assert await count_rows(s, BroadcastMessage, status=DeliveryStatus.SENT) == 2


async def test_running_a_completed_broadcast_again_sends_nothing(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9012)
        broadcast = await _broadcast(s, actor)
        await s.commit()
        broadcast_id = broadcast.id

    first = FakeBot()
    await run_broadcast(db_factory, first, broadcast_id)
    second = FakeBot()
    await run_broadcast(db_factory, second, broadcast_id)

    assert len(first.sent) == 1
    assert second.sent == [], "a finished broadcast must never send again"


async def test_two_workers_racing_deliver_once_each(db_factory):
    """Concurrent workers share the queue without doubling any recipient."""
    async with db_factory() as s:
        actor = await make_user(s, 9013)
        for telegram_id in range(9014, 9020):
            await make_user(s, telegram_id)
        broadcast = await _broadcast(s, actor)
        await s.commit()
        broadcast_id = broadcast.id

    bots = [FakeBot(), FakeBot()]
    await asyncio.gather(*(run_broadcast(db_factory, bot, broadcast_id) for bot in bots))

    delivered = [chat for bot in bots for chat, _ in bot.sent]
    assert len(delivered) == len(set(delivered)) == 7


# ---------- failure classification ----------


async def test_a_blocked_user_is_skipped_not_failed(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9020)
        await make_user(s, 9021)
        broadcast = await _broadcast(s, actor)
        await s.commit()
        broadcast_id = broadcast.id

    await run_broadcast(db_factory, FakeBot(blocked={9021}), broadcast_id)

    async with db_factory() as s:
        row = await s.get(Broadcast, broadcast_id)
        assert (row.sent_count, row.blocked_count, row.failed_count) == (1, 1, 0)
        assert await count_rows(s, BroadcastMessage, status=DeliveryStatus.SKIPPED) == 1


async def test_a_retry_after_is_honoured_and_succeeds(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9022)
        broadcast = await _broadcast(s, actor)
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot(retry_once={9022})
    await run_broadcast(db_factory, bot, broadcast_id)

    assert len(bot.sent) == 1
    async with db_factory() as s:
        assert await count_rows(s, BroadcastMessage, status=DeliveryStatus.SENT) == 1


async def test_a_persistently_failing_recipient_is_bounded(db_factory):
    """
    Retryable does not mean forever. After MAX_ATTEMPTS the row stops
    being picked up, so the run ends instead of looping.
    """
    async with db_factory() as s:
        actor = await make_user(s, 9023)
        broadcast = await _broadcast(s, actor)
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot(always_fail={9023})
    await run_broadcast(db_factory, bot, broadcast_id)

    async with db_factory() as s:
        message = (
            await s.execute(select(BroadcastMessage).where(BroadcastMessage.broadcast_id == broadcast_id))
        ).scalar_one()
        assert message.attempts <= MAX_ATTEMPTS
        assert message.status in (DeliveryStatus.PENDING, DeliveryStatus.FAILED)
        assert message.error, "the reason is recorded"
        assert (await s.get(Broadcast, broadcast_id)).status == BroadcastStatus.COMPLETED


# ---------- crash and resume ----------


async def test_a_crash_leaves_the_rest_pending_and_resume_finishes_it(db_factory):
    """Crash during a batch: delivered rows stay delivered, the rest resume."""
    async with db_factory() as s:
        actor = await make_user(s, 9030)
        for telegram_id in range(9031, 9036):
            await make_user(s, telegram_id)
        broadcast = await _broadcast(s, actor)
        await s.commit()
        broadcast_id = broadcast.id

    crashing = FakeBot(crash_after=2)
    await run_broadcast(db_factory, crashing, broadcast_id)
    delivered_first = {chat for chat, _ in crashing.sent}
    assert len(delivered_first) == 2

    async with db_factory() as s:
        row = await s.get(Broadcast, broadcast_id)
        assert row.status == BroadcastStatus.FAILED, "a dead run must not read as completed"
        # Wind the clock back so the recovery sweep considers it stale.
        await s.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status=BroadcastStatus.SENDING,
                started_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
        )
        await s.commit()

    resuming = FakeBot()
    assert await resume_stale_broadcasts(db_factory, resuming) == 1

    delivered_second = {chat for chat, _ in resuming.sent}
    assert not (delivered_first & delivered_second), "nobody is sent to twice"
    assert delivered_first | delivered_second == set(range(9030, 9036))

    async with db_factory() as s:
        assert (await s.get(Broadcast, broadcast_id)).status == BroadcastStatus.COMPLETED


async def test_a_row_left_in_flight_is_returned_to_the_queue(db_factory):
    """Crash after claiming but before Telegram answered."""
    async with db_factory() as s:
        actor = await make_user(s, 9040)
        broadcast = await _broadcast(s, actor)
        await s.flush()
        s.add(
            BroadcastMessage(
                broadcast_id=broadcast.id,
                user_id=actor.id,
                status=DeliveryStatus.SENDING,
                attempts=1,
            )
        )
        await s.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast.id)
            .values(
                status=BroadcastStatus.SENDING,
                started_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
        )
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    assert await resume_stale_broadcasts(db_factory, bot) == 1
    assert [chat for chat, _ in bot.sent] == [9040]

    async with db_factory() as s:
        message = (
            await s.execute(select(BroadcastMessage).where(BroadcastMessage.broadcast_id == broadcast_id))
        ).scalar_one()
        assert message.status == DeliveryStatus.SENT
        assert message.attempts == 2, "the earlier attempt is still counted"


async def test_recovery_is_idempotent(db_factory):
    """A second cron run finds nothing to do."""
    async with db_factory() as s:
        actor = await make_user(s, 9041)
        broadcast = await _broadcast(s, actor)
        await s.flush()
        await s.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast.id)
            .values(
                status=BroadcastStatus.SENDING,
                started_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
        )
        await s.commit()

    first = FakeBot()
    assert await resume_stale_broadcasts(db_factory, first) == 1
    second = FakeBot()
    assert await resume_stale_broadcasts(db_factory, second) == 0
    assert second.sent == []


async def test_a_healthy_slow_broadcast_is_not_reclaimed(db_factory):
    """Recovery must not steal work from a worker that is simply still going."""
    async with db_factory() as s:
        actor = await make_user(s, 9042)
        broadcast = await _broadcast(s, actor)
        await s.flush()
        await s.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast.id)
            .values(status=BroadcastStatus.SENDING, started_at=datetime.now(timezone.utc))
        )
        await s.commit()

    assert await resume_stale_broadcasts(db_factory, FakeBot()) == 0


def test_the_crash_window_between_telegram_and_commit_is_documented():
    """
    Delivery is at-least-once, and the module says so.

    Telegram accepting a message and this process committing that fact
    cannot be made atomic; a crash in between means one recipient may see
    it twice on resume. Marking SENT *before* sending would lose messages
    instead, which is worse for a notification. This test exists so the
    trade-off cannot be quietly forgotten.
    """
    from app.services import broadcast

    doc = broadcast.run_broadcast.__doc__ or ""
    assert "at-least-once" in doc
    assert "exactly-once" in doc


# ---------- localization ----------


async def test_each_recipient_reads_their_own_language(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9050)
        uz = await make_user(s, 9051)
        ru = await make_user(s, 9052)
        en = await make_user(s, 9053)
        uz.language, ru.language, en.language = UILanguage.UZ, UILanguage.RU, UILanguage.EN
        broadcast = await _broadcast(s, actor, "Salom")
        await s.flush()
        await set_translations(
            s,
            broadcast.id,
            {UILanguage.RU: "Привет", UILanguage.EN: "Hello"},
        )
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)
    delivered = dict(bot.sent)

    assert delivered[9051] == "Salom"
    assert delivered[9052] == "Привет"
    assert delivered[9053] == "Hello"


async def test_a_missing_translation_falls_back_to_the_default_body(db_factory):
    """A broadcast is never undeliverable because a language was skipped."""
    async with db_factory() as s:
        actor = await make_user(s, 9054)
        ru = await make_user(s, 9055)
        ru.language = UILanguage.RU
        broadcast = await _broadcast(s, actor, "Faqat o'zbekcha")
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)

    assert dict(bot.sent)[9055] == "Faqat o'zbekcha"


async def test_the_admins_language_is_never_used_for_a_recipient(db_factory):
    """Writing in Uzbek must not mean a Russian speaker receives Uzbek."""
    async with db_factory() as s:
        actor = await make_user(s, 9056)
        actor.language = UILanguage.UZ
        ru = await make_user(s, 9057)
        ru.language = UILanguage.RU
        broadcast = await _broadcast(s, actor, "Uzbek body")
        await s.flush()
        await set_translations(s, broadcast.id, {UILanguage.RU: "Русский текст"})
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)
    assert dict(bot.sent)[9057] == "Русский текст"


async def test_translations_upsert_rather_than_accumulate(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9058)
        broadcast = await _broadcast(s, actor)
        await s.flush()
        await set_translations(s, broadcast.id, {UILanguage.RU: "First"})
        await set_translations(s, broadcast.id, {UILanguage.RU: "Second"})
        await s.commit()

        rows = (
            await s.execute(
                select(BroadcastTranslation).where(BroadcastTranslation.broadcast_id == broadcast.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].body == "Second"


# ---------- audiences and compatibility ----------


async def test_banned_users_are_never_materialised(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9060)
        banned = await make_user(s, 9061)
        banned.is_banned = True
        broadcast = await _broadcast(s, actor)
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)

    assert [chat for chat, _ in bot.sent] == [9060]
    async with db_factory() as s:
        assert await count_rows(s, BroadcastMessage, broadcast_id=broadcast_id) == 1


async def test_premium_and_free_audiences_still_split(db_factory):
    from tests.conftest import make_paid_plan
    from app.db.models.user import Subscription

    async with db_factory() as s:
        actor = await make_user(s, 9062)
        subscriber = await make_user(s, 9063)
        plan = await make_paid_plan(s)
        s.add(
            Subscription(
                user_id=subscriber.id,
                plan_id=plan.id,
                started_at=datetime.now(timezone.utc) - timedelta(days=1),
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            )
        )
        premium = await _broadcast(s, actor, "Premium", BroadcastAudience.PREMIUM)
        free = await _broadcast(s, actor, "Free", BroadcastAudience.FREE)
        await s.commit()
        premium_id, free_id = premium.id, free.id

    premium_bot = FakeBot()
    await run_broadcast(db_factory, premium_bot, premium_id)
    free_bot = FakeBot()
    await run_broadcast(db_factory, free_bot, free_id)

    assert [chat for chat, _ in premium_bot.sent] == [9063]
    assert [chat for chat, _ in free_bot.sent] == [9062]


async def test_a_historical_completed_broadcast_is_left_alone(db_factory):
    """
    Broadcasts that finished before this feature have no recipient rows
    and must not gain any — inventing them would fabricate delivery
    evidence.
    """
    async with db_factory() as s:
        actor = await make_user(s, 9064)
        broadcast = await _broadcast(s, actor)
        await s.flush()
        await s.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast.id)
            .values(
                status=BroadcastStatus.COMPLETED,
                sent_count=500,
                total_recipients=500,
                completed_at=datetime.now(timezone.utc) - timedelta(days=30),
            )
        )
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)
    assert await resume_stale_broadcasts(db_factory, bot) == 0

    async with db_factory() as s:
        row = await s.get(Broadcast, broadcast_id)
        assert row.sent_count == 500, "its historical counters are untouched"
        assert await count_rows(s, BroadcastMessage, broadcast_id=broadcast_id) == 0
    assert bot.sent == []


async def test_two_broadcasts_do_not_share_recipient_state(db_factory):
    """Cross-broadcast isolation: finishing one leaves the other's queue intact."""
    async with db_factory() as s:
        actor = await make_user(s, 9070)
        await make_user(s, 9071)
        first = await _broadcast(s, actor, "First")
        second = await _broadcast(s, actor, "Second")
        await s.commit()
        first_id, second_id = first.id, second.id

    await run_broadcast(db_factory, FakeBot(), first_id)

    async with db_factory() as s:
        assert await count_rows(s, BroadcastMessage, broadcast_id=second_id) == 0
        assert (await s.get(Broadcast, second_id)).status == BroadcastStatus.PENDING

    bot = FakeBot()
    await run_broadcast(db_factory, bot, second_id)
    assert sorted(chat for chat, _ in bot.sent) == [9070, 9071]
    assert all(text == "Second" for _, text in bot.sent)
