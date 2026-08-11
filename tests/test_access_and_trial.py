"""
Who may watch what, and the new-user trial.

Two rules decide every case, and their *order* is the product decision:

  1. An active subscription outranks channel membership. A paying viewer
     is never also asked to join a channel.
  2. A premium title is not unlocked by membership. That is the entire
     meaning of the flag.

Both surfaces ask the same function, so the matrix below is the whole
policy for the bot and the Mini App alike. The bypass tests matter most:
a client can hide a button, but the refusal has to happen on the server.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models.content import ContentType, Title
from app.db.models.user import Subscription, SubscriptionPlan, User, UserRole
from app.services.access import AccessDecision, check_title_access
from app.services.settings_store import (
    REQUIRE_MEMBERSHIP,
    REQUIRED_CHANNEL,
    get_trial_config,
    set_setting,
    set_trial_config,
)
from app.services.trial import grant_trial_if_eligible
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


@pytest.fixture(autouse=True)
def offline_membership(monkeypatch):
    """
    Answers the membership question from the FakeBot, without Redis.

    `is_channel_member` is Redis-backed, and `REDIS_URL` points at the
    real hosted instance — so exercising it here both reached production
    infrastructure and let a cached *yes* survive between runs, making a
    later non-member look like a member. That is a false pass on an access
    gate, the worst kind to have.

    The cache itself is pre-existing and covered elsewhere; what these
    tests are for is the *policy* in `check_title_access`. So the lookup is
    replaced with the FakeBot's own answer and the decision logic is
    exercised in full.
    """
    from app.services import membership as membership_module

    async def offline(bot, channel, telegram_id):
        return bot.member

    # The same seam the rest of the suite patches, so this file cannot
    # drift from the convention or quietly stop taking effect.
    monkeypatch.setattr(membership_module, "is_channel_member", offline)


class FakeBot:
    """Stands in for Telegram's membership answer."""

    def __init__(self, member: bool = True):
        self.member = member
        self.asked = 0

    async def get_chat_member(self, chat_id, user_id):
        self.asked += 1

        class Result:
            status = "member" if self.member else "left"

        return Result()


async def _title(session, name: str, *, premium: bool) -> Title:
    title = Title(
        name=name, content_type=ContentType.FILM, is_active=True, is_premium=premium
    )
    session.add(title)
    await session.flush()
    return title


async def _require_channel(session, user: User) -> None:
    await set_setting(session, REQUIRE_MEMBERSHIP, "true")
    await set_setting(session, REQUIRED_CHANNEL, f"@chan{user.telegram_id}")


async def _subscribe(session, user: User, *, days: int) -> Subscription:
    now = datetime.now(timezone.utc)
    sub = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.PREMIUM,
        started_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=days),
    )
    session.add(sub)
    await session.flush()
    return sub


# ---------- free titles ----------


async def test_a_channel_member_can_watch_a_free_title(db_session):
    user = await make_user(db_session, 9901)
    await _require_channel(db_session, user)
    title = await _title(db_session, "Free", premium=False)

    result = await check_title_access(db_session, FakeBot(member=True), user, title)
    assert result.decision is AccessDecision.ALLOWED


async def test_a_non_member_cannot_watch_a_free_title(db_session):
    user = await make_user(db_session, 9902)
    await _require_channel(db_session, user)
    title = await _title(db_session, "Free", premium=False)

    result = await check_title_access(db_session, FakeBot(member=False), user, title)
    assert result.decision is AccessDecision.NEEDS_MEMBERSHIP


async def test_with_no_channel_configured_free_titles_are_open(db_session):
    user = await make_user(db_session, 9903)
    title = await _title(db_session, "Free", premium=False)

    result = await check_title_access(db_session, FakeBot(member=False), user, title)
    assert result.decision is AccessDecision.ALLOWED


# ---------- premium titles ----------


async def test_a_channel_member_without_a_subscription_cannot_watch_a_premium_title(db_session):
    """Membership must never unlock paid content — the whole point of the flag."""
    user = await make_user(db_session, 9904)
    await _require_channel(db_session, user)
    title = await _title(db_session, "Paid", premium=True)

    result = await check_title_access(db_session, FakeBot(member=True), user, title)
    assert result.decision is AccessDecision.NEEDS_PREMIUM


