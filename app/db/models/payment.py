"""Manual receipt payment models: cards to pay into, and submitted payment receipts."""
import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.user import SubscriptionPlan, User


class PaymentStatus(str, enum.Enum):
    """
    Lifecycle of one submitted payment.

    MISMATCH is deliberately distinct from REJECTED. "The number you typed
    does not match your receipt" is a correctable mistake — the user is
    told both figures and can resubmit — whereas REJECTED covers a
    judgement about the payment itself. Collapsing them would make an
    honest typo look like a refused payment.

    Only PENDING is reviewable; every other value is terminal, which is
    what makes repeated approval a no-op.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MISMATCH = "mismatch"
    CANCELLED = "cancelled"

    @property
    def is_reviewable(self) -> bool:
        return self is PaymentStatus.PENDING

    @property
    def is_retryable(self) -> bool:
        """States the user can correct and submit again from."""
        return self in (PaymentStatus.REJECTED, PaymentStatus.MISMATCH)


class RejectionReason(Base):
    """
    A reason a payment can be turned down.

    Built-in rows carry a stable `code` and are rendered through the
    locale catalogs (`payment.reject.<code>`), so the user reads the
    reason in their own language. Rows an administrator adds later have no
    code and carry their `label` verbatim — a single-language string is
    honest about what it is, rather than pretending to be translated.

    Seeded, not hardcoded, so the set can grow without a release; kept as
    rows rather than an enum so adding one costs no migration.
    """

    __tablename__ = "chp_rejection_reasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(64), unique=True)
    label: Mapped[str | None] = mapped_column(String(200))

    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def i18n_key(self) -> str | None:
        """Locale key for a built-in reason; None for an admin-authored one."""
        return f"payment.reject.{self.code}" if self.code else None


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

    # What the reviewer actually read on the receipt, recorded only when it
    # disagrees with `amount` (which is what the user declared). Keeping
    # both is what lets the mismatch message name the two figures instead
    # of saying "wrong amount" and leaving the user to guess.
    verified_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    rejection_reason_id: Mapped[int | None] = mapped_column(
        ForeignKey("chp_rejection_reasons.id")
    )

    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("chp_users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
