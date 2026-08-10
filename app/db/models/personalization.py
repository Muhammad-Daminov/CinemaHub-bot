"""
Per-user personalization state.

One row per user, holding only what is *expensive to derive and needed on
every feed render*: which kind of content they actually watch. Everything
else — the full distribution, ranks, continue-watching — is still
computed live from `chp_watch_history`, which is cheap and never stale.

Materialising more than this would buy nothing and create a second place
for the truth to drift from.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserInterestProfile(Base):
    """
    What one user is into, recalculated periodically.

    Keyed by `user_id` as the primary key, not merely indexed: one profile
    per user is a structural fact, and making the database enforce it is
    what stops a race between two recalculations leaving a user with two
    contradictory profiles.

    `dominant_type` is deliberately nullable. A user who has watched two
    things has no dominant interest, and inventing one would hand them a
    badge and a themed feed off a pair of clicks — the exact failure the
    brief warns about.
    """

    __tablename__ = "chp_user_interest_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("chp_users.id", ondelete="CASCADE"), primary_key=True
    )

    # ContentType value, or NULL when nothing dominates yet.
    dominant_type: Mapped[str | None] = mapped_column(String(32), index=True)
    # Distinct titles of the dominant type — what badge tiers are read from.
    dominant_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Distinct titles overall, the denominator for "is anything dominant".
    total_titles: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
