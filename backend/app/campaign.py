"""The D-1 outbound intake campaign (doc 01 §4.2, doc 03 §1b).

> "Appointment exists (D-1) → Outbound AI call in patient's language, evening
>  slot → Conversational intake … → On arrival: registration scans phone/ID →
>  token issued instantly, intake already done" — doc 01 §4.2
> "retry policy 2 attempts then WhatsApp fallback message" — doc 03 §1b

Four steps, each a separate job so a failure in one does not strand the others
(`app.worker` puts them on Celery beat):

1. **plan** — who gets called tomorrow. Pure read; `make campaign-dryrun` prints
   it and the AC asserts on it.
2. **launch** — write one `OutboundCall` per target. Idempotent by
   `UNIQUE (appointment_id, purpose)`, so re-running the beat job at 18:05 after
   a crash at 18:00 re-dials nobody.
3. **dial** — place the calls that are due, through `TelephonyProvider`. The
   applet Exotel runs on answer is voice-gw's Voicebot websocket (S14): the
   patient hears the same intake the kiosk gives, in their language.
4. **reconcile** — the Exotel status callback lands minutes later (a different
   process, `app.routes.telephony`), meters the minutes, and either completes the
   row or puts it back on the ladder.

## The ladder is a database row, not a retry decorator

Attempts, the next attempt time and the vendor's last word live on
`outbound_calls` because the thing being retried spans processes and hours: a
call placed by the worker at 18:00 is settled by a webhook at 18:03, and a box
restart in between must not lose that a patient has already been rung twice.
`MAX_ATTEMPTS` is doc 03 §1b's two; after that the patient gets the WhatsApp
message instead, which is the last rung, not an error.

## What a dry run is for

Nobody's phone should ring because a developer ran a script. `plan` and
`launch(dry_run=True)` do the whole selection and print it; only `dial` picks up
a handset, and only when `campaign_enabled` is on.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import channel_state, resolve_config
from app.config import Settings, get_settings
from app.models.enums import Channel, Lang, OutboundCallState
from app.models.org import Hospital
from app.models.patient import Patient
from app.models.scheduling import Appointment, OutboundCall
from app.notify import send_intake_call_fallback
from app.providers.base import ProviderError
from app.providers.registry import get_telephony_provider
from app.providers.runtime import effective_settings as runtime_settings
from app.providers.telephony import CallHandle, CallRequest, CallState
from app.scheduling import appointments_on, hospital_tz

logger = logging.getLogger(__name__)

#: doc 03 §1b: "retry policy 2 attempts then WhatsApp fallback message".
MAX_ATTEMPTS = 2

#: A patient who did not pick up at 18:00 is often eating at 18:05. Long enough to
#: be a different moment in their evening, short enough to still be that evening.
RETRY_AFTER_MINUTES = 45

CAMPAIGN_PURPOSE = "d1_intake"

#: Never ring after this hour, local time. A hospital calling a cancer patient at
#: 22:30 is a complaint, not a reminder.
LATEST_CALL_HOUR = 21


@dataclass(frozen=True, slots=True)
class CampaignTarget:
    """One patient to reach, flattened — the list a coordinator can read."""

    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    phone: str
    lang: Lang
    slot_at: datetime
    #: How we invite her: `phone` (ring her) or `whatsapp` (message her). Decided
    #: by the channel mix, deterministically in her patient id (S-GL.1, doc 12
    #: §1.2). `Channel.PHONE` when no mix is configured, which is what the
    #: campaign did before the mix existed.
    channel: Channel = Channel.PHONE

    def line(self) -> str:
        local = self.slot_at.astimezone(hospital_tz())
        return (
            f"{self.patient_name} <{self.phone}> {self.lang} "
            f"[{self.channel.value}] — {local:%Y-%m-%d %H:%M}"
        )


@dataclass(slots=True)
class CampaignPlan:
    for_date: date
    targets: list[CampaignTarget] = field(default_factory=list)
    #: (reason, appointment_id) for everyone deliberately not called. Kept because
    #: "why was my mother not rung?" is a question with an answer.
    skipped: list[tuple[str, uuid.UUID]] = field(default_factory=list)
    #: The mix actually applied, after closed channels were dropped from it. Kept
    #: on the plan so the dry run can state what it did rather than what was
    #: configured — those differ the moment a channel is shut.
    mix: dict[Channel, int] = field(default_factory=dict)

    def by_channel(self, channel: Channel) -> list[CampaignTarget]:
        return [t for t in self.targets if t.channel is channel]

    @property
    def to_call(self) -> list[CampaignTarget]:
        return self.by_channel(Channel.PHONE)

    def report(self) -> str:
        counts = Counter(t.channel.value for t in self.targets)
        summary = ", ".join(f"{n} by {channel}" for channel, n in sorted(counts.items()))
        lines = [
            f"D-1 intake campaign for {self.for_date}: "
            f"{len(self.targets)} to reach ({summary or 'nobody'})"
        ]
        if self.mix:
            lines.append(
                "  mix: " + ", ".join(f"{c.value} {pct}%" for c, pct in sorted(self.mix.items()))
            )
        lines += [f"  {t.line()}" for t in self.targets]
        if self.skipped:
            lines.append(f"  skipped {len(self.skipped)}:")
            lines += [f"    {reason} ({appt})" for reason, appt in self.skipped]
        return "\n".join(lines)


def tomorrow(*, settings: Settings | None = None) -> date:
    """The clinic day a campaign run tonight is about, in hospital-local time."""
    settings = settings or get_settings()
    return (datetime.now(UTC).astimezone(hospital_tz()) + timedelta(days=1)).date()


# -- 1. plan -------------------------------------------------------------------


def assign_channel(patient_id: uuid.UUID, mix: dict[Channel, int]) -> Channel:
    """Which channel invites this patient, deterministically in her id.

    **Deterministic, not random** (doc 12 §1.2). A coordinator re-runs the dry run
    three times before the evening launch; if the split were random, each run
    would move patients between the call list and the message list, and the list
    she checked would not be the list that went out. Hashing the patient id means
    the same person lands on the same channel every time, and re-planning is safe.

    The buckets are laid out in a fixed channel order rather than dict order, so
    the same mix always produces the same assignment regardless of how the
    document happened to be written.
    """
    if not mix:
        return Channel.PHONE
    digest = hashlib.sha256(patient_id.bytes).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    edge = 0
    for channel in sorted(mix, key=lambda c: c.value):
        edge += mix[channel]
        if bucket < edge:
            return channel
    return sorted(mix, key=lambda c: c.value)[-1]  # pragma: no cover - the mix sums to 100


async def campaign_mix(
    session: AsyncSession, settings: Settings | None = None
) -> dict[Channel, int]:
    """The configured mix, with shut channels dropped and the rest renormalised.

    A mix naming a channel that is closed would silently drop that share of
    tomorrow's list — the patients assigned to WhatsApp simply never hear from
    anybody. So a closed channel's share is redistributed across the open ones,
    and if *no* mixed channel is open the mix is emptied, which falls back to
    "ring everyone we can" and lets the ordinary dial path report the outage.
    """
    settings = settings or get_settings()
    config = await resolve_config(session)
    configured = config.campaign_mix
    if not configured:
        return {}

    effective = await runtime_settings(session, settings)
    open_channels = [
        channel
        for channel in configured
        if channel_state(config, channel, effective).is_open and configured[channel] > 0
    ]
    if not open_channels:
        logger.warning("campaign mix names no open channel — falling back to calling everyone")
        return {}
    if len(open_channels) == len(configured):
        return dict(configured)

    # Renormalise onto what is open, giving the rounding remainder to the largest
    # share so the total is exactly 100 and nobody falls off the list.
    total = sum(configured[c] for c in open_channels)
    share = {c: configured[c] * 100 // total for c in open_channels}
    largest = max(open_channels, key=lambda c: (configured[c], c.value))
    share[largest] += 100 - sum(share.values())
    logger.info(
        "campaign mix renormalised onto open channels: %s",
        {c.value: pct for c, pct in share.items()},
    )
    return share


async def plan_campaign(session: AsyncSession, *, for_date: date) -> CampaignPlan:
    """Who gets a pre-visit call for `for_date`, who gets a message, and who
    neither — and why.

    Reads only. This is the AC's "campaign dry-run produces correct call list".
    """
    plan = CampaignPlan(for_date=for_date, mix=await campaign_mix(session))
    appointments = await appointments_on(session, day=for_date)
    if not appointments:
        return plan

    patients = await _patients_by_id(session, {a.patient_id for a in appointments})
    already = await _existing_calls(session, {a.id for a in appointments})

    seen_patients: set[uuid.UUID] = set()
    for appointment in appointments:
        patient = patients.get(appointment.patient_id)
        if patient is None:
            plan.skipped.append(("no patient record", appointment.id))
            continue
        if not patient.phone:
            plan.skipped.append(("no phone number", appointment.id))
            continue
        existing = already.get(appointment.id)
        if existing is not None and existing.state is not OutboundCallState.CANCELLED:
            plan.skipped.append((f"already queued ({existing.state})", appointment.id))
            continue
        if appointment.patient_id in seen_patients:
            # Two appointments tomorrow (a review and a chemo slot) is one call,
            # not two: the intake covers the patient, not the booking.
            plan.skipped.append(("patient already on the list", appointment.id))
            continue

        seen_patients.add(appointment.patient_id)
        plan.targets.append(
            CampaignTarget(
                appointment_id=appointment.id,
                patient_id=patient.id,
                patient_name=patient.name,
                phone=patient.phone,
                lang=patient.lang or Lang.HI,
                slot_at=appointment.slot_at,
                channel=assign_channel(patient.id, plan.mix),
            )
        )
    return plan


# -- 2. launch -----------------------------------------------------------------


async def launch_campaign(
    session: AsyncSession,
    *,
    for_date: date,
    dry_run: bool = True,
    now: datetime | None = None,
) -> CampaignPlan:
    """Materialise the plan as `OutboundCall` rows, due immediately.

    `dry_run=True` (the default, deliberately) plans without writing. Re-running a
    real launch is safe: the unique constraint means an existing row is found and
    left alone rather than duplicated.
    """
    plan = await plan_campaign(session, for_date=for_date)
    if dry_run:
        return plan

    due_at = now or datetime.now(UTC)
    for target in plan.to_call:
        session.add(
            OutboundCall(
                appointment_id=target.appointment_id,
                patient_id=target.patient_id,
                purpose=CAMPAIGN_PURPOSE,
                for_date=for_date,
                to_phone=target.phone,
                state=OutboundCallState.PENDING,
                attempts=0,
                next_attempt_at=due_at,
            )
        )
    await session.flush()

    # The WhatsApp share is *invited*, not queued: there is no ladder to walk, so
    # it goes out here as the same message the call ladder falls back to. Failures
    # are logged rather than raised — one unreachable patient must not stop the
    # rest of tomorrow's list from being invited.
    messaged = 0
    for target in plan.by_channel(Channel.WHATSAPP):
        if await _invite_on_whatsapp(session, target):
            messaged += 1

    logger.info(
        "campaign for %s queued %d calls and messaged %d",
        for_date,
        len(plan.to_call),
        messaged,
    )
    return plan


async def _invite_on_whatsapp(session: AsyncSession, target: CampaignTarget) -> bool:
    """Send one patient the D-1 intake invitation on WhatsApp."""
    patient = await session.get(Patient, target.patient_id)
    appointment = await session.get(Appointment, target.appointment_id)
    if patient is None or appointment is None:  # pragma: no cover - FK-guarded
        return False
    hospital = await session.get(Hospital, patient.hospital_id)
    try:
        await send_intake_call_fallback(
            session,
            appointment=appointment,
            patient=patient,
            hospital_name=(
                hospital.name_in(patient.lang) if hospital is not None else "the hospital"
            ),
        )
    except Exception:  # noqa: BLE001 — one bad number must not stop the campaign
        logger.exception("campaign: whatsapp invite to patient %s failed", target.patient_id)
        return False
    return True


# -- 3. dial -------------------------------------------------------------------


async def due_calls(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 50
) -> list[OutboundCall]:
    now = now or datetime.now(UTC)
    found = await session.execute(
        select(OutboundCall)
        .where(
            OutboundCall.deleted_at.is_(None),
            OutboundCall.state == OutboundCallState.PENDING,
            OutboundCall.next_attempt_at <= now,
        )
        .order_by(OutboundCall.next_attempt_at)
        .limit(limit)
    )
    return list(found.scalars().all())


def within_calling_hours(now: datetime, *, settings: Settings | None = None) -> bool:
    local = now.astimezone(hospital_tz())
    return 8 <= local.hour < LATEST_CALL_HOUR


async def dial_due_calls(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 50,
    settings: Settings | None = None,
) -> list[OutboundCall]:
    """Place every call that is due. Returns the rows it dialled.

    A dial that the vendor refuses is not a lost patient: the attempt is counted
    and the row goes back on the ladder (or to the WhatsApp rung), exactly as if
    the phone had rung out.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    if not within_calling_hours(now, settings=settings):
        logger.info("outside calling hours (%s local) — dialling nothing", now)
        return []

    # S-GL.1: a campaign that keeps ringing after phone was switched off is the
    # dishonesty the switchboard exists to remove. Queued rows are left alone
    # rather than failed — the channel is expected back, and their ladder is
    # unchanged when it is.
    settings = await runtime_settings(session, settings)
    config = await resolve_config(session)
    phone = channel_state(config, Channel.PHONE, settings)
    if not phone.is_open:
        logger.info("phone channel closed (%s) — dialling nothing", phone.reason or "not open")
        return []

    provider = get_telephony_provider(settings)
    dialled: list[OutboundCall] = []

    for call in await due_calls(session, now=now, limit=limit):
        call.attempts += 1
        call.last_attempt_at = now
        request = CallRequest(
            to=call.to_phone,
            applet_url=settings.exotel_applet_url,
            caller_id=settings.exotel_caller_id or None,
            status_callback=settings.exotel_status_callback_url or None,
            # Echoed back on the callback: this is how a cost and an outcome land
            # on the right row minutes later.
            reference=str(call.id),
        )
        try:
            handle = await provider.place_call(request)
        except ProviderError as exc:
            logger.warning("campaign dial failed for %s: %s", call.id, exc)
            _advance_ladder(call, outcome="dial_failed", now=now)
        else:
            call.last_call_sid = handle.call_sid
            call.state = OutboundCallState.DIALING
            dialled.append(call)

    await session.flush()
    return dialled


