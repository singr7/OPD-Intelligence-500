"""Digital prescription (doc 03 §8) — what a signed consult note becomes.

Signing a dictation is the clinical act (doc 03 §7); S10 deliberately emitted
nothing from it. This module is the thing it emits. `generate` is called *inside*
`app.dictation.sign`, not exposed as a verb a client can call, because a
prescription that exists without a signature is a prescription nobody stands
behind.

## What this module is not allowed to do

The S10 boundary continues here, and paper is where it finally matters:

* **A drug name is copied, never rewritten.** `app.formulary` sets `known` on an
  exact match alone; `suggestions` were advice on a screen and they do not become
  a printed name. What the doctor said is what prints.
* **A drug the system flagged prints flagged.** `known: false` (not on the
  formulary) and `unsaid` (the model produced a name the doctor is not recorded
  as having said) both carry a visible marker onto the page. The doctor
  acknowledged those to sign; the pharmacist did not, and a page that hides the
  distinction launders a model's guess into an instruction.
* **A dosing schedule is never inferred.** See `parse_schedule` — the pictograms
  exist for a patient who cannot read, and an icon is read as an instruction.
  Guessing which *time of day* "BD" meant would be inventing a dose.

## Why the prescription is built once and stored

`Prescription.meds` is a snapshot taken at signing, not a view over the
dictation. The dictation is already frozen (signing locks it), so the two cannot
drift today — but the check-in plan (§9, S17) and any future amendment flow will
want to know what was *printed*, which is a different question from what was
mapped.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dictation import DictationMapping, MedLine, current_mapping
from app.models.clinical import Dictation, Prescription, Visit
from app.models.org import Doctor

logger = logging.getLogger(__name__)


class PrescriptionError(Exception):
    """A prescription could not be produced from this record."""


# -- the dosing schedule ------------------------------------------------------
#
# This is the one piece of interpretation on the page, so it is the one piece
# with a hard rule: it may only report what the doctor's own words state.


@dataclass(frozen=True, slots=True)
class Schedule:
    """When a drug is taken, as far as the dictation actually says.

    Two different kinds of knowledge, kept apart on purpose:

    * `morning`/`afternoon`/`night` — the *slots*, set only when the words name
      them ("1-0-1", "subah aur raat", "at night").
    * `per_day` — how many doses, which some forms give without naming slots
      ("BD", "twice a day").

    "BD" is conventionally morning-and-night in Indian practice, and that
    convention is exactly what this refuses to encode. A pictogram is read by
    someone who cannot read the words next to it; rendering a sun and a moon for
    a prescription that said only "twice a day" would put a time of day on the
    page that no clinician wrote. When slots are unknown the patient copy shows
    the *count* (N tablet glyphs) and the doctor's own phrase instead.
    """

    morning: bool = False
    afternoon: bool = False
    night: bool = False
    per_day: int | None = None
    #: True when the slots above are stated rather than absent.
    slots_known: bool = False
    #: The phrase this was read from, kept for the clinical copy + audit.
    source: str = ""

    @property
    def doses(self) -> int:
        """Doses per day: from the slots when known, else the stated count."""
        if self.slots_known:
            return sum((self.morning, self.afternoon, self.night))
        return self.per_day or 0


#: `1-0-1`, `1/2-0-1/2`, `1 - 0 - 1`. The dominant Indian prescription notation,
#: and the only one that states slots and count unambiguously.
_SLOT_NOTATION = re.compile(
    r"^\s*(\d+(?:\.\d+)?|\d/\d)\s*[-–/]\s*(\d+(?:\.\d+)?|\d/\d)\s*[-–/]\s*(\d+(?:\.\d+)?|\d/\d)\s*$"
)

#: Phrases that name a time of day. Hindi/Hinglish included because the doctor
#: dictates in it (S10's fixtures are Hinglish) — matched on word boundaries so
#: "raat" does not fire inside another word.
_MORNING = (r"morning", r"subah", r"savere", r"breakfast", r"naashta")
_AFTERNOON = (r"afternoon", r"noon", r"dopahar", r"lunch", r"din me?n? ka khana")
_NIGHT = (r"night", r"raat", r"bedtime", r"hs\b", r"dinner", r"sone se pehle")

#: Count-only forms. These give doses per day and say nothing about when.
_COUNT_WORDS: tuple[tuple[str, int], ...] = (
    (
        r"\bod\b|\bonce\s+(?:a\s+)?(?:day|daily)\b|\bdin\s+mei?n?\s+ek\s+baar\b|\broz\s+ek\s+baar\b",
        1,
    ),
    (r"\bbd\b|\bbid\b|\btwice\s+(?:a\s+)?(?:day|daily)\b|\bdin\s+mei?n?\s+do\s+baar\b", 2),
    (
        r"\btds\b|\btid\b|\bthrice\s+(?:a\s+)?(?:day|daily)\b|\bthree\s+times\s+(?:a\s+)?day\b"
        r"|\bdin\s+mei?n?\s+teen\s+baar\b",
        3,
    ),
    (r"\bqid\b|\bqds\b|\bfour\s+times\s+(?:a\s+)?day\b|\bdin\s+mei?n?\s+chaar\s+baar\b", 4),
)


def parse_schedule(freq: str | None) -> Schedule | None:
    """Read a dictated frequency into a schedule, or return `None`.

    `None` is a first-class answer and the safe one: the patient copy then prints
    the doctor's words with no pictogram at all. Every unrecognised phrase must
    land here rather than in a plausible guess — "SOS", "as needed", "alternate
    days" and "weekly" all describe regimens these three icons cannot express,
    and an icon a patient can misread is worse than words they must ask about.
    """
    if not freq:
        return None
    text = freq.strip()
    if not text:
        return None

    notation = _SLOT_NOTATION.match(text)
    if notation:
        morning, afternoon, night = (_positive(group) for group in notation.groups())
        if not (morning or afternoon or night):
            # "0-0-0" is not a prescription; treat it as unreadable rather than
            # printing a drug with an empty schedule.
            return None
        return Schedule(
            morning=morning,
            afternoon=afternoon,
            night=night,
            per_day=sum((morning, afternoon, night)),
            slots_known=True,
            source=text,
        )

    lowered = text.lower()
    morning = _mentions(lowered, _MORNING)
    afternoon = _mentions(lowered, _AFTERNOON)
    night = _mentions(lowered, _NIGHT)
    if morning or afternoon or night:
        return Schedule(
            morning=morning,
            afternoon=afternoon,
            night=night,
            per_day=sum((morning, afternoon, night)),
            slots_known=True,
            source=text,
        )

    for pattern, count in _COUNT_WORDS:
        if re.search(pattern, lowered):
            # Count without slots: honest about both halves.
            return Schedule(per_day=count, slots_known=False, source=text)

    return None


def _positive(group: str) -> bool:
    """Is this slot's quantity non-zero? Handles `1`, `0.5` and `1/2`."""
    if "/" in group:
        numerator, _, denominator = group.partition("/")
        try:
            return float(numerator) / float(denominator) > 0
        except (ValueError, ZeroDivisionError):
            return False
    try:
        return float(group) > 0
    except ValueError:
        return False


