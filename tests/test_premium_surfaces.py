"""
Premium enforcement and movie codes at the *surfaces*.

`tests/test_access_and_trial.py` pins the policy inside
`check_title_access`. This file pins the thing a policy cannot guarantee
on its own: that every way into a file actually asks it, and that the
admin panel can switch the flag the policy reads.

The distinction matters because both bugs this phase fixed were of that
second kind. The rule was right and the surfaces were wrong — one
delivery path skipped the check for an unrecognised viewer, and no API
could set `is_premium` at all, so the gate guarded a flag nobody could
turn on.

The bypass tests are the point. A client can hide a padlock; only the
server can refuse.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.auth import get_current_user
from app.db.models.content import (
    AudioLanguage,
    ContentType,
    Episode,
    MediaFile,
    Title,
    VideoQuality,
)
from app.db.models.user import Subscription, SubscriptionPlan, User, UserRole
from app.db.session import get_db_session
from app.main import app
from app.services.access import unlocks_premium
from app.services.settings_store import (
    REQUIRE_MEMBERSHIP,
    REQUIRED_CHANNEL,
    set_setting,
)
from tests.conftest import make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]


@pytest.fixture(autouse=True)
def offline_membership(monkeypatch):
    """Same seam the rest of the suite patches — never touches Redis or Telegram."""
    from app.services import membership as membership_module

    async def offline(bot, channel, telegram_id):
        return True

    monkeypatch.setattr(membership_module, "is_channel_member", offline)


async def _playable(session, name: str, *, premium: bool) -> tuple[Title, Episode]:
    """A title with a real file behind it, so delivery is reachable."""
    title = Title(
        name=name, content_type=ContentType.FILM, is_active=True, is_premium=premium
    )
    session.add(title)
    await session.flush()
    episode = Episode(title_id=title.id, season=1, number=1)
    session.add(episode)
    await session.flush()
    session.add(
        MediaFile(
            episode_id=episode.id,
            file_id=f"file{episode.id}",
            language=AudioLanguage.UZ_DUB,
            quality=VideoQuality.HD_720,
        )
    )
    await session.flush()
    return title, episode


async def _subscribe(session, user: User, *, days: int) -> Subscription:
    """`days` may be negative — that is how an expired subscription is built."""
    now = datetime.now(timezone.utc)
    sub = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.PREMIUM,
        started_at=now - timedelta(days=30),
        expires_at=now + timedelta(days=days),
    )
    session.add(sub)
    await session.flush()
    return sub


@pytest.fixture
def as_user(db_session):
    """An HTTP client authenticated as `user` — identity never comes from the request body."""

    def _install(user) -> AsyncClient:
        async def override_session():
            yield db_session

        async def override_user():
            return user

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _install
    app.dependency_overrides.clear()


# ---------- the viewer API exposes the new fields ----------


async def test_a_card_carries_its_code_and_premium_state(db_session, as_user):
    user = await make_user(db_session, 9601)
    title, _ = await _playable(db_session, "Paid", premium=True)
    title.code = "4242"
    await db_session.flush()
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.get(f"/api/movies/{title.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "4242"
    assert body["is_premium"] is True
    # No subscription, so this viewer sees it locked.
    assert body["is_locked"] is True


async def test_a_subscriber_sees_a_premium_title_unlocked(db_session, as_user):
    user = await make_user(db_session, 9602)
    await _subscribe(db_session, user, days=30)
    title, _ = await _playable(db_session, "Paid", premium=True)
    await db_session.commit()

    async with as_user(user) as client:
        body = (await client.get(f"/api/movies/{title.id}")).json()

    assert body["is_premium"] is True
    assert body["is_locked"] is False


async def test_a_free_title_is_never_locked(db_session, as_user):
    user = await make_user(db_session, 9603)
    title, _ = await _playable(db_session, "Free", premium=False)
    await db_session.commit()

    async with as_user(user) as client:
        body = (await client.get(f"/api/movies/{title.id}")).json()

    assert body["is_premium"] is False
    assert body["is_locked"] is False


async def test_an_expired_subscriber_sees_it_locked_again(db_session, as_user):
    """No sweep, no cached flag — expiry is read from the row on every request."""
    user = await make_user(db_session, 9604)
    await _subscribe(db_session, user, days=-1)
    title, _ = await _playable(db_session, "Paid", premium=True)
    await db_session.commit()

    async with as_user(user) as client:
        body = (await client.get(f"/api/movies/{title.id}")).json()

    assert body["is_locked"] is True


# ---------- code search, both surfaces, same lookup ----------


async def test_code_search_finds_the_title_through_the_api(db_session, as_user):
    user = await make_user(db_session, 9605)
    title, _ = await _playable(db_session, "Findable", premium=False)
    title.code = "5150"
    await db_session.flush()
    await db_session.commit()

    async with as_user(user) as client:
        results = (await client.get("/api/movies/search", params={"q": "5150"})).json()

    assert [item["id"] for item in results] == [title.id]


async def test_a_nonexistent_code_falls_back_to_name_search(db_session, as_user):
    """A title genuinely called "1984" must stay findable."""
    user = await make_user(db_session, 9606)
    title, _ = await _playable(db_session, "1984", premium=False)
    await db_session.commit()

    async with as_user(user) as client:
        results = (await client.get("/api/movies/search", params={"q": "1984"})).json()

    assert [item["id"] for item in results] == [title.id]


async def test_code_search_still_reports_a_premium_title_as_locked(db_session, as_user):
    """
    Found, but not opened. Hiding it would be worse product *and* worse
    security theatre: the viewer should see what a subscription would buy,
    and the refusal belongs on the delivery path either way.
    """
    user = await make_user(db_session, 9607)
    title, _ = await _playable(db_session, "Paid", premium=True)
    title.code = "7007"
    await db_session.flush()
    await db_session.commit()

    async with as_user(user) as client:
        results = (await client.get("/api/movies/search", params={"q": "7007"})).json()

    assert len(results) == 1
    assert results[0]["is_locked"] is True


# ---------- the gate itself ----------


async def test_watch_is_refused_for_a_premium_title_without_a_subscription(db_session, as_user):
    """The direct API call, with no client involved at all."""
    user = await make_user(db_session, 9608)
    title, episode = await _playable(db_session, "Paid", premium=True)
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.post(
            f"/api/movies/{title.id}/watch", json={"episode_id": episode.id}
        )

    assert response.status_code == 403


async def test_watch_is_refused_after_the_subscription_expires(db_session, as_user):
    user = await make_user(db_session, 9609)
    await _subscribe(db_session, user, days=-1)
    title, episode = await _playable(db_session, "Paid", premium=True)
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.post(
            f"/api/movies/{title.id}/watch", json={"episode_id": episode.id}
        )

    assert response.status_code == 403


async def test_a_forged_user_id_in_the_body_changes_nothing(db_session, as_user):
    """
    Identity comes from verified initData, never from the request.

    The body below names a subscriber; the caller is not one. If the
    endpoint ever grew a `user_id` parameter this test is what fails.
    """
    subscriber = await make_user(db_session, 9610)
    await _subscribe(db_session, subscriber, days=30)
    attacker = await make_user(db_session, 9611)
    title, episode = await _playable(db_session, "Paid", premium=True)
    await db_session.commit()

    async with as_user(attacker) as client:
        response = await client.post(
            f"/api/movies/{title.id}/watch",
            json={
                "episode_id": episode.id,
                "user_id": subscriber.id,
                "telegram_id": subscriber.telegram_id,
                "is_premium": True,
                "is_locked": False,
            },
        )

    assert response.status_code == 403


async def test_a_subscriber_may_watch_a_premium_title(db_session, as_user, monkeypatch):
    """The other half — the gate must not refuse someone who paid."""
    delivered: list[int] = []

    async def fake_deliver(**kwargs):
        delivered.append(kwargs["episode"].id)

        class Sent:
            message_id = 1

        return Sent()

    from app.services import streaming as streaming_module

    monkeypatch.setattr(streaming_module.streaming_service, "deliver_episode", fake_deliver)

    user = await make_user(db_session, 9612)
    await _subscribe(db_session, user, days=30)
    title, episode = await _playable(db_session, "Paid", premium=True)
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.post(
            f"/api/movies/{title.id}/watch", json={"episode_id": episode.id}
        )

    assert response.status_code == 200
    assert delivered == [episode.id]


# ---------- a subscription excuses the channel requirement ----------


async def test_a_subscriber_is_not_also_asked_to_join_the_channel(db_session, monkeypatch):
    """
    Charging someone and then still gating them behind a join is how you
    earn a refund request. Membership is answered "no" here; the
    subscription must carry them anyway.
    """
    from app.services import membership as membership_module

    asked = []

    async def refuse(bot, channel, telegram_id):
        asked.append(telegram_id)
        return False

    monkeypatch.setattr(membership_module, "is_channel_member", refuse)

    user = await make_user(db_session, 9613)
    await _subscribe(db_session, user, days=30)
    await set_setting(db_session, REQUIRE_MEMBERSHIP, "true")
    await set_setting(db_session, REQUIRED_CHANNEL, "@chan")

    assert await unlocks_premium(db_session, user) is True


async def test_an_unpaid_user_still_follows_the_channel_rule(db_session, monkeypatch):
    """The membership policy must not regress for everyone else."""
    from app.services import membership as membership_module
    from app.services.access import AccessDecision, check_title_access

    async def refuse(bot, channel, telegram_id):
        return False

    monkeypatch.setattr(membership_module, "is_channel_member", refuse)

    user = await make_user(db_session, 9614)
    await set_setting(db_session, REQUIRE_MEMBERSHIP, "true")
    await set_setting(db_session, REQUIRED_CHANNEL, "@chan")
    title, _ = await _playable(db_session, "Free", premium=False)

    result = await check_title_access(db_session, None, user, title)
    assert result.decision is AccessDecision.NEEDS_MEMBERSHIP


# ---------- the bot's delivery chokepoint ----------


class RecordingBot:
    """Captures what the bot would have said, and answers membership yes."""

    def __init__(self):
        self.messages: list[str] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append(text)
        return None


@pytest.fixture
def no_delivery(monkeypatch):
    """Records delivery attempts instead of calling Telegram."""
    sent: list[int] = []

    async def fake_deliver(**kwargs):
        sent.append(kwargs["episode"].id)

        class Sent:
            async def answer(self, *args, **kwargs):
                return None

        return Sent()

    from app.services import streaming as streaming_module

    monkeypatch.setattr(streaming_module.streaming_service, "deliver_episode", fake_deliver)
    return sent


async def test_the_bot_refuses_a_premium_title_to_a_non_subscriber(db_session, no_delivery):
    from app.bot.handlers.streaming import deliver_and_warn

    user = await make_user(db_session, 9619)
    title, episode = await _playable(db_session, "Paid", premium=True)
    await db_session.flush()

    bot = RecordingBot()
    result = await deliver_and_warn(
        bot, db_session, user.telegram_id, user.telegram_id, episode,
        user.language, lambda key, **kw: key,
    )

    assert result is None
    assert no_delivery == []
    assert bot.messages == ["access.premium_required"]


async def test_the_bot_delivers_a_premium_title_to_a_subscriber(db_session, no_delivery):
    from app.bot.handlers.streaming import deliver_and_warn

    user = await make_user(db_session, 9620)
    await _subscribe(db_session, user, days=30)
    title, episode = await _playable(db_session, "Paid", premium=True)
    await db_session.flush()

    bot = RecordingBot()
    await deliver_and_warn(
        bot, db_session, user.telegram_id, user.telegram_id, episode,
        user.language, lambda key, **kw: key,
    )

    assert no_delivery == [episode.id]


async def test_delivery_fails_closed_for_an_unrecognised_viewer(db_session, no_delivery):
    """
    The bypass this phase fixed.

    The gate previously ran only `if viewer is not None`, so a telegram_id
    with no row — a deleted account mid-session, or any future path that
    reaches delivery before provisioning — skipped the check entirely and
    was handed the file. An identity we cannot resolve is one whose
    subscription we cannot verify, and the safe answer to that is no.
    """
    from app.bot.handlers.streaming import deliver_and_warn

    title, episode = await _playable(db_session, "Paid", premium=True)
    await db_session.flush()

    bot = RecordingBot()
    result = await deliver_and_warn(
        bot, db_session, 999_000_111, 999_000_111, episode,
        "uz", lambda key, **kw: key,
    )

    assert result is None
    assert no_delivery == [], "an unknown viewer must never receive a file"
    assert bot.messages == ["common.need_start"]


# ---------- the two surfaces agree about what a code does ----------


class CardBot:
    """Captures the card a code search would render."""

    def __init__(self):
        self.captions: list[str] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.captions.append(text)


async def test_a_code_for_a_locked_title_still_shows_the_card(db_session):
    """
    The bot shows it with the reason attached, exactly as the Mini App
    draws a padlock rather than hiding the row.

    This handler previously answered a bare refusal and rendered nothing,
    which disagreed with the Mini App about what a code does and also
    contradicted the membership policy, which gates delivery and leaves
    the catalog open. Showing it loosens nothing — the card carries no
    file, and the watch button is gated again on the way to delivery.
    """
    from app.bot.handlers.catalog import handle_movie_code

    user = await make_user(db_session, 9621)
    title, _episode = await _playable(db_session, "Paid", premium=True)
    title.code = "6006"
    await db_session.flush()

    sent: list[str] = []

    class FakeMessage:
        text = "6006"
        bot = None
        from_user = type("U", (), {"id": user.telegram_id})()

        async def answer(self, text, **kwargs):
            sent.append(text)

        async def answer_photo(self, photo, caption="", **kwargs):
            sent.append(caption)

    await handle_movie_code(FakeMessage(), db_session, user.language, lambda key, **kw: key)

    assert len(sent) == 1
    # The card was rendered, and the refusal reason came with it.
    assert "access.premium_required" in sent[0]


# ---------- admin can actually operate the flag ----------


@pytest.fixture
def as_admin(db_session):
    from app.api.admin import get_current_admin

    def _install(user) -> AsyncClient:
        async def override_session():
            yield db_session

        async def override_user():
            return user

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_current_admin] = override_user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _install
    app.dependency_overrides.clear()


async def test_an_admin_can_mark_a_title_premium_and_unmark_it(db_session, as_admin):
    """
    The gap this phase closed: the column and its enforcement shipped with
    no API able to set either, so premium could never be switched on.
    """
    admin = await make_user(db_session, 9615)
    admin.role = UserRole.SUPER_ADMIN
    title, _ = await _playable(db_session, "Film", premium=False)
    await db_session.commit()

    async with as_admin(admin) as client:
        on = await client.patch(f"/api/admin/titles/{title.id}", json={"is_premium": True})
        assert on.status_code == 200
        assert on.json()["is_premium"] is True

        off = await client.patch(f"/api/admin/titles/{title.id}", json={"is_premium": False})
        assert off.status_code == 200
        assert off.json()["is_premium"] is False


async def test_the_admin_title_response_carries_the_code(db_session, as_admin):
    admin = await make_user(db_session, 9616)
    admin.role = UserRole.SUPER_ADMIN
    title, _ = await _playable(db_session, "Film", premium=False)
    title.code = "3003"
    await db_session.flush()
    await db_session.commit()

    async with as_admin(admin) as client:
        body = (await client.patch(f"/api/admin/titles/{title.id}", json={})).json()

    assert body["code"] == "3003"


async def test_reassigning_a_code_to_a_taken_one_is_refused(db_session, as_admin):
    """A collision must be a 409, not a 500 from the unique index."""
    admin = await make_user(db_session, 9617)
    admin.role = UserRole.SUPER_ADMIN
    first, _ = await _playable(db_session, "First", premium=False)
    first.code = "8001"
    second, _ = await _playable(db_session, "Second", premium=False)
    second.code = "8002"
    await db_session.flush()
    await db_session.commit()

    async with as_admin(admin) as client:
        response = await client.patch(f"/api/admin/titles/{second.id}", json={"code": "8001"})

    assert response.status_code == 409


async def test_the_admin_list_filters_by_premium(db_session, as_admin):
    """
    Server-side, because the list is paged: filtering in the client would
    leave `total` and the page count describing a different set than the
    one on screen, and would get slower as the catalog grows.
    """
    admin = await make_user(db_session, 9622)
    admin.role = UserRole.SUPER_ADMIN
    paid, _ = await _playable(db_session, "Paid", premium=True)
    free, _ = await _playable(db_session, "Free", premium=False)
    await db_session.commit()

    async with as_admin(admin) as client:
        everything = (await client.get("/api/admin/titles")).json()
        premium_only = (
            await client.get("/api/admin/titles", params={"is_premium": "true"})
        ).json()
        free_only = (
            await client.get("/api/admin/titles", params={"is_premium": "false"})
        ).json()

    assert {item["id"] for item in everything["items"]} >= {paid.id, free.id}
    assert [item["id"] for item in premium_only["items"]] == [paid.id]
    assert [item["id"] for item in free_only["items"]] == [free.id]
    # The count must describe the filtered set, not the whole catalog —
    # otherwise the pager offers pages that do not exist.
    assert premium_only["total"] == 1
    assert free_only["total"] == 1


async def test_the_premium_filter_composes_with_search(db_session, as_admin):
    """Filters narrow together; one must not discard the other."""
    admin = await make_user(db_session, 9623)
    admin.role = UserRole.SUPER_ADMIN
    wanted, _ = await _playable(db_session, "Dune Premium", premium=True)
    await _playable(db_session, "Dune Free", premium=False)
    await _playable(db_session, "Other Premium", premium=True)
    await db_session.commit()

    async with as_admin(admin) as client:
        page = (
            await client.get(
                "/api/admin/titles", params={"q": "Dune", "is_premium": "true"}
            )
        ).json()

    assert [item["id"] for item in page["items"]] == [wanted.id]
    assert page["total"] == 1


async def test_the_admin_list_carries_the_code_and_premium_state(db_session, as_admin):
    """So an operator can read both without opening every editor."""
    admin = await make_user(db_session, 9624)
    admin.role = UserRole.SUPER_ADMIN
    title, _ = await _playable(db_session, "Listed", premium=True)
    title.code = "9119"
    await db_session.flush()
    await db_session.commit()

    async with as_admin(admin) as client:
        items = (await client.get("/api/admin/titles", params={"q": "Listed"})).json()["items"]

    assert items[0]["code"] == "9119"
    assert items[0]["is_premium"] is True


async def test_an_ordinary_user_cannot_list_titles_with_the_filter(db_session, as_user):
    """The new parameter must not become a way around the permission gate."""
    user = await make_user(db_session, 9625)
    await _playable(db_session, "Paid", premium=True)
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.get("/api/admin/titles", params={"is_premium": "true"})

    assert response.status_code in (401, 403)


# ---------- the bot's conversion path ----------


async def test_a_locked_card_offers_the_subscribe_button(db_session):
    """
    The bot's dead end, closed.

    Watch is *replaced*, not accompanied: for this viewer it could only
    ever be refused, and a button whose one outcome is an error is worse
    than no button.
    """
    from app.bot.keyboards.catalog import SUBSCRIBE_CALLBACK, get_title_card_keyboard

    markup = get_title_card_keyboard(1, "uz", is_favorite=False, locked=True)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert SUBSCRIBE_CALLBACK in callbacks
    assert not any(cb.startswith("ttl:") for cb in callbacks), "Watch must not survive on a locked card"


async def test_an_unlocked_card_keeps_the_watch_button(db_session):
    from app.bot.keyboards.catalog import SUBSCRIBE_CALLBACK, get_title_card_keyboard

    markup = get_title_card_keyboard(1, "uz", is_favorite=False, locked=False)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert any(cb.startswith("ttl:") for cb in callbacks)
    assert SUBSCRIBE_CALLBACK not in callbacks


async def test_the_bot_card_is_locked_only_without_entitlement(db_session):
    """The card's lock mirrors the gate — one rule, asked the same way."""
    from app.services.access import unlocks_premium_by_id

    plain = await make_user(db_session, 9627)
    subscriber = await make_user(db_session, 9628)
    await _subscribe(db_session, subscriber, days=30)
    await db_session.flush()

    assert await unlocks_premium_by_id(db_session, plain.id) is False
    assert await unlocks_premium_by_id(db_session, subscriber.id) is True
    # Nobody at all — a card built before /start must not read as unlocked.
    assert await unlocks_premium_by_id(db_session, None) is False


