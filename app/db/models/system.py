"""
Platform-operations tables: broadcasts and system settings.

Both exist because the alternative was configuration that only an
engineer can change. A required-membership channel or a broadcast is an
operational decision taken by whoever runs the platform, often at short
notice; an environment variable means a redeploy, and a redeploy means
the decision waits for someone with deploy access.
"""
import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.user import UILanguage


class BroadcastAudience(str, enum.Enum):
    """
    Who receives a broadcast. Resolved to user ids at send time from
    authoritative database state — never from anything the client sends.

    INTEREST and BADGE are *alternative* segments, not extra conditions
    layered onto PREMIUM/FREE: `INTEREST=anime` means everyone whose
    profile says anime, paying or not. There is deliberately no hidden AND
    — an operator who cannot tell from the audience name who will receive
    a message will eventually send one to the wrong people.
    """

    ALL = "all"
    PREMIUM = "premium"
    FREE = "free"
    INTEREST = "interest"
    BADGE = "badge"

    @property
    def needs_target(self) -> bool:
        """Whether this audience is meaningless without a `target_value`."""
        return self in (BroadcastAudience.INTEREST, BroadcastAudience.BADGE)


class BroadcastMedia(str, enum.Enum):
    """
    What a broadcast carries besides text.

    A closed allowlist, not a passthrough of Telegram's media vocabulary:
    each value has a matching send path in the worker, and anything else
    would have nowhere to go.
    """

    NONE = "none"
    PHOTO = "photo"
    VIDEO = "video"

    @property
    def needs_file(self) -> bool:
        return self is not BroadcastMedia.NONE


class BroadcastStatus(str, enum.Enum):
    """
    Lifecycle of one broadcast.

    PENDING → SENDING is the transition that makes a duplicate send
    impossible: it is performed under a row lock, so a second worker
    picking up the same broadcast finds it already SENDING and stops.
    """

    PENDING = "pending"
    SENDING = "sending"
    COMPLETED = "completed"
    FAILED = "failed"


class Broadcast(Base):
    """
    One message sent to a segment of users, with its delivery outcome.

    The counters are the whole reason this is a table rather than a
    fire-and-forget call: Telegram will refuse a share of any large send
    (users who blocked the bot, deleted accounts), and an operator who
    cannot see that number has no way to tell a delivery problem from an
    audience that simply is not there.

    Who actually received it lives in `chp_broadcast_messages`, one row per
    recipient — the counters here are derived from those rows rather than
    incremented in memory, so they survive a crash and a resume.
    """

    __tablename__ = "chp_broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("chp_users.id"), nullable=False, index=True
    )

    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Media travels as a Telegram file_id — the bytes stay on Telegram's
    # servers and are never downloaded, stored or proxied by us. The id is
    # only meaningful to our bot, so it is not a secret, but it is still
    # kept out of every user-facing response.
    media_type: Mapped[BroadcastMedia] = mapped_column(
        default=BroadcastMedia.NONE, server_default=text("'NONE'"), nullable=False
    )
    media_file_id: Mapped[str | None] = mapped_column(String(255))

    audience: Mapped[BroadcastAudience] = mapped_column(
        default=BroadcastAudience.ALL, nullable=False
    )
    # What INTEREST/BADGE targets — a content type value, or a badge key or
    # badge-family prefix. Validated against the allowlists derived from
    # Phase 9B's badge tables before it is ever stored, and NULL for every
    # untargeted audience so a row always reads honestly.
    #
    # Kept as an audit record of what was *asked for*. It is not consulted
    # after materialisation: the recipient rows are the frozen truth, so a
    # user who becomes an anime watcher tomorrow does not retroactively
    # join yesterday's send.
    target_value: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[BroadcastStatus] = mapped_column(
        default=BroadcastStatus.PENDING, nullable=False, index=True
    )

    # Size of the audience when the send began. Recorded rather than
    # recomputed, so progress stays meaningful if users sign up mid-send.
    total_recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Users who have blocked the bot or deleted their account. Separated
    # from failures because it is not an error to fix — it is churn.
    blocked_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemSetting(Base):
    """
    One admin-editable platform setting, keyed by name.

    A key/value table rather than a column per setting: these are read by
    name at the point of use and never queried across, so a new setting
    should cost a row, not a migration against a production database.
    Values are stored as text and parsed by the accessor that owns the
    key — see app.services.settings_store, which is the only reader.
    """

    __tablename__ = "chp_system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(500))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("chp_users.id"))


class DeliveryStatus(str, enum.Enum):
    """
    Lifecycle of one broadcast message to one user.

    SENDING exists so a crash is distinguishable from work not yet
    started: a row left SENDING was in flight when the process died, and
    the resume path can decide whether to retry it or give up rather than
    guessing.

    SKIPPED is separated from FAILED because a user who blocked the bot is
    churn, not a delivery fault, and mixing the two makes the failure
    count meaningless.
    """

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        return self in (DeliveryStatus.SENT, DeliveryStatus.FAILED, DeliveryStatus.SKIPPED)


class BroadcastMessage(Base):
    """
    One broadcast's delivery to one user — the source of truth for who has
    received what.

    The unique constraint on (broadcast_id, user_id) is the whole point.
    Counters alone cannot answer "has this person already had it?", so a
    resumed or retried broadcast had no way to avoid sending twice. With a
    row per recipient the database answers that question, and the
    constraint makes a duplicate impossible rather than unlikely.

    Deliberately internal: these rows are never exposed through a
    user-facing endpoint. They say who was messaged and when, which is
    nobody's business but the platform's.
    """

    __tablename__ = "chp_broadcast_messages"
    __table_args__ = (
        UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_recipient"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("chp_broadcasts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("chp_users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[DeliveryStatus] = mapped_column(
        default=DeliveryStatus.PENDING, nullable=False, index=True
    )
    # Counted before each send, not after, so a row that crashed mid-flight
    # still shows the attempt and cannot be retried forever.
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Truncated and never formatted with credentials — see the send loop.
    error: Mapped[str | None] = mapped_column(String(300))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BroadcastTranslation(Base):
    """
    One broadcast's body in one interface language.

    The recipient's own language decides which row is used; the
    broadcast's `message` is the fallback. The admin's language is never
    consulted — writing in Uzbek must not mean Russian speakers receive
    Uzbek.

    Mirrors `chp_title_translations` rather than inventing a second
    localization mechanism.
    """

    __tablename__ = "chp_broadcast_translations"
    __table_args__ = (
        UniqueConstraint("broadcast_id", "language", name="uq_broadcast_translation_language"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("chp_broadcasts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[UILanguage] = mapped_column(nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
