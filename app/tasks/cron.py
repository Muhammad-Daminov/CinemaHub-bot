"""
Scheduled maintenance tasks.

Deliberately a standalone script (`python -m app.tasks.cron`), meant to
run as a Render Cron Job, NOT another asyncio.sleep loop bolted onto
the web service's lifespan like Phase 5's auto-delete worker. Reasons:

  - Most state in this system already self-cleans via Redis TTLs
    (throttling keys, AI quota counters, auto-delete queue) — no cron
    needed for those at all.
  - What's left (monthly order-limit reset, stale payment receipts,
    expired promos) only needs to run once a day/month, not be polled
    continuously — a separate scheduled process is the right shape,
    and it keeps this maintenance work decoupled from the web
    service's own uptime/scaling.
  - It's naturally safe to run more than once (every operation here
    is idempotent — resetting an already-reset counter or expiring an
    already-expired receipt is a no-op), so overlapping Render cron
    runs are never a correctness risk.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.db.models.payment import PaymentReceipt, PaymentStatus
from app.db.models.promo import PromoCode
from app.db.models.user import User
from app.db.session import db_session_ctx, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cron")

STALE_RECEIPT_DAYS = 7


async def reset_monthly_order_limits(session) -> int:
    """Zeroes monthly_orders_count for anyone whose reset date has rolled over."""
    today = datetime.now(timezone.utc).date()
    result = await session.execute(
        update(User)
        .where(User.monthly_limit_reset_at < today.replace(day=1))
        .values(monthly_orders_count=0, monthly_limit_reset_at=today)
    )
    return result.rowcount or 0


async def expire_stale_payment_receipts(session) -> int:
    """Auto-rejects PENDING receipts nobody reviewed within STALE_RECEIPT_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_RECEIPT_DAYS)
    result = await session.execute(
        update(PaymentReceipt)
        .where(PaymentReceipt.status == PaymentStatus.PENDING, PaymentReceipt.created_at < cutoff)
        .values(
            status=PaymentStatus.REJECTED,
            admin_notes=f"Avtomatik bekor qilindi ({STALE_RECEIPT_DAYS} kun ichida ko'rib chiqilmadi).",
            reviewed_at=datetime.now(timezone.utc),
        )
    )
    return result.rowcount or 0


async def deactivate_expired_promos(session) -> int:
    """Flips is_active off for promo codes past their valid_until — query hygiene, not a hard gate."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(PromoCode)
        .where(PromoCode.is_active.is_(True), PromoCode.valid_until.is_not(None), PromoCode.valid_until < now)
        .values(is_active=False)
    )
    return result.rowcount or 0


async def run_all() -> None:
    async with db_session_ctx() as session:
        reset_count = await reset_monthly_order_limits(session)
        expired_receipts = await expire_stale_payment_receipts(session)
        deactivated_promos = await deactivate_expired_promos(session)

    logger.info(
        "cron done: monthly_limits_reset=%d stale_receipts_expired=%d promos_deactivated=%d",
        reset_count, expired_receipts, deactivated_promos,
    )
    await engine.dispose()  # short-lived process — release the pool explicitly before exit


if __name__ == "__main__":
    asyncio.run(run_all())
