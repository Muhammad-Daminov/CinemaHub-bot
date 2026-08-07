"""plan priority, uploaded images, and queued subscriptions

Revision ID: f6b2d94ae713
Revises: e58a3c7b91d4
Create Date: 2026-08-07

Three additions, all backward compatible:

1. `chp_subscription_plans.priority` — tier rank, distinct from
   `sort_order`, which is display order. Every upgrade/downgrade rule
   compares this, so a new tier is a number rather than a code change.
   Seeded so the plans that exist today already rank correctly: the free
   plan at 0, everything else by price.

2. `chp_uploaded_images` — bytes for receipts and posters, referenced by
   foreign key from titles, collections and receipts. Postgres rather
   than disk because Render's filesystem is ephemeral, and rather than
   object storage because none is configured.

3. Queued subscriptions need **no new column**. A queued purchase is an
   ordinary row whose `started_at` is in the future, so it activates by
   the clock. Only an index is added, because `get_active_subscription`
   now filters on that column on every premium check.

`receipt_photo_file_id` becomes nullable-by-default: a Mini App upload
carries bytes instead of a Telegram file id, and the column was NOT NULL
with no default.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6b2d94ae713"
down_revision: Union[str, None] = "e58a3c7b91d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 1. tier priority ----
    op.add_column(
        "chp_subscription_plans",
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_chp_subscription_plans_priority", "chp_subscription_plans", ["priority"])

    # Rank what already exists: free stays 0, paid plans ascend by price.
    # Doing this in SQL keeps the migration correct whatever plans the
    # target database happens to hold.
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (ORDER BY price, id) AS rank
            FROM chp_subscription_plans
            WHERE NOT is_free
        )
        UPDATE chp_subscription_plans p
        SET priority = ranked.rank
        FROM ranked
        WHERE p.id = ranked.id
        """
    )
    op.execute("UPDATE chp_subscription_plans SET priority = 0 WHERE is_free")

    # ---- 2. image storage ----
    op.create_table(
        "chp_uploaded_images",
        sa.Column("id", sa.Integer(), nullable=False),
        # Nullable: the retention job drops the bytes and keeps the row, so
        # a reference from permanent history still resolves.
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), server_default="0", nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # The retention sweep asks "which images are older than N days and not
    # yet purged" — this is the index that keeps it from a full scan.
    op.create_index("ix_chp_uploaded_images_created_at", "chp_uploaded_images", ["created_at"])

    for table, column in (
        ("chp_titles", "poster_image_id"),
        ("chp_collections", "poster_image_id"),
        ("chp_payment_receipts", "receipt_image_id"),
    ):
        op.add_column(table, sa.Column(column, sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_{column}", table, "chp_uploaded_images", [column], ["id"]
        )

    # ---- 3. queued subscriptions ----
    # No column: a queued row is one whose started_at has not arrived.
    # Indexed because every premium check now filters on it.
    op.create_index(
        "ix_chp_subscriptions_started_at", "chp_subscriptions", ["user_id", "started_at"]
    )

    # A Mini App receipt has bytes, not a Telegram file id.
    op.alter_column(
        "chp_payment_receipts",
        "receipt_photo_file_id",
        existing_type=sa.String(length=255),
        server_default="",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "chp_payment_receipts",
        "receipt_photo_file_id",
        existing_type=sa.String(length=255),
        server_default=None,
        existing_nullable=False,
    )
    op.drop_index("ix_chp_subscriptions_started_at", table_name="chp_subscriptions")

    for table, column in (
        ("chp_payment_receipts", "receipt_image_id"),
        ("chp_collections", "poster_image_id"),
        ("chp_titles", "poster_image_id"),
    ):
        op.drop_constraint(f"fk_{table}_{column}", table, type_="foreignkey")
        op.drop_column(table, column)

    op.drop_index("ix_chp_uploaded_images_created_at", table_name="chp_uploaded_images")
    op.drop_table("chp_uploaded_images")

    op.drop_index("ix_chp_subscription_plans_priority", table_name="chp_subscription_plans")
    op.drop_column("chp_subscription_plans", "priority")
