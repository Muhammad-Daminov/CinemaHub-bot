"""
Subscription plans as data.

Replaces the two-member `SubscriptionPlan` enum plus the `PREMIUM_PRICE`
and `PREMIUM_SUBSCRIPTION_DAYS` environment variables, under which a
price change meant a redeploy and a second plan meant a code change.

Three entities, deliberately separated:

  Plan      what is sold — price, duration, ordering, on/off.
  Feature   a named capability, defined once and shared across plans.
            Separate from benefits so the Mini App can render a real
            comparison matrix ("does plan X include Y?") rather than
            diffing marketing prose.
  PlanFeature  which plan grants which feature, with an optional value
            for quantitative limits ("5 devices"). Phase 5 enforces
            these; this phase only records them.

`benefits` is a plain string array on the plan rather than a fourth
table: it is ordered display copy edited as a block, and a row per
bullet would buy nothing but joins.

Plan text (name, description, benefits) is admin-authored content and is
stored single-language, exactly like `Title.name`. Per-language content
is FR-6 requirement 3, deferred to Phase 7, and inventing a second
localization mechanism here would mean unpicking it there.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    LargeBinary,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SubscriptionPlanModel(Base):
    """
    One purchasable plan.

    Named `SubscriptionPlanModel` because `SubscriptionPlan` is still the
    legacy enum in app.db.models.user during the expand/contract
    migration; the enum disappears once every deployment reads `plan_id`.
    """

    __tablename__ = "chp_subscription_plans"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Stable identifier for code that must name a specific plan — the
    # migration maps the old FREE/PREMIUM enum onto these. Never shown to
    # a user; rename `name` for that.
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))

    # Numeric, never float — this is money, and the balance it is paid
    # from is Numeric too. Mixing the two reintroduces rounding drift.
    price: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)

    # Ordered display copy. An array rather than a table: it is edited as
    # a block and never queried across plans.
    benefits: Mapped[list[str] | None] = mapped_column(ARRAY(String(200)))

    # Disabled plans stay purchasable-by-history: existing subscribers keep
    # their terms, the plan simply stops being offered.
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)

    # Tier rank. Deliberately NOT sort_order: a plan may be displayed first
    # yet rank below another. Every upgrade/downgrade rule compares this,
    # never a plan name, so a new tier needs only a number — no code change.
    priority: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False, index=True
    )

    # A plan costing nothing that every user implicitly holds. Exactly one
    # is expected; the service enforces that, since two "free" plans would
    # make "what does a user without a subscription get?" ambiguous.
    is_free: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SubscriptionFeature(Base):
    """
    A named capability a plan can grant.

    Defined once and referenced by many plans so the comparison matrix has
    a stable row per feature — describing the same capability twice in two
    plans' prose is how a matrix ends up with near-duplicate rows.
    """

    __tablename__ = "chp_subscription_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    # What Phase 5 will branch on. Stable, never displayed.
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanFeature(Base):
    """
    Grants one feature to one plan.

    `value` carries a quantitative limit where the feature needs one —
    "5" devices, "1080" quality — and is NULL for a plain on/off. Storing
    it here rather than on the feature is what lets two plans include the
    same capability at different levels, which is the usual shape of a
    tiered offering.
    """

    __tablename__ = "chp_plan_features"
    __table_args__ = (UniqueConstraint("plan_id", "feature_id", name="uq_plan_feature"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("chp_subscription_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    feature_id: Mapped[int] = mapped_column(
        ForeignKey("chp_subscription_features.id", ondelete="CASCADE"), nullable=False, index=True
    )
    value: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UploadedImage(Base):
    """
    An image uploaded by a user or administrator.

    Bytes live in Postgres. That is a deliberate trade, not an oversight:
    Render's filesystem is ephemeral, so anything written to disk vanishes
    on the next deploy, and no object-store credentials exist in this
    project. For receipts and posters — a few hundred kilobytes each, a
    few hundred a month — a bytea column is the only option that survives
    a redeploy without new infrastructure. Revisit if volume grows;
    swapping the storage backend means changing this table's readers, not
    every caller, which is why the bytes are behind one entity.

    One table serves receipts, title posters and collection posters, each
    of which points at it by foreign key. Retention differs per owner and
    lives with the owner, not here.
    """

    __tablename__ = "chp_uploaded_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL means the bytes were purged but the row is kept, so a reference
    # from history still resolves to something rather than dangling.
    data: Mapped[bytes | None] = mapped_column(LargeBinary)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    # When the bytes were dropped. Set by the retention job; the row and
    # every reference to it survive.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
