"""
Test fixtures.

Two rules govern everything here:

1. **Tests never touch production.** `settings.DATABASE_URL` points at the
   live Neon database, and importing anything from `app` loads the real
   `.env`, so a fixture that reached for `settings.DATABASE_URL` would run
   the suite against real users. Database tests read `TEST_DATABASE_URL`
   and nothing else, and refuse outright to run against a Neon host.
2. **Absent database means skipped, not failed.** There is no test
   database provisioned yet (see TASKS.md P0-3), so DB-backed tests skip
   cleanly. They are written and ready for the moment one exists.
"""
import os

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# A test suite that drops tables must never be pointed at the production
# host by accident. Neon is where production lives; refuse it outright
# rather than trusting whoever set the variable.
if TEST_DATABASE_URL and "neon.tech" in TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL points at a Neon host. The suite creates and drops "
        "tables — refusing to run against production infrastructure."
    )

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set — see TASKS.md P0-3 for provisioning a test database",
)


@pytest.fixture(scope="session")
def db_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    return TEST_DATABASE_URL


@pytest.fixture
async def db_factory(db_url):
    """
    A session factory over a freshly built schema, torn down afterwards.

    Yields the *factory* rather than a session because the concurrency
    tests need several independent sessions — one transaction each is the
    only way to exercise a row lock, since two coroutines sharing a
    session share its transaction and can never contend.

    Deliberately builds via `metadata.create_all` rather than running
    Alembic: this verifies the models as the code sees them, and keeps the
    suite from depending on migration history staying replayable.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db.models  # noqa: F401  — populates Base.metadata
    from app.db.base import Base

    engine = create_async_engine(db_url, connect_args={"statement_cache_size": 0})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(bind=engine, expire_on_commit=False)

    await engine.dispose()


@pytest.fixture
async def db_session(db_factory):
    async with db_factory() as session:
        yield session


async def make_user(session, telegram_id: int, balance: str = "0"):
    """Shared factory — every money test needs a user and they all need the same one."""
    from decimal import Decimal

    from app.db.models.user import User

    user = User(
        telegram_id=telegram_id,
        referral_code=f"REF{telegram_id}",
        balance=Decimal(balance),
    )
    session.add(user)
    await session.flush()
    return user


async def make_paid_plan(session, code: str = "premium", price: str = "50000", days: int = 30):
    """
    A purchasable plan.

    Money tests need one now that plans are data: approving a subscription
    receipt resolves the plan to read its duration, and a database built
    from `metadata.create_all` has none of the rows the migration seeds.
    """
    from decimal import Decimal

    from app.db.models.subscription import SubscriptionPlanModel

    plan = SubscriptionPlanModel(
        code=code, name=code.title(), price=Decimal(price), duration_days=days, is_active=True
    )
    session.add(plan)
    await session.flush()
    return plan


async def count_rows(session, model, **filters) -> int:
    """COUNT(*) with equality filters, so assertions read as counts rather than queries."""
    from sqlalchemy import func, select

    stmt = select(func.count()).select_from(model)
    for column, value in filters.items():
        stmt = stmt.where(getattr(model, column) == value)
    return (await session.execute(stmt)).scalar_one()


@pytest.fixture
def silence_bot(monkeypatch):
    """
    Stops approval/rejection from making a real Telegram call.

    `payment_review` notifies the user as part of approving, which is
    correct behaviour and wrong in a test — without this the suite would
    message real people.
    """
    sent: list[tuple[int, str]] = []

    async def fake_send_message(chat_id, text, *args, **kwargs):
        sent.append((chat_id, text))

    from app.bot.instance import bot

    monkeypatch.setattr(bot, "send_message", fake_send_message)
    return sent
