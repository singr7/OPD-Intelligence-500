"""The scheduled jobs (S15).

Celery is not installed in this venv — only in the worker/beat images — which is
exactly why `app.worker`'s jobs are plain coroutines and its schedule is plain
data. These tests run the real jobs against the test session and assert on the
schedule without a broker anywhere in sight.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import campaign, scheduling, worker
from app.models.enums import Channel, OutboundCallState
from tests.factories import build_clinic, make_slot, make_slot_template

pytestmark = pytest.mark.asyncio


async def test_every_scheduled_job_exists(settings):
    """A beat entry naming a job that was renamed is a silent nightly no-op."""
    assert set(worker.SCHEDULE) <= set(worker.JOBS)
    assert set(worker.JOBS) == {
        "opd.slots.generate",
        "opd.campaign.launch",
        "opd.campaign.dial",
        "opd.campaign.fallback",
    }


async def test_the_campaign_launches_in_the_evening(settings):
    """doc 01 §4.2: an *evening* call about tomorrow."""
    hour, minute = worker.SCHEDULE["opd.campaign.launch"]
    assert worker.beat_hour("opd.campaign.launch", hour, settings) == str(settings.campaign_hour)
    assert 16 <= settings.campaign_hour <= 20
    assert minute == "0"


async def test_slot_generation_runs_before_the_opd_opens(settings):
    hour, _ = worker.SCHEDULE["opd.slots.generate"]
    assert int(hour) < 8
    assert worker.beat_hour("opd.slots.generate", hour, settings) == hour


async def test_the_generation_job_materialises_inventory(session, settings):
    clinic = await build_clinic(session)
    session.add(make_slot_template(clinic["doctor"]))
    await session.flush()

    result = await worker.generate_slots_job(session, settings)

    assert result.startswith("generated ")
    offers = await scheduling.find_slots(session, doctor_id=clinic["doctor"].id, limit=1)
    assert offers, "the nightly job produced nothing bookable"


async def test_the_campaign_jobs_do_nothing_while_the_flag_is_off(session, settings, providers):
    """The flag is off by default: a box that boots with real Exotel credentials
    must not start ringing patients because beat came up."""
    assert settings.campaign_enabled is False
    for job in (worker.campaign_launch_job, worker.campaign_dial_job, worker.campaign_fallback_job):
        assert await job(session, settings) == "campaign disabled"


async def test_the_launch_job_queues_tomorrows_calls_when_enabled(session, settings, providers):
    clinic = await build_clinic(session)
    # Tomorrow in the *hospital's* timezone, which is what `campaign.tomorrow`
    # means by tomorrow. Built from the UTC clock it was wrong for the five and a
    # half hours after midnight IST, when the UTC date is still yesterday — the
    # test failed at 00:38 IST and passed again at breakfast. Midday IST is
    # inside the clinic day in every timezone this runs in (S16, found by the
    # gate rather than by a user).
    slot_at = (
        (datetime.now(scheduling.hospital_tz()) + timedelta(days=1))
        .replace(hour=12, minute=0, second=0, microsecond=0)
        .astimezone(UTC)
    )
    slot = make_slot(clinic["doctor"], slot_at, capacity=5)
    session.add(slot)
    await session.flush()
    await scheduling.book(session, patient=clinic["patient"], slot_id=slot.id, source=Channel.KIOSK)

    settings.campaign_enabled = True
    try:
        result = await worker.campaign_launch_job(session, settings)
    finally:
        settings.campaign_enabled = False

    assert result.startswith("queued 1 calls")
    [queued] = await campaign.due_calls(session)
    assert queued.state is OutboundCallState.PENDING


async def test_an_unknown_job_name_is_an_error_not_a_silent_pass():
    with pytest.raises(KeyError):
        await worker.run_job("opd.campaign.does-not-exist")