async def test_an_expired_subscriber_sees_the_locked_card_again(db_session):
    from app.services.access import unlocks_premium_by_id

    user = await make_user(db_session, 9629)
    await _subscribe(db_session, user, days=-1)
    await db_session.flush()

    assert await unlocks_premium_by_id(db_session, user.id) is False


async def test_the_subscribe_button_carries_no_title_id(db_session):
    """
    A subscription is not sold per film.

    If this ever carries an id, someone has begun building a second
    purchase concept — one that would need its own price, its own
    entitlement and its own refund story.
    """
    from app.bot.keyboards.catalog import SUBSCRIBE_CALLBACK

    assert ":" not in SUBSCRIBE_CALLBACK


async def test_an_active_subscriber_tapping_a_stale_card_is_not_sold_a_second_plan(db_session):
    """
    Money safety on the one path that can still reach the button.

    A subscriber's card is not built locked, so they get here only from a
    card scrolled back to — and that must not walk them into paying twice.
    No receipt may be created.
    """
    from app.bot.handlers.payment import handle_subscribe_from_card
    from app.db.models.payment import PaymentReceipt

    user = await make_user(db_session, 9630)
    await _subscribe(db_session, user, days=30)
    await db_session.flush()

    answered: list[str] = []

    class FakeCallback:
        data = "subscribe"
        from_user = type("U", (), {"id": user.telegram_id})()
        message = None

        async def answer(self, text=None, show_alert=False):
            answered.append(text or "")

    class FakeState:
        async def update_data(self, **kwargs):
            raise AssertionError("an active subscriber must not enter the payment flow")

        async def set_state(self, *args):
            raise AssertionError("an active subscriber must not enter the payment flow")

    await handle_subscribe_from_card(
        FakeCallback(), FakeState(), db_session, user.language, lambda key, **kw: key
    )

    assert answered == ["payment.already_subscribed"]
    receipts = (
        await db_session.execute(
            select(PaymentReceipt).where(PaymentReceipt.user_id == user.id)
        )
    ).scalars().all()
    assert receipts == []


