"""photo and video on broadcasts

Revision ID: d5e8c62a91b4
Revises: c9d4a71e35f8
Create Date: 2026-08-10

Phase 9E-B. Two columns on `chp_broadcasts`.

Media travels as a Telegram `file_id`: the bytes stay on Telegram's
servers and are never downloaded, stored or proxied by us, so there is no
media table and no blob column here. `media_file_id` is a 255-character
reference, nothing more.

`media_type` is NOT NULL defaulting to NONE, so every existing broadcast
— including completed historical ones — remains exactly what it was: a
text broadcast. No backfill, no rewrite.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5e8c62a91b4"
down_revision: Union[str, None] = "c9d4a71e35f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # add_column does not create an enum type the way create_table does —
    # it emits the ALTER and assumes the type exists. Create it first.
    media = postgresql.ENUM("NONE", "PHOTO", "VIDEO", name="broadcastmedia")
    media.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "chp_broadcasts",
        sa.Column(
            "media_type",
            postgresql.ENUM(
                "NONE", "PHOTO", "VIDEO", name="broadcastmedia", create_type=False
            ),
            server_default=sa.text("'NONE'"),
            nullable=False,
        ),
    )
    op.add_column("chp_broadcasts", sa.Column("media_file_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("chp_broadcasts", "media_file_id")
    op.drop_column("chp_broadcasts", "media_type")
    postgresql.ENUM(name="broadcastmedia").drop(op.get_bind(), checkfirst=True)
