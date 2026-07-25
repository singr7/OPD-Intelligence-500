"""From a signed note to an approved plan (doc 03 §9).

> "On dictation sign: `treatment_events` + protocol templates (per regimen
>  family, admin-editable) → LLM personalizes a check-in plan (days, channels,
>  question sets) referencing the doctor's actual notes; doctor approves in one
>  tap (edit optional)." — doc 03 §9

Three steps, in this order, and the order is the whole design:

1. **Choose a protocol** — deterministically, from the drugs the doctor actually
   prescribed (their formulary *class*) and the words they actually used. A
   model does not pick the regimen family.
2. **Draft the schedule** — deterministically, from that protocol's day offsets
   and question sets, anchored on the treatment date the doctor dictated.
3. **Personalise the messages** — and *only* the messages. The LLM is handed the
   skeleton and the note and asked to write the covering line a patient reads;
   whatever it returns is matched back against the deterministic draft rung by
   rung, and anything that does not line up is discarded in favour of the draft.

So the worst a bad model day can do is send a generic sentence. It cannot move a
day, drop a rung, change a question set, or write a message onto a check-in that
is not in the protocol — because step 3 never produces the plan, it only supplies
strings to a plan step 2 already built.

## Nothing here can fail a signature

`draft_from_dictation` is called inside `app.dictation.sign`, next to the
prescription. A signature is a clinical act with a patient in the room; a
follow-up plan is a message next week. So every failure in this module — no
matching protocol, the LLM down, a malformed personalisation — degrades to
"fewer or plainer check-ins", never to "the doctor could not sign". The same
stance `app.prescription` takes about a failed delivery.

## The plan is frozen at approval

`CheckinPlan.schedule` is a snapshot, and `Checkin.asked` freezes the questions
themselves. Re-authoring a protocol next month changes what the *next* patient is
asked and nothing about a plan already approved — the S11 prescription-snapshot
argument, for the same reason: a patient answered the question she was shown.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checkins import protocols as protocol_bank
from app.checkins.window import send_time_on
from app.config import Settings, get_settings
from app.dictation import DictationMapping, current_mapping
from app.formulary import lookup
from app.models.clinical import Dictation
from app.models.content import Checkin, CheckinPlan
from app.models.enums import Channel, CheckinPlanStatus, CheckinState, Lang, UsagePurpose
from app.models.org import Doctor
from app.models.patient import Patient
from app.prompts.loader import load
from app.providers.base import ProviderError, with_fallback
from app.providers.llm import LLMRequest
from app.providers.registry import llm_chain

logger = logging.getLogger(__name__)

PROMPT_ID = "checkin_personalize"
PROMPT_VERSION = 1

#: The delivery ladder, in order (doc 03 §9: "WhatsApp → AI voice call → SMS").
#: The first rung a patient can actually use becomes the check-in's starting
#: channel; `app.checkins.delivery` walks the rest when a rung fails.
LADDER: tuple[Channel, ...] = (Channel.WHATSAPP, Channel.PHONE, Channel.SMS)


class PlanError(Exception):
    """A caller asked for something the plan's state does not allow."""


# -- 1. choose the protocol ----------------------------------------------------


def _haystack(mapping: DictationMapping) -> str:
    """The doctor's own words, lowercased, that a keyword may match.

    Deliberately *not* the raw transcript: the transcript contains the patient's
    speech and the room's small talk, and matching "operation" out of "my
    daughter had an operation last year" would put a woman on a wound-care
    protocol. Only the fields the doctor's structured note asserts.
    """
    parts = [mapping.diagnosis or "", mapping.follow_up.instructions, *mapping.advice]
    parts += [f"{e.regimen} {e.as_spoken}" for e in mapping.treatment_events]
    return " ".join(parts).lower()


