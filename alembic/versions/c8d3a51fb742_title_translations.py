"""per-language title names and descriptions

Revision ID: c8d3a51fb742
Revises: b2f7c1a95e30
Create Date: 2026-08-08

Phase 7 (FR-6 requirement 3). One new table, `chp_title_translations`,
holding a title's name and description in one interface language.

A table rather than `name_ru` / `name_en` columns on `chp_titles`: a
fourth language then costs a row instead of a migration against
production, and a title with no translation has no row rather than a
column full of NULLs.

**No backfill, and `chp_titles.name` is not touched.** That column stays
authoritative and is the fallback for every language — it holds the
Uzbek name this catalog is indexed by, which is why `apply_tmdb_match`
has always refused to overwrite it. Copying it into a `uz` row would
duplicate the same string 102 times and create two places to edit it.

The unique constraint on (title_id, language) is what makes the admin
write path an upsert rather than a delete-and-insert, so an edit keeps
its `created_at` and two administrators saving at once cannot collide.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8d3a51fb742"
down_revision: Union[str, None] = "b2f7c1a95e30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chp_title_translations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title_id", sa.Integer(), nullable=False),
        # Reuses the existing `uilanguage` type rather than declaring a new
        # one. It must be postgresql.ENUM, not sa.Enum: only the dialect
        # type accepts create_type=False, and without it CREATE TABLE tries
        # to CREATE TYPE a type chp_users.language already owns and fails
        # with DuplicateObjectError.
        sa.Column(
            "language",
            postgresql.ENUM("UZ", "RU", "EN", name="uilanguage", create_type=False),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column(
            "source",
            sa.Enum("MANUAL", "TMDB", name="translationsource"),
            nullable=False,
        ),
        # NOT NULL, matching the model's non-Optional annotations. Written
        # nullable first and caught by `alembic check` — the same drift
        # a91c4e7f20b8 exists to correct, and the reason the test schema
        # (built from the models) and production must be compared.
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        # Cascade at the database as well as in delete_title's child-first
        # Core deletes: a translation outliving its title would be a row
        # nothing can ever reach or remove.
        sa.ForeignKeyConstraint(["title_id"], ["chp_titles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("title_id", "language", name="uq_title_translation_language"),
    )
    op.create_index(
        "ix_chp_title_translations_title_id", "chp_title_translations", ["title_id"]
    )
    op.create_index(
        "ix_chp_title_translations_language", "chp_title_translations", ["language"]
    )
    # Search matches translated names with ILIKE across every language, so
    # this column is read on every catalog search, not just on display.
    op.create_index("ix_chp_title_translations_name", "chp_title_translations", ["name"])


def downgrade() -> None:
    op.drop_index("ix_chp_title_translations_name", table_name="chp_title_translations")
    op.drop_index("ix_chp_title_translations_language", table_name="chp_title_translations")
    op.drop_index("ix_chp_title_translations_title_id", table_name="chp_title_translations")
    op.drop_table("chp_title_translations")
    # Dropped because this revision created it. `uilanguage` is left alone
    # — chp_users.language depends on it and predates this table.
    sa.Enum(name="translationsource").drop(op.get_bind(), checkfirst=True)