# -- 4. reconcile (the status callback) ----------------------------------------


def _advance_ladder(call: OutboundCall, *, outcome: str, now: datetime) -> None:
    """One rung down: retry later, or fall to WhatsApp when the attempts are gone."""
    call.outcome = outcome
    if call.attempts >= MAX_ATTEMPTS:
        call.state = OutboundCallState.FAILED
        call.next_attempt_at = None
    else:
        call.state = OutboundCallState.PENDING
        call.next_attempt_at = now + timedelta(minutes=RETRY_AFTER_MINUTES)


async def call_by_sid(session: AsyncSession, call_sid: str) -> OutboundCall | None:
    found = await session.execute(
        select(OutboundCall).where(OutboundCall.last_call_sid == call_sid)
    )
    return found.scalars().first()


async def call_by_reference(session: AsyncSession, reference: str) -> OutboundCall | None:
    """Look a row up by the `reference` we put on the dial — the primary path, since
    Exotel echoes our own id back and a sid can be missing on a failed dial."""
    try:
        call_id = uuid.UUID(reference)
    except (ValueError, AttributeError, TypeError):
        return None
    return await session.get(OutboundCall, call_id)


async def record_call_result(
    session: AsyncSession,
    *,
    handle: CallHandle,
    reference: str | None = None,
    intake_id: uuid.UUID | None = None,
    now: datetime | None = None,
    meter: bool = True,
) -> OutboundCall | None:
    """Settle one campaign call from the Exotel status callback.

    Meters the call's minutes through the provider (`record_call_completed` — the
    only place per-call telephony cost can be known, since duration arrives with
    the callback), then either completes the row or advances the ladder.
    """
    now = now or datetime.now(UTC)
    call = None
    if reference:
        call = await call_by_reference(session, reference)
    if call is None and handle.call_sid:
        call = await call_by_sid(session, handle.call_sid)
    if call is None:
        # Not ours: an inbound receptionist call, or a campaign row from a wiped
        # database. Nothing to reconcile, and not an error.
        logger.info("status callback for unknown call %s", handle.call_sid)
        return None

    if meter:
        get_telephony_provider().record_call_completed(handle)

    call.last_call_sid = handle.call_sid or call.last_call_sid
    if handle.state is CallState.COMPLETED:
        call.state = OutboundCallState.COMPLETED
        call.outcome = str(handle.state)
        call.next_attempt_at = None
        if intake_id is not None:
            call.intake_id = intake_id
    else:
        _advance_ladder(call, outcome=str(handle.state), now=now)

    await session.flush()
    return call


