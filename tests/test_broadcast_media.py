"""
Photo and video broadcasts.

Media changes *which Telegram call is made* and nothing else: the same
per-recipient rows, the same unique constraint, the same retry, resume
and recovery from 9E-A. These tests exist to prove that — a photo
broadcast must be as resumable and as duplicate-proof as a text one.

The bytes never touch this server. A `file_id` captured through the
trusted admin forward flow is passed straight to Telegram.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select, update

from app.db.models.system import (
    Broadcast,
    BroadcastAudience,
    BroadcastMedia,
    BroadcastMessage,
    BroadcastStatus,
    DeliveryStatus,
)
from app.db.models.user import UILanguage
from app.services.broadcast import (
    MAX_CAPTION_LENGTH,
    BroadcastError,
    create_broadcast,
    resume_stale_broadcasts,
    run_broadcast,
    set_translations,
)
from tests.conftest import count_rows, make_user, requires_db

pytestmark = [requires_db, pytest.mark.db]

PHOTO_ID = "AgACAgIAAxkBAAIB_photo_file_id"
VIDEO_ID = "BAACAgIAAxkBAAIB_video_file_id"


class FakeBot:
    """Records which Telegram method each recipient got, and with what."""

    def __init__(self, fail_media=False, retry_once=None, blocked=None, crash_after=None):
        self.messages: list[tuple[int, str]] = []
        self.photos: list[tuple[int, str, str | None]] = []
        self.videos: list[tuple[int, str, str | None]] = []
        self.fail_media = fail_media
        self.retry_once = set(retry_once or [])
        self.blocked = blocked or set()
        self.crash_after = crash_after
        self._retried: set[int] = set()

    @property
    def deliveries(self) -> int:
        return len(self.messages) + len(self.photos) + len(self.videos)

    def _guard(self, chat_id):
        if self.crash_after is not None and self.deliveries >= self.crash_after:
            raise RuntimeError("worker died")
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        if chat_id in self.retry_once and chat_id not in self._retried:
            self._retried.add(chat_id)
            raise TelegramRetryAfter(method=None, message="flood", retry_after=0)

    async def send_message(self, chat_id, text, *args, **kwargs):
        self._guard(chat_id)
        self.messages.append((chat_id, text))

    async def send_photo(self, chat_id, photo, caption=None, **kwargs):
        self._guard(chat_id)
        if self.fail_media:
            raise TelegramBadRequest(method=None, message="wrong file identifier/HTTP URL specified")
        self.photos.append((chat_id, photo, caption))

    async def send_video(self, chat_id, video, caption=None, **kwargs):
        self._guard(chat_id)
        if self.fail_media:
            raise TelegramBadRequest(method=None, message="wrong file identifier/HTTP URL specified")
        self.videos.append((chat_id, video, caption))


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    from app.services import broadcast as module

    monkeypatch.setattr(module, "SEND_INTERVAL_SECONDS", 0)


# ---------- creation and validation ----------


async def test_a_text_broadcast_still_has_no_media(db_session):
    """Backward compatibility, asserted directly."""
    actor = await make_user(db_session, 9301)
    broadcast = await create_broadcast(db_session, actor, "Hello", BroadcastAudience.ALL)

    assert broadcast.media_type == BroadcastMedia.NONE
    assert broadcast.media_file_id is None


@pytest.mark.parametrize(
    "media,file_id", [(BroadcastMedia.PHOTO, PHOTO_ID), (BroadcastMedia.VIDEO, VIDEO_ID)]
)
async def test_media_broadcasts_store_only_the_reference(db_session, media, file_id):
    actor = await make_user(db_session, 9302 if media == BroadcastMedia.PHOTO else 9303)
    broadcast = await create_broadcast(
        db_session, actor, "Caption", BroadcastAudience.ALL, media_type=media, media_file_id=file_id
    )

    assert broadcast.media_type == media
    assert broadcast.media_file_id == file_id
    # Nothing resembling bytes is stored.
    assert len(broadcast.media_file_id) < 256


@pytest.mark.parametrize("media", [BroadcastMedia.PHOTO, BroadcastMedia.VIDEO])
async def test_media_without_a_file_id_is_refused(db_session, media):
    actor = await make_user(db_session, 9304 if media == BroadcastMedia.PHOTO else 9305)
    for missing in (None, "", "   "):
        with pytest.raises(BroadcastError):
            await create_broadcast(
                db_session, actor, "x", BroadcastAudience.ALL,
                media_type=media, media_file_id=missing,
            )


async def test_a_file_id_without_media_is_refused(db_session):
    """A stray file_id on a text broadcast would silently never be sent."""
    actor = await make_user(db_session, 9306)
    with pytest.raises(BroadcastError):
        await create_broadcast(
            db_session, actor, "x", BroadcastAudience.ALL,
            media_type=BroadcastMedia.NONE, media_file_id=PHOTO_ID,
        )


async def test_a_caption_longer_than_telegram_allows_is_refused(db_session):
    """
    Caught at creation, not on the first recipient — by then the row
    already says SENDING and the operator is guessing.
    """
    actor = await make_user(db_session, 9307)
    with pytest.raises(BroadcastError):
        await create_broadcast(
            db_session, actor, "x" * (MAX_CAPTION_LENGTH + 1), BroadcastAudience.ALL,
            media_type=BroadcastMedia.PHOTO, media_file_id=PHOTO_ID,
        )
    # The same text is fine without media, where the limit is far higher.
    assert await create_broadcast(
        db_session, actor, "x" * (MAX_CAPTION_LENGTH + 1), BroadcastAudience.ALL
    )


async def test_an_unknown_media_type_cannot_be_constructed(db_session):
    """The allowlist is an enum — 'animation', 'document' and friends do not exist."""
    with pytest.raises(ValueError):
        BroadcastMedia("animation")
    assert {m.value for m in BroadcastMedia} == {"none", "photo", "video"}


# ---------- sending ----------


async def test_a_photo_broadcast_uses_send_photo_with_the_caption(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9310)
        broadcast = await create_broadcast(
            s, actor, "Look at this", BroadcastAudience.ALL,
            media_type=BroadcastMedia.PHOTO, media_file_id=PHOTO_ID,
        )
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)

    assert bot.photos == [(9310, PHOTO_ID, "Look at this")]
    assert bot.messages == [], "a photo broadcast must not also send text"


async def test_a_video_broadcast_uses_send_video_with_the_caption(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9311)
        broadcast = await create_broadcast(
            s, actor, "Trailer", BroadcastAudience.ALL,
            media_type=BroadcastMedia.VIDEO, media_file_id=VIDEO_ID,
        )
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)

    assert bot.videos == [(9311, VIDEO_ID, "Trailer")]
    assert bot.messages == []


async def test_a_text_broadcast_still_uses_send_message(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9312)
        broadcast = await create_broadcast(s, actor, "Plain", BroadcastAudience.ALL)
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)

    assert bot.messages == [(9312, "Plain")]
    assert bot.photos == [] and bot.videos == []


# ---------- media failures ----------


async def test_a_rejected_file_id_fails_permanently(db_factory):
    """
    A bad file_id fails identically for everyone. Retrying it would mean
    MAX_ATTEMPTS × the whole audience of calls that cannot succeed.
    """
    async with db_factory() as s:
        actor = await make_user(s, 9320)
        await make_user(s, 9321)
        broadcast = await create_broadcast(
            s, actor, "Caption", BroadcastAudience.ALL,
            media_type=BroadcastMedia.PHOTO, media_file_id="not-a-real-id",
        )
        await s.commit()
        broadcast_id = broadcast.id

    await run_broadcast(db_factory, FakeBot(fail_media=True), broadcast_id)

    async with db_factory() as s:
        rows = (
            await s.execute(select(BroadcastMessage).where(BroadcastMessage.broadcast_id == broadcast_id))
        ).scalars().all()
        assert {row.status for row in rows} == {DeliveryStatus.FAILED}
        assert all(row.attempts == 1 for row in rows), "no pointless retries"
        assert all(row.error for row in rows), "the reason is recorded for the operator"
        assert (await s.get(Broadcast, broadcast_id)).failed_count == 2


async def test_a_retry_after_still_works_for_media(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9322)
        broadcast = await create_broadcast(
            s, actor, "Caption", BroadcastAudience.ALL,
            media_type=BroadcastMedia.PHOTO, media_file_id=PHOTO_ID,
        )
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot(retry_once={9322})
    await run_broadcast(db_factory, bot, broadcast_id)

    assert len(bot.photos) == 1
    async with db_factory() as s:
        assert await count_rows(s, BroadcastMessage, status=DeliveryStatus.SENT) == 1


async def test_a_blocked_user_is_skipped_for_media_too(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9323)
        await make_user(s, 9324)
        broadcast = await create_broadcast(
            s, actor, "Caption", BroadcastAudience.ALL,
            media_type=BroadcastMedia.VIDEO, media_file_id=VIDEO_ID,
        )
        await s.commit()
        broadcast_id = broadcast.id

    await run_broadcast(db_factory, FakeBot(blocked={9324}), broadcast_id)

    async with db_factory() as s:
        row = await s.get(Broadcast, broadcast_id)
        assert (row.sent_count, row.blocked_count) == (1, 1)


# ---------- delivery guarantees carry over ----------


async def test_a_media_broadcast_resumes_without_duplicating(db_factory):
    """The 9E-A guarantee, re-proven for photo."""
    async with db_factory() as s:
        actor = await make_user(s, 9330)
        for telegram_id in range(9331, 9336):
            await make_user(s, telegram_id)
        broadcast = await create_broadcast(
            s, actor, "Caption", BroadcastAudience.ALL,
            media_type=BroadcastMedia.PHOTO, media_file_id=PHOTO_ID,
        )
        await s.commit()
        broadcast_id = broadcast.id

    crashing = FakeBot(crash_after=2)
    await run_broadcast(db_factory, crashing, broadcast_id)
    first = {chat for chat, _, _ in crashing.photos}
    assert len(first) == 2

    async with db_factory() as s:
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
    second = {chat for chat, _, _ in resuming.photos}

    assert not (first & second), "nobody receives the photo twice"
    assert first | second == set(range(9330, 9336))
    # The resumed run still sends the media, not a text fallback.
    assert resuming.messages == []
    assert all(file_id == PHOTO_ID for _, file_id, _ in resuming.photos)


async def test_concurrent_workers_do_not_double_send_media(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9340)
        for telegram_id in range(9341, 9346):
            await make_user(s, telegram_id)
        broadcast = await create_broadcast(
            s, actor, "Caption", BroadcastAudience.ALL,
            media_type=BroadcastMedia.VIDEO, media_file_id=VIDEO_ID,
        )
        await s.commit()
        broadcast_id = broadcast.id

    bots = [FakeBot(), FakeBot()]
    await asyncio.gather(*(run_broadcast(db_factory, bot, broadcast_id) for bot in bots))

    delivered = [chat for bot in bots for chat, _, _ in bot.videos]
    assert len(delivered) == len(set(delivered)) == 6


# ---------- localization ----------


async def test_each_recipient_gets_their_own_caption(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9350)
        actor.language = UILanguage.UZ
        uz = await make_user(s, 9351)
        ru = await make_user(s, 9352)
        en = await make_user(s, 9353)
        uz.language, ru.language, en.language = UILanguage.UZ, UILanguage.RU, UILanguage.EN
        broadcast = await create_broadcast(
            s, actor, "Salom", BroadcastAudience.ALL,
            media_type=BroadcastMedia.PHOTO, media_file_id=PHOTO_ID,
        )
        await s.flush()
        await set_translations(
            s, broadcast.id, {UILanguage.RU: "Привет", UILanguage.EN: "Hello"}, with_media=True
        )
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)
    captions = {chat: caption for chat, _, caption in bot.photos}

    assert captions[9351] == "Salom"
    assert captions[9352] == "Привет"
    assert captions[9353] == "Hello"
    # Every recipient got the same media, only the words differ.
    assert {file_id for _, file_id, _ in bot.photos} == {PHOTO_ID}


async def test_a_missing_caption_translation_falls_back(db_factory):
    async with db_factory() as s:
        actor = await make_user(s, 9354)
        ru = await make_user(s, 9355)
        ru.language = UILanguage.RU
        broadcast = await create_broadcast(
            s, actor, "Faqat o'zbekcha", BroadcastAudience.ALL,
            media_type=BroadcastMedia.VIDEO, media_file_id=VIDEO_ID,
        )
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)
    assert {chat: caption for chat, _, caption in bot.videos}[9355] == "Faqat o'zbekcha"


async def test_an_overlong_translated_caption_is_refused(db_session):
    actor = await make_user(db_session, 9356)
    broadcast = await create_broadcast(
        db_session, actor, "ok", BroadcastAudience.ALL,
        media_type=BroadcastMedia.PHOTO, media_file_id=PHOTO_ID,
    )
    await db_session.flush()
    with pytest.raises(BroadcastError):
        await set_translations(
            db_session, broadcast.id, {UILanguage.RU: "x" * (MAX_CAPTION_LENGTH + 1)}, with_media=True
        )


# ---------- security ----------


async def test_the_file_id_is_not_exposed_in_the_admin_list(db_session):
    """
    The panel needs to know a broadcast carries a photo, not which file.
    Asserted against the response model so a future field addition is
    deliberate.
    """
    from app.api.admin import BroadcastOut

    assert "media_type" in BroadcastOut.model_fields
    assert "media_file_id" not in BroadcastOut.model_fields


async def test_no_response_schema_returns_the_file_id(db_session):
    """
    An admin *sends* a file_id when creating a broadcast, so the request
    model carries it legitimately. No **response** may hand it back —
    that is the direction a leak would travel.
    """
    from app.main import app

    schema = app.openapi()
    leaking = [
        name
        for name, model in schema["components"]["schemas"].items()
        if "media_file_id" in (model.get("properties") or {}) and not name.endswith("In")
    ]
    assert leaking == [], f"response models exposing the file reference: {leaking}"

    # And nothing outside /api/admin mentions broadcast media at all.
    for path, methods in schema["paths"].items():
        if path.startswith("/api/admin"):
            continue
        assert "media_file_id" not in str(methods), path


async def test_a_malicious_caption_is_stored_verbatim_not_executed(db_factory):
    """
    A caption is text handed to Telegram, never rendered as markup by us.
    It is stored and delivered exactly as typed — there is no HTML sink in
    this path at all.
    """
    payload = "<script>alert(1)</script>"
    async with db_factory() as s:
        actor = await make_user(s, 9360)
        broadcast = await create_broadcast(
            s, actor, payload, BroadcastAudience.ALL,
            media_type=BroadcastMedia.PHOTO, media_file_id=PHOTO_ID,
        )
        await s.commit()
        broadcast_id = broadcast.id

    bot = FakeBot()
    await run_broadcast(db_factory, bot, broadcast_id)
    assert bot.photos[0][2] == payload


async def test_an_ordinary_user_cannot_create_a_media_broadcast(db_session):
    from httpx import ASGITransport, AsyncClient

    from app.api.auth import get_current_user
    from app.db.session import get_db_session
    from app.main import app

    user = await make_user(db_session, 9361)
    await db_session.commit()

    async def override_session():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/admin/broadcasts",
            json={"message": "x", "audience": "all", "media_type": "photo", "media_file_id": PHOTO_ID},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 403


async def test_the_api_rejects_an_invalid_media_combination(db_session):
    from httpx import ASGITransport, AsyncClient

    from app.api.auth import get_current_user
    from app.db.models.user import UserRole
    from app.db.session import get_db_session
    from app.main import app

    admin = await make_user(db_session, 9362)
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()

    async def override_session():
        yield db_session

    async def override_user():
        return admin

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post(
            "/api/admin/broadcasts",
            json={"message": "x", "audience": "all", "media_type": "photo"},
        )
        unknown = await client.post(
            "/api/admin/broadcasts",
            json={"message": "x", "audience": "all", "media_type": "animation", "media_file_id": "z"},
        )
    app.dependency_overrides.clear()

    assert missing.status_code == 422, "photo without a file_id"
    assert unknown.status_code == 422, "media type outside the allowlist"
