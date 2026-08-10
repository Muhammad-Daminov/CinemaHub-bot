"""admin-configurable themes and assignments

Revision ID: a2e6d91c4b73
Revises: f3c8b1d47e29
Create Date: 2026-08-10

Phase 9D. Three tables:

  chp_themes             a named palette, one of which is the default
  chp_theme_tokens       one design token's value, one row each
  chp_theme_assignments  which kind of user receives which theme

Tokens are rows rather than a JSON blob: each is validated individually
against a fixed allowlist of CSS custom property names and a hex colour
pattern, and a blob makes "which themes set the accent?" unanswerable.

No seed. With no themes configured the resolver returns the palette
compiled into the frontend, so the app looks exactly as it does today —
which is what makes this migration safe to apply ahead of any admin
actually building a theme.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2e6d91c4b73"
down_revision: Union[str, None] = "f3c8b1d47e29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chp_themes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chp_themes_key", "chp_themes", ["key"], unique=True)
    op.create_index("ix_chp_themes_is_default", "chp_themes", ["is_default"])
    op.create_index("ix_chp_themes_is_active", "chp_themes", ["is_active"])

    op.create_table(
        "chp_theme_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("theme_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["theme_id"], ["chp_themes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("theme_id", "token", name="uq_theme_token"),
    )
    op.create_index("ix_chp_theme_tokens_theme_id", "chp_theme_tokens", ["theme_id"])

    op.create_table(
        "chp_theme_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("theme_id", sa.Integer(), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("USER", "BADGE", "INTEREST", "SUBSCRIPTION", "GLOBAL", name="themescope"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("target_value", sa.String(length=64), nullable=True),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["theme_id"], ["chp_themes.id"], ondelete="CASCADE"),
        # An assignment to a deleted account disappears with them; only a
        # foreign key makes the database enforce that.
        sa.ForeignKeyConstraint(["user_id"], ["chp_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Stops two rules of the same scope fighting over one target —
        # without it, "which theme wins" would depend on row order.
        sa.UniqueConstraint("scope", "user_id", "target_value", name="uq_theme_assignment_target"),
    )
    op.create_index("ix_chp_theme_assignments_theme_id", "chp_theme_assignments", ["theme_id"])
    op.create_index("ix_chp_theme_assignments_scope", "chp_theme_assignments", ["scope"])
    op.create_index("ix_chp_theme_assignments_user_id", "chp_theme_assignments", ["user_id"])


def downgrade() -> None:
    op.drop_table("chp_theme_assignments")
    op.drop_table("chp_theme_tokens")
    op.drop_index("ix_chp_themes_is_active", table_name="chp_themes")
    op.drop_index("ix_chp_themes_is_default", table_name="chp_themes")
    op.drop_index("ix_chp_themes_key", table_name="chp_themes")
    op.drop_table("chp_themes")
    sa.Enum(name="themescope").drop(op.get_bind(), checkfirst=True)