def _mentions(text: str, patterns: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"\b{pattern}" if not pattern.endswith(r"\b") else pattern, text)
        for pattern in patterns
    )


# -- the printable line -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RxLine:
    """One drug as it will appear on paper.

    `med` is the S10 record verbatim. Nothing here rewrites it; this only adds
    what the page needs to lay it out.
    """

    med: MedLine
    schedule: Schedule | None

    @property
    def flagged(self) -> bool:
        """Does this line carry a warning the pharmacist must see?

        Mirrors `DictationMapping.meds_needing_attention` *before* the doctor's
        acknowledgement is applied: acknowledging let them sign, it did not make
        the drug known. The page still says so.
        """
        return not self.med.known or self.med.unsaid

    @property
    def flag_reason(self) -> str | None:
        if self.med.unsaid:
            return "not heard in the dictation — confirm with the doctor"
        if not self.med.known:
            return "not on the hospital formulary — dictated as written"
        return None

    def to_dict(self) -> dict[str, Any]:
        schedule = self.schedule
        return {
            **self.med.to_dict(),
            "schedule": (
                None
                if schedule is None
                else {
                    "morning": schedule.morning,
                    "afternoon": schedule.afternoon,
                    "night": schedule.night,
                    "per_day": schedule.per_day,
                    "slots_known": schedule.slots_known,
                    "source": schedule.source,
                }
            ),
            "flagged": self.flagged,
            "flag_reason": self.flag_reason,
        }


def build_lines(mapping: DictationMapping) -> tuple[RxLine, ...]:
    """The signed mapping's meds, each with whatever schedule its words support."""
    return tuple(RxLine(med=med, schedule=parse_schedule(med.freq)) for med in mapping.meds)


# -- generation ---------------------------------------------------------------