async def test_a_subscriber_can_watch_a_premium_title(db_session):
    user = await make_user(db_session, 9905)
    await _require_channel(db_session, user)
    await _subscribe(db_session, user, days=30)
    title = await _title(db_session, "Paid", premium=True)

    result = await check_title_access(db_session, FakeBot(member=False), user, title)
    assert result.decision is AccessDecision.ALLOWED


async def test_a_subscription_excuses_channel_membership(db_session):
    """
    The priority rule. A paying viewer is not also asked to join, and the
    membership question is never even put to Telegram.
    """
    user = await make_user(db_session, 9906)
    await _require_channel(db_session, user)
    await _subscribe(db_session, user, days=30)
    title = await _title(db_session, "Free", premium=False)
    bot = FakeBot(member=False)

    result = await check_title_access(db_session, bot, user, title)

    assert result.decision is AccessDecision.ALLOWED
    assert bot.asked == 0, "a subscriber should not be asked about membership at all"


# ---------- expiry ----------


async def test_an_expired_subscription_no_longer_unlocks_premium(db_session):
    user = await make_user(db_session, 9907)
    title = await _title(db_session, "Paid", premium=True)
    now = datetime.now(timezone.utc)
    db_session.add(
        Subscription(
            user_id=user.id,
            plan=SubscriptionPlan.PREMIUM,
            started_at=now - timedelta(days=40),
            expires_at=now - timedelta(days=1),
        )
    )
    await db_session.flush()

    result = await check_title_access(db_session, FakeBot(member=True), user, title)
    assert result.decision is AccessDecision.NEEDS_PREMIUM


async def test_an_expired_subscriber_falls_back_to_needing_membership(db_session):
    """Lapsing returns them to the ordinary free-content rules, not to nothing."""
    user = await make_user(db_session, 9908)
    await _require_channel(db_session, user)
    title = await _title(db_session, "Free", premium=False)
    now = datetime.now(timezone.utc)
    db_session.add(
        Subscription(
            user_id=user.id,
            plan=SubscriptionPlan.PREMIUM,
            started_at=now - timedelta(days=40),
            expires_at=now - timedelta(days=1),
        )
    )
    await db_session.flush()

    # Non-member first: only a positive answer is cached, so asserting the
    # member case first would carry a yes into the second call.
    assert (
        await check_title_access(db_session, FakeBot(member=False), user, title)
    ).decision is AccessDecision.NEEDS_MEMBERSHIP
    assert (
        await check_title_access(db_session, FakeBot(member=True), user, title)
    ).decision is AccessDecision.ALLOWED


async def test_a_subscription_expiring_in_a_second_still_counts(db_session):
    user = await make_user(db_session, 9909)
    title = await _title(db_session, "Paid", premium=True)
    now = datetime.now(timezone.utc)
    db_session.add(
        Subscription(
            user_id=user.id,
            plan=SubscriptionPlan.PREMIUM,
            started_at=now - timedelta(days=1),
            expires_at=now + timedelta(seconds=30),
        )
    )
    await db_session.flush()

    result = await check_title_access(db_session, FakeBot(member=False), user, title)
    assert result.decision is AccessDecision.ALLOWED


async def test_an_admin_passes_regardless(db_session):
    """An operator has to be able to check the file they just uploaded."""
    admin = await make_user(db_session, 9910)
    admin.role = UserRole.ADMIN
    await _require_channel(db_session, admin)
    title = await _title(db_session, "Paid", premium=True)

    result = await check_title_access(db_session, FakeBot(member=False), admin, title)
    assert result.decision is AccessDecision.ALLOWED


async def test_two_users_are_decided_independently(db_session):
    subscriber = await make_user(db_session, 9911)
    plain = await make_user(db_session, 9912)
    await _subscribe(db_session, subscriber, days=30)
    title = await _title(db_session, "Paid", premium=True)

    assert (
        await check_title_access(db_session, FakeBot(member=True), subscriber, title)
    ).decision is AccessDecision.ALLOWED
    assert (
        await check_title_access(db_session, FakeBot(member=True), plain, title)
    ).decision is AccessDecision.NEEDS_PREMIUM


# ---------- trial ----------


async def test_the_trial_is_off_until_configured(db_session):
    config = await get_trial_config(db_session)
    assert config.enabled is False


