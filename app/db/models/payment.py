"""Manual receipt payment models: cards to pay into, and submitted payment receipts."""
import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.user import SubscriptionPlan, User


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PaymentPurpose(str, enum.Enum):
    TOPUP = "topup"
    SUBSCRIPTION = "subscription"


class AdminCard(Base):
    """A card admins publish for users to send manual bank transfers to."""

    __tablename__ = "chp_admin_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_number: Mapped[str] = mapped_column(String(32), nullable=False)
    holder_name: Mapped[str] = mapped_column(String(128), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentReceipt(Base):
    """One user-submitted payment: a screenshot pending admin review."""

    __tablename__ = "chp_payment_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("chp_users.id"), nullable=False, index=True)
    admin_card_id: Mapped[int | None] = mapped_column(ForeignKey("chp_admin_cards.id"))

    purpose: Mapped[PaymentPurpose] = mapped_column(nullable=False)
    # Authoritative; `subscription_plan` is the legacy enum kept for
    # rollback safety during the expand/contract migration.
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("chp_subscription_plans.id"), index=True
    )
    subscription_plan: Mapped["SubscriptionPlan | None"] = mapped_column()
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    # Telegram-hosted receipt (bot flow). Empty for Mini App uploads,
    # which carry their own bytes via receipt_image_id.
    receipt_photo_file_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    receipt_image_id: Mapped[int | None] = mapped_column(ForeignKey("chp_uploaded_images.id"))
    status: Mapped[PaymentStatus] = mapped_column(default=PaymentStatus.PENDING, nullable=False, index=True)
    admin_notes: Mapped[str | None] = mapped_column(String(500))

    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("chp_users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
