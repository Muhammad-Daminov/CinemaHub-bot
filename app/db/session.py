"""
Async SQLAlchemy engine + session factory.

Tuned for NeonDB's pooled connection endpoint:
- Neon's pooler runs PgBouncer in transaction mode, which does not
  support server-side prepared statements -> we disable asyncpg's
  statement cache (statement_cache_size=0) to avoid
  "prepared statement already exists" errors under load.
- pool_pre_ping guards against Neon's autosuspend/idle-connection
  drops on the free/scale-to-zero tiers.
- Pool sizes stay modest since Neon's own pooler is doing the heavy
  multiplexing; we're not trying to out-pool the pooler.
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,
    connect_args={"statement_cache_size": 0},
)

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session, commits/rolls back, always closes."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def db_session_ctx() -> AsyncGenerator[AsyncSession, None]:
    """Same contract as get_db_session, for use outside FastAPI (aiogram middlewares, cron jobs)."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_connection() -> bool:
    """Lightweight health check — used by /health endpoint and startup checks."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True
