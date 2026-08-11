"""public movie code and premium flag on titles

Revision ID: f2b9c04e7a13
Revises: e1a7d2c94f63
Create Date: 2026-08-11

Two columns on `chp_titles`, both genuinely new information with nowhere
existing to put them:

`code` — the short number a viewer types to find a title. Deliberately
not the primary key: an id leaks how much catalog exists and which rows
were deleted, can never be reassigned, and would tie a public-facing
identifier to an internal sequence forever. Unique, so one code can never
resolve to two films, and nullable so a title can exist before it is
given one.

Existing rows are backfilled with sequential codes from 1000, ordered by
id so the numbering is stable and reproducible. Purely additive: no row
loses anything, and a title that somehow already had a code would be left
alone (there are none — the column does not exist yet).

Future codes come from a dedicated sequence rather than from `MAX(code)`.
That distinction matters: a maximum taken over surviving rows hands a
deleted title's number to the next one, and that number may already be
printed on a poster or sitting in a channel post. A sequence is a
high-water mark — it does not roll back on delete and it is atomic under
concurrency, which `SELECT MAX(...) + 1` is not.

`is_premium` — whether watching requires a subscription. NOT NULL with a
`false` server default, so every existing title stays exactly as
accessible as it is today and no backfill decision is made on an
administrator's behalf.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2b9c04e7a13"
down_revision: Union[str, None] = "e1a7d2c94f63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Low enough to stay short to type, high enough that codes never collide
# with the small numbers used elsewhere in the bot's numeric interactions.
FIRST_CODE = 1000

# Owns the numbering from here on. Named rather than tied to a column, so
# it is never dropped by an unrelated column change.
CODE_SEQUENCE = "chp_title_code_seq"


def upgrade() -> None:
    op.add_column("chp_titles", sa.Column("code", sa.String(length=16), nullable=True))
    op.add_column(
        "chp_titles",
        sa.Column(
            "is_premium",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # Ordered by id so a re-run on a copy of the same data produces the
    # same codes. Written before the unique index exists, so a duplicate
    # would fail loudly at index creation rather than silently persisting.
    op.execute(
        sa.text(
            """
            UPDATE chp_titles AS t
            SET code = numbered.assigned::text
            FROM (
                SELECT id, :first + ROW_NUMBER() OVER (ORDER BY id) - 1 AS assigned
                FROM chp_titles
            ) AS numbered
            WHERE t.id = numbered.id
            """
        ).bindparams(first=FIRST_CODE)
    )

    op.create_index("ix_chp_titles_code", "chp_titles", ["code"], unique=True)
    op.create_index("ix_chp_titles_is_premium", "chp_titles", ["is_premium"])

    # Starts past whatever the backfill just assigned, so the first new
    # title continues the sequence rather than colliding with an existing
    # code. `COALESCE` covers an empty catalog.
    op.execute(sa.text(f"CREATE SEQUENCE IF NOT EXISTS {CODE_SEQUENCE}"))
    op.execute(
        sa.text(
            f"""
            SELECT setval(
                '{CODE_SEQUENCE}',
                GREATEST(
                    COALESCE((SELECT MAX(code::bigint) FROM chp_titles
                              WHERE code ~ '^[0-9]+$'), 0),
                    :first - 1
                )
            )
            """
        ).bindparams(first=FIRST_CODE)
    )


def downgrade() -> None:
    op.execute(sa.text(f"DROP SEQUENCE IF EXISTS {CODE_SEQUENCE}"))
    op.drop_index("ix_chp_titles_is_premium", table_name="chp_titles")
    op.drop_index("ix_chp_titles_code", table_name="chp_titles")
    op.drop_column("chp_titles", "is_premium")
    op.drop_column("chp_titles", "code")
