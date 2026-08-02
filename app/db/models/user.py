"""
Core domain models: User, Subscription, BalanceHistory.

All new tables — nothing here touches or reflects the existing legacy
schema. Money is stored as Numeric (never float) to avoid rounding
drift on balances.
"""
import enum
from datetime import datetime, date

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UILanguage(str, enum.Enum):
    """Interface language — separate from the audio language of a media file."""

    UZ = "uz"
    RU = "ru"
    EN = "en"


class UserRole(str, enum.Enum):
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


class SubscriptionPlan(str, enum.Enum):
    FREE = "free"
    PREMIUM = "premium"


class BalanceTxType(str, enum.Enum):
    TOPUP = "topup"              # manual receipt approved
    DEDUCTION = "deduction"      # purchase / order
    REFUND = "refund"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    PROMO_CREDIT = "promo_credit"


class User(Base):
    __tablename__ = "chp_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(default=UserRole.USER, nullable=False)

    referral_code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("chp_users.id"))

    balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)

    ai_requests_today: Mapped[int] = mapped_column(default=0, nullable=False)
    ai_limit_reset_at: Mapped[date] = mapped_column(Date, server_default=func.current_date())

    monthly_orders_count: Mapped[int] = mapped_column(default=0, nullable=False)
    monthly_limit_reset_at: Mapped[date] = mapped_column(Date, server_default=func.current_date())

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # UI language for bot/Mini App labels.
    language: Mapped[UILanguage] = mapped_column(default=UILanguage.UZ, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    referred_by: Mapped["User | None"] = relationship(remote_side=[id])
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    balance_history: Mapped[list["BalanceHistory"]] = relationship(back_populates="user")

    # No active_subscription/is_premium properties here by design: they would
    # read the lazy .subscriptions relationship, which raises MissingGreenlet
    # under async SQLAlchemy. Use app.services.subscriptions instead —
    # get_active_subscription(session, user_id) / is_user_premium(session, user_id).


class Subscription(Base):
    __tablename__ = "chp_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("chp_users.id"), nullable=False, index=True)

    plan: Mapped[SubscriptionPlan] = mapped_column(default=SubscriptionPlan.FREE, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="subscriptions")

    @property
    def is_active(self) -> bool:
        return self.expires_at > datetime.utcnow()


class BalanceHistory(Base):
    __tablename__ = "chp_balance_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("chp_users.id"), nullable=False, index=True)

    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)  # signed: +credit / -debit
    tx_type: Mapped[BalanceTxType] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    reference_id: Mapped[str | None] = mapped_column(String(64))  # e.g. receipt/order/promo id

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="balance_history")
