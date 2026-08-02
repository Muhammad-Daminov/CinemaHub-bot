"""Promo/voucher/campaign models."""
import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PromoDiscountType(str, enum.Enum):
    FIXED_AMOUNT_BALANCE = "fixed_amount_balance"
    PREMIUM_DAYS = "premium_days"
    PERCENTAGE_DISCOUNT = "percentage_discount"


class PromoCode(Base):
    __tablename__ = "chp_promo_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    campaign_name: Mapped[str | None] = mapped_column(String(128))

    discount_type: Mapped[PromoDiscountType] = mapped_column(nullable=False)
    value: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # amount, days, or percent depending on type

    max_uses: Mapped[int | None] = mapped_column()  # None = unlimited
    current_uses: Mapped[int] = mapped_column(default=0, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # None = no expiry
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("chp_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromoUsage(Base):
    __tablename__ = "chp_promo_usages"
    __table_args__ = (UniqueConstraint("promo_code_id", "user_id", name="uq_promo_usage_per_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    promo_code_id: Mapped[int] = mapped_column(ForeignKey("chp_promo_codes.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("chp_users.id"), nullable=False, index=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