async def test_a_subscriber_can_still_buy_deliberately_from_the_premium_menu(db_session):
    """
    The stale-card guard must not become "subscribers may never pay again".

    Extending or upgrading is a real purchase the Mini App already offers,
    so blocking it everywhere would both cost revenue and put the two
    surfaces in disagreement. The guard is scoped to the *card* button —
    an accidental tap on something that says "locked" when it is not —
    while the Premium menu, which is unambiguous intent, is untouched.
    """
    from app.bot.handlers.payment import handle_premium_start

    user = await make_user(db_session, 9631)
    await _subscribe(db_session, user, days=30)
    await db_session.flush()

    reached: list[str] = []

    class FakeMessage:
        from_user = type("U", (), {"id": user.telegram_id})()

        async def answer(self, text, **kwargs):
            reached.append(text)

    class FakeState:
        async def update_data(self, **kwargs):
            reached.append("entered-payment-flow")

        async def set_state(self, *args):
            return None

    await handle_premium_start(
        FakeMessage(), FakeState(), db_session, user.language, lambda key, **kw: key
    )

    # Either it entered the flow, or it stopped for a reason unrelated to
    # entitlement (no plan / no card configured in this fixture) — what it
    # must never do is refuse because they are already subscribed.
    assert "payment.already_subscribed" not in reached


