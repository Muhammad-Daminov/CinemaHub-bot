"""per-user interest profiles

Revision ID: e7b2f45a9c81
Revises: d4a7e9c31b56
Create Date: 2026-08-09

Phase 9B. One row per user holding only what is expensive to derive and
needed on every feed render: which kind of content they actually watch.

`user_id` is the primary key, not merely an index. One profile per user
is a structural fact, and letting the database enforce it is what stops a
race between the scheduled sweep and a live read leaving one user with
two contradictory profiles.

No backfill. A user with no row gets one computed the first time it is
read, and the scheduled sweep keeps them fresh from there — backfilling
508 profiles inside a migration would run the aggregation 508 times
while holding a transaction open against production.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7b2f45a9c81"
down_revision: Union[str, None] = "d4a7e9c31b56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chp_user_interest_profiles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        # NULL until something actually dominates — a user with two watches
        # has no dominant interest, and inventing one would hand them a
        # badge and a themed feed off a pair of clicks.
        sa.Column("dominant_type", sa.String(length=32), nullable=True),
        sa.Column("dominant_count", sa.Integer(), nullable=False),
        sa.Column("total_titles", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["chp_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_chp_user_interest_profiles_dominant_type",
        "chp_user_interest_profiles",
        ["dominant_type"],
    )
    # The staleness sweep orders by this column on every run.
    op.create_index(
        "ix_chp_user_interest_profiles_computed_at",
        "chp_user_interest_profiles",
        ["computed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chp_user_interest_profiles_computed_at", table_name="chp_user_interest_profiles"
    )
    op.drop_index(
        "ix_chp_user_interest_profiles_dominant_type", table_name="chp_user_interest_profiles"
    )
    op.drop_table("chp_user_interest_profiles")