async def test_a_new_user_receives_the_trial_when_enabled(db_session):
    await set_trial_config(db_session, enabled=True, days=3)
    user = await make_user(db_session, 9920)

    trial = await grant_trial_if_eligible(db_session, user)

    assert trial is not None
    delta = trial.expires_at - trial.started_at
    assert 2 < delta.days + delta.seconds / 86400 <= 3.01


async def test_a_new_user_receives_nothing_when_the_trial_is_off(db_session):
    await set_trial_config(db_session, enabled=False, days=3)
    user = await make_user(db_session, 9921)

    assert await grant_trial_if_eligible(db_session, user) is None


async def test_the_trial_is_granted_only_once(db_session):
    """Repeated /start must not renew it."""
    await set_trial_config(db_session, enabled=True, days=3)
    user = await make_user(db_session, 9922)

    assert await grant_trial_if_eligible(db_session, user) is not None
    assert await grant_trial_if_eligible(db_session, user) is None
    assert await grant_trial_if_eligible(db_session, user) is None


async def test_someone_who_already_bought_a_plan_gets_no_trial(db_session):
    """Not a new user, whatever the subscription's origin."""
    await set_trial_config(db_session, enabled=True, days=3)
    user = await make_user(db_session, 9923)
    await _subscribe(db_session, user, days=30)

    assert await grant_trial_if_eligible(db_session, user) is None


async def test_someone_whose_subscription_expired_gets_no_second_trial(db_session):
    await set_trial_config(db_session, enabled=True, days=3)
    user = await make_user(db_session, 9924)
    now = datetime.now(timezone.utc)
    db_session.add(
        Subscription(
            user_id=user.id,
            plan=SubscriptionPlan.PREMIUM,
            started_at=now - timedelta(days=40),
            expires_at=now - timedelta(days=10),
        )
    )
    await db_session.flush()

    assert await grant_trial_if_eligible(db_session, user) is None


async def test_the_trial_grants_premium_access(db_session):
    """It is a real subscription, so it unlocks premium titles like one."""
    await set_trial_config(db_session, enabled=True, days=3)
    user = await make_user(db_session, 9925)
    await grant_trial_if_eligible(db_session, user)
    title = await _title(db_session, "Paid", premium=True)

    result = await check_title_access(db_session, FakeBot(member=False), user, title)
    assert result.decision is AccessDecision.ALLOWED


async def test_a_duration_change_does_not_touch_existing_trials(db_session):
    await set_trial_config(db_session, enabled=True, days=3)
    user = await make_user(db_session, 9926)
    trial = await grant_trial_if_eligible(db_session, user)
    original = trial.expires_at

    await set_trial_config(db_session, enabled=True, days=30)
    await db_session.refresh(trial)

    assert trial.expires_at == original


async def test_a_nonsense_duration_falls_back_rather_than_granting_forever(db_session):
    await set_setting(db_session, "trial_enabled", "true")
    await set_setting(db_session, "trial_days", "not-a-number")

    config = await get_trial_config(db_session)
    assert config.enabled is True
    assert config.days == 3


async def test_the_duration_is_clamped(db_session):
    await set_trial_config(db_session, enabled=True, days=99999)
    assert (await get_trial_config(db_session)).days == 365

    await set_trial_config(db_session, enabled=True, days=0)
    assert (await get_trial_config(db_session)).days == 1


async def test_two_simultaneous_signups_grant_one_trial(db_factory):
    """/start twice in the same instant is two concurrent transactions."""
    import asyncio

    async with db_factory() as setup:
        await set_trial_config(setup, enabled=True, days=3)
        user = await make_user(setup, 9930)
        user_id = user.id
        await setup.commit()

    async def attempt():
        async with db_factory() as session:
            target = await session.get(User, user_id)
            granted = await grant_trial_if_eligible(session, target)
            await session.commit()
            return granted is not None

    results = await asyncio.gather(attempt(), attempt())

    assert results.count(True) == 1, results
    async with db_factory() as check:
        from sqlalchemy import func, select

        count = (
            await check.execute(
                select(func.count()).select_from(Subscription).where(
                    Subscription.user_id == user_id
                )
            )
        ).scalar_one()
        assert count == 1
