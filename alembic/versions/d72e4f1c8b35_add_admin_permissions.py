"""add chp_admin_permissions and seed administrators

Revision ID: d72e4f1c8b35
Revises: c41d5b8ae902
Create Date: 2026-08-07

Moves administrator authority out of the ADMIN_IDS environment variable
and into the database.

Seeding matters here. Before this migration, every id in ADMIN_IDS had
unrestricted access; after it, authority comes from a role plus explicit
grants. Migrating without seeding would lock the existing administrators
out of a running platform, so each of them is promoted to ADMIN and given
the full permission set — exactly what they already had. Narrowing them
is then the Super Admin's decision, made deliberately in the panel rather
than silently by a deploy.

The Super Admin named by SUPER_ADMIN_TELEGRAM_ID is promoted and holds
every permission implicitly, so it gets no rows of its own.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.config import settings
from app.core.permissions import ALL_PERMISSIONS

revision: str = "d72e4f1c8b35"
down_revision: Union[str, None] = "c41d5b8ae902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chp_admin_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.Column("granted_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["chp_users.id"]),
        sa.ForeignKeyConstraint(["granted_by_id"], ["chp_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "permission", name="uq_admin_permission"),
    )
    op.create_index(
        "ix_chp_admin_permissions_user_id", "chp_admin_permissions", ["user_id"]
    )

    connection = op.get_bind()

    super_admin_id = settings.SUPER_ADMIN_TELEGRAM_ID
    if super_admin_id:
        connection.execute(
            sa.text("UPDATE chp_users SET role = 'SUPER_ADMIN' WHERE telegram_id = :tid"),
            {"tid": super_admin_id},
        )

    # Everyone else previously in ADMIN_IDS keeps the access they had.
    legacy_admin_ids = [i for i in settings.admin_ids_list if i != super_admin_id]
    if not legacy_admin_ids:
        return

    connection.execute(
        sa.text("UPDATE chp_users SET role = 'ADMIN' WHERE telegram_id = ANY(:ids)"),
        {"ids": legacy_admin_ids},
    )
    rows = connection.execute(
        sa.text("SELECT id FROM chp_users WHERE telegram_id = ANY(:ids)"),
        {"ids": legacy_admin_ids},
    ).fetchall()

    for (user_id,) in rows:
        for permission in sorted(p.value for p in ALL_PERMISSIONS):
            connection.execute(
                sa.text(
                    "INSERT INTO chp_admin_permissions (user_id, permission) "
                    "VALUES (:uid, :perm) ON CONFLICT DO NOTHING"
                ),
                {"uid": user_id, "perm": permission},
            )


def downgrade() -> None:
    connection = op.get_bind()
    # Roles first: dropping the table while rows still claim ADMIN would
    # leave administrators with a role and no permissions behind it.
    connection.execute(
        sa.text("UPDATE chp_users SET role = 'USER' WHERE role IN ('ADMIN', 'SUPER_ADMIN')")
    )
    op.drop_index("ix_chp_admin_permissions_user_id", table_name="chp_admin_permissions")
    op.drop_table("chp_admin_permissions")
