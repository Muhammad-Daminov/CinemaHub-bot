"""broadcasts, system settings, and the referral bonus ledger type

Revision ID: b2f7c1a95e30
Revises: a91c4e7f20b8
Create Date: 2026-08-08

Phase 6. Three additions, no changes to anything already stored:

1. `chp_broadcasts` — one row per admin broadcast, with its delivery
   counts. The status column is what makes a duplicate send impossible.
2. `chp_system_settings` — key/value platform settings, so the required
   channel is an operator decision rather than a redeploy.
3. `REFERRAL_BONUS` added to the `balancetxtype` enum. Referral payouts
   are idempotent through the existing partial unique index on
   (user_id, tx_type, reference_id), which only works if the bonus has a
   type of its own — sharing PROMO_CREDIT would let a promo and a
   referral collide on the same reference.

Nothing here backfills, and no existing row is touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2f7c1a95e30"
down_revision: Union[str, None] = "a91c4e7f20b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alembic runs a whole upgrade in one transaction, and PostgreSQL
    # refuses to use an enum label added in the transaction that added it.
    # autocommit_block is the documented escape; IF NOT EXISTS keeps a
    # partially-applied deploy re-runnable, which matters more here
    # precisely because this step self-commits.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE balancetxtype ADD VALUE IF NOT EXISTS 'REFERRAL_BONUS'")

    op.create_table(
        "chp_broadcasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "audience",
            sa.Enum("ALL", "PREMIUM", "FREE", name="broadcastaudience"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("PENDING", "SENDING", "COMPLETED", "FAILED", name="broadcaststatus"),
            nullable=False,
        ),
        sa.Column("total_recipients", sa.Integer(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("blocked_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["chp_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chp_broadcasts_created_at", "chp_broadcasts", ["created_at"])
    op.create_index("ix_chp_broadcasts_created_by_id", "chp_broadcasts", ["created_by_id"])
    op.create_index("ix_chp_broadcasts_status", "chp_broadcasts", ["status"])

    op.create_table(
        "chp_system_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by_id"], ["chp_users.id"]),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("chp_system_settings")
    op.drop_index("ix_chp_broadcasts_status", table_name="chp_broadcasts")
    op.drop_index("ix_chp_broadcasts_created_by_id", table_name="chp_broadcasts")
    op.drop_index("ix_chp_broadcasts_created_at", table_name="chp_broadcasts")
    op.drop_table("chp_broadcasts")
    sa.Enum(name="broadcaststatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="broadcastaudience").drop(op.get_bind(), checkfirst=True)
    # The REFERRAL_BONUS label stays: PostgreSQL cannot drop one label, and
    # rebuilding the type would mean rewriting chp_balance_history — a far
    # larger risk than an unused label.
