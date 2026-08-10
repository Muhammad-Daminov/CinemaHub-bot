"""card shape and decoration on themes

Revision ID: b8f1e2c74d05
Revises: a2e6d91c4b73
Create Date: 2026-08-10

Phase 9D, second part. Two presentation choices that are not colours, so
they do not belong in `chp_theme_tokens` (which holds validated hex
values keyed by CSS custom property).

Both are short keys validated against a fixed allowlist in
`app.services.themes` — an admin picks "rounded", they never write a
border-radius, so neither column can carry CSS.

NOT NULL with server defaults matching the built-in behaviour, so every
existing theme keeps looking exactly as it does now.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8f1e2c74d05"
down_revision: Union[str, None] = "a2e6d91c4b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chp_themes",
        sa.Column("card_shape", sa.String(length=32), server_default=sa.text("'rounded'"), nullable=False),
    )
    op.add_column(
        "chp_themes",
        sa.Column("decoration", sa.String(length=32), server_default=sa.text("'none'"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chp_themes", "decoration")
    op.drop_column("chp_themes", "card_shape")
