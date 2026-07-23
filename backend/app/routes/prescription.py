"""The prescription surface (doc 03 §8).

`require_doctor`, like the dictation routes and for the same reason: this is the
patient's medication list, and the queue coordinator has no business in it. The
verbs are narrow because the record is created by *signing*, not by a client —
there is no `POST /prescriptions`, and there must never be one.

    GET  /prescriptions/visits/{visit_id}      the visit's prescription
    GET  /prescriptions/patients/{patient_id}  Rx history on the patient file
    GET  /prescriptions/{id}/print             print-ready HTML (clinical|patient)
    POST /prescriptions/{id}/deliver           WhatsApp / SMS, via the providers

`/print` returns **HTML the browser prints**, the same stance as S8's downtime
sheets — see `app.rx_sheets` for why that is not a shortcut.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import dictation as dictation_svc
from app import doctor as doctor_svc
from app import prescription as rx_svc
from app import rx_sheets
from app.auth.rbac import Principal, require_doctor
from app.config import Settings, get_settings
from app.db import get_session
from app.models.clinical import Dictation, Prescription, Visit
from app.models.enums import Lang, UsagePurpose
from app.models.org import Department, Doctor, Hospital
from app.models.patient import Patient
from app.providers.base import ProviderError
from app.providers.messaging import OutboundMessage
from app.providers.metering import usage_scope
from app.providers.registry import get_messaging_provider, get_sms_provider
from app.providers.sms import SmsMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prescriptions", tags=["prescriptions"])


# -- wire models --------------------------------------------------------------


class RxMedOut(BaseModel):
    #: Exactly what the doctor dictated. Never a formulary name (doc 03 §7/§8).
    name: str
    dose: str | None = None
    route: str | None = None
    freq: str | None = None
    duration: str | None = None
    known: bool = False
    #: The page shows this; acknowledgement unlocked signing, it did not clear it.
    flagged: bool = False
    flag_reason: str | None = None
    schedule: dict[str, Any] | None = None


class PrescriptionOut(BaseModel):
    id: uuid.UUID
    visit_id: uuid.UUID
    dictation_id: uuid.UUID | None = None
    meds: list[RxMedOut]
    delivered_via: dict[str, Any] = {}


class HistoryRowOut(BaseModel):
    prescription_id: uuid.UUID
    visit_id: uuid.UUID
    date: str
    med_names: list[str]
    flagged_count: int


class DeliverIn(BaseModel):
    channel: Literal["whatsapp", "sms"]
    #: Send to the caregiver's number instead of the patient's own.
    to_caregiver: bool = False


def _out(prescription: Prescription) -> PrescriptionOut:
    return PrescriptionOut(
        id=prescription.id,
        visit_id=prescription.visit_id,
        dictation_id=prescription.dictation_id,
        meds=[
            RxMedOut(
                name=line.med.name,
                dose=line.med.dose,
                route=line.med.route,
                freq=line.med.freq,
                duration=line.med.duration,
                known=line.med.known,
                flagged=line.flagged,
                flag_reason=line.flag_reason,
                schedule=line.to_dict()["schedule"],
            )
            for line in rx_svc.lines_of(prescription)
        ],
        delivered_via=prescription.delivered_via or {},
    )


# -- helpers ------------------------------------------------------------------


async def _doctor(session: AsyncSession, principal: Principal) -> Doctor:
    try:
        return await doctor_svc.resolve_doctor(session, user_id=principal.id)
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _load(session: AsyncSession, prescription_id: uuid.UUID, doctor: Doctor) -> Prescription:
    prescription = await session.get(Prescription, prescription_id)
    if prescription is None or prescription.deleted_at is not None:
        raise HTTPException(status_code=404, detail="no such prescription")
    # Same scoping as the S9 card and the S10 note: the department, not the
    # individual, so a colleague covering the room can reprint.
    try:
        await dictation_svc.assert_visit_scope(
            session, visit_id=prescription.visit_id, doctor=doctor
        )
    except dictation_svc.DictationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return prescription


class _Context:
    """Everything the sheets need, loaded once."""

    def __init__(
        self,
        *,
        visit: Visit,
        patient: Patient,
        department: Department,
        hospital: Hospital,
        signer: Doctor,
        mapping_extras: dict[str, Any],
    ) -> None:
        self.visit = visit
        self.patient = patient
        self.department = department
        self.hospital = hospital
        self.signer = signer
        self.extras = mapping_extras


async def _context(session: AsyncSession, prescription: Prescription, fallback: Doctor) -> _Context:
    visit = await session.get(Visit, prescription.visit_id)
    if visit is None:
        raise HTTPException(status_code=404, detail="no such visit")
    patient = await session.get(Patient, visit.patient_id)
    department = await session.get(Department, visit.department_id)
    if patient is None or department is None:
        raise HTTPException(status_code=404, detail="visit is missing patient or department")
    hospital = await session.get(Hospital, department.hospital_id)
    if hospital is None:
        raise HTTPException(status_code=404, detail="department is missing its hospital")

    # The signature on the page is whoever signed the note, not whoever is
    # printing it — a covering colleague reprinting does not become the
    # prescriber.
    signer = fallback
    extras: dict[str, Any] = {}
    if prescription.dictation_id is not None:
        dictation = await session.get(Dictation, prescription.dictation_id)
        if dictation is not None:
            if dictation.signed_by is not None:
                signed_by = await session.get(Doctor, dictation.signed_by)
                if signed_by is not None:
                    signer = signed_by
            mapping = dictation_svc.current_mapping(dictation)
            if mapping is not None:
                extras = {
                    "diagnosis": mapping.diagnosis,
                    "advice": mapping.advice,
                    "follow_up": mapping.follow_up.when or mapping.follow_up.instructions or None,
                }
    return _Context(
        visit=visit,
        patient=patient,
        department=department,
        hospital=hospital,
        signer=signer,
        mapping_extras=extras,
    )


# -- routes -------------------------------------------------------------------


@router.get("/visits/{visit_id}", response_model=PrescriptionOut | None)
async def read(
    visit_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> PrescriptionOut | None:
    """This visit's prescription, or null if the note is unsigned or med-free."""
    doctor = await _doctor(session, principal)
    try:
        await dictation_svc.assert_visit_scope(session, visit_id=visit_id, doctor=doctor)
    except dictation_svc.DictationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    prescription = await rx_svc.for_visit(session, visit_id=visit_id)
    return _out(prescription) if prescription else None