# -- the last rung: WhatsApp ---------------------------------------------------


async def send_call_fallbacks(session: AsyncSession, *, limit: int = 100) -> list[OutboundCall]:
    """Message everyone the ladder gave up on (doc 03 §1b).

    Sent once per row — `fallback_sent_at` is the guard, so a job that runs every
    ten minutes does not message the same patient every ten minutes.
    """
    found = await session.execute(
        select(OutboundCall)
        .where(
            OutboundCall.deleted_at.is_(None),
            OutboundCall.state == OutboundCallState.FAILED,
            OutboundCall.fallback_sent_at.is_(None),
        )
        .limit(limit)
    )
    calls = list(found.scalars().all())
    if not calls:
        return []

    patients = await _patients_by_id(session, {c.patient_id for c in calls})
    sent: list[OutboundCall] = []
    for call in calls:
        patient = patients.get(call.patient_id)
        appointment = await session.get(Appointment, call.appointment_id)
        if patient is None or appointment is None:  # pragma: no cover - FK-guarded
            continue
        hospital = await session.get(Hospital, patient.hospital_id)
        await send_intake_call_fallback(
            session,
            appointment=appointment,
            patient=patient,
            hospital_name=(
                hospital.name_in(patient.lang) if hospital is not None else "the hospital"
            ),
        )
        call.fallback_sent_at = datetime.now(UTC)
        call.state = OutboundCallState.FALLBACK_SENT
        sent.append(call)

    await session.flush()
    return sent


