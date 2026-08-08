"""The imaging surface (plan §2).

    GET /imaging/visits/{visit_id}/studies        what the PACS has
    GET /imaging/visits/{visit_id}/studies/{uid}/report   the radiology report

`require_doctor` on both, scoped by department. Two routes and no third: there
is no series listing, no instance endpoint and no pixel proxy, because the
viewing is done by a viewer somebody else already built and connected to the
same PACS (plan decision 5).

**The browser never talks to Orthanc.** Credentials stay here, and the one
handoff is a popup URL carrying a StudyInstanceUID and nothing else — no token,
no patient identifier. The URL is built server-side (`imaging.viewer_url`) and
handed to the client ready-made, so the console never learns the viewer's shape
and cannot be talked into composing a different one.

Both routes write an audit row (`AuditAction.READ`). The `before_flush` hook
cannot: a study never enters this database, so there is nothing to flush and
nothing to attribute — and "who looked at this patient's scans" is the first
question an access review asks.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import doctor as doctor_svc
from app import imaging as imaging_svc
from app.audit import record_read
from app.auth.rbac import Principal, require_doctor
from app.config import Settings, get_settings
from app.db import get_session
from app.providers.registry import pacs_provider

router = APIRouter(prefix="/imaging", tags=["imaging"])


# -- wire models --------------------------------------------------------------


class StudyOut(BaseModel):
    study_uid: str
    #: Null when the study carried no StudyDate. Rendered as "date not
    #: recorded"; never defaulted, which would sort a decade-old scan to today.
    study_date: date | None = None
    modality: str = ""
    description: str = ""
    series_count: int | None = None
    #: Where the doctor's popup goes. Built server-side so the console never
    #: composes a viewer URL of its own.
    viewer_url: str = ""


class StudiesOut(BaseModel):
    """The list, and why it is the length it is.

    `state` is the point of this payload. A doctor told "no imaging on file"
    when the truth is "we could not reach the PACS" has been told something
    false about their patient, so the four cases stay four cases all the way to
    the screen — see `app.imaging` for what each means.
    """

    state: str
    studies: list[StudyOut] = []
    #: The PACS AE title and port, for the line that helps whoever is debugging
    #: a missing study. Not a secret, and not clinical.
    aet: str = ""


# -- helpers ------------------------------------------------------------------


async def _doctor(session: AsyncSession, principal: Principal):
    try:
        return await doctor_svc.resolve_doctor(session, user_id=principal.id)
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _config(settings: Settings) -> imaging_svc.ImagingConfig:
    return imaging_svc.ImagingConfig(
        enabled=settings.pacs_enabled,
        viewer_url=settings.pacs_viewer_url,
        aet=settings.pacs_aet,
        dicom_port=settings.pacs_dicom_port,
    )


def _fail(exc: imaging_svc.ImagingError) -> HTTPException:
    text = str(exc)
    if "another department" in text:
        return HTTPException(status_code=403, detail=text)
    if "no such visit" in text or "does not belong" in text:
        return HTTPException(status_code=404, detail=text)
    return HTTPException(status_code=400, detail=text)


# -- routes -------------------------------------------------------------------


@router.get("/visits/{visit_id}/studies", response_model=StudiesOut)
async def studies(
    visit_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> StudiesOut:
    """What the PACS has for this patient, or the honest reason it has nothing."""
    doctor = await _doctor(session, principal)
    config = _config(settings)
    try:
        lookup = await imaging_svc.studies_for_visit(
            session,
            visit_id=visit_id,
            doctor=doctor,
            provider=pacs_provider(settings),
            config=config,
        )
    except imaging_svc.ImagingError as exc:
        raise _fail(exc) from exc

    # Audited even when the answer is empty: "this doctor asked what scans this
    # patient has" is the event, and it happened whether or not there were any.
    record_read(
        session,
        entity="pacs_studies",
        entity_id=visit_id,
        meta={"state": lookup.state.value, "count": len(lookup.studies)},
    )
    await session.commit()

    return StudiesOut(
        state=lookup.state.value,
        aet=config.aet,
        studies=[
            StudyOut(
                study_uid=study.study_uid,
                study_date=study.study_date,
                modality=study.modality,
                description=study.description,
                series_count=study.series_count,
                viewer_url=imaging_svc.viewer_url(config, study.study_uid),
            )
            for study in lookup.studies
        ],
    )


@router.get("/visits/{visit_id}/studies/{study_uid}/report")
async def report(
    visit_id: uuid.UUID,
    study_uid: str,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Stream the radiology report, if the study has one.

    404 when it does not, and that is an ordinary answer rather than a fault:
    plenty of studies are acquired before the radiologist has reported them.
    The service re-checks the UID against this patient's own list first — a
    StudyInstanceUID is not a secret, so a route that streamed any UID to any
    authenticated doctor would be an enumeration hole.
    """
    doctor = await _doctor(session, principal)
    try:
        found = await imaging_svc.report_for_study(
            session,
            visit_id=visit_id,
            study_uid=study_uid,
            doctor=doctor,
            provider=pacs_provider(settings),
            config=_config(settings),
        )
    except imaging_svc.ImagingError as exc:
        raise _fail(exc) from exc

    if found is None:
        raise HTTPException(status_code=404, detail="this study has not been reported yet")

    record_read(
        session,
        entity="pacs_report",
        entity_id=visit_id,
        meta={"study_uid": study_uid, "bytes": len(found.content)},
    )
    await session.commit()

    return Response(
        content=found.content,
        media_type=found.media_type,
        # `inline`, not `attachment`: the doctor is reading it, not filing it,
        # and a PDF that lands in Downloads is a patient's report left on a
        # shared laptop. The filename carries no patient identifier either.
        headers={"Content-Disposition": f'inline; filename="{found.filename}"'},
    )
