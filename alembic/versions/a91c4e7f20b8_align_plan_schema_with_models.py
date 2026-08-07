"""align the plan tables with their models

Revision ID: a91c4e7f20b8
Revises: f6b2d94ae713
Create Date: 2026-08-07

Corrects drift introduced by hand-writing migration e58a3c7b91d4.

`alembic check` compares the models against the migrated schema, and it
reported three differences. They mattered more than they look: the test
suite builds its schema with `metadata.create_all` (from the models)
while production is built by migrations, so the two were running against
subtly different databases — the situation where a test passes and
production does not.

1. `created_at` / `updated_at` were left nullable. The models declare
   them as non-Optional, so SQLAlchemy infers NOT NULL.
2. `code` on plans and features carried *two* uniqueness mechanisms: a
   named UNIQUE constraint from the migration and a separate non-unique
   index. The models ask for a single unique index. The duplicate cost an
   extra index write on every insert and made the drift report noisy.

No data changes; every affected column is already populated by a server
default, and both existing `code` columns are already unique.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a91c4e7f20b8"
down_revision: Union[str, None] = "f6b2d94ae713"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TIMESTAMP_COLUMNS = [
    ("chp_subscription_plans", "created_at"),
    ("chp_subscription_plans", "updated_at"),
    ("chp_subscription_features", "created_at"),
    ("chp_plan_features", "created_at"),
]

# (table, redundant unique constraint, index to rebuild as unique)
_CODE_INDEXES = [
    ("chp_subscription_plans", "uq_subscription_plan_code", "ix_chp_subscription_plans_code"),
    ("chp_subscription_features", "uq_subscription_feature_code", "ix_chp_subscription_features_code"),
]


def upgrade() -> None:
    for table, column in _TIMESTAMP_COLUMNS:
        # Safe without a backfill: the server default has populated every
        # existing row, so none of them are NULL.
        op.alter_column(
            table, column, existing_type=sa.DateTime(timezone=True), nullable=False
        )

    for table, constraint, index in _CODE_INDEXES:
        op.drop_constraint(constraint, table, type_="unique")
        op.drop_index(index, table_name=table)
        op.create_index(index, table, ["code"], unique=True)


def downgrade() -> None:
    for table, constraint, index in _CODE_INDEXES:
        op.drop_index(index, table_name=table)
        op.create_index(index, table, ["code"])
        op.create_unique_constraint(constraint, table, ["code"])

    for table, column in _TIMESTAMP_COLUMNS:
        op.alter_column(
            table, column, existing_type=sa.DateTime(timezone=True), nullable=True
        )
