"""
Broadcasts: audience selection, and the guarantee that one broadcast is
sent once.

Sending twice is the failure that matters here — it is visible to every
user at once and cannot be recalled. The claim is a row lock, so the
duplicate test drives two independent sessions; sharing one session
would share its transaction and never contend.
"""
import asyncio

import pytest
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select

from app.db.models.system import Broadcast, BroadcastAudience, BroadcastStatus
from app.db.models.user import Subscription, User
from app.services.broadcast import (
    BroadcastError,
    audience_size,
    create_broadcast,
    run_broadcast,
)
from tests.conftest import make_paid_plan, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


class FakeBot:
    """Records what would have been sent, and fails for the ids it is told to."""

    def __init__(self, blocked: set[int] | None = None):
        self.sent: list[int] = []
        self.blocked = blocked or set()

    async def send_message(self, chat_id, text, *args, **kwargs):
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        self.sent.append(chat_id)


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    """The real send paces itself at ~20/s; the tests do not need the wall clock."""
    from app.services import broadcast as broadcast_module

    monkeypatch.setattr(broadcast_module, "SEND_INTERVAL_SECONDS", 0)


async def _premium(session, user: User) -> None:
    from datetime import datetime, timedelta, timezone

    plan = await make_paid_plan(session, code=f"p{user.id}")
    session.add(
        Subscription(
            user_id=user.id,
            plan_id=plan.id,
            started_at=datetime.now(timezone.utc) - timedelta(days=1),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    await session.flush()


# ---------- audiences ----------


async def test_all_counts_every_active_user(db_session):
    for telegram_id in range(9950, 9953):
        await make_user(db_session, telegram_id)
    assert await audience_size(db_session, BroadcastAudience.ALL) == 3


async def test_banned_users_are_in_no_audience(db_session):
    """They are blocked from the platform; messaging them anyway is only noise."""
    kept = await make_user(db_session, 9954)
    banned = await make_user(db_session, 9955)
    banned.is_banned = True
    await db_session.flush()

    assert await audience_size(db_session, BroadcastAudience.ALL) == 1
    assert await audience_size(db_session, BroadcastAudience.FREE) == 1
    assert kept.is_banned is False


async def test_premium_and_free_split_the_audience(db_session):
    subscriber = await make_user(db_session, 9956)
    await make_user(db_session, 9957)
    await _premium(db_session, subscriber)

    assert await audience_size(db_session, BroadcastAudience.PREMIUM) == 1
    assert await audience_size(db_session, BroadcastAudience.FREE) == 1


# ---------- creating ----------


async def test_an_empty_message_is_refused(db_session):
    actor = await make_user(db_session, 9958)
    with pytest.raises(BroadcastError):
        await create_broadcast(db_session, actor, "   ", BroadcastAudience.ALL)


async def test_an_overlong_message_is_refused_before_sending(db_session):
    """Telegram would reject it on the first recipient, with the row already SENDING."""
    actor = await make_user(db_session, 9959)
    with pytest.raises(BroadcastError):
        await create_broadcast(db_session, actor, "x" * 5000, BroadcastAudience.ALL)


async def test_a_new_broadcast_starts_pending(db_session):
    actor = await make_user(db_session, 9960)
    broadcast = await create_broadcast(db_session, actor, "hello", BroadcastAudience.ALL)
    assert broadcast.status == BroadcastStatus.PENDING
    assert broadcast.sent_count == 0


# ---------- sending ----------


async def test_sending_reaches_the_audience_and_records_it(db_factory):
    async with db_factory() as setup:
        actor = await make_user(setup, 9961)
        await make_user(setup, 9962)
        broadcast = await create_broadcast(setup, actor, "hi", BroadcastAudience.ALL)
        await setup.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)

    assert sorted(bot.sent) == [9961, 9962]
    async with db_factory() as check:
        row = await check.get(Broadcast, broadcast_id)
        assert row.status == BroadcastStatus.COMPLETED
        assert row.sent_count == 2
        assert row.total_recipients == 2
        assert row.completed_at is not None


async def test_a_user_who_blocked_the_bot_is_counted_separately(db_factory):
    """Churn, not a delivery fault — and not a reason to call the send failed."""
    async with db_factory() as setup:
        actor = await make_user(setup, 9963)
        await make_user(setup, 9964)
        broadcast = await create_broadcast(setup, actor, "hi", BroadcastAudience.ALL)
        await setup.commit()
        broadcast_id = broadcast.id

    await run_broadcast(db_factory, FakeBot(blocked={9964}), broadcast_id)

    async with db_factory() as check:
        row = await check.get(Broadcast, broadcast_id)
        assert row.status == BroadcastStatus.COMPLETED
        assert (row.sent_count, row.blocked_count, row.failed_count) == (1, 1, 0)


async def test_a_completed_broadcast_cannot_be_run_again(db_factory):
    async with db_factory() as setup:
        actor = await make_user(setup, 9965)
        broadcast = await create_broadcast(setup, actor, "hi", BroadcastAudience.ALL)
        await setup.commit()
        broadcast_id = broadcast.id

    first = FakeBot()
    await run_broadcast(db_factory, first, broadcast_id)
    second = FakeBot()
    await run_broadcast(db_factory, second, broadcast_id)

    assert len(first.sent) == 1
    assert second.sent == [], "a finished broadcast must never send again"


async def test_two_workers_racing_send_it_once(db_factory):
    """
    The regression test for the whole design. Without the FOR UPDATE claim
    both readers see PENDING and every user receives the message twice.
    """
    async with db_factory() as setup:
        actor = await make_user(setup, 9966)
        for telegram_id in range(9967, 9970):
            await make_user(setup, telegram_id)
        broadcast = await create_broadcast(setup, actor, "hi", BroadcastAudience.ALL)
        await setup.commit()
        broadcast_id = broadcast.id

    bots = [FakeBot(), FakeBot()]
    await asyncio.gather(*(run_broadcast(db_factory, bot, broadcast_id) for bot in bots))

    delivered = bots[0].sent + bots[1].sent
    assert len(delivered) == 4, f"each user must receive it once: {delivered}"
    assert len(set(delivered)) == 4


async def test_a_premium_broadcast_skips_everyone_else(db_factory):
    async with db_factory() as setup:
        actor = await make_user(setup, 9971)
        subscriber = await make_user(setup, 9972)
        await _premium(setup, subscriber)
        broadcast = await create_broadcast(setup, actor, "hi", BroadcastAudience.PREMIUM)
        await setup.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)
    assert bot.sent == [9972]


