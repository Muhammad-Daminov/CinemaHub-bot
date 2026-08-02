"""
Alembic migration environment.

SAFETY STRATEGY for the existing NeonDB schema:
Autogenerate normally proposes DROP statements for any DB table/column
that isn't present in the target metadata. Since app.db.base.Base only
ever contains models WE define, every legacy table would look
"removed" to Alembic and get flagged for deletion.

`include_object()` below prevents that: any object that already exists
in the database (reflected=True) but has no corresponding definition
in our metadata (compare_to is None) is excluded from comparison
entirely. Alembic will then never generate a DROP/ALTER for it — it
can only ever generate ADD operations for genuinely new objects.

This makes "additive-only" a structural property of the setup, not a
matter of remembering to review diffs carefully (though reviewing
generated migrations before applying is still required practice).
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.base import Base
import app.db.models

# Import new models here so they register on Base.metadata before
# autogenerate runs, e.g.:
#   from app.db.models import user, subscription  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Inject the real DB URL from our single settings source (never from alembic.ini).
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def include_object(object, name, type_, reflected, compare_to):
    """Exclude any existing DB object not owned by our metadata — see module docstring."""
    if reflected and compare_to is None:
        return False
    return True


def run_migrations_offline() -> None:
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
