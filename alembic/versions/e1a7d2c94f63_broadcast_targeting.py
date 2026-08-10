"""interest and badge targeting on broadcasts

Revision ID: e1a7d2c94f63
Revises: d5e8c62a91b4
Create Date: 2026-08-10

Phase 9E-C. Two new labels on the audience enum, and one nullable column.

Purely additive. Every existing broadcast keeps its audience and gets
`target_value` NULL, which is exactly what an untargeted audience means —
so completed historical rows read correctly with no backfill and no
rewrite.

`ALTER TYPE … ADD VALUE` cannot run inside a transaction block on
PostgreSQL, hence the autocommit block. It is also irreversible: a label
cannot be dropped from an enum without recreating the type. The downgrade
therefore leaves the two labels in place rather than rebuilding the type
under a column that is in use — an unused label is inert, while a botched
type swap on a live table is not. The column, which is the part that
carries data, does come back out.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1a7d2c94f63"
down_revision: Union[str, None] = "d5e8c62a91b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE broadcastaudience ADD VALUE IF NOT EXISTS 'INTEREST'")
        op.execute("ALTER TYPE broadcastaudience ADD VALUE IF NOT EXISTS 'BADGE'")

    op.add_column(
        "chp_broadcasts", sa.Column("target_value", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("chp_broadcasts", "target_value")
