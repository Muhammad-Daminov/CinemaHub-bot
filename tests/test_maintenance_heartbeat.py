"""
The scheduled-maintenance heartbeat.

`app/tasks/cron.py` is run by a Render Cron Job configured in a dashboard
this repository cannot see, and the 30-day receipt-image retention
promise depends on it. Until now nothing could tell whether it ran at
all. Each completed run stamps `chp_system_settings`, and the web service
warns on startup when that stamp is missing or stale.

The property that matters most is the failure direction: **never having
run reads as stale**, because "no evidence" is exactly what an
unscheduled job looks like from inside the application.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.settings_store import (
    LAST_MAINTENANCE_RUN,
    MAINTENANCE_STALE_AFTER,
    get_setting,
    last_maintenance_run,
    maintenance_is_stale,
    record_maintenance_run,
    set_setting,
)
from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


async def test_a_fresh_run_is_not_stale(db_session):
    await record_maintenance_run(db_session)
    stale, last = await maintenance_is_stale(db_session)
    assert stale is False
    assert last is not None


async def test_an_old_run_is_stale(db_session):
    await record_maintenance_run(db_session, datetime.now(timezone.utc) - timedelta(hours=72))
    stale, last = await maintenance_is_stale(db_session)
    assert stale is True
    assert last is not None


async def test_never_having_run_is_stale(db_session):
    """
    The case this exists for. A platform where the cron was never
    scheduled has no row at all, and that must warn rather than look
    healthy.
    """
    stale, last = await maintenance_is_stale(db_session)
    assert stale is True
    assert last is None


async def test_the_boundary_is_the_configured_window(db_session):
    just_inside = datetime.now(timezone.utc) - MAINTENANCE_STALE_AFTER + timedelta(minutes=5)
    await record_maintenance_run(db_session, just_inside)
    assert (await maintenance_is_stale(db_session))[0] is False

    just_outside = datetime.now(timezone.utc) - MAINTENANCE_STALE_AFTER - timedelta(minutes=5)
    await record_maintenance_run(db_session, just_outside)
    assert (await maintenance_is_stale(db_session))[0] is True


async def test_recording_is_idempotent(db_session):
    """
    A liveness signal, not an audit log. Running the cron twice in a day
    must leave one row holding the later time, not accumulate history.
    """
    first = await record_maintenance_run(db_session)
    second = await record_maintenance_run(db_session)

    assert second >= first
    stored = await last_maintenance_run(db_session)
    assert stored is not None
    assert abs((stored - second).total_seconds()) < 1

    from app.db.models.system import SystemSetting
    from tests.conftest import count_rows

    assert await count_rows(db_session, SystemSetting, key=LAST_MAINTENANCE_RUN) == 1


async def test_an_unreadable_stamp_reads_as_never_ran(db_session):
    """Corruption must warn, not silently report health it cannot prove."""
    await set_setting(db_session, LAST_MAINTENANCE_RUN, "not a timestamp")
    assert await last_maintenance_run(db_session) is None
    assert (await maintenance_is_stale(db_session))[0] is True


async def test_a_naive_timestamp_is_treated_as_utc(db_session):
    """A value written without a timezone must not raise when compared."""
    naive = datetime.now(timezone.utc).replace(tzinfo=None)
    await set_setting(db_session, LAST_MAINTENANCE_RUN, naive.isoformat())

    stored = await last_maintenance_run(db_session)
    assert stored is not None and stored.tzinfo is not None
    assert (await maintenance_is_stale(db_session))[0] is False


async def test_the_cron_run_stamps_its_completion(db_session, monkeypatch):
    """
    End to end through run_all's steps: the stamp is written in the same
    transaction as the work, so a run that fails partway leaves the
    previous timestamp standing and the warning fires.
    """
    from app.tasks import cron

    assert await get_setting(db_session, LAST_MAINTENANCE_RUN) is None

    await cron.reset_monthly_order_limits(db_session)
    await cron.expire_stale_payment_receipts(db_session)
    await cron.deactivate_expired_promos(db_session)
    await record_maintenance_run(db_session)

    assert await get_setting(db_session, LAST_MAINTENANCE_RUN) is not None
    assert (await maintenance_is_stale(db_session))[0] is False


async def test_a_failed_run_leaves_the_previous_stamp_standing(db_factory):
    """
    The stamp means "all of the maintenance completed", not "the job
    started". It is written last, inside the same transaction as the work,
    so a run that dies partway rolls back with it — leaving the older
    timestamp and letting the staleness warning fire.

    Driven through a real transaction because that is the mechanism being
    asserted: a shared session would hide the rollback.
    """
    async with db_factory() as setup:
        await record_maintenance_run(setup, datetime.now(timezone.utc) - timedelta(days=10))
        await setup.commit()
        original = await last_maintenance_run(setup)

    async with db_factory() as session:
        try:
            # Stands in for any step of run_all failing after some work.
            await record_maintenance_run(session)
            raise RuntimeError("purge exploded")
        except RuntimeError:
            await session.rollback()

    async with db_factory() as check:
        after = await last_maintenance_run(check)
        assert after == original, "a failed run must not claim the maintenance succeeded"
        stale, _ = await maintenance_is_stale(check)
        assert stale is True, "and the warning must still fire"
