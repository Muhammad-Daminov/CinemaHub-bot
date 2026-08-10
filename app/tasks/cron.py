"""
Scheduled maintenance tasks.

Deliberately a standalone script (`python -m app.tasks.cron`), meant to
be invoked by whatever schedules jobs on the host — `cron`, a `systemd`
timer, a hosted scheduler — and NOT an asyncio.sleep loop bolted onto the
web service's lifespan. Reasons:

  - Most state in this system already self-cleans via Redis TTLs
    (throttling keys, AI quota counters) — no cron
    needed for those at all.
  - What's left (monthly order-limit reset, stale payment receipts,
    expired promos) only needs to run once a day/month, not be polled
    continuously — a separate scheduled process is the right shape,
    and it keeps this maintenance work decoupled from the web
    service's own uptime/scaling.
  - Receipt images are purged 30 days after upload here. That promise
    depends on this script actually being scheduled — with no scheduler
    entry, images are kept indefinitely and nobody is told.

  - Each completed run stamps `last_maintenance_run` in
    chp_system_settings, and the web service warns on startup when that
    stamp is older than 48 hours. That is the only way this repository
    can tell whether the job is scheduled at all — the scheduler entry
    lives on the host, not in this repo. See TASKS.md P0-5.

  - It's naturally safe to run more than once (every operation here
    is idempotent — resetting an already-reset counter or expiring an
    already-expired receipt is a no-op), so overlapping cron runs are
    never a correctness risk.

  - Steps are isolated from one another: one failing step is logged and
    the rest still run, but the heartbeat is stamped only when every step
    succeeded. See `run_all`.
"""
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.db.models.payment import PaymentReceipt, PaymentStatus
from app.db.models.promo import PromoCode
from app.db.models.user import User
from app.bot.instance import bot
from app.db.session import AsyncSessionFactory, db_session_ctx, engine
from app.services.images import purge_expired_receipt_images
from app.services.broadcast import resume_stale_broadcasts
from app.services.personalization import recalculate_stale_profiles
from app.services.settings_store import record_maintenance_run

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


async def _in_session(step):
    """Runs one database step in a transaction of its own."""
    async with db_session_ctx() as session:
        return await step(session)


# Every independent unit of maintenance, in execution order. A list rather
# than a straight-line function so one broken step cannot decide whether
# the others get to run: they share nothing, and losing the monthly limit
# reset because image purging is failing would be a second outage caused
# by the first.
MAINTENANCE_STEPS: tuple[tuple[str, object], ...] = (
    ("monthly_limits_reset", lambda: _in_session(reset_monthly_order_limits)),
    ("stale_receipts_expired", lambda: _in_session(expire_stale_payment_receipts)),
    ("promos_deactivated", lambda: _in_session(deactivate_expired_promos)),
    ("receipt_images_purged", lambda: _in_session(purge_expired_receipt_images)),
    # Keeps personalized feeds current without making a feed render pay for
    # the aggregation. Bounded per run; whatever is missed here is picked up
    # lazily by get_profile the next time it is read.
    ("interest_profiles_refreshed", lambda: _in_session(recalculate_stale_profiles)),
    # Owns its own sessions and sends its own messages, so it must not run
    # inside a maintenance transaction.
    ("broadcasts_resumed", lambda: resume_stale_broadcasts(AsyncSessionFactory, bot)),
)


async def run_all() -> int:
    """
    Runs every maintenance step and returns how many failed.

    **Each step is isolated.** Previously they shared one transaction, so
    the first failure rolled back the work that had already succeeded and
    skipped everything after it — meaning one persistently broken step
    silently suspended *all* maintenance for as long as the bug lived.
    Now a step that raises is logged and the rest still run, which is the
    difference between one thing being broken and everything being broken.

    **The heartbeat still means "all of it completed."** It is stamped only
    when every step succeeded, in a transaction of its own after the fact,
    so a partial run leaves the previous timestamp standing and the startup
    staleness warning fires. That contract is what `maintenance_is_stale`
    and the operator reading the log both depend on; isolating failures
    must not turn the stamp into "something ran".

    Returns a count rather than raising so the caller decides the exit
    status — non-zero is how `cron`'s MAILTO and `systemd`'s `OnFailure`
    learn that a scheduled run needs attention.
    """
    started = time.monotonic()
    logger.info("maintenance starting: %d steps", len(MAINTENANCE_STEPS))

    outcomes: dict[str, int] = {}
    failed: list[str] = []

    try:
        for name, step in MAINTENANCE_STEPS:
            try:
                outcomes[name] = await step()
                logger.info("  %s: ok (%s)", name, outcomes[name])
            except Exception:
                # Logged with a traceback and carried on. Never swallowed:
                # the name lands in `failed`, which suppresses the heartbeat
                # and sets a non-zero exit status.
                failed.append(name)
                logger.exception("  %s: FAILED", name)

        if failed:
            logger.error(
                "maintenance INCOMPLETE in %.1fs — %d/%d steps failed (%s); "
                "heartbeat NOT stamped, staleness warning will fire",
                time.monotonic() - started, len(failed), len(MAINTENANCE_STEPS),
                ", ".join(failed),
            )
        else:
            async with db_session_ctx() as session:
                ran_at = await record_maintenance_run(session)
            logger.info(
                "maintenance complete at %s in %.1fs: %s",
                ran_at.isoformat(),
                time.monotonic() - started,
                " ".join(f"{name}={count}" for name, count in outcomes.items()),
            )
    finally:
        # Always — a short-lived process must release the pool and exit even
        # when a step blew up, or the scheduler is left with a hung job.
        await engine.dispose()

    return len(failed)


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(run_all()) else 0)