def matching_protocols(
    mapping: DictationMapping, *, bank: protocol_bank.ProtocolBank | None = None
) -> list[protocol_bank.Protocol]:
    """Every regimen family this note matches, most specific first.

    Matching is on the formulary **class** of each prescribed drug (so a note
    saying "Kemocarb" matches platinum without the bank listing brand names) and
    on lowercase keywords over the doctor's structured note.
    """
    bank = bank or protocol_bank.get_bank()
    classes = {
        found.drug_class
        for med in mapping.meds
        if (found := lookup(med.name)).drug_class is not None
    }
    text = _haystack(mapping)

    matched = [
        protocol
        for protocol in bank.protocols.values()
        if (protocol.drug_classes & classes) or any(word in text for word in protocol.keywords)
    ]
    return sorted(matched, key=lambda p: -p.precedence)


def choose_protocol(
    mapping: DictationMapping, *, bank: protocol_bank.ProtocolBank | None = None
) -> protocol_bank.Protocol | None:
    """The one family this plan follows, or None — not every consult starts one.

    A note with no chemotherapy, no radiotherapy, no operation and no palliative
    intent gets **no plan at all**. Drafting an empty one so the doctor has
    something to approve would train them to tap through it.
    """
    matched = matching_protocols(mapping, bank=bank)
    return matched[0] if matched else None


# -- 2. draft the schedule -----------------------------------------------------


