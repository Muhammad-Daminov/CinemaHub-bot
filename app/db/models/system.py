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

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BroadcastAudience(str, enum.Enum):
    """Who receives a broadcast. Resolved to user ids at send time, not stored."""

    ALL = "all"
    PREMIUM = "premium"
    FREE = "free"


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

    Deliberately stores no recipient list. Who received it is derivable
    from the audience filter, and materialising a per-user row for every
    broadcast would be a second copy of the user table with no reader.
    """

    __tablename__ = "chp_broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("chp_users.id"), nullable=False, index=True
    )

    message: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[BroadcastAudience] = mapped_column(
        default=BroadcastAudience.ALL, nullable=False
    )
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