# ---------- opening a locked title must cost nothing ----------


async def test_opening_a_locked_title_creates_no_subscription_or_charge(db_session, as_user):
    """
    The conversion CTA opens a sheet; it must not *be* a purchase.

    Guards the money-safety half of this phase: viewing a locked film is a
    read, so no subscription may appear and no balance may move. If the CTA
    were ever wired to the purchase endpoint instead of the plans sheet,
    this is what fails.
    """
    from decimal import Decimal

    from app.db.models.user import Subscription

    user = await make_user(db_session, 9626, balance="10000")
    title, _ = await _playable(db_session, "Paid", premium=True)
    await db_session.commit()

    async with as_user(user) as client:
        assert (await client.get(f"/api/movies/{title.id}")).status_code == 200
        assert (await client.get("/api/movies/search", params={"q": "Paid"})).status_code == 200

    subscriptions = (
        await db_session.execute(
            select(Subscription).where(Subscription.user_id == user.id)
        )
    ).scalars().all()
    await db_session.refresh(user)

    assert subscriptions == []
    assert Decimal(user.balance) == Decimal("10000")


async def test_an_ordinary_user_cannot_mark_a_title_premium(db_session, as_user):
    """Authorization, not just authentication — the viewer client is not an admin."""
    user = await make_user(db_session, 9618)
    title, _ = await _playable(db_session, "Film", premium=False)
    await db_session.commit()

    async with as_user(user) as client:
        response = await client.patch(f"/api/admin/titles/{title.id}", json={"is_premium": True})

    assert response.status_code in (401, 403)