async def test_the_row_never_stays_sending_after_a_crash(db_factory):
    """A stuck SENDING row would block the retry that the claim guard allows."""
    async with db_factory() as setup:
        actor = await make_user(setup, 9973)
        broadcast = await create_broadcast(setup, actor, "hi", BroadcastAudience.ALL)
        await setup.commit()
        broadcast_id = broadcast.id

    class ExplodingBot:
        async def send_message(self, *args, **kwargs):
            raise RuntimeError("network is gone")

    await run_broadcast(db_factory, ExplodingBot(), broadcast_id)

    async with db_factory() as check:
        row = await check.get(Broadcast, broadcast_id)
        assert row.status == BroadcastStatus.FAILED
        assert row.error


async def test_the_history_reports_counts_not_recipients(db_factory):
    """Nothing in the stored record identifies who was messaged."""
    async with db_factory() as setup:
        actor = await make_user(setup, 9974)
        broadcast = await create_broadcast(setup, actor, "hi", BroadcastAudience.ALL)
        await setup.commit()
        broadcast_id = broadcast.id

    await run_broadcast(db_factory, FakeBot(), broadcast_id)

    async with db_factory() as check:
        row = (
            await check.execute(select(Broadcast).where(Broadcast.id == broadcast_id))
        ).scalar_one()
        assert not hasattr(row, "recipients")
        assert row.sent_count == 1