def _parse_date(value: str | None) -> datetime | None:
    """A dictated ISO date, at the hospital's send hour. `None` for anything the
    mapper left as words — a date we cannot read is not a date we guess."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=UTC)
    except ValueError:
        return None


def treatment_anchor(mapping: DictationMapping, *, signed_at: datetime) -> datetime:
    """The instant "D+2" counts from.

    The treatment date the doctor dictated, when they dictated one; otherwise the
    signature. Falling back to the signature is honest — the consult is when the
    treatment was given, in the overwhelming case.
    """
    for event in mapping.treatment_events:
        when = _parse_date(event.date)
        if when is not None:
            return when
    return signed_at


def next_cycle_at(
    mapping: DictationMapping, *, protocol: protocol_bank.Protocol, anchor: datetime
) -> datetime | None:
    """When the next cycle is due, for the D-2/D-0 reminders.

    The doctor's own `next_due` wins. Only if they did not say one do we fall
    back to the protocol's cycle length — and only for a regimen that *has* one,
    so radiotherapy and post-op plans never invent a next cycle.
    """
    for event in mapping.treatment_events:
        when = _parse_date(event.next_due)
        if when is not None:
            return when
    if protocol.cycle_days > 0:
        return anchor + timedelta(days=protocol.cycle_days)
    return None


def reachable_channels(patient: Patient) -> tuple[Channel, ...]:
    """The rungs of the ladder this patient can actually be reached on.

    Today that is "does she have a phone number", because every channel we have
    rides on one. It is a named function rather than an inline `if` because it is
    where a real reachability signal belongs when there is one to read (a patient
    who has never opened WhatsApp, a number that is her son's).
    """
    if not patient.phone:
        return ()
    return LADDER


@dataclass(slots=True)
class DraftRung:
    """One rung of a drafted plan, before it is a row."""

    day_offset: int
    question_set: str
    channel: Channel
    due_at: datetime
    message: str

    def to_json(self) -> dict[str, Any]:
        return {
            "day_offset": self.day_offset,
            "question_set": self.question_set,
            "channel": str(self.channel),
            "due_at": self.due_at.isoformat(),
            "message": self.message,
        }


@dataclass(slots=True)
class Draft:
    protocol_key: str
    anchor: datetime
    lang: Lang
    rungs: list[DraftRung] = field(default_factory=list)
    next_cycle_at: datetime | None = None
    personalisation: dict[str, Any] = field(default_factory=dict)


#: The plain covering line, per language, when there is no model to write a
#: better one. `{title}` is the question set's own title, which is already
#: authored in four languages — so the fallback is never English at a patient.
_PLAIN_MESSAGE: dict[Lang, str] = {
    Lang.EN: "Namaste. From the hospital — a few questions about how you are: {title}.",
    Lang.HI: "नमस्ते। अस्पताल से — आप कैसे हैं, इस बारे में कुछ सवाल: {title}।",
    Lang.MR: "नमस्कार. रुग्णालयाकडून — तुम्ही कसे आहात याबद्दल काही प्रश्न: {title}.",
    Lang.TE: "నమస్తే. ఆసుపత్రి నుండి — మీరు ఎలా ఉన్నారనే దానిపై కొన్ని ప్రశ్నలు: {title}.",
}


def plain_message(qset: protocol_bank.QuestionSet, lang: Lang) -> str:
    template = _PLAIN_MESSAGE.get(lang, _PLAIN_MESSAGE[Lang.EN])
    return template.format(title=qset.title.get(lang) or qset.title[Lang.EN])


def draft_schedule(
    *,
    protocol: protocol_bank.Protocol,
    anchor: datetime,
    lang: Lang,
    channels: tuple[Channel, ...],
    bank: protocol_bank.ProtocolBank | None = None,
    settings: Settings | None = None,
) -> list[DraftRung]:
    """The protocol's own days, as instants, with the plain message.

    This is the plan. Everything after it is wording.
    """
    bank = bank or protocol_bank.get_bank()
    settings = settings or get_settings()
    first = channels[0] if channels else Channel.WHATSAPP
    rungs = []
    for scheduled in protocol.checkins:
        qset = bank.question_set(scheduled.question_set)
        due = send_time_on(anchor + timedelta(days=scheduled.day_offset), settings=settings)
        rungs.append(
            DraftRung(
                day_offset=scheduled.day_offset,
                question_set=scheduled.question_set,
                channel=first,
                due_at=due,
                message=plain_message(qset, lang),
            )
        )
    return rungs


# -- 3. personalise the messages ----------------------------------------------


def _patient_line(patient: Patient) -> str:
    age = f", {patient.age}" if getattr(patient, "age", None) else ""
    return f"{patient.name}{age}, speaks {patient.lang or Lang.HI}"


def apply_personalisation(
    rungs: list[DraftRung], payload: Any, *, channels: tuple[Channel, ...]
) -> tuple[int, list[str]]:
    """Copy the model's messages onto the draft, rung by rung. Nothing else.

    Returns `(applied, complaints)`. A rung the model did not cover, covered
    twice, or covered with an empty string keeps its plain message; a channel it
    proposes is taken only if the patient can be reached on it. The day offsets
    and question sets are never read back — they are already correct, and reading
    them would be the door through which a model reschedules a clinical follow-up.
    """
    complaints: list[str] = []
    if not isinstance(payload, dict):
        return 0, ["personalisation was not an object"]
    schedule = payload.get("schedule")
    if not isinstance(schedule, list):
        return 0, ["personalisation had no schedule list"]

    by_rung: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in schedule:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("day_offset"), entry.get("question_set"))
        if key in by_rung:
            complaints.append(f"two entries for day {key[0]}")
            continue
        by_rung[key] = entry  # type: ignore[index]

    applied = 0
    for rung in rungs:
        entry = by_rung.get((rung.day_offset, rung.question_set))
        if entry is None:
            complaints.append(f"no entry for day {rung.day_offset} / {rung.question_set}")
            continue
        message = entry.get("message")
        if not isinstance(message, str) or not message.strip():
            complaints.append(f"day {rung.day_offset}: empty message")
            continue
        rung.message = message.strip()
        applied += 1

        proposed = entry.get("channel")
        try:
            channel = Channel(proposed)
        except ValueError:
            continue
        if channel in channels:
            rung.channel = channel
        else:
            complaints.append(f"day {rung.day_offset}: unreachable channel {proposed}")

    return applied, complaints


async def personalise(
    draft: Draft,
    *,
    mapping: DictationMapping,
    patient: Patient,
    channels: tuple[Channel, ...],
    bank: protocol_bank.ProtocolBank | None = None,
    settings: Settings | None = None,
) -> None:
    """Ask the model for better wording; keep the draft if anything is off.

    Records what happened on `draft.personalisation` either way, so the doctor
    approving the plan (and anyone reading it in six months) can tell a
    personalised message from a plain one.
    """
    bank = bank or protocol_bank.get_bank()
    settings = settings or get_settings()
    prompt = load(PROMPT_ID, PROMPT_VERSION)
    rendered = prompt.render(
        today=draft.anchor.date().isoformat(),
        patient=_patient_line(patient),
        reachability=", ".join(str(c) for c in channels) or "none",
        protocol=str(bank.prompt_payload(draft.protocol_key)),
        dictation=str(mapping.to_dict()),
    )
    request = LLMRequest(
        prompt=rendered,
        system=prompt.system,
        prompt_ref=prompt.ref,
        json_output=True,
        # Writing, not transcription — but barely. This is one sentence a
        # frightened person reads, not prose.
        temperature=0.2,
        max_tokens=900,
    )
    try:
        result = await with_fallback(
            llm_chain(settings),
            lambda provider: provider.complete(request, purpose=UsagePurpose.CHECKIN),
        )
        payload = result.json()
    except (ProviderError, ValueError) as exc:
        # A patient still gets her check-in, in her language, saying what it is
        # about. She just does not get "you said the nausea was bad last time".
        draft.personalisation = {
            "prompt_ref": prompt.ref,
            "applied": 0,
            "error": str(exc),
        }
        logger.warning("check-in personalisation failed: %s", exc)
        return

    applied, complaints = apply_personalisation(draft.rungs, payload, channels=channels)
    draft.personalisation = {
        "model": result.model,
        "prompt_ref": prompt.ref,
        "applied": applied,
        "of": len(draft.rungs),
        "notes_for_doctor": str(payload.get("notes_for_doctor", ""))[:400]
        if isinstance(payload, dict)
        else "",
        "complaints": complaints,
    }


# -- the entry point -----------------------------------------------------------


async def draft_from_dictation(
    session: AsyncSession,
    *,
    dictation: Dictation,
    doctor: Doctor,
    settings: Settings | None = None,
) -> CheckinPlan | None:
    """Draft this visit's follow-up. Called from `dictation.sign`; never raises.

    Idempotent per dictation: a re-signature (there is no such thing today, but
    `sign` is the kind of code that acquires a retry) finds the existing plan and
    returns it rather than drafting a second.
    """
    settings = settings or get_settings()
    existing = await session.scalar(
        select(CheckinPlan).where(
            CheckinPlan.dictation_id == dictation.id, CheckinPlan.deleted_at.is_(None)
        )
    )
    if existing is not None:
        return existing

    try:
        return await _draft(session, dictation=dictation, doctor=doctor, settings=settings)
    except Exception:  # noqa: BLE001 - a follow-up must never cost a signature
        logger.exception("check-in plan drafting failed for dictation %s", dictation.id)
        return None


async def _draft(
    session: AsyncSession,
    *,
    dictation: Dictation,
    doctor: Doctor,
    settings: Settings,
) -> CheckinPlan | None:
    mapping = current_mapping(dictation)
    if mapping is None:
        return None
    protocol = choose_protocol(mapping)
    if protocol is None:
        logger.info("dictation %s matches no regimen family — no plan", dictation.id)
        return None

    patient = await _patient_for(session, dictation=dictation)
    if patient is None:  # pragma: no cover - FK-guaranteed
        return None

    lang = patient.lang or Lang.HI
    anchor = treatment_anchor(mapping, signed_at=dictation.signed_at or datetime.now(UTC))
    channels = reachable_channels(patient)
    draft = Draft(
        protocol_key=protocol.key,
        anchor=anchor,
        lang=lang,
        rungs=draft_schedule(
            protocol=protocol, anchor=anchor, lang=lang, channels=channels, settings=settings
        ),
        next_cycle_at=next_cycle_at(mapping, protocol=protocol, anchor=anchor),
    )
    await personalise(draft, mapping=mapping, patient=patient, channels=channels, settings=settings)
    draft.personalisation["matched_protocols"] = [p.key for p in matching_protocols(mapping)]

    plan = CheckinPlan(
        patient_id=patient.id,
        visit_id=dictation.visit_id,
        dictation_id=dictation.id,
        protocol_key=protocol.key,
        treatment_at=anchor,
        lang=lang,
        schedule=[rung.to_json() for rung in draft.rungs],
        personalisation=draft.personalisation,
        next_cycle_at=draft.next_cycle_at,
        status=CheckinPlanStatus.DRAFT,
    )
    session.add(plan)
    await session.flush()
    logger.info(
        "drafted check-in plan %s (%s, %d rungs) for dictation %s",
        plan.id,
        protocol.key,
        len(draft.rungs),
        dictation.id,
    )
    return plan


async def _patient_for(session: AsyncSession, *, dictation: Dictation) -> Patient | None:
    from app.models.clinical import Visit

    visit = await session.get(Visit, dictation.visit_id)
    if visit is None:
        return None
    return await session.get(Patient, visit.patient_id)


# -- approval ------------------------------------------------------------------


async def approve(
    session: AsyncSession,
    *,
    plan: CheckinPlan,
    doctor: Doctor,
    now: datetime | None = None,
) -> list[Checkin]:
    """The doctor's one tap: freeze the plan and materialise its check-ins.

    Nothing is delivered before this. A drafted plan that no doctor approves
    messages nobody — which is the correct failure, because the alternative is a
    hospital messaging a patient about a treatment plan a clinician never read.
    """
    if plan.status is not CheckinPlanStatus.DRAFT:
        raise PlanError(f"plan {plan.id} is {plan.status}, not a draft")

    now = now or datetime.now(UTC)
    bank = protocol_bank.get_bank()
    created: list[Checkin] = []
    for rung in plan.schedule or []:
        qset = bank.question_set(str(rung["question_set"]))
        due_at = datetime.fromisoformat(str(rung["due_at"]))
        checkin = Checkin(
            plan_id=plan.id,
            due_at=due_at,
            day_offset=int(rung["day_offset"]),
            question_set=qset.key,
            asked=[question.to_json() for question in qset.questions],
            message=str(rung.get("message", "")),
            lang=plan.lang,
            channel=Channel(str(rung["channel"])),
            state=CheckinState.PENDING,
            # Due in the past (a plan approved days late) is due now, not
            # skipped: the questions are still worth asking.
            next_attempt_at=max(due_at, now),
        )
        session.add(checkin)
        created.append(checkin)

    plan.approved_by = doctor.id
    plan.approved_at = now
    plan.status = CheckinPlanStatus.ACTIVE
    await session.flush()
    logger.info("plan %s approved by doctor %s — %d check-ins", plan.id, doctor.id, len(created))
    return created


async def cancel(session: AsyncSession, *, plan: CheckinPlan) -> None:
    """Stop a plan and everything it has not yet sent.

    Answered check-ins keep their answers — they happened. Only the pending ones
    are cancelled, because the clinical record of what a patient said does not
    become untrue when a plan is stopped.
    """
    plan.status = CheckinPlanStatus.CANCELLED
    pending = await session.scalars(
        select(Checkin).where(
            Checkin.plan_id == plan.id,
            Checkin.state.in_([CheckinState.PENDING, CheckinState.SENT]),
            Checkin.deleted_at.is_(None),
        )
    )
    for checkin in pending:
        checkin.state = CheckinState.CANCELLED
        checkin.next_attempt_at = None
    await session.flush()


async def plans_for_patient(session: AsyncSession, *, patient_id: uuid.UUID) -> list[CheckinPlan]:
    found = await session.scalars(
        select(CheckinPlan)
        .where(CheckinPlan.patient_id == patient_id, CheckinPlan.deleted_at.is_(None))
        .order_by(CheckinPlan.created_at.desc())
    )
    return list(found)
