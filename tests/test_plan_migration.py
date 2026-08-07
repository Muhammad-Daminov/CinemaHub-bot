"""
Migration e58a3c7b91d4 — moving plans from the enum into a table.

Runs the real migration against a throwaway database seeded with
pre-migration data, rather than asserting on the models. The thing that
can go wrong here is not the schema; it is losing the subscription
someone already paid for, and only executing the migration proves that
did not happen.

Each test owns its own database so a failure cannot poison the next.
"""
import os
import subprocess
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = [requires_db, pytest.mark.db]

PGBIN = os.environ.get("PGBIN", "/usr/lib/postgresql/16/bin")
PRE_MIGRATION_REVISION = "d72e4f1c8b35"
PLAN_REVISION = "e58a3c7b91d4"


def _admin_url() -> tuple[str, str, int, str]:
    """(user, host, port, base) parsed from TEST_DATABASE_URL."""
    from urllib.parse import urlsplit

    parts = urlsplit(TEST_DATABASE_URL)
    return parts.username or "postgres", parts.hostname or "127.0.0.1", parts.port or 5432, parts.path


@pytest.fixture
async def migration_db():
    """
    A fresh database migrated to just *before* the plan migration.

    Named uniquely per test: alembic runs against a real database, and
    sharing one between tests would let an earlier upgrade leak into a
    later assertion.
    """
    user, host, port, _ = _admin_url()
    name = f"mig_{uuid.uuid4().hex[:12]}"
    url = f"postgresql+asyncpg://{user}@{host}:{port}/{name}"

    subprocess.run(
        [f"{PGBIN}/createdb", "-h", host, "-p", str(port), "-U", user, name],
        check=True, capture_output=True,
    )
    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run(
        ["alembic", "upgrade", PRE_MIGRATION_REVISION],
        check=True, capture_output=True, env=env,
    )
    try:
        yield url, env
    finally:
        subprocess.run(
            [f"{PGBIN}/dropdb", "--force", "-h", host, "-p", str(port), "-U", user, name],
            check=False, capture_output=True,
        )


async def _seed_legacy(url: str) -> None:
    """A user holding a PREMIUM subscription and an approved PREMIUM receipt."""
    engine = create_async_engine(url, connect_args={"statement_cache_size": 0})
    async with engine.begin() as c:
        # Columns whose defaults are Python-side only must be supplied
        # explicitly here — this is raw SQL, not the ORM.
        await c.execute(text(
            "INSERT INTO chp_users "
            "(id, telegram_id, referral_code, balance, role, language, language_selected, "
            " ai_requests_today, monthly_orders_count, is_banned) "
            "VALUES (1, 111, 'REF111', 0, 'USER', 'UZ', true, 0, 0, false)"
        ))
        await c.execute(text(
            "INSERT INTO chp_subscriptions (user_id, plan, expires_at, auto_renew) "
            "VALUES (1, 'PREMIUM', now() + interval '20 days', false)"
        ))
        await c.execute(text(
            "INSERT INTO chp_payment_receipts "
            "(user_id, purpose, subscription_plan, amount, receipt_photo_file_id, status) "
            "VALUES (1, 'SUBSCRIPTION', 'PREMIUM', 50000, 'f1', 'APPROVED')"
        ))
    await engine.dispose()


def _upgrade(env, revision=PLAN_REVISION):
    return subprocess.run(
        ["alembic", "upgrade", revision], capture_output=True, text=True, env=env
    )


async def _query(url: str, sql: str):
    engine = create_async_engine(url, connect_args={"statement_cache_size": 0})
    async with engine.connect() as c:
        rows = (await c.execute(text(sql))).fetchall()
    await engine.dispose()
    return rows


