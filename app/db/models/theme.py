"""
Admin-configurable themes and who gets them.

Three tables, deliberately normalised rather than one row of JSON:

  Theme            a named palette, one of which is the default
  ThemeToken       one design token's value, one row each
  ThemeAssignment  a rule saying which theme a kind of user receives

Tokens as rows rather than a JSON blob because they are validated
individually, the vocabulary is fixed and small, and a blob makes "which
themes set the accent colour?" unanswerable without scanning every row.

The token *names* are the CSS variables the frontend already uses
(`--color-bg`, `--color-ink`, …). Thirty-two components consume them
through Tailwind today; inventing a parallel vocabulary would mean
rewriting all of them for no gain, so the existing names are the
contract and new tokens extend it.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ThemeScope(str, enum.Enum):
    """
    What kind of user an assignment targets.

    Declaration order is **not** the precedence order — precedence lives in
    `app.services.themes.SCOPE_PRECEDENCE`, where it is written down once
    and tested, rather than being an accident of how this enum is spelled.
    """

    USER = "user"
    BADGE = "badge"
    INTEREST = "interest"
    SUBSCRIPTION = "subscription"
    GLOBAL = "global"


class Theme(Base):
    """
    One palette.

    `is_default` marks the fallback every user lands on when no assignment
    matches. Exactly one theme may hold it, and it cannot be deleted or
    disabled — a platform with no fallback theme is a platform that can be
    made unusable from an admin form.
    """

    __tablename__ = "chp_themes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable identifier for code and seeds; never shown to a user.
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))

    # Presentation choices that are not colours. Stored as short keys
    # validated against a fixed allowlist — never raw CSS, so an admin can
    # pick a shape but cannot write a border-radius.
    card_shape: Mapped[str] = mapped_column(
        String(32), default="rounded", server_default=text("'rounded'"), nullable=False
    )
    decoration: Mapped[str] = mapped_column(
        String(32), default="none", server_default=text("'none'"), nullable=False
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tokens: Mapped[list["ThemeToken"]] = relationship(
        back_populates="theme", cascade="all, delete-orphan", lazy="selectin"
    )


class ThemeToken(Base):
    """
    One design token's value within one theme.

    `token` is validated against a fixed allowlist and `value` against a
    colour pattern before either reaches the database — the frontend
    writes these into CSS custom properties, so an unchecked value would
    be a stylesheet injection point.
    """

    __tablename__ = "chp_theme_tokens"
    __table_args__ = (UniqueConstraint("theme_id", "token", name="uq_theme_token"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    theme_id: Mapped[int] = mapped_column(
        ForeignKey("chp_themes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(String(32), nullable=False)

    theme: Mapped["Theme"] = relationship(back_populates="tokens")


class ThemeAssignment(Base):
    """
    A rule granting a theme to a kind of user.

    `user_id` is a real foreign key rather than an id squeezed into
    `target_value`: an assignment to a deleted account should disappear
    with them, and only a foreign key makes the database enforce that.

    The unique constraint is what stops two rules of the same scope
    fighting over the same target — without it, "which theme wins" would
    depend on row order.
    """

    __tablename__ = "chp_theme_assignments"
    __table_args__ = (
        UniqueConstraint("scope", "user_id", "target_value", name="uq_theme_assignment_target"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    theme_id: Mapped[int] = mapped_column(
        ForeignKey("chp_themes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[ThemeScope] = mapped_column(nullable=False, index=True)

    # Set for USER scope only.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("chp_users.id", ondelete="CASCADE"), index=True
    )
    # Badge key prefix, content type, or "premium"/"free". NULL for GLOBAL.
    target_value: Mapped[str | None] = mapped_column(String(64))

    # Tiebreak within one scope, so two matching rules of equal rank still
    # resolve to exactly one theme.
    priority: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
