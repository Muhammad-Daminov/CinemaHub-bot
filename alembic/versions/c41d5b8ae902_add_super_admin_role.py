"""add SUPER_ADMIN to the userrole enum

Revision ID: c41d5b8ae902
Revises: a3f1c92d7e04
Create Date: 2026-08-07

Split from the migration that uses it on purpose.

PostgreSQL will not let a value added by ALTER TYPE ... ADD VALUE be used
in the same transaction that added it, and Alembic runs each migration in
one transaction. Adding the label here and seeding roles in the next
revision is the supported way round that — the alternative is committing
mid-migration, which gives up the ability to roll the step back.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c41d5b8ae902"
down_revision: Union[str, None] = "a3f1c92d7e04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Committed on its own connection. PostgreSQL refuses to *use* an enum
    # label added in the still-open transaction that added it, and Alembic
    # runs an entire `upgrade head` as one transaction by default — so
    # without this the next revision fails with
    # UnsafeNewEnumValueUsageError even though it is a separate revision.
    #
    # IF NOT EXISTS so a partially-applied deploy can be re-run safely,
    # which matters more here precisely because this step self-commits.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SUPER_ADMIN'")


def downgrade() -> None:
    # PostgreSQL cannot drop a single enum label. Removing it would mean
    # rebuilding the type and rewriting every dependent column, which is a
    # far larger risk than leaving an unused label in place.
    pass
