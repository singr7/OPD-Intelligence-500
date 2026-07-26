"""Answers in, a colour out, and who gets woken (doc 03 §9).

> "Responses graded green/amber/red (deterministic rules first, LLM assist for
>  free text); amber → nurse review queue; red → immediate call task + doctor
>  notification; all visible on patient timeline." — doc 03 §9

## Deterministic rules first, and last

`grade` evaluates the question set's own rules — the S4 red-flag language, the S4
evaluator — over the answers, and the colour that comes out is the colour. A
model is not consulted about a temperature of 38.4. This is the same boundary
every other clinical decision in this codebase sits behind (`app.trees.rules`,
the summariser, the answer interpreter), and it is what makes a check-in grade
reviewable: an oncologist can read `seeds/protocols.json` and know exactly which
answers ring a phone.

## What the LLM assist may do, and the one thing it may not

A `free_voice` question ("is anything else troubling you?") produces ASR text
that no rule can match by construction. `assess_free_text` is the doc's "LLM
assist": it reads that sentence and says whether a human should look at it.

It may **raise a green to an amber. It may not produce a red, and it may not
lower anything.** That is a deliberate narrowing of doc 03 §9, and the reason is
the same one that keeps rules off `free_voice` in the first place: a red is an
immediate call task and a doctor's phone at 22:00, and making that depend on how
Whisper heard an accent is a system that cries wolf until nobody answers. An
amber is a nurse reading the sentence herself within the hour, which is the
correct handling of "she said something we could not parse". Structured
questions — the ones an oncologist wrote and reviewed — are what escalate.

Every reason carries its `source` (`rule` or `llm`), so the nurse queue never
shows a model's opinion dressed as a protocol.

## Grades are recomputed, never accumulated

Same rule as red flags (S5): a corrected answer that removes the alarming value
removes the grade. `grade` is a pure function of the answers and the frozen
question snapshot, so re-running it is always safe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checkins import protocols as protocol_bank
from app.config import Settings, get_settings
from app.models.clinical import Dictation
from app.models.content import Checkin, CheckinPlan
from app.models.enums import CheckinGrade, CheckinState, Lang, UsagePurpose
from app.models.org import Doctor
from app.models.patient import Patient
from app.prompts.loader import load
from app.providers.base import ProviderError, with_fallback
from app.providers.llm import LLMRequest
from app.providers.registry import get_sms_provider, llm_chain
from app.providers.sms import SmsMessage

logger = logging.getLogger(__name__)

TRIAGE_PROMPT_ID = "checkin_triage"
TRIAGE_PROMPT_VERSION = 1

#: Grades, worst first. `_worst` picks by this order rather than by comparing
#: strings, so adding a grade never silently reorders severity.
_SEVERITY: tuple[CheckinGrade, ...] = (CheckinGrade.RED, CheckinGrade.AMBER, CheckinGrade.GREEN)


class AnswerError(ValueError):
    """An answer the frozen question cannot accept."""


@dataclass(frozen=True, slots=True)
class Reason:
    """One line on the nurse's queue: what fired, and where it came from."""

    id: str
    grade: CheckinGrade
    reason: str
    #: "rule" (deterministic, from the protocol bank) or "llm" (the free-text
    #: assist). Never merged — the queue shows which is which.
    source: str = "rule"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "grade": str(self.grade),
            "reason": self.reason,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class Grading:
    grade: CheckinGrade
    reasons: tuple[Reason, ...]

    @property
    def needs_review(self) -> bool:
        return self.grade in {CheckinGrade.AMBER, CheckinGrade.RED}


def _worst(grades: list[CheckinGrade]) -> CheckinGrade:
    for grade in _SEVERITY:
        if grade in grades:
            return grade
    return CheckinGrade.GREEN


# -- answers -------------------------------------------------------------------