async def generate(
    session: AsyncSession, *, dictation: Dictation, doctor: Doctor
) -> Prescription | None:
    """Create the prescription for a just-signed dictation (idempotent).

    Returns `None` when the note prescribes nothing — a consult that ends in
    advice and a follow-up date is a complete consult, and an empty prescription
    is a form, not a document. Callers must handle `None` rather than assume a
    row.

    Idempotent by `dictation_id`: signing is terminal, so a second call can only
    come from a retry, and a retry must not produce a second prescription for the
    same signature.
    """
    mapping = current_mapping(dictation)
    if mapping is None:
        raise PrescriptionError("cannot prescribe from a dictation that has not been mapped")

    existing = await for_dictation(session, dictation_id=dictation.id)
    if existing is not None:
        return existing

    lines = build_lines(mapping)
    if not lines:
        logger.info("dictation %s signed with no meds; no prescription", dictation.id)
        return None

    prescription = Prescription(
        visit_id=dictation.visit_id,
        dictation_id=dictation.id,
        meds=[line.to_dict() for line in lines],
        delivered_via={},
    )
    session.add(prescription)
    await session.flush()
    logger.info(
        "prescription %s generated from dictation %s (%d meds, %d flagged) by doctor %s",
        prescription.id,
        dictation.id,
        len(lines),
        sum(1 for line in lines if line.flagged),
        doctor.id,
    )
    return prescription


async def for_dictation(session: AsyncSession, *, dictation_id: uuid.UUID) -> Prescription | None:
    result = await session.execute(
        select(Prescription).where(
            Prescription.dictation_id == dictation_id,
            Prescription.deleted_at.is_(None),
        )
    )
    return result.scalars().first()


async def for_visit(session: AsyncSession, *, visit_id: uuid.UUID) -> Prescription | None:
    result = await session.execute(
        select(Prescription)
        .where(Prescription.visit_id == visit_id, Prescription.deleted_at.is_(None))
        .order_by(Prescription.created_at.desc())
    )
    return result.scalars().first()


async def history(
    session: AsyncSession, *, patient_id: uuid.UUID, limit: int = 20
) -> list[tuple[Prescription, Visit]]:
    """Every prescription this patient has been given, newest first (doc 03 §8).

    Returned with each visit so a caller can date the prescription without a
    second round trip — the visit is what carries the date and the department.
    """
    result = await session.execute(
        select(Prescription, Visit)
        .join(Visit, Prescription.visit_id == Visit.id)
        .where(
            Visit.patient_id == patient_id,
            Prescription.deleted_at.is_(None),
            Visit.deleted_at.is_(None),
        )
        .order_by(Visit.date.desc(), Prescription.created_at.desc())
        .limit(limit)
    )
    return [(row[0], row[1]) for row in result.all()]


def lines_of(prescription: Prescription) -> tuple[RxLine, ...]:
    """The stored snapshot back as `RxLine`s, for rendering and delivery.

    Reads `meds` as written at signing — it does not re-derive the schedule, so a
    later change to `parse_schedule` cannot silently re-interpret a prescription
    that has already been handed to a patient.
    """
    from app.dictation import DictationMapping as _Mapping  # local: avoids a cycle at import

    lines: list[RxLine] = []
    for row in prescription.meds:
        if not isinstance(row, dict):
            continue
        med = _Mapping.parse({"meds": [row]}).meds
        if not med:
            continue
        stored = row.get("schedule")
        schedule = (
            Schedule(
                morning=bool(stored.get("morning")),
                afternoon=bool(stored.get("afternoon")),
                night=bool(stored.get("night")),
                per_day=stored.get("per_day"),
                slots_known=bool(stored.get("slots_known")),
                source=str(stored.get("source") or ""),
            )
            if isinstance(stored, dict)
            else None
        )
        lines.append(RxLine(med=med[0], schedule=schedule))
    return tuple(lines)


def record_delivery(
    prescription: Prescription, *, channel: str, status: str, detail: str | None = None
) -> None:
    """Stamp one delivery attempt onto `delivered_via` (doc 02 §4's shape).

    Per channel, last attempt wins — the interesting question at the desk is "did
    the patient get it", not the full history, and `audit_log` already holds the
    trail of every write.
    """
    entry: dict[str, Any] = {"at": datetime.now(UTC).isoformat(), "status": status}
    if detail:
        entry["detail"] = detail
    # Reassign rather than mutate: a JSONB column mutated in place is not seen as
    # dirty by SQLAlchemy and the write is silently dropped.
    prescription.delivered_via = {**(prescription.delivered_via or {}), channel: entry}
