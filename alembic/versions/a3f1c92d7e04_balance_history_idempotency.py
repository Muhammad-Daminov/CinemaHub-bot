"""balance history idempotency backstop

Revision ID: a3f1c92d7e04
Revises: 6b7ec8ebd218
Create Date: 2026-08-05

Database-level guarantee that one source event credits a balance once.

The application fix (a row lock in app/services/payment_review.py) is
what actually prevents the duplicate; this index is the backstop for
every future path that touches the ledger without knowing that history.
Approving receipt #1 five times produced five identical TOPUP rows, and
nothing in the schema objected.

Scoped to (user_id, tx_type, reference_id) rather than reference_id
alone, because reference_id is only unique within its own kind of event:
two different users redeeming the same promo code both legitimately
write PROMO_CREDIT with that promo's id, and a global constraint would
reject the second.

Partial on reference_id IS NOT NULL so that ADMIN_ADJUSTMENT rows, which
carry no reference, remain unconstrained.

⚠️ PRE-FLIGHT: this migration FAILS on any database still holding
duplicate ledger rows. Production currently has four such rows from the
receipt #1 incident. Clean them up first — see CHANGELOG under
[Unreleased] — then apply.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f1c92d7e04"
down_revision: Union[str, None] = "6b7ec8ebd218"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_balance_history_event"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "chp_balance_history",
        ["user_id", "tx_type", "reference_id"],
        unique=True,
        # sa.text, not op.inline_literal — the latter renders a quoted
        # string literal, which Postgres then rejects as a non-boolean
        # WHERE clause.
        postgresql_where=sa.text("reference_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="chp_balance_history")
