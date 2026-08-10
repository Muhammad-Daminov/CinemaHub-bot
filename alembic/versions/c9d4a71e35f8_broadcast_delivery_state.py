"""per-recipient broadcast delivery state and translations

Revision ID: c9d4a71e35f8
Revises: b8f1e2c74d05
Create Date: 2026-08-10

Phase 9E-A. Two tables that turn broadcast delivery from a set of
counters into recorded state.

`chp_broadcast_messages` is one row per (broadcast, user). The unique
constraint is the feature: counters cannot answer "has this person
already received it?", so a resumed or retried broadcast had no way to
avoid a second delivery. With a row per recipient the database answers
it, and the constraint makes the duplicate impossible rather than
unlikely.

`chp_broadcast_translations` holds the body per interface language, so a
recipient reads in *their* language rather than the admin's. Mirrors
chp_title_translations rather than inventing a second mechanism.

**No backfill.** Broadcasts already completed have no recipient rows and
must not get any: inventing rows for a send that finished weeks ago would
be fabricating delivery evidence. They remain valid historical records —
their counters still read correctly, and nothing resumes them because
only SENDING rows are ever reclaimed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d4a71e35f8"
down_revision: Union[str, None] = "b8f1e2c74d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chp_broadcast_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("broadcast_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "SENDING", "SENT", "FAILED", "SKIPPED", name="deliverystatus"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["broadcast_id"], ["chp_broadcasts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["chp_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # The idempotency guarantee. Everything else in the worker leans
        # on this constraint existing.
        sa.UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_recipient"),
    )
    op.create_index("ix_chp_broadcast_messages_broadcast_id", "chp_broadcast_messages", ["broadcast_id"])
    op.create_index("ix_chp_broadcast_messages_user_id", "chp_broadcast_messages", ["user_id"])
    # The batch claim filters on status every iteration.
    op.create_index("ix_chp_broadcast_messages_status", "chp_broadcast_messages", ["status"])

    op.create_table(
        "chp_broadcast_translations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("broadcast_id", sa.Integer(), nullable=False),
        sa.Column("language", postgresql.ENUM("UZ", "RU", "EN", name="uilanguage", create_type=False), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["broadcast_id"], ["chp_broadcasts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("broadcast_id", "language", name="uq_broadcast_translation_language"),
    )
    op.create_index(
        "ix_chp_broadcast_translations_broadcast_id", "chp_broadcast_translations", ["broadcast_id"]
    )


def downgrade() -> None:
    op.drop_table("chp_broadcast_translations")
    op.drop_index("ix_chp_broadcast_messages_status", table_name="chp_broadcast_messages")
    op.drop_index("ix_chp_broadcast_messages_user_id", table_name="chp_broadcast_messages")
    op.drop_index("ix_chp_broadcast_messages_broadcast_id", table_name="chp_broadcast_messages")
    op.drop_table("chp_broadcast_messages")
    sa.Enum(name="deliverystatus").drop(op.get_bind(), checkfirst=True)
    # `uilanguage` is left alone — chp_users.language owns it.