def frozen_questions(checkin: Checkin) -> list[dict[str, Any]]:
    """The questions as they were asked. Read from the check-in, never the bank —
    the bank may have been re-authored since the message went out."""
    return list(checkin.asked or [])


def validate_answer(question: dict[str, Any], raw: Any) -> Any:
    """One answer against one frozen question, or `AnswerError`.

    Numbers are bounded (`min`/`max` from the snapshot), options must be one of
    the ids that were offered, free text is taken verbatim and never graded by a
    rule. There is no coercion of a nonsense value into a plausible one: "hundred
    and two" is not silently 102, because a patient reporting a Fahrenheit
    temperature into a Celsius question is a real thing and guessing is how it
    becomes a red flag nobody meant.
    """
    kind = question.get("type")
    if kind == "free_voice":
        return str(raw).strip()
    if kind in {"number", "scale"}:
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            raise AnswerError(f"{question['id']}: {raw!r} is not a number") from None
        low, high = question.get("min"), question.get("max")
        if low is not None and value < float(low):
            raise AnswerError(f"{question['id']}: {value} is below {low}")
        if high is not None and value > float(high):
            raise AnswerError(f"{question['id']}: {value} is above {high}")
        return int(value) if value.is_integer() else value
    options = {o["id"] for o in question.get("options", ())}
    if str(raw) not in options:
        raise AnswerError(f"{question['id']}: {raw!r} is not one of {sorted(options)}")
    return str(raw)


def record_answer(checkin: Checkin, *, question_id: str, raw: Any) -> Any:
    """Validate and store one answer on the check-in. Returns the stored value."""
    question = next((q for q in frozen_questions(checkin) if q.get("id") == question_id), None)
    if question is None:
        raise AnswerError(f"{question_id} was not asked in this check-in")
    value = validate_answer(question, raw)
    checkin.responses = {**(checkin.responses or {}), question_id: value}
    return value


def unanswered(checkin: Checkin) -> list[dict[str, Any]]:
    """The questions still outstanding, in the order they were asked."""
    answered = set(checkin.responses or {})
    return [q for q in frozen_questions(checkin) if q.get("id") not in answered]


# -- grading -------------------------------------------------------------------


def grade(checkin: Checkin, *, bank: protocol_bank.ProtocolBank | None = None) -> Grading:
    """The deterministic grade. Pure, and safe to re-run after a correction."""
    bank = bank or protocol_bank.get_bank()
    try:
        qset = bank.question_set(checkin.question_set)
    except protocol_bank.ProtocolError:
        # A question set retired from the bank after this check-in went out. The
        # answers are still on the record; nobody is escalated on a rule set we
        # can no longer read, and the nurse queue is told why.
        logger.warning(
            "check-in %s cites unknown question set %s", checkin.id, checkin.question_set
        )
        return Grading(
            grade=CheckinGrade.AMBER,
            reasons=(
                Reason(
                    id="bank.missing",
                    grade=CheckinGrade.AMBER,
                    reason=f"Question set {checkin.question_set!r} is no longer in the protocol "
                    "bank — grade by hand",
                    source="system",
                ),
            ),
        )

    values = dict(checkin.responses or {})
    reasons = [
        Reason(id=rule.id, grade=rule.grade, reason=rule.reason)
        for rule in qset.grading
        if protocol_bank.rule_lang.evaluate(rule.when, values)
    ]
    return Grading(grade=_worst([r.grade for r in reasons]), reasons=tuple(reasons))


def apply_grade(checkin: Checkin, grading: Grading) -> None:
    checkin.grade = grading.grade
    checkin.grade_reasons = [reason.to_json() for reason in grading.reasons]


# -- the free-text assist ------------------------------------------------------


def free_text_answers(checkin: Checkin) -> list[tuple[str, str]]:
    """(question_id, text) for every free_voice answer on this check-in."""
    responses = checkin.responses or {}
    return [
        (q["id"], str(responses[q["id"]]))
        for q in frozen_questions(checkin)
        if q.get("type") == "free_voice" and responses.get(q["id"])
    ]