async def test_existing_subscription_survives_and_is_repointed(migration_db):
    """The property that matters: nobody loses access they paid for."""
    url, env = migration_db
    await _seed_legacy(url)
    before = await _query(url, "SELECT expires_at FROM chp_subscriptions WHERE user_id = 1")

    assert _upgrade(env).returncode == 0

    rows = await _query(
        url,
        "SELECT s.plan, p.code, s.expires_at FROM chp_subscriptions s "
        "JOIN chp_subscription_plans p ON p.id = s.plan_id WHERE s.user_id = 1",
    )
    assert len(rows) == 1, "the subscription must still exist"
    legacy_plan, code, expires_at = rows[0]
    assert code == "premium", "repointed at the matching plan"
    assert legacy_plan == "PREMIUM", "legacy column left intact for rollback"
    assert expires_at == before[0][0], "the term itself must not move"


async def test_receipts_are_repointed_too(migration_db):
    url, env = migration_db
    await _seed_legacy(url)
    assert _upgrade(env).returncode == 0

    rows = await _query(
        url,
        "SELECT p.code FROM chp_payment_receipts r "
        "JOIN chp_subscription_plans p ON p.id = r.plan_id",
    )
    assert [r[0] for r in rows] == ["premium"]


async def test_seeded_premium_reproduces_the_terms_in_force(migration_db):
    """
    The seeded plan must match what the platform was actually charging, or
    the existing subscriber's renewal silently changes price or length.
    """
    from app.core.config import settings

    url, env = migration_db
    assert _upgrade(env).returncode == 0

    rows = await _query(
        url, "SELECT price, duration_days FROM chp_subscription_plans WHERE code = 'premium'"
    )
    price, days = rows[0]
    assert float(price) == float(settings.PREMIUM_PRICE)
    assert days == int(settings.PREMIUM_SUBSCRIPTION_DAYS)


async def test_exactly_one_free_plan_is_seeded(migration_db):
    url, env = migration_db
    assert _upgrade(env).returncode == 0
    rows = await _query(url, "SELECT code FROM chp_subscription_plans WHERE is_free")
    assert [r[0] for r in rows] == ["free"]


async def test_no_orphaned_plan_references_after_migration(migration_db):
    url, env = migration_db
    await _seed_legacy(url)
    assert _upgrade(env).returncode == 0

    orphans = await _query(
        url,
        "SELECT count(*) FROM chp_subscriptions s "
        "LEFT JOIN chp_subscription_plans p ON p.id = s.plan_id "
        "WHERE s.plan_id IS NOT NULL AND p.id IS NULL",
    )
    assert orphans[0][0] == 0

    unmapped = await _query(
        url, "SELECT count(*) FROM chp_subscriptions WHERE plan_id IS NULL"
    )
    assert unmapped[0][0] == 0, "every legacy row must have been mapped"


async def test_migration_is_idempotent_across_downgrade_and_reupgrade(migration_db):
    """
    A deploy that has to be rolled back and retried must not end up with
    duplicate plans or a subscription pointing at a plan that no longer
    exists.
    """
    url, env = migration_db
    await _seed_legacy(url)
    assert _upgrade(env).returncode == 0

    down = subprocess.run(
        ["alembic", "downgrade", PRE_MIGRATION_REVISION],
        capture_output=True, text=True, env=env,
    )
    assert down.returncode == 0, down.stderr

    # The legacy enum column is still the source of truth underneath, so a
    # downgrade loses nothing.
    rows = await _query(url, "SELECT plan FROM chp_subscriptions WHERE user_id = 1")
    assert [r[0] for r in rows] == ["PREMIUM"]

    assert _upgrade(env).returncode == 0
    plans = await _query(url, "SELECT code FROM chp_subscription_plans ORDER BY sort_order")
    assert [p[0] for p in plans] == ["free", "premium"], "no duplicates after re-upgrade"

    rows = await _query(
        url,
        "SELECT p.code FROM chp_subscriptions s "
        "JOIN chp_subscription_plans p ON p.id = s.plan_id WHERE s.user_id = 1",
    )
    assert [r[0] for r in rows] == ["premium"]


async def test_migrating_an_empty_database_is_safe(migration_db):
    """A fresh install has no legacy rows; the seed must still produce both plans."""
    url, env = migration_db
    assert _upgrade(env).returncode == 0
    plans = await _query(url, "SELECT code FROM chp_subscription_plans ORDER BY sort_order")
    assert [p[0] for p in plans] == ["free", "premium"]
