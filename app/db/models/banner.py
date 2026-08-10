"""
Promotional hero banners.

A campaign points at a title and decides *who* sees it. The carousel that
renders them already exists and is unchanged — this supplies its slides.

Text here is admin-authored content, stored single-language like
`Title.name`, collection names and plan benefits. Per-language banner
copy is the same gap those have, tracked as P2-13; the one piece of text
that *is* localized is `label_key`, which names a locale key from a fixed
allowlist rather than free text ("Coming Soon" must read in the viewer's
language, and it is a label, not content).
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BannerAudience(str, enum.Enum):
    """
    Who a campaign is for.

    Resolved against the viewer's own interest profile (Phase 9B) and
    subscription at request time — never precomputed into a shared list,
    because a cached "current banners" that is not keyed by user is
    exactly how one person's personalization reaches another.
    """

    GLOBAL = "global"
    # Matches the viewer's dominant content type ("anime", "film", …).
    CONTENT_TYPE = "content_type"
    # Matches a badge key prefix, e.g. "badge.anime." for every anime tier.
    BADGE = "badge"
    PREMIUM = "premium"
    FREE = "free"


class Banner(Base):
    """
    One promotional campaign.

    `title_id` is nullable so a campaign can promote something not yet in
    the catalog — "Avengers: Doomsday, coming soon" has no watchable title
    behind it, and requiring one would make upcoming promotions
    impossible. A banner with no title simply is not clickable.
    """

    __tablename__ = "chp_banners"

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int | None] = mapped_column(
        ForeignKey("chp_titles.id", ondelete="SET NULL"), index=True
    )

    # Admin-authored, plain text. Rendered as text by React, never as
    # markup — see app.services.banners for the validation that keeps it
    # that way.
    headline: Mapped[str | None] = mapped_column(String(120))
    subtitle: Mapped[str | None] = mapped_column(String(200))
    # A locale key from a fixed allowlist ("banner.label.coming_soon"), so
    # the badge over the artwork reads in the viewer's language and cannot
    # be arbitrary text.
    label_key: Mapped[str | None] = mapped_column(String(64))
    # Overrides the title's poster. Absent means "use the title's artwork",
    # which is what makes promoting an existing film a one-field campaign.
    image_url: Mapped[str | None] = mapped_column(String(512))

    audience: Mapped[BannerAudience] = mapped_column(
        default=BannerAudience.GLOBAL, nullable=False, index=True
    )
    # Content type value or badge-key prefix, depending on `audience`.
    # NULL for GLOBAL/PREMIUM/FREE, which need no parameter.
    target_value: Mapped[str | None] = mapped_column(String(64))

    # Higher wins. Ties fall back to newest, so ordering is always total —
    # a carousel whose order changed between requests would look broken.
    priority: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False, index=True
    )

    # Both nullable: a campaign with no window runs until switched off.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