async def assess_free_text(
    text: str, *, lang: Lang, settings: Settings | None = None
) -> Reason | None:
    """Ask the model whether a human should read this sentence.

    Returns an **amber** reason or None. It cannot return red (see the module
    docstring) and it cannot clear an existing grade — the caller merges it into
    a grading that the rules already decided.
    """
    settings = settings or get_settings()
    if not text.strip():
        return None
    prompt = load(TRIAGE_PROMPT_ID, TRIAGE_PROMPT_VERSION)
    request = LLMRequest(
        prompt=prompt.render(text=text.strip(), lang=str(lang)),
        system=prompt.system,
        prompt_ref=prompt.ref,
        json_output=True,
        temperature=0.0,
        max_tokens=200,
    )
    try:
        result = await with_fallback(
            llm_chain(settings),
            lambda provider: provider.complete(request, purpose=UsagePurpose.CHECKIN),
        )
        payload = result.json()
    except (ProviderError, ValueError) as exc:
        # No assist is not an emergency: the sentence is on the record and the
        # patient's structured answers were graded by the rules regardless.
        logger.info("free-text assist unavailable: %s", exc)
        return None

    if not isinstance(payload, dict) or not payload.get("concerning"):
        return None
    summary = str(payload.get("summary", "")).strip()[:200]
    return Reason(
        id="llm.free_text",
        grade=CheckinGrade.AMBER,
        reason=f"Patient's own words need a nurse's eye: {summary}"
        if summary
        else "Patient's own words need a nurse's eye",
        source="llm",
    )


async def grade_with_assist(
    checkin: Checkin,
    *,
    bank: protocol_bank.ProtocolBank | None = None,
    settings: Settings | None = None,
) -> Grading:
    """The rules, then the assist on top — raising only."""
    grading = grade(checkin, bank=bank)
    if grading.grade is CheckinGrade.RED:
        # Already the worst it can be; a model has nothing to add and there is no
        # reason to spend a token or a second on it.
        return grading

    extra: list[Reason] = []
    for _, text in free_text_answers(checkin):
        reason = await assess_free_text(text, lang=checkin.lang, settings=settings)
        if reason is not None:
            extra.append(reason)
    if not extra:
        return grading

    reasons = (*grading.reasons, *extra)
    return Grading(grade=_worst([r.grade for r in reasons]), reasons=reasons)


# -- escalation ----------------------------------------------------------------

#: What a red check-in says to the clinician, and what an amber says to nobody
#: (an amber waits on the nurse queue rather than ringing a phone). English:
#: this is staff text, like the queue's priority chips.
_ALERT = (
    "{hospital}: check-in RED — {patient} ({phone}), day {day} after treatment. "
    "{reasons}. Call the patient."
)


