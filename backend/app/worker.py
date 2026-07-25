"""Celery app + the scheduled jobs for the `worker` and `beat` services.

S1 shipped the wiring and a healthcheck. S15 gives it its first real work: the
nightly slot generation and the three rungs of the D-1 outbound intake campaign
(doc 01 §4.2).

## The jobs are plain coroutines; Celery is the alarm clock

Everything below `JOBS` is an ordinary async function over a session, tested by
`tests/test_worker.py` with no broker and no Celery installed — which matters,
because Celery is only in the worker/beat images and importing it in the api
service or the test venv would fail. The Celery task is a three-line wrapper that
runs the coroutine. Put logic in a task body and it becomes untestable; put it
here and both the scheduler and a human with `python -m app.worker <job>` can run
it.

## Why the campaign is three jobs, not one

Launch (write the call list), dial (place the due calls), fallback (message the
ones the ladder gave up on) run on different clocks and fail differently. One
combined job that dies halfway leaves a campaign in a state nobody can name; three
idempotent jobs re-run safely and independently. `app.campaign` holds the rules.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def _with_session(work: Callable[[AsyncSession, Settings], Awaitable[str]]) -> str:
    """One job, one session, one commit. Jobs never share a session — a beat tick
    that overlaps the previous one must not join its transaction."""
    from app.db import build_engine, build_sessionmaker

    settings = get_settings()
    engine = build_engine()
    try:
        async with build_sessionmaker(engine)() as session:
            result = await work(session, settings)
            await session.commit()
            return result
    finally:
        await engine.dispose()


# -- the jobs ------------------------------------------------------------------


async def generate_slots_job(session: AsyncSession, settings: Settings) -> str:
    """Keep bookable inventory ahead of the callers (doc 03 §2).

    Idempotent by construction (`app.scheduling.generate_slots` skips instants
    that already exist), so running it nightly costs nothing and a missed night
    is invisible.
    """
    from app.scheduling import generate_slots, hospital_tz

    today = datetime.now(UTC).astimezone(hospital_tz()).date()
    created = await generate_slots(session, start=today, days=settings.slot_generation_horizon_days)
    return f"generated {len(created)} slots"


async def campaign_launch_job(session: AsyncSession, settings: Settings) -> str:
    """Queue tomorrow's pre-visit calls. Does not dial — `campaign_dial_job` does."""
    from app.campaign import launch_campaign, tomorrow

    if not settings.campaign_enabled:
        return "campaign disabled"
    plan = await launch_campaign(session, for_date=tomorrow(settings=settings), dry_run=False)
    return f"queued {len(plan.targets)} calls, skipped {len(plan.skipped)}"


async def campaign_dial_job(session: AsyncSession, settings: Settings) -> str:
    """Place the calls that are due. Runs often; refuses outside calling hours."""
    from app.campaign import dial_due_calls

    if not settings.campaign_enabled:
        return "campaign disabled"
    dialled = await dial_due_calls(session, settings=settings)
    return f"dialled {len(dialled)}"


async def campaign_fallback_job(session: AsyncSession, settings: Settings) -> str:
    """WhatsApp the patients two calls could not reach (doc 03 §1b)."""
    from app.campaign import send_call_fallbacks

    if not settings.campaign_enabled:
        return "campaign disabled"
    sent = await send_call_fallbacks(session)
    return f"messaged {len(sent)}"


#: Job name → coroutine. The Celery tasks and the CLI both dispatch through this,
#: so there is exactly one list of what this worker can be asked to do.
JOBS: dict[str, Callable[[AsyncSession, Settings], Awaitable[str]]] = {
    "opd.slots.generate": generate_slots_job,
    "opd.campaign.launch": campaign_launch_job,
    "opd.campaign.dial": campaign_dial_job,
    "opd.campaign.fallback": campaign_fallback_job,
}

#: name → (hour, minute) as crontab fields, in the hospital timezone (beat runs on
#: `settings.timezone`). Kept as plain data so `tests/test_worker.py` can assert
#: the schedule without importing Celery.
SCHEDULE: dict[str, tuple[str, str]] = {
    # Before the campaign needs the inventory, and before the OPD opens.
    "opd.slots.generate": ("2", "30"),
    # doc 01 §4.2's "evening slot" — patients are home and the call is about
    # tomorrow. `settings.campaign_hour` replaces the hour at runtime.
    "opd.campaign.launch": ("18", "0"),
    # Every 15 minutes so the 45-minute retry lands promptly. The job is a no-op
    # outside calling hours, so running it round the clock costs nothing.
    "opd.campaign.dial": ("*", "*/15"),
    "opd.campaign.fallback": ("*", "20"),
}


async def run_job(name: str) -> str:
    job = JOBS.get(name)
    if job is None:
        raise KeyError(f"unknown job {name!r}; known: {sorted(JOBS)}")
    result = await _with_session(job)
    logger.info("job %s: %s", name, result)
    return result


# -- celery --------------------------------------------------------------------


def make_celery():
    from celery import Celery
    from celery.schedules import crontab

    settings = get_settings()
    broker = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    app = Celery("opd", broker=broker, backend=broker)
    app.conf.update(task_track_started=True, timezone=settings.timezone)

    @app.task(name="opd.ping")
    def ping() -> str:
        return "pong"

    def _register(name: str):
        @app.task(name=name)
        def task() -> str:
            return asyncio.run(run_job(name))

        return task

    for name in JOBS:
        _register(name)

    app.conf.beat_schedule = {
        name: {
            "task": name,
            "schedule": crontab(minute=minute, hour=beat_hour(name, hour, settings)),
        }
        for name, (hour, minute) in SCHEDULE.items()
    }
    return app


def beat_hour(name: str, hour: str, settings: Settings) -> str:
    """The campaign's launch hour is operator-configurable; everything else is
    fixed. Split out so the test can check it without a broker."""
    return str(settings.campaign_hour) if name == "opd.campaign.launch" else hour


# Module-level instance the celery CLI binds to. Guarded so importing this
# module in an environment without celery (api service, pytest) does not crash;
# the worker/beat containers always have celery and get a real app.
try:
    celery_app = make_celery()
except ImportError:  # pragma: no cover - celery absent outside worker/beat
    celery_app = None


def main() -> None:
    """`python -m app.worker <job>` — run one job by hand, no broker needed."""
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: python -m app.worker <job>\njobs: {', '.join(sorted(JOBS))}")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(asyncio.run(run_job(sys.argv[1])))


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
