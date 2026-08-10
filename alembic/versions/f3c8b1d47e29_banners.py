"""promotional hero banners

Revision ID: f3c8b1d47e29
Revises: e7b2f45a9c81
Create Date: 2026-08-09

Phase 9C. One table of campaigns feeding the existing hero carousel.

`title_id` is nullable and ON DELETE SET NULL: a campaign can promote
something not yet in the catalog ("Avengers: Doomsday, coming soon"), and
deleting a title must not delete the campaign that mentioned it — the
banner simply stops being clickable.

Additive; nothing existing is read or rewritten.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3c8b1d47e29"
down_revision: Union[str, None] = "e7b2f45a9c81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chp_banners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title_id", sa.Integer(), nullable=True),
        sa.Column("headline", sa.String(length=120), nullable=True),
        sa.Column("subtitle", sa.String(length=200), nullable=True),
        sa.Column("label_key", sa.String(length=64), nullable=True),
        sa.Column("image_url", sa.String(length=512), nullable=True),
        sa.Column(
            "audience",
            sa.Enum("GLOBAL", "CONTENT_TYPE", "BADGE", "PREMIUM", "FREE", name="banneraudience"),
            nullable=False,
        ),
        sa.Column("target_value", sa.String(length=64), nullable=True),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["title_id"], ["chp_titles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chp_banners_title_id", "chp_banners", ["title_id"])
    op.create_index("ix_chp_banners_audience", "chp_banners", ["audience"])
    # Every resolution filters on this first.
    op.create_index("ix_chp_banners_is_active", "chp_banners", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_chp_banners_is_active", table_name="chp_banners")
    op.drop_index("ix_chp_banners_audience", table_name="chp_banners")
    op.drop_index("ix_chp_banners_title_id", table_name="chp_banners")
    op.drop_table("chp_banners")
    sa.Enum(name="banneraudience").drop(op.get_bind(), checkfirst=True)