async def escalate(
    session: AsyncSession,
    *,
    checkin: Checkin,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> list[str]:
    """A red check-in: notify the doctor who signed, and the coordinator.

    Returns the numbers actually messaged. Never raises — an alert that cannot be
    sent must still leave the check-in escalated and at the top of the nurse
    queue, because the queue is the backstop and a swallowed exception here would
    lose both.

    The doc's "immediate call task" is that queue entry: this pilot has no task
    table, and the call is placed by the human the alert reaches. Registered as
    such in STATE.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    checkin.escalated_at = now

    plan = await session.get(CheckinPlan, checkin.plan_id)
    patient = await session.get(Patient, plan.patient_id) if plan is not None else None
    doctor = await _doctor_for(session, plan=plan)
    if doctor is not None and doctor.user_id is not None:
        checkin.escalated_to = doctor.user_id

    reasons = "; ".join(r.get("reason", "") for r in (checkin.grade_reasons or []))
    body = _ALERT.format(
        hospital="OPD",
        patient=patient.name if patient is not None else "a patient",
        phone=patient.phone if patient is not None else "no number",
        day=checkin.day_offset,
        reasons=reasons or "graded red",
    )

    numbers = [n for n in (doctor.phone if doctor else "", settings.coordinator_phone) if n]
    sent: list[str] = []
    for number in dict.fromkeys(numbers):  # de-duplicated, order kept
        try:
            await get_sms_provider(settings).send(
                SmsMessage(to=number, body=body, template_key="checkin_red_alert"),
                purpose=UsagePurpose.CHECKIN,
            )
            sent.append(number)
        except ProviderError as exc:
            logger.warning("red check-in alert to %s failed: %s", number, exc)

    checkin.delivery = [
        *(checkin.delivery or []),
        {
            "at": now.isoformat(),
            "channel": "sms",
            "status": "sent" if sent else "failed",
            "detail": f"red alert to {len(sent)} number(s)",
        },
    ]
    await session.flush()
    logger.info("check-in %s escalated red; alerted %d", checkin.id, len(sent))
    return sent


async def _doctor_for(session: AsyncSession, *, plan: CheckinPlan | None) -> Doctor | None:
    """The doctor who signed the note this plan came out of."""
    if plan is None or plan.dictation_id is None:
        return None
    dictation = await session.get(Dictation, plan.dictation_id)
    if dictation is None:
        return None
    doctor_id = dictation.signed_by or dictation.doctor_id
    return await session.get(Doctor, doctor_id) if doctor_id else None


async def submit(
    session: AsyncSession,
    *,
    checkin: Checkin,
    answers: dict[str, Any],
    now: datetime | None = None,
    settings: Settings | None = None,
) -> Grading:
    """Record a patient's answers, grade them, and escalate if red.

    The one entry point every channel uses — the WhatsApp bot, the voice call,
    the app and the staff route all land here, so "what happens when a patient
    answers" has exactly one implementation.
    """
    now = now or datetime.now(UTC)
    for question_id, raw in answers.items():
        record_answer(checkin, question_id=question_id, raw=raw)

    grading = await grade_with_assist(checkin, settings=settings)
    apply_grade(checkin, grading)
    checkin.state = CheckinState.ANSWERED
    checkin.answered_at = now
    checkin.next_attempt_at = None
    await session.flush()

    if grading.grade is CheckinGrade.RED:
        await escalate(session, checkin=checkin, now=now, settings=settings)
    return grading


# -- the nurse review queue ----------------------------------------------------


async def review_queue(
    session: AsyncSession, *, limit: int = 100
) -> list[tuple[Checkin, CheckinPlan | None, Patient | None]]:
    """Everything a nurse has to look at, worst and oldest first.

    Red before amber, then oldest answer first — a queue sorted by time alone
    puts a fever behind three sore mouths.
    """
    found = await session.scalars(
        select(Checkin)
        .where(
            Checkin.deleted_at.is_(None),
            Checkin.state == CheckinState.ANSWERED,
            Checkin.grade.in_([CheckinGrade.RED, CheckinGrade.AMBER]),
            Checkin.resolved_at.is_(None),
        )
        .order_by(Checkin.answered_at)
        .limit(limit)
    )
    checkins = list(found)
    rows = []
    for checkin in checkins:
        plan = await session.get(CheckinPlan, checkin.plan_id)
        patient = await session.get(Patient, plan.patient_id) if plan is not None else None
        rows.append((checkin, plan, patient))
    rows.sort(key=lambda row: (row[0].grade is not CheckinGrade.RED, row[0].answered_at))
    return rows


async def resolve(
    session: AsyncSession,
    *,
    checkin: Checkin,
    user_id,
    note: str = "",
    now: datetime | None = None,
) -> Checkin:
    """A nurse has dealt with it. The grade stays — what happened stays true."""
    checkin.resolved_at = now or datetime.now(UTC)
    checkin.resolved_by = user_id
    checkin.resolution_note = note.strip()[:2000]
    await session.flush()
    return checkin
