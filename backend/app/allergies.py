"""What this record knows about a patient's allergies, and how it says so.

Session B closed with the console telling the truth in words: *"Allergies not
captured by this system yet — ask the patient."* Nothing recorded one, so the
spine said so, and it pointedly refused to say "no known allergies" — a clinical
claim the record could not make and a prescribing doctor would act on. Six
sessions later that line was still the largest gap in the spine's four elements.

This module is the other half of that refusal: now that statements *can* be
recorded, the honesty has to move from a hardcoded sentence into a derivation.
Everything here exists to keep three states apart, on every surface, forever:

    never_asked   no statement exists. Nobody has asked this patient.
    none_stated   somebody asked and was told there are none — with a source
                  and a date attached, because "the patient said none at the
                  kiosk this morning" and "her oncologist confirmed none" are
                  different amounts of evidence and the doctor picks.
    known         one or more live substances, whatever else was ever said.

**The states are ordered, and `known` wins.** A `none_known` statement never
suppresses a live substance row, even a much older one: it is far likelier that
a rushed second asking produced "no" than that a penicillin anaphylaxis stopped
being true. Un-saying an allergy is a deliberate act — `retract` — and it takes
a clinician and a reason.

## What this module will not do

- **It never composes the phrase "no known allergies".** `none_stated` always
  travels with its source and its date, and the rendering surfaces are tested on
  it. The bare phrase is a summary of a chart review nobody here has performed.
- **It does not check drugs.** Nothing in this module reads the formulary and
  nothing in the prescription path calls it. An interaction checker that matches
  free text a patient typed at a kiosk against a drug list would be a safety
  feature made of guesses, and the failure mode of a *missed* match is a doctor
  who trusted a green tick. The spine puts the words in front of the doctor and
  the doctor decides. This is deliberate and should stay that way until there is
  a coded substance vocabulary and somebody clinical owns it.
- **It does not merge duplicates.** "Penicillin" said in March and "penicilin"
  said in August are two statements by two people at two times, and collapsing
  them would throw away the provenance that makes either readable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AllergyKind, AllergySeverity, AllergySource
from app.models.org import Doctor
from app.models.patient import PatientAllergy

#: What a substance string may be. Long enough for "sulfa drugs (rash as a
#: child)" as a patient might type it, short enough that a stuck STT stream
#: cannot write a paragraph into the top of every doctor's screen.
MAX_SUBSTANCE = 200
#: How many substances one kiosk intake may state. A patient tapping "yes" and
#: naming twelve things has misunderstood the question; the cap keeps the spine
#: renderable and the excess is dropped rather than silently truncated into one
#: run-on row (see `from_intake`).
MAX_FROM_INTAKE = 6


class AllergyError(ValueError):
    """A statement this record will not accept, or one it cannot find."""


@dataclass(slots=True)
class AllergyEntry:
    """One statement, flattened for a reader.

    Carries its own provenance rather than leaving the surface to look it up:
    every rendering of an allergy in this product states who said it and when,
    and a shape that makes that optional invites a surface to drop it.
    """

    id: uuid.UUID
    kind: str
    substance: str | None
    substance_en: str | None
    reaction: str | None
    severity: str
    source: str
    stated_at: datetime
    #: Set when a clinician has stood behind this specific statement.
    confirmed_at: datetime | None = None
    confirmed_by_name: str | None = None
    recorded_by_name: str | None = None
    retracted_at: datetime | None = None
    retracted_by_name: str | None = None
    retracted_reason: str | None = None

    @property
    def is_severe(self) -> bool:
        return self.severity == AllergySeverity.SEVERE


@dataclass(slots=True)
class AllergyView:
    """Everything a surface needs to render allergies honestly.

    `state` is the only thing a caller should branch on. It is computed here
    exactly once so the spine, the History tab and any later surface cannot
    disagree about what this patient's record actually says.
    """

    state: str
    #: Live substances, severe first, then most recently stated. The order is
    #: the reading order on a screen a doctor scans in two seconds.
    entries: list[AllergyEntry] = field(default_factory=list)
    #: The latest live "none" statement, when that is the whole story.
    none_statement: AllergyEntry | None = None
    #: Struck-out statements, newest first. Never shown in the spine; the
    #: History tab shows them because "this record once said penicillin and a
    #: doctor withdrew it" is a fact a later reader needs.
    retracted: list[AllergyEntry] = field(default_factory=list)

    @property
    def has_severe(self) -> bool:
        return any(e.is_severe for e in self.entries)

    @property
    def unconfirmed_count(self) -> int:
        """Live substances no clinician has stood behind yet."""
        return sum(1 for e in self.entries if e.confirmed_at is None)


NEVER_ASKED = "never_asked"
NONE_STATED = "none_stated"
KNOWN = "known"


async def for_patient(session: AsyncSession, *, patient_id: uuid.UUID) -> AllergyView:
    """The current picture, derived from the statement log.

    One query and two dict lookups for the doctor names — this runs on every
    patient card, which is the hot path of the console.
    """
    result = await session.execute(
        select(PatientAllergy)
        .where(
            PatientAllergy.patient_id == patient_id,
            PatientAllergy.deleted_at.is_(None),
        )
        .order_by(PatientAllergy.created_at.desc())
    )
    rows = list(result.scalars().all())
    if not rows:
        return AllergyView(state=NEVER_ASKED)

    names = await _doctor_names(session, rows)
    live = [r for r in rows if r.is_live]
    substances = [_entry(r, names) for r in live if r.kind is AllergyKind.SUBSTANCE]
    retracted = [_entry(r, names) for r in rows if not r.is_live]

    if substances:
        substances.sort(key=lambda e: (not e.is_severe, -e.stated_at.timestamp()))
        return AllergyView(state=KNOWN, entries=substances, retracted=retracted)

    nones = [r for r in live if r.kind is AllergyKind.NONE_KNOWN]
    if nones:
        # Newest first from the query, but prefer a clinician's statement: a
        # doctor who asked outranks a tablet, regardless of which came later.
        nones.sort(key=lambda r: (r.source is not AllergySource.DOCTOR, -r.created_at.timestamp()))
        return AllergyView(
            state=NONE_STATED, none_statement=_entry(nones[0], names), retracted=retracted
        )

    # Everything on file has been struck out. That is not "nobody asked" — a
    # doctor withdrew something, and the next reader should be asking again
    # rather than reading silence as reassurance.
    return AllergyView(state=NEVER_ASKED, retracted=retracted)


def _entry(row: PatientAllergy, names: dict[uuid.UUID, str]) -> AllergyEntry:
    return AllergyEntry(
        id=row.id,
        kind=str(row.kind),
        substance=row.substance,
        substance_en=row.substance_en,
        reaction=row.reaction,
        severity=str(row.severity),
        source=str(row.source),
        stated_at=row.created_at,
        confirmed_at=row.confirmed_at,
        confirmed_by_name=names.get(row.confirmed_by_doctor_id)
        if row.confirmed_by_doctor_id
        else None,
        recorded_by_name=names.get(row.recorded_by_doctor_id)
        if row.recorded_by_doctor_id
        else None,
        retracted_at=row.retracted_at,
        retracted_by_name=names.get(row.retracted_by_doctor_id)
        if row.retracted_by_doctor_id
        else None,
        retracted_reason=row.retracted_reason,
    )


async def _doctor_names(session: AsyncSession, rows: list[PatientAllergy]) -> dict[uuid.UUID, str]:
    ids = {
        doctor_id
        for row in rows
        for doctor_id in (
            row.recorded_by_doctor_id,
            row.confirmed_by_doctor_id,
            row.retracted_by_doctor_id,
        )
        if doctor_id is not None
    }
    if not ids:
        return {}
    result = await session.execute(select(Doctor.id, Doctor.name).where(Doctor.id.in_(ids)))
    return {row_id: name for row_id, name in result.all()}


# -- writes -------------------------------------------------------------------


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = " ".join(text.split())
    return stripped[:MAX_SUBSTANCE] or None


async def from_intake(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    visit_id: uuid.UUID | None,
    caregiver: bool,
    none_known: bool,
    substances: list[dict[str, str | None]] | None = None,
) -> list[PatientAllergy]:
    """Record what the patient said at the kiosk. Idempotent per visit.

    Idempotency is not decoration: the offline kiosk re-sends an intake whenever
    the network returns mid-batch (`app.offline.sync_intake` keys on
    `Intake.client_id` for exactly this reason), and a retry that appended a
    second "penicillin" would show the doctor the same allergy twice with two
    different timestamps. So a statement identical to one already on file *for
    this visit* is skipped rather than written.

    A patient who both taps "yes" and names nothing has told us nothing, and
    writes nothing — an empty `known` state would render as an allergy warning
    with no substance in it.
    """
    source = AllergySource.CAREGIVER_KIOSK if caregiver else AllergySource.PATIENT_KIOSK
    items = substances or []
    written: list[PatientAllergy] = []

    existing = await _live_for_visit(session, patient_id=patient_id, visit_id=visit_id)

    for item in items[:MAX_FROM_INTAKE]:
        substance = _clean(item.get("substance"))
        if not substance:
            continue
        if any(
            row.kind is AllergyKind.SUBSTANCE and row.substance == substance for row in existing
        ):
            continue
        row = PatientAllergy(
            patient_id=patient_id,
            visit_id=visit_id,
            kind=AllergyKind.SUBSTANCE,
            substance=substance,
            substance_en=_clean(item.get("substance_en")),
            # Severity stays `unknown` on everything the kiosk writes. The
            # patient named a substance; nobody asked what it did to them, and
            # a default of `mild` would be the system inventing the reassuring
            # half of a fact it does not have.
            severity=AllergySeverity.UNKNOWN,
            source=source,
        )
        session.add(row)
        written.append(row)

    # "None" is only recorded when nothing was named. A patient who taps yes,
    # names one drug and leaves the second box empty has not asserted a
    # negative about everything else.
    if none_known and not written and not items:
        if not any(row.kind is AllergyKind.NONE_KNOWN for row in existing):
            row = PatientAllergy(
                patient_id=patient_id,
                visit_id=visit_id,
                kind=AllergyKind.NONE_KNOWN,
                source=source,
            )
            session.add(row)
            written.append(row)

    if written:
        await session.flush()
    return written


async def _live_for_visit(
    session: AsyncSession, *, patient_id: uuid.UUID, visit_id: uuid.UUID | None
) -> list[PatientAllergy]:
    if visit_id is None:
        return []
    result = await session.execute(
        select(PatientAllergy).where(
            PatientAllergy.patient_id == patient_id,
            PatientAllergy.visit_id == visit_id,
            PatientAllergy.deleted_at.is_(None),
            PatientAllergy.retracted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def record_by_doctor(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    visit_id: uuid.UUID | None,
    doctor: Doctor,
    substance: str | None,
    reaction: str | None = None,
    severity: AllergySeverity = AllergySeverity.UNKNOWN,
    none_known: bool = False,
) -> PatientAllergy:
    """A clinician states an allergy, or states that there are none.

    Both are the same act and the same row, which is what lets a doctor who
    asked and got "nothing" leave that on the record instead of leaving the
    spine saying nobody ever asked. `confirmed_at` is stamped immediately: a
    doctor's own statement does not need a second doctor to stand behind it.
    """
    if none_known:
        kind = AllergyKind.NONE_KNOWN
        cleaned = None
    else:
        cleaned = _clean(substance)
        if not cleaned:
            raise AllergyError("an allergy needs a substance")
        kind = AllergyKind.SUBSTANCE

    now = datetime.now(UTC)
    row = PatientAllergy(
        patient_id=patient_id,
        visit_id=visit_id,
        kind=kind,
        substance=cleaned,
        # A doctor typing at the console types in English; there is no second
        # language to preserve, unlike the kiosk path.
        substance_en=cleaned,
        reaction=_clean(reaction) if kind is AllergyKind.SUBSTANCE else None,
        severity=severity if kind is AllergyKind.SUBSTANCE else AllergySeverity.UNKNOWN,
        source=AllergySource.DOCTOR,
        recorded_by_doctor_id=doctor.id,
        confirmed_by_doctor_id=doctor.id,
        confirmed_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def confirm(
    session: AsyncSession, *, allergy_id: uuid.UUID, patient_id: uuid.UUID, doctor: Doctor
) -> PatientAllergy:
    """A clinician stands behind a statement somebody else made.

    The commonest and most valuable act on this surface: the patient named
    penicillin at a tablet, the doctor asked about it in the room, and the
    record should now show that a clinician has heard it. Re-confirming is a
    no-op rather than an error — two doctors confirming the same allergy is
    agreement, not a conflict, and the first one keeps the credit.
    """
    row = await _load(session, allergy_id=allergy_id, patient_id=patient_id)
    if row.retracted_at is not None:
        raise AllergyError("that statement has been withdrawn")
    if row.confirmed_at is None:
        row.confirmed_by_doctor_id = doctor.id
        row.confirmed_at = datetime.now(UTC)
        await session.flush()
    return row


async def retract(
    session: AsyncSession,
    *,
    allergy_id: uuid.UUID,
    patient_id: uuid.UUID,
    doctor: Doctor,
    reason: str | None = None,
) -> PatientAllergy:
    """Withdraw a statement. The row stays; it stops counting.

    A wrong allergy is the most dangerous stale fact this record can carry — it
    is read at prescribing time and it steers away from the right drug — so this
    is the one correction path built before any of the others. It is a state
    change and never a delete: the History tab keeps showing it struck out, with
    who withdrew it and why, because a record that silently loses a retracted
    penicillin allergy cannot answer the only question a review will ask.
    """
    row = await _load(session, allergy_id=allergy_id, patient_id=patient_id)
    if row.retracted_at is None:
        row.retracted_at = datetime.now(UTC)
        row.retracted_by_doctor_id = doctor.id
        row.retracted_reason = _clean(reason)
        await session.flush()
    return row


async def _load(
    session: AsyncSession, *, allergy_id: uuid.UUID, patient_id: uuid.UUID
) -> PatientAllergy:
    row = await session.get(PatientAllergy, allergy_id)
    if row is None or row.deleted_at is not None:
        raise AllergyError("no such allergy")
    if row.patient_id != patient_id:
        # Not a 404: the id exists, it just belongs to somebody else. Saying so
        # this way keeps a console bug from quietly writing onto the wrong chart.
        raise AllergyError("that allergy belongs to another patient")
    return row