@router.get("/patients/{patient_id}", response_model=list[HistoryRowOut])
async def history(
    patient_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> list[HistoryRowOut]:
    """Rx history on the patient file (doc 03 §8).

    Scoped by the doctor's own hospital rather than the department: a patient's
    medication history crosses departments by nature, and that is the point of
    having it on the file.
    """
    doctor = await _doctor(session, principal)
    patient = await session.get(Patient, patient_id)
    if patient is None or patient.deleted_at is not None:
        raise HTTPException(status_code=404, detail="no such patient")
    department = await session.get(Department, doctor.department_id)
    if department is None or patient.hospital_id != department.hospital_id:
        raise HTTPException(status_code=403, detail="this patient is not in your hospital")

    rows = await rx_svc.history(session, patient_id=patient_id)
    return [
        HistoryRowOut(
            prescription_id=prescription.id,
            visit_id=visit.id,
            date=visit.date.isoformat(),
            med_names=[line.med.name for line in rx_svc.lines_of(prescription)],
            flagged_count=sum(1 for line in rx_svc.lines_of(prescription) if line.flagged),
        )
        for prescription, visit in rows
    ]


@router.get("/{prescription_id}/print", response_class=HTMLResponse)
async def print_sheet(
    prescription_id: uuid.UUID,
    copy: Literal["clinical", "patient"] = Query("clinical"),
    lang: Lang | None = Query(None),
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Print-ready HTML for one copy (doc 03 §8).

    The patient copy defaults to the **patient's** language, not the caller's —
    the sheet goes home with them.
    """
    doctor = await _doctor(session, principal)
    prescription = await _load(session, prescription_id, doctor)
    ctx = await _context(session, prescription, doctor)
    lines = rx_svc.lines_of(prescription)

    if copy == "patient":
        html = rx_sheets.render_patient_copy(
            lines=lines,
            lang=lang or ctx.patient.lang,
            hospital=ctx.hospital.name,
            department=ctx.department.name,
            patient_name=ctx.patient.name,
            visit_date=ctx.visit.date,
            token_no=ctx.visit.token_no,
            advice=tuple(ctx.extras.get("advice") or ()),
            follow_up=ctx.extras.get("follow_up"),
        )
    else:
        html = rx_sheets.render_clinical_copy(
            lines=lines,
            hospital=ctx.hospital.name,
            department=ctx.department.name,
            doctor_name=ctx.signer.name,
            doctor_reg_no=ctx.signer.reg_no,
            doctor_qualification=ctx.signer.qualification,
            patient_name=ctx.patient.name,
            patient_mrn=ctx.patient.mrn,
            patient_age=ctx.patient.age,
            patient_sex=str(ctx.patient.sex) if ctx.patient.sex else None,
            visit_date=ctx.visit.date,
            token_no=ctx.visit.token_no,
            diagnosis=ctx.extras.get("diagnosis"),
            advice=tuple(ctx.extras.get("advice") or ()),
            follow_up=ctx.extras.get("follow_up"),
        )
    rx_svc.record_delivery(prescription, channel="print", status="rendered", detail=copy)
    return HTMLResponse(content=html)


@router.post("/{prescription_id}/deliver", response_model=PrescriptionOut)
async def deliver(
    prescription_id: uuid.UUID,
    body: DeliverIn,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PrescriptionOut:
    """Send the prescription to the patient over WhatsApp or SMS.

    Both go through the provider layer, so the pilot runs on fakes and a real
    vendor is a config change (doc 02 §9). A failed send is **recorded, not
    raised past a 502**: the paper copy is the delivery that actually happened,
    and the desk needs to see that the message did not.
    """
    doctor = await _doctor(session, principal)
    prescription = await _load(session, prescription_id, doctor)
    ctx = await _context(session, prescription, doctor)
    lines = rx_svc.lines_of(prescription)

    to = ctx.patient.caregiver_phone if body.to_caregiver else ctx.patient.phone
    if not to:
        raise HTTPException(status_code=422, detail="no phone number on file for that recipient")

    text = rx_sheets.sms_body(lines=lines, hospital=ctx.hospital.name, lang=ctx.patient.lang)
    with usage_scope(channel=ctx.visit.channel, visit_id=ctx.visit.id):
        try:
            if body.channel == "whatsapp":
                provider = get_messaging_provider(settings)
                result = await provider.send(
                    OutboundMessage(to=to, text=text), purpose=UsagePurpose.OTHER
                )
                detail = result.message_id
            else:
                provider = get_sms_provider(settings)
                sms = await provider.send(SmsMessage(to=to, body=text), purpose=UsagePurpose.OTHER)
                detail = sms.message_id
        except ProviderError as exc:
            rx_svc.record_delivery(
                prescription, channel=body.channel, status="failed", detail=str(exc)
            )
            await session.flush()
            logger.warning(
                "prescription %s delivery over %s failed: %s", prescription.id, body.channel, exc
            )
            return _out(prescription)

    rx_svc.record_delivery(prescription, channel=body.channel, status="sent", detail=detail)
    await session.flush()
    return _out(prescription)
