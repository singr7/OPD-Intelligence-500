"""Quiet hours — the one scheduling rule doc 03 §9 states outright.

> "Celery beat scheduler; quiet hours 21:00–08:00." — doc 03 §9

Everything about a check-in is negotiable except this. A plan can be re-drafted,
a channel can fail over, a grade can be reviewed by a nurse — but a voice call at
22:30 to someone three days into a chemotherapy cycle is a complaint, and worse,
it is the thing that makes a family stop answering the number the hospital rings
from. So the rule lives in one module that both the scheduler (which picks
`due_at` when a plan is approved) and the ladder (which picks the next attempt
when a send fails) call, rather than being re-implemented at two call sites that
will drift.

## It defers, it never drops

`next_sendable` moves a moment forward to the next allowed one; it never returns
"do not send". A patient whose D+2 lands at 23:00 is asked at 08:00 on D+3, and
the check-in records the delay in its own `due_at`. Silently skipping the rung
would lose the one clinical signal the day was for.

## Hospital-local, not UTC

`app.scheduling.hospital_tz` is the clock on the wall the patient lives under —
the same timezone the appointment times and the campaign's calling hours already
use. Doing this arithmetic in UTC works fine for a pilot in Alwar and breaks the
first time this codebase is deployed anywhere else.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import Settings, get_settings
from app.scheduling import hospital_tz


def is_quiet(moment: datetime, *, settings: Settings | None = None) -> bool:
    """Is this instant inside 21:00–08:00, hospital-local?"""
    settings = settings or get_settings()
    hour = moment.astimezone(hospital_tz()).hour
    start, end = settings.checkin_quiet_start_hour, settings.checkin_quiet_end_hour
    if start == end:  # pragma: no cover - a degenerate config, not reachable today
        return False
    if start > end:  # the normal case: the window wraps midnight
        return hour >= start or hour < end
    return start <= hour < end


def next_sendable(moment: datetime, *, settings: Settings | None = None) -> datetime:
    """`moment`, or the first instant after it that is not in quiet hours.

    Returns an aware UTC datetime, so callers can store it without thinking about
    which clock it came from.
    """
    settings = settings or get_settings()
    if moment.tzinfo is None:  # pragma: no cover - callers pass aware datetimes
        moment = moment.replace(tzinfo=UTC)
    if not is_quiet(moment, settings=settings):
        return moment.astimezone(UTC)

    tz = hospital_tz()
    local = moment.astimezone(tz)
    end = settings.checkin_quiet_end_hour
    opens = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if opens <= local:
        # We are in the evening half of the window; the next opening is tomorrow.
        opens += timedelta(days=1)
    return opens.astimezone(UTC)


def send_time_on(day: datetime, *, settings: Settings | None = None) -> datetime:
    """The instant a check-in due on `day` should actually go out.

    `checkin_send_hour` hospital-local, pushed out of quiet hours if an operator
    has configured the two into conflict.
    """
    settings = settings or get_settings()
    local = day.astimezone(hospital_tz()).replace(
        hour=settings.checkin_send_hour, minute=0, second=0, microsecond=0
    )
    return next_sendable(local.astimezone(UTC), settings=settings)