# -- helpers -------------------------------------------------------------------


async def _patients_by_id(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, Patient]:
    if not ids:
        return {}
    found = await session.execute(select(Patient).where(Patient.id.in_(ids)))
    return {patient.id: patient for patient in found.scalars()}


async def _existing_calls(
    session: AsyncSession, appointment_ids: set[uuid.UUID]
) -> dict[uuid.UUID, OutboundCall]:
    if not appointment_ids:
        return {}
    found = await session.execute(
        select(OutboundCall).where(
            OutboundCall.appointment_id.in_(appointment_ids),
            OutboundCall.purpose == CAMPAIGN_PURPOSE,
            OutboundCall.deleted_at.is_(None),
        )
    )
    return {call.appointment_id: call for call in found.scalars()}


def main() -> None:
    """`python -m app.campaign` — print tomorrow's call list and exit.

    The dry run doc 06's AC asks for, as a command a coordinator can run before
    the evening. It dials nobody and writes nothing.
    """
    import asyncio

    from app.db import build_engine, build_sessionmaker

    async def _run() -> str:
        engine = build_engine()
        try:
            async with build_sessionmaker(engine)() as session:
                plan = await plan_campaign(session, for_date=tomorrow())
                return plan.report()
        finally:
            await engine.dispose()

    print(asyncio.run(_run()))


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
