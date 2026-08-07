"""database-driven subscription plans

Revision ID: e58a3c7b91d4
Revises: d72e4f1c8b35
Create Date: 2026-08-07

Moves subscription plans out of the two-member SubscriptionPlan enum and
the PREMIUM_PRICE / PREMIUM_SUBSCRIPTION_DAYS environment variables, and
into `chp_subscription_plans`.

**Expand, not replace.** `plan_id` is added alongside the existing enum
columns and backfilled; the enum columns are left in place. Dropping them
here would break the currently-deployed release the moment the migration
ran, since it still reads them — and migrations are applied before the
new code ships. Contracting them is a follow-up, tracked in TASKS.md.

Seeding preserves what users already have:

  * A `premium` plan is created from the values in force today, so the
    single existing subscriber keeps exactly the terms they bought.
  * A `free` plan exists so "no paid subscription" has a row to point at
    rather than a NULL that every caller must special-case.
  * Every existing subscription and subscription receipt is repointed at
    the matching plan by its old enum value.

Idempotent: re-running finds the plans by code and re-backfills only
rows whose plan_id is still NULL, so a partially-applied deploy can be
retried without duplicating plans.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision: str = "e58a3c7b91d4"
down_revision: Union[str, None] = "d72e4f1c8b35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chp_subscription_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("price", sa.Numeric(precision=12, scale=2), server_default="0", nullable=False),
        sa.Column("duration_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("benefits", sa.ARRAY(sa.String(length=200)), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_free", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_subscription_plan_code"),
    )
    op.create_index("ix_chp_subscription_plans_code", "chp_subscription_plans", ["code"])
    op.create_index("ix_chp_subscription_plans_is_active", "chp_subscription_plans", ["is_active"])

    op.create_table(
        "chp_subscription_features",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_subscription_feature_code"),
    )
    op.create_index("ix_chp_subscription_features_code", "chp_subscription_features", ["code"])

    op.create_table(
        "chp_plan_features",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["plan_id"], ["chp_subscription_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feature_id"], ["chp_subscription_features.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "feature_id", name="uq_plan_feature"),
    )
    op.create_index("ix_chp_plan_features_plan_id", "chp_plan_features", ["plan_id"])
    op.create_index("ix_chp_plan_features_feature_id", "chp_plan_features", ["feature_id"])

    # Nullable on purpose: the legacy enum columns stay authoritative for
    # the currently-deployed release, and a NOT NULL here would reject its
    # inserts. Tightened when the enum is dropped.
    op.add_column("chp_subscriptions", sa.Column("plan_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_subscriptions_plan", "chp_subscriptions", "chp_subscription_plans", ["plan_id"], ["id"]
    )
    op.create_index("ix_chp_subscriptions_plan_id", "chp_subscriptions", ["plan_id"])

    op.add_column("chp_payment_receipts", sa.Column("plan_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_receipts_plan", "chp_payment_receipts", "chp_subscription_plans", ["plan_id"], ["id"]
    )
    op.create_index("ix_chp_payment_receipts_plan_id", "chp_payment_receipts", ["plan_id"])

    _seed_and_backfill()


def _seed_and_backfill() -> None:
    connection = op.get_bind()

    def upsert_plan(code, name, price, days, benefits, is_free, sort_order, description):
        """Finds the plan by code or creates it — makes a re-run a no-op."""
        existing = connection.execute(
            sa.text("SELECT id FROM chp_subscription_plans WHERE code = :code"), {"code": code}
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        return connection.execute(
            sa.text(
                "INSERT INTO chp_subscription_plans "
                "(code, name, description, price, duration_days, benefits, is_active, is_free, sort_order) "
                "VALUES (:code, :name, :description, :price, :days, :benefits, true, :is_free, :sort_order) "
                "RETURNING id"
            ),
            {
                "code": code,
                "name": name,
                "description": description,
                "price": price,
                "days": days,
                "benefits": benefits,
                "is_free": is_free,
                "sort_order": sort_order,
            },
        ).scalar_one()

    free_id = upsert_plan(
        "free", "Free", 0, 36500,
        ["Katalogdan bepul kontent", "3 AI so'rov / kun"],
        True, 0,
        "Default access for every user.",
    )
    # Seeded from the values actually in force, so the existing subscriber's
    # terms are reproduced rather than guessed at.
    premium_id = upsert_plan(
        "premium", "Premium",
        float(settings.PREMIUM_PRICE), int(settings.PREMIUM_SUBSCRIPTION_DAYS),
        ["Cheksiz AI tavsiyalar", "Barcha kontentga kirish"],
        False, 1,
        "Full access with unlimited AI recommendations.",
    )

    # Repoint history. Only rows still NULL are touched, so a re-run cannot
    # overwrite a plan an administrator has since changed by hand.
    for table, column in (("chp_subscriptions", "plan"), ("chp_payment_receipts", "subscription_plan")):
        for enum_value, plan_id in (("FREE", free_id), ("PREMIUM", premium_id)):
            connection.execute(
                sa.text(
                    f"UPDATE {table} SET plan_id = :plan_id "
                    f"WHERE {column} = :enum_value AND plan_id IS NULL"
                ),
                {"plan_id": plan_id, "enum_value": enum_value},
            )


def downgrade() -> None:
    op.drop_index("ix_chp_payment_receipts_plan_id", table_name="chp_payment_receipts")
    op.drop_constraint("fk_receipts_plan", "chp_payment_receipts", type_="foreignkey")
    op.drop_column("chp_payment_receipts", "plan_id")

    op.drop_index("ix_chp_subscriptions_plan_id", table_name="chp_subscriptions")
    op.drop_constraint("fk_subscriptions_plan", "chp_subscriptions", type_="foreignkey")
    op.drop_column("chp_subscriptions", "plan_id")

    # Safe because the enum columns were never stopped being written — the
    # previous release's source of truth is still intact underneath.
    op.drop_table("chp_plan_features")
    op.drop_table("chp_subscription_features")
    op.drop_table("chp_subscription_plans")
