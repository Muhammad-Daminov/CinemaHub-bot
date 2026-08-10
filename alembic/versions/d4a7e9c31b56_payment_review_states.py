"""payment mismatch state, structured rejection reasons

Revision ID: d4a7e9c31b56
Revises: c8d3a51fb742
Create Date: 2026-08-09

Phase 9A. Manual card payment review gains the vocabulary it was missing:

1. `chp_rejection_reasons`, seeded with the seven built-ins. Rows rather
   than an enum so an administrator can add one without a migration;
   built-ins carry a stable `code` and render through the locale catalogs
   (`payment.reject.<code>`), so the user reads the reason in their own
   language.
2. `MISMATCH` and `CANCELLED` on the `paymentstatus` enum. MISMATCH is
   deliberately not REJECTED: "the number you typed does not match your
   receipt" is a correctable mistake the user can resubmit from, not a
   judgement about the payment.
3. `verified_amount` and `rejection_reason_id` on receipts. Keeping the
   declared and the observed figure side by side is what lets the message
   name both numbers instead of saying "wrong amount".

Additive only. No existing row is read or rewritten, and every new column
is nullable — an in-flight PENDING receipt is unaffected.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7e9c31b56"
down_revision: Union[str, None] = "c8d3a51fb742"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (code, sort_order). The label lives in the locale catalogs, never here —
# storing English in the database is how a "translated" product ends up
# showing one language to everyone.
BUILT_IN_REASONS = [
    ("incorrect_amount", 10),
    ("suspicious_receipt", 20),
    ("unreadable_receipt", 30),
    ("unverifiable", 40),
    ("wrong_destination", 50),
    ("duplicate_payment", 60),
    ("other", 70),
]


def upgrade() -> None:
    # Alembic runs the whole upgrade in one transaction and PostgreSQL
    # refuses to use an enum label added within it. autocommit_block is the
    # documented escape; IF NOT EXISTS keeps a partially-applied deploy
    # re-runnable, which matters more here because this step self-commits.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'MISMATCH'")
        op.execute("ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'CANCELLED'")

    reasons = op.create_table(
        "chp_rejection_reasons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        # NOT NULL to match the model's non-Optional annotation. Written
        # nullable first and caught by `alembic check` — the same drift
        # a91c4e7f20b8 exists to correct.
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_rejection_reason_code"),
    )
    op.bulk_insert(
        reasons,
        [
            {"code": code, "label": None, "sort_order": order, "is_active": True}
            for code, order in BUILT_IN_REASONS
        ],
    )

    op.add_column(
        "chp_payment_receipts", sa.Column("verified_amount", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column(
        "chp_payment_receipts", sa.Column("rejection_reason_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_receipt_rejection_reason",
        "chp_payment_receipts",
        "chp_rejection_reasons",
        ["rejection_reason_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_receipt_rejection_reason", "chp_payment_receipts", type_="foreignkey")
    op.drop_column("chp_payment_receipts", "rejection_reason_id")
    op.drop_column("chp_payment_receipts", "verified_amount")
    op.drop_table("chp_rejection_reasons")
    # The two enum labels stay: PostgreSQL cannot drop a single label, and
    # rebuilding `paymentstatus` would mean rewriting every receipt row —
    # a far larger risk than two unused labels. Any receipt already in
    # MISMATCH/CANCELLED would also become unreadable, so this is the safe
    # direction even though it is not a perfect inverse.
