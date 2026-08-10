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


# ---------- run_all: step isolation and the heartbeat contract ----------
#
# `run_all` itself had no test — the case above reimplements its steps by
# hand, which cannot catch a change to how the steps are orchestrated.
# These drive the real function.


@pytest.fixture
def isolated_cron(monkeypatch, db_factory):
    """
    Points `run_all`'s session helpers at the test database.

    `app/tasks/cron.py` reaches for the application's own factory, which
    is production. Every test here must redirect it or the suite would
    run maintenance against the live database — the same class of leak
    Phase 9E-B hit through BackgroundTasks.
    """
    from contextlib import asynccontextmanager

    from app.tasks import cron

    @asynccontextmanager
    async def ctx():
        async with db_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    class InertEngine:
        """Disposing the *test* pool would break the rest of the suite."""

        async def dispose(self):
            return None

    monkeypatch.setattr(cron, "db_session_ctx", ctx)
    monkeypatch.setattr(cron, "AsyncSessionFactory", db_factory)
    monkeypatch.setattr(cron, "engine", InertEngine())
    return cron


def _steps_with(cron, name, replacement):
    """The real step table with one entry swapped for a stub."""
    return tuple(
        (step_name, replacement if step_name == name else step)
        for step_name, step in cron.MAINTENANCE_STEPS
    )


async def test_a_clean_run_stamps_the_heartbeat_and_reports_no_failures(
    isolated_cron, db_factory, monkeypatch
):
    cron = isolated_cron

    async def no_resume():
        return 0

    monkeypatch.setattr(
        cron, "MAINTENANCE_STEPS", _steps_with(cron, "broadcasts_resumed", no_resume)
    )

    failures = await cron.run_all()

    assert failures == 0
    async with db_factory() as check:
        assert await last_maintenance_run(check) is not None
        assert (await maintenance_is_stale(check))[0] is False


async def test_one_failing_step_does_not_stop_the_others(
    isolated_cron, db_factory, monkeypatch, caplog
):
    """
    The regression this rework exists for. A single broken step used to
    roll back the work already done and skip everything after it, so one
    bug suspended all maintenance indefinitely.
    """
    cron = isolated_cron
    reached: list[str] = []

    async def boom():
        raise RuntimeError("purge exploded")

    async def watched_resume():
        reached.append("broadcasts_resumed")
        return 0

    steps = _steps_with(cron, "receipt_images_purged", boom)
    steps = tuple(
        (name, watched_resume if name == "broadcasts_resumed" else step)
        for name, step in steps
    )
    monkeypatch.setattr(cron, "MAINTENANCE_STEPS", steps)

    with caplog.at_level("INFO"):
        failures = await cron.run_all()

    assert failures == 1
    # The step *after* the failure still ran.
    assert reached == ["broadcasts_resumed"]
    assert "receipt_images_purged: FAILED" in caplog.text
    assert "purge exploded" in caplog.text, "the traceback must not be swallowed"


async def test_a_partial_run_does_not_stamp_the_heartbeat(
    isolated_cron, db_factory, monkeypatch
):
    """The stamp still means "all of it completed", not "something ran"."""
    cron = isolated_cron

    async def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(
        cron, "MAINTENANCE_STEPS", _steps_with(cron, "promos_deactivated", boom)
    )

    assert await cron.run_all() == 1

    async with db_factory() as check:
        assert await last_maintenance_run(check) is None
        assert (await maintenance_is_stale(check))[0] is True


async def test_a_partial_run_leaves_an_earlier_stamp_untouched(
    isolated_cron, db_factory, monkeypatch
):
    cron = isolated_cron
    earlier = datetime.now(timezone.utc) - timedelta(days=10)

    async with db_factory() as setup:
        await record_maintenance_run(setup, earlier)
        await setup.commit()

    async def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(
        cron, "MAINTENANCE_STEPS", _steps_with(cron, "monthly_limits_reset", boom)
    )

    assert await cron.run_all() == 1

    async with db_factory() as check:
        assert await last_maintenance_run(check) == earlier


async def test_running_twice_in_a_row_is_safe(isolated_cron, db_factory, monkeypatch):
    """
    Overlapping or double-fired schedules must not be a correctness risk.
    Every step is idempotent, so a second immediate run repeats cleanly
    and simply re-stamps the heartbeat.
    """
    cron = isolated_cron

    async def no_resume():
        return 0

    monkeypatch.setattr(
        cron, "MAINTENANCE_STEPS", _steps_with(cron, "broadcasts_resumed", no_resume)
    )

    assert await cron.run_all() == 0
    async with db_factory() as check:
        first = await last_maintenance_run(check)

    assert await cron.run_all() == 0
    async with db_factory() as check:
        second = await last_maintenance_run(check)

    assert second is not None and first is not None
    assert second >= first, "the later run holds the later stamp"


async def test_every_step_runs_exactly_once_per_invocation(
    isolated_cron, monkeypatch
):
    """No step is registered twice — a duplicated entry would double the work."""
    cron = isolated_cron
    names = [name for name, _ in cron.MAINTENANCE_STEPS]
    assert len(names) == len(set(names)), f"duplicate maintenance step: {names}"


async def test_the_summary_log_carries_no_sensitive_values(
    isolated_cron, monkeypatch, caplog
):
    """
    Maintenance touches receipts, images and broadcasts. The log must
    report counts and step names only — never a token, a file id, or a
    user's identity.
    """
    cron = isolated_cron

    async def no_resume():
        return 0

    monkeypatch.setattr(
        cron, "MAINTENANCE_STEPS", _steps_with(cron, "broadcasts_resumed", no_resume)
    )

    with caplog.at_level("INFO"):
        await cron.run_all()

    from app.core.config import settings

    lowered = caplog.text.lower()
    assert settings.BOT_TOKEN.lower() not in lowered
    assert settings.DATABASE_URL.lower() not in lowered
    for probe in ("file_id", "telegram_id", "password", "token="):
        assert probe not in lowered
