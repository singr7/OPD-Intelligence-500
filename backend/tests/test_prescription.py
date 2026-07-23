"""Digital prescription (S11, doc 03 §8).

Two properties carry this session, and both are about what does *not* reach the
page:

1. **A schedule is never inferred.** `parse_schedule` may only report what the
   words state. The table below is mostly negative cases, because the failure
   that matters is a confident icon on a prescription that never specified a
   time of day.
2. **A flagged drug stays flagged on paper.** The doctor's acknowledgement
   unlocked signing (S10); it did not make the drug known, and the pharmacist
   reading the sheet never saw the console.

The rest drives generation off the signature, delivery bookkeeping, and history.
No test here calls a vendor: delivery goes through the provider-layer fakes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import dictation as dic
from app import prescription as rx
from app import queue as q
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.clinical import Prescription
from app.models.enums import Channel, Role
from app.providers.llm import FakeLLMProvider, FakeLLMScript

TODAY = q.today()


# =============================================================================
# 1. The schedule is read, never guessed
# =============================================================================


@pytest.mark.parametrize(
    ("freq", "morning", "afternoon", "night"),
    [
        ("1-0-1", True, False, True),
        ("1-1-1", True, True, True),
        ("0-0-1", False, False, True),
        ("1 - 0 - 0", True, False, False),
        # A half tablet is still a dose in that slot.
        ("1/2-0-1/2", True, False, True),
        ("0.5-0-0", True, False, False),
        # Words that name the time of day, in both languages the doctor uses.
        ("morning and night", True, False, True),
        ("subah aur raat", True, False, True),
        ("raat ko", False, False, True),
        ("at bedtime", False, False, True),
        ("dopahar me", False, True, False),
    ],
)
def test_a_stated_schedule_is_read_exactly(
    freq: str, morning: bool, afternoon: bool, night: bool
) -> None:
    schedule = rx.parse_schedule(freq)

    assert schedule is not None
    assert schedule.slots_known is True
    assert (schedule.morning, schedule.afternoon, schedule.night) == (morning, afternoon, night)
    assert schedule.source == freq


@pytest.mark.parametrize(
    ("freq", "per_day"),
    [
        ("OD", 1),
        ("BD", 2),
        ("bd", 2),
        ("TDS", 3),
        ("twice a day", 2),
        ("three times a day", 3),
        ("din me do baar", 2),
        ("din mein teen baar", 3),
    ],
)
def test_a_count_without_a_time_of_day_reports_the_count_and_no_slots(
    freq: str, per_day: int
) -> None:
    """The session's central refusal.

    "BD" is conventionally morning-and-night in Indian practice. Encoding that
    convention would print a sun and a moon for a prescription that said only
    "twice a day" — a time of day no clinician wrote, on a sheet whose whole
    purpose is to be read by someone who cannot read the words beside it.
    """
    schedule = rx.parse_schedule(freq)

    assert schedule is not None
    assert schedule.per_day == per_day
    assert schedule.slots_known is False
    assert (schedule.morning, schedule.afternoon, schedule.night) == (False, False, False)
    assert schedule.doses == per_day


@pytest.mark.parametrize(
    "freq",
    [
        None,
        "",
        "   ",
        "SOS",
        "as needed",
        "zaroorat padne par",
        "alternate days",
        "weekly",
        "once a week",
        "every 6 hours",
        "before chemo",
        "0-0-0",
        "continue same",
    ],
)
def test_an_unreadable_frequency_yields_no_schedule(freq: str | None) -> None:
    """`None` is the safe answer: the page then prints the doctor's words alone.

    Every regimen here is real and none of them is expressible in three
    time-of-day icons. An icon a patient can misread is worse than words they
    have to ask about.
    """
    assert rx.parse_schedule(freq) is None


def test_the_schedule_is_not_re_derived_when_a_stored_prescription_is_read() -> None:
    """A handed-over prescription cannot be re-interpreted by a later code change.

    `lines_of` reads the snapshot; it does not call `parse_schedule` again. If it
    did, tightening the parser would silently change what a patient was told
    they had been given.
    """
    stored = Prescription(
        visit_id=f.new_uuid(),
        dictation_id=f.new_uuid(),
        meds=[
            {
                "name": "Ondansetron",
                "freq": "SOS",  # unreadable today
                "known": True,
                "schedule": {  # but the snapshot says otherwise
                    "morning": True,
                    "afternoon": False,
                    "night": True,
                    "per_day": 2,
                    "slots_known": True,
                    "source": "1-0-1",
                },
            }
        ],
    )

    (line,) = rx.lines_of(stored)

    assert line.schedule is not None
    assert line.schedule.slots_known is True
    assert (line.schedule.morning, line.schedule.night) == (True, True)


# =============================================================================
# 2. A flagged drug stays flagged
# =============================================================================


def _line(**med_kwargs: Any) -> rx.RxLine:
    return rx.RxLine(med=dic.MedLine(**med_kwargs), schedule=None)


def test_an_off_formulary_drug_is_flagged_on_the_page() -> None:
    line = _line(name="Zolfenac", known=False)

    assert line.flagged is True
    assert line.flag_reason is not None
    assert "formulary" in line.flag_reason


def test_a_drug_the_doctor_did_not_say_is_flagged_even_if_the_formulary_knows_it() -> None:
    """The S10 rename hole, carried onto paper.

    A model that turns "Vinblastin" into "vinblastine" produces a real drug that
    the formulary recognises — `known` is true and every other check passes. The
    only signal is that the doctor never said it.
    """
    line = _line(name="Vinblastine", known=True, unsaid=True)

    assert line.flagged is True
    assert "not heard" in (line.flag_reason or "")


def test_acknowledgement_unlocks_signing_but_does_not_clear_the_flag() -> None:
    """S10's `meds_needing_attention` drops an acknowledged drug — that is what
    lets the doctor sign. The page must not drop it: the pharmacist did not see
    the console, and the acknowledgement was about the doctor's intent, not
    about the drug becoming known."""
    line = _line(name="Zolfenac", known=False, acknowledged=True)

    assert line.flagged is True


def test_a_dictated_name_is_never_replaced_by_a_suggestion() -> None:
    """The S10 invariant at its last chance to break: `suggestions` are advice on
    a screen and must not become a printed name."""
    line = _line(
        name="Vinblastin",
        known=False,
        suggestions=({"name": "Vinblastine", "score": 0.94},),
    )

    assert line.to_dict()["name"] == "Vinblastin"
    assert line.flagged is True


# =============================================================================
# 3. Generation hangs off the signature
# =============================================================================


def _mapper(payload: dict[str, Any]) -> dic.DictationMapper:
    provider = FakeLLMProvider(script=[FakeLLMScript(text=json.dumps(payload))])
    return dic.DictationMapper([provider])


_NOTE = {
    "diagnosis": "Ca breast, post cycle 3",
    "meds": [
        {
            "name": "Ondansetron",
            "dose": "8 mg",
            "route": "oral",
            "freq": "1-0-1",
            "duration": "5 days",
            "as_spoken": "ondansetron aath emji subah shaam paanch din",
        }
    ],
    "advice": ["Plenty of fluids"],
    "follow_up": {"when": "2026-08-14", "as_spoken": "chaudah tareekh ko"},
}


async def _signed(session: AsyncSession, payload: dict[str, Any] | None = None):
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()
    dictation = await dic.start(
        session,
        visit_id=visit.id,
        doctor=clinic["doctor"],
        transcript="ondansetron aath emji subah shaam paanch din",
    )
    dictation = await dic.map_transcript(
        session,
        dictation=dictation,
        doctor=clinic["doctor"],
        mapper=_mapper(payload or _NOTE),
    )
    dictation = await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])
    return clinic, visit, dictation


async def test_signing_generates_the_prescription(session: AsyncSession) -> None:
    """doc 03 §7 says signing generates the Rx; S10 deliberately emitted nothing.
    This is that promise being kept."""
    _clinic, visit, dictation = await _signed(session)

    prescription = await rx.for_dictation(session, dictation_id=dictation.id)

    assert prescription is not None
    assert prescription.visit_id == visit.id
    assert len(prescription.meds) == 1
    assert prescription.meds[0]["name"] == "Ondansetron"
    assert prescription.meds[0]["schedule"]["slots_known"] is True


async def test_generation_is_idempotent(session: AsyncSession) -> None:
    """Signing is terminal, so a second call can only be a retry — and a retry
    must not hand the patient two prescriptions for one signature."""
    clinic, _visit, dictation = await _signed(session)

    first = await rx.for_dictation(session, dictation_id=dictation.id)
    again = await rx.generate(session, dictation=dictation, doctor=clinic["doctor"])

    assert first is not None
    assert again is not None
    assert again.id == first.id
    rows = await session.execute(
        select(Prescription).where(Prescription.dictation_id == dictation.id)
    )
    assert len(rows.scalars().all()) == 1


async def test_a_note_with_no_meds_produces_no_prescription(session: AsyncSession) -> None:
    """A consult that ends in advice and a follow-up date is a complete consult.
    An empty prescription is a form, not a document."""
    advice_only = {"diagnosis": "Recovering well", "meds": [], "advice": ["Rest"]}
    _clinic, _visit, dictation = await _signed(session, advice_only)

    assert await rx.for_dictation(session, dictation_id=dictation.id) is None


async def test_the_prescription_is_audited(session: AsyncSession) -> None:
    """`Prescription` subclasses `Clinical`, so the audit is structural — this
    guards the marker staying on the model."""
    from app.models.audit import AuditLog

    _clinic, _visit, dictation = await _signed(session)
    prescription = await rx.for_dictation(session, dictation_id=dictation.id)
    assert prescription is not None

    rows = await session.execute(select(AuditLog).where(AuditLog.entity_id == prescription.id))
    assert rows.scalars().first() is not None


# =============================================================================
# 4. Delivery bookkeeping + history
# =============================================================================


def test_recording_a_delivery_reassigns_rather_than_mutates() -> None:
    """A JSONB column mutated in place is not seen as dirty by SQLAlchemy and the
    write is silently dropped — the bug is invisible until a patient says the
    message never came."""
    prescription = Prescription(visit_id=f.new_uuid(), meds=[], delivered_via={})

    before = prescription.delivered_via
    rx.record_delivery(prescription, channel="whatsapp", status="sent", detail="fake-wa-1")

    assert prescription.delivered_via is not before
    assert prescription.delivered_via["whatsapp"]["status"] == "sent"
    assert prescription.delivered_via["whatsapp"]["detail"] == "fake-wa-1"
    assert "at" in prescription.delivered_via["whatsapp"]


def test_each_channel_keeps_its_own_last_attempt() -> None:
    prescription = Prescription(visit_id=f.new_uuid(), meds=[], delivered_via={})

    rx.record_delivery(prescription, channel="whatsapp", status="failed")
    rx.record_delivery(prescription, channel="print", status="printed")
    rx.record_delivery(prescription, channel="whatsapp", status="sent")

    assert prescription.delivered_via["whatsapp"]["status"] == "sent"
    assert prescription.delivered_via["print"]["status"] == "printed"


async def test_history_lists_the_patients_prescriptions_newest_first(
    session: AsyncSession,
) -> None:
    clinic, visit, _dictation = await _signed(session)

    rows = await rx.history(session, patient_id=clinic["patient"].id)

    assert len(rows) == 1
    prescription, listed_visit = rows[0]
    assert listed_visit.id == visit.id
    assert prescription.meds[0]["name"] == "Ondansetron"


async def test_history_does_not_leak_another_patients_prescription(
    session: AsyncSession,
) -> None:
    clinic, _visit, _dictation = await _signed(session)
    other = f.make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()

    assert await rx.history(session, patient_id=other.id) == []


# =============================================================================
# 5. HTTP: read, print, deliver, history
# =============================================================================


def _headers(settings: Settings, user) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


async def test_routes_require_a_doctor(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, visit, _dictation = await _signed(session)
    assert (await client.get(f"/prescriptions/visits/{visit.id}")).status_code == 401

    coordinator = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(coordinator)
    await session.flush()
    resp = await client.get(
        f"/prescriptions/visits/{visit.id}", headers=_headers(settings, coordinator)
    )
    assert resp.status_code == 403


async def test_reading_the_visits_prescription(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, visit, _dictation = await _signed(session)

    resp = await client.get(
        f"/prescriptions/visits/{visit.id}", headers=_headers(settings, clinic["user"])
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["meds"][0]["name"] == "Ondansetron"
    assert body["meds"][0]["schedule"]["slots_known"] is True


async def test_an_unsigned_visit_has_no_prescription(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()

    resp = await client.get(
        f"/prescriptions/visits/{visit.id}", headers=_headers(settings, clinic["user"])
    )

    assert resp.status_code == 200 and resp.json() is None


async def test_the_clinical_copy_prints_the_letterhead_and_the_dictated_name(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, _visit, dictation = await _signed(session)
    prescription = await rx.for_dictation(session, dictation_id=dictation.id)
    assert prescription is not None

    resp = await client.get(
        f"/prescriptions/{prescription.id}/print?copy=clinical",
        headers=_headers(settings, clinic["user"]),
    )

    assert resp.status_code == 200
    html = resp.text
    assert clinic["hospital"].name in html
    assert clinic["doctor"].reg_no in html  # the signature block
    assert "Ondansetron" in html
    assert "1-0-1" in html  # frequency printed as dictated


async def test_the_patient_copy_draws_pictograms_only_for_a_stated_schedule(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """The AC's low-literacy check, at the HTTP edge.

    One drug states its slots and gets sun/moon; one gives only a count and gets
    tablet glyphs; one is SOS and gets the doctor's words with no icon at all.
    """
    # `acknowledged` because these drugs are not in this test's transcript, so
    # `_was_said` flags them (S10) and an unacknowledged flag blocks signing.
    # The schedule is what is under test here, not the flag.
    note = {
        "diagnosis": "Ca breast",
        "meds": [
            {"name": "Ondansetron", "dose": "8 mg", "freq": "1-0-1", "duration": "5 days"},
            {
                "name": "Pantoprazole",
                "dose": "40 mg",
                "freq": "BD",
                "duration": "7 days",
                "acknowledged": True,
            },
            {
                "name": "Paracetamol",
                "dose": "650 mg",
                "freq": "SOS",
                "duration": "3 days",
                "acknowledged": True,
            },
        ],
    }
    clinic, _visit, dictation = await _signed(session, note)
    prescription = await rx.for_dictation(session, dictation_id=dictation.id)
    assert prescription is not None

    resp = await client.get(
        f"/prescriptions/{prescription.id}/print?copy=patient&lang=hi",
        headers=_headers(settings, clinic["user"]),
    )

    assert resp.status_code == 200
    html = resp.text
    # Slots stated -> the sun and moon are lit.
    assert "slot on" in html
    assert "सुबह" in html and "रात" in html
    # Count only -> tablet glyphs, and no invented time of day for that band.
    assert "⬤⬤" in html
    # Unreadable -> the doctor's own word, no icon.
    assert "SOS" in html


async def test_a_flagged_drug_is_visibly_flagged_on_both_copies(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """The pharmacist and the patient both learn that this one was acknowledged,
    not confirmed."""
    note = {
        "meds": [
            {"name": "Zolfenac", "dose": "50 mg", "freq": "1-0-1", "acknowledged": True},
        ]
    }
    clinic, _visit, dictation = await _signed(session, note)
    prescription = await rx.for_dictation(session, dictation_id=dictation.id)
    assert prescription is not None
    headers = _headers(settings, clinic["user"])

    clinical = await client.get(
        f"/prescriptions/{prescription.id}/print?copy=clinical", headers=headers
    )
    patient = await client.get(
        f"/prescriptions/{prescription.id}/print?copy=patient", headers=headers
    )

    assert "flagged" in clinical.text and "formulary" in clinical.text
    assert "flagged" in patient.text
    # And the name is the dictated one on both.
    assert "Zolfenac" in clinical.text and "Zolfenac" in patient.text


async def test_delivery_over_whatsapp_is_recorded(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, _visit, dictation = await _signed(session)
    prescription = await rx.for_dictation(session, dictation_id=dictation.id)
    assert prescription is not None

    resp = await client.post(
        f"/prescriptions/{prescription.id}/deliver",
        json={"channel": "whatsapp"},
        headers=_headers(settings, clinic["user"]),
    )

    assert resp.status_code == 200
    assert resp.json()["delivered_via"]["whatsapp"]["status"] == "sent"


async def test_a_failed_send_is_recorded_rather_than_raised(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """The paper copy is the delivery that actually happened. A vendor outage
    must show at the desk as "not sent", not as a 500 that loses the record."""
    from app.providers.base import ProviderUnavailable
    from app.providers.messaging import FakeMessagingProvider

    clinic, _visit, dictation = await _signed(session)
    prescription = await rx.for_dictation(session, dictation_id=dictation.id)
    assert prescription is not None

    broken = FakeMessagingProvider()
    broken.fail_with = ProviderUnavailable("whatsapp is down")
    monkeypatch.setattr(
        "app.routes.prescription.get_messaging_provider", lambda settings=None: broken
    )

    resp = await client.post(
        f"/prescriptions/{prescription.id}/deliver",
        json={"channel": "whatsapp"},
        headers=_headers(settings, clinic["user"]),
    )

    assert resp.status_code == 200
    assert resp.json()["delivered_via"]["whatsapp"]["status"] == "failed"


async def test_delivery_to_a_missing_number_is_refused(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, _visit, dictation = await _signed(session)
    prescription = await rx.for_dictation(session, dictation_id=dictation.id)
    assert prescription is not None
    clinic["patient"].caregiver_phone = None
    await session.flush()

    resp = await client.post(
        f"/prescriptions/{prescription.id}/deliver",
        json={"channel": "sms", "to_caregiver": True},
        headers=_headers(settings, clinic["user"]),
    )

    assert resp.status_code == 422


async def test_history_over_http(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, visit, _dictation = await _signed(session)

    resp = await client.get(
        f"/prescriptions/patients/{clinic['patient'].id}",
        headers=_headers(settings, clinic["user"]),
    )

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["visit_id"] == str(visit.id)
    assert rows[0]["med_names"] == ["Ondansetron"]
