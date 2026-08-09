"""Admin console + analytics HTTP surface (doc 03 §10/§11, S18).

Every route here is `require_admin` — this is the one surface that edits live
content and exposes whole-hospital cost. Two shapes worth knowing:

- **Money is a string on the wire.** Costs are `Decimal` end to end (they sum
  into an invoice reconciliation view); serialising them as JSON numbers would
  reintroduce the float this system spent effort to avoid. Every ₹ field is typed
  `str` and carries an exact decimal.
- **The analytics reads are thin over `app.analytics`; the editor writes are thin
  over `app.admin`.** This file parses query params, guards the role, and shapes
  the response — no business logic, so the services stay unit-testable.

S-GL.2 added the last two panels doc 03 §10 asked for and nothing had built —
**people** (staff onboarding) and the **roster** (slot templates, and importing
them from a spreadsheet). With those, no route on this router is a deferral
marker any more.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import admin as admin_svc
from app import analytics, facility, people, roster
from app import channels as channel_svc
from app.auth.rbac import Principal, require_admin
from app.checkins import store as checkin_store
from app.config import Settings, get_settings
from app.db import get_session
from app.models.enums import (
    CareSystem,
    Channel,
    IntakeTier,
    Lang,
    PriceUnit,
    Role,
    SlotType,
    UsagePurpose,
)
from app.models.org import Department
from app.providers import runtime
from app.providers.costguard import CostGuard, get_guard
from app.whatsapp import templates as wa_templates

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# -- shared helpers -----------------------------------------------------------


def _default_window() -> tuple[datetime, datetime]:
    """The last 7 days, the dashboard's default range (doc 03 §11 'week range')."""
    now = datetime.now(UTC)
    return now - timedelta(days=7), now


def _filters(
    channel: Channel | None,
    tier: IntakeTier | None,
    purpose: UsagePurpose | None,
    model: str | None,
    provider: str | None,
) -> analytics.Filters:
    return analytics.Filters(
        channel=channel, tier=tier, purpose=purpose, model=model, provider=provider
    )


# -- analytics: live strip ----------------------------------------------------


class LiveOut(BaseModel):
    tokens_per_min: int
    inr_per_min: str
    active_sessions_by_tier: dict[str, int]
    at: datetime


@router.get("/analytics/live", response_model=LiveOut)
async def analytics_live(session: AsyncSession = Depends(get_session)) -> LiveOut:
    strip = await analytics.live_strip(session)
    return LiveOut(
        tokens_per_min=strip.tokens_per_min,
        inr_per_min=str(strip.inr_per_min),
        active_sessions_by_tier=strip.active_sessions_by_tier,
        at=strip.at,
    )


# -- analytics: time series ---------------------------------------------------


class SeriesPointOut(BaseModel):
    at: datetime
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    audio_seconds: str
    cost_inr: str


class SeriesOut(BaseModel):
    start: datetime
    end: datetime
    granularity: str
    points: list[SeriesPointOut]


@router.get("/analytics/series", response_model=SeriesOut)
async def analytics_series(
    start: datetime | None = None,
    end: datetime | None = None,
    granularity: analytics.Granularity = analytics.Granularity.MINUTE,
    channel: Channel | None = None,
    tier: IntakeTier | None = None,
    purpose: UsagePurpose | None = None,
    model: str | None = None,
    provider: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> SeriesOut:
    lo, hi = _default_window()
    start, end = start or lo, end or hi
    points = await analytics.time_series(
        session,
        start=start,
        end=end,
        granularity=granularity,
        filters=_filters(channel, tier, purpose, model, provider),
    )
    return SeriesOut(
        start=start,
        end=end,
        granularity=granularity.value,
        points=[
            SeriesPointOut(
                at=p.at,
                tokens_in=p.tokens_in,
                tokens_out=p.tokens_out,
                cached_tokens=p.cached_tokens,
                audio_seconds=str(p.audio_seconds),
                cost_inr=str(p.cost_inr),
            )
            for p in points
        ],
    )


# -- analytics: breakdown -----------------------------------------------------


class BreakdownRowOut(BaseModel):
    provider: str
    model: str | None
    purpose: str
    tokens_in: int
    tokens_out: int
    audio_seconds: str
    calls: int
    cost_inr: str
    pct_of_spend: float


@router.get("/analytics/breakdown", response_model=list[BreakdownRowOut])
async def analytics_breakdown(
    start: datetime | None = None,
    end: datetime | None = None,
    channel: Channel | None = None,
    tier: IntakeTier | None = None,
    purpose: UsagePurpose | None = None,
    model: str | None = None,
    provider: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[BreakdownRowOut]:
    lo, hi = _default_window()
    rows = await analytics.breakdown(
        session,
        start=start or lo,
        end=end or hi,
        filters=_filters(channel, tier, purpose, model, provider),
    )
    return [
        BreakdownRowOut(
            provider=r.provider,
            model=r.model,
            purpose=str(r.purpose),
            tokens_in=r.tokens_in,
            tokens_out=r.tokens_out,
            audio_seconds=str(r.audio_seconds),
            calls=r.calls,
            cost_inr=str(r.cost_inr),
            pct_of_spend=round(r.pct_of_spend, 2),
        )
        for r in rows
    ]


# -- analytics: unit economics ------------------------------------------------


class UnitCostOut(BaseModel):
    channel: str | None
    tier: str | None
    count: int
    median_inr: str | None
    p90_inr: str | None


class UnitEconomicsOut(BaseModel):
    per_completed_intake: list[UnitCostOut]
    per_abandoned_intake: UnitCostOut
    per_dictation: UnitCostOut
    overall_per_intake: UnitCostOut


def _unit_out(u: analytics.UnitCost) -> UnitCostOut:
    return UnitCostOut(
        channel=u.channel.value if u.channel else None,
        tier=u.tier.value if u.tier else None,
        count=u.count,
        median_inr=str(u.median_inr) if u.median_inr is not None else None,
        p90_inr=str(u.p90_inr) if u.p90_inr is not None else None,
    )


@router.get("/analytics/unit-economics", response_model=UnitEconomicsOut)
async def analytics_unit_economics(
    start: datetime | None = None,
    end: datetime | None = None,
    session: AsyncSession = Depends(get_session),
) -> UnitEconomicsOut:
    lo, hi = _default_window()
    ue = await analytics.unit_economics(session, start=start or lo, end=end or hi)
    return UnitEconomicsOut(
        per_completed_intake=[_unit_out(u) for u in ue.per_completed_intake],
        per_abandoned_intake=_unit_out(ue.per_abandoned_intake),
        per_dictation=_unit_out(ue.per_dictation),
        overall_per_intake=_unit_out(ue.overall_per_intake),
    )


# -- analytics: what-if -------------------------------------------------------


class PriceOverrideIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    factor: str = "1"


class WhatIfIn(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    overrides: list[PriceOverrideIn] = Field(default_factory=list)


class WhatIfOut(BaseModel):
    baseline_inr: str
    adjusted_inr: str
    delta_inr: str


@router.post("/analytics/whatif", response_model=WhatIfOut)
async def analytics_whatif(
    body: WhatIfIn, session: AsyncSession = Depends(get_session)
) -> WhatIfOut:
    lo, hi = _default_window()
    try:
        overrides = [
            analytics.PriceOverride(provider=o.provider, model=o.model, factor=Decimal(o.factor))
            for o in body.overrides
        ]
    except InvalidOperation as exc:
        raise HTTPException(status_code=422, detail="factor must be a decimal") from exc
    result = await analytics.what_if(
        session, start=body.start or lo, end=body.end or hi, overrides=overrides
    )
    return WhatIfOut(
        baseline_inr=str(result.baseline_inr),
        adjusted_inr=str(result.adjusted_inr),
        delta_inr=str(result.delta_inr),
    )


class TierMixOut(BaseModel):
    channel: str
    from_tier: str
    to_tier: str
    intakes: int
    from_median_inr: str | None
    to_median_inr: str | None
    baseline_inr: str
    adjusted_inr: str
    delta_inr: str
    basis: str


@router.get("/analytics/tier-mix", response_model=TierMixOut)
async def analytics_tier_mix(
    channel: Channel,
    from_tier: IntakeTier,
    to_tier: IntakeTier,
    start: datetime | None = None,
    end: datetime | None = None,
    session: AsyncSession = Depends(get_session),
) -> TierMixOut:
    """ "If this channel had run that tier" — doc 03 §11's tier-mix recompute."""
    lo, hi = _default_window()
    mix = await analytics.tier_mix(
        session,
        start=start or lo,
        end=end or hi,
        channel=channel,
        from_tier=from_tier,
        to_tier=to_tier,
    )
    return TierMixOut(
        channel=mix.channel.value,
        from_tier=mix.from_tier.value,
        to_tier=mix.to_tier.value,
        intakes=mix.intakes,
        from_median_inr=str(mix.from_median_inr) if mix.from_median_inr is not None else None,
        to_median_inr=str(mix.to_median_inr) if mix.to_median_inr is not None else None,
        baseline_inr=str(mix.baseline_inr),
        adjusted_inr=str(mix.adjusted_inr),
        delta_inr=str(mix.delta_inr),
        basis=mix.basis,
    )


# -- analytics: anomalies -----------------------------------------------------


class AnomalyOut(BaseModel):
    kind: str
    detail: str
    value: str


@router.get("/analytics/anomalies", response_model=list[AnomalyOut])
async def analytics_anomalies(
    session: AsyncSession = Depends(get_session),
) -> list[AnomalyOut]:
    flags = await analytics.anomalies(session)
    return [AnomalyOut(kind=a.kind, detail=a.detail, value=str(a.value)) for a in flags]


# -- analytics: ops (Tab 2) ---------------------------------------------------


class FunnelRowOut(BaseModel):
    channel: str
    started: int
    completed: int
    confirmed: int
    median_duration_s: float | None


class OpsOut(BaseModel):
    funnel: list[FunnelRowOut]
    tier_downgrades: int
    intakes_by_lang: dict[str, int]


@router.get("/analytics/ops", response_model=OpsOut)
async def analytics_ops(
    start: datetime | None = None,
    end: datetime | None = None,
    session: AsyncSession = Depends(get_session),
) -> OpsOut:
    lo, hi = _default_window()
    ops = await analytics.ops_metrics(session, start=start or lo, end=end or hi)
    return OpsOut(
        funnel=[
            FunnelRowOut(
                channel=f.channel.value,
                started=f.started,
                completed=f.completed,
                confirmed=f.confirmed,
                median_duration_s=f.median_duration_s,
            )
            for f in ops.funnel
        ],
        tier_downgrades=ops.tier_downgrades,
        intakes_by_lang=ops.intakes_by_lang,
    )


# -- analytics: ambient note tags (M4) ----------------------------------------


class TagCountOut(BaseModel):
    label: str
    notes: int


class SymptomCountOut(BaseModel):
    label: str
    notes: int
    #: Notes where the doctor **said** a grade. Not "notes where it was graded" —
    #: nothing in this system grades a symptom.
    with_grade: int


class NoteTagsOut(BaseModel):
    notes_counted: int
    drafts_excluded: int
    problems: list[TagCountOut]
    symptoms: list[SymptomCountOut]
    followups: list[TagCountOut]
    #: Carried on the payload rather than left to each client to remember. These
    #: counts come from tags a model suggested and a doctor accepted, and every
    #: surface that renders them has to say so (plan §3.2).
    basis: str = (
        "Model-assisted: tags are suggested by a model and accepted by the doctor "
        "at confirm time. Confirmed notes only."
    )


@router.get("/analytics/note-tags", response_model=NoteTagsOut)
async def analytics_note_tags(
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 10,
    session: AsyncSession = Depends(get_session),
) -> NoteTagsOut:
    """What the clinic's ambient notes were about (M4, plan §3.2).

    The proof that mapping a spoken observation into a small shape buys something
    a transcript does not: symptom burden, follow-up debt and problem prevalence
    over a period, from notes a doctor confirmed.
    """
    lo, hi = _default_window()
    tags = await analytics.note_tags(
        session, start=start or lo, end=end or hi, limit=max(1, min(limit, 50))
    )
    return NoteTagsOut(
        notes_counted=tags.notes_counted,
        drafts_excluded=tags.drafts_excluded,
        problems=[TagCountOut(label=t.label, notes=t.notes) for t in tags.problems],
        symptoms=[
            SymptomCountOut(label=s.label, notes=s.notes, with_grade=s.with_grade)
            for s in tags.symptoms
        ],
        followups=[TagCountOut(label=t.label, notes=t.notes) for t in tags.followups],
    )


# -- editor: trees ------------------------------------------------------------


class TreeVersionOut(BaseModel):
    id: uuid.UUID
    key: str
    version: int
    status: str
    department_code: str | None
    published_at: datetime | None
    node_count: int


def _tree_out(v: admin_svc.TreeVersion) -> TreeVersionOut:
    return TreeVersionOut(
        id=v.id,
        key=v.key,
        version=v.version,
        status=v.status,
        department_code=v.department_code,
        published_at=v.published_at,
        node_count=v.node_count,
    )


@router.get("/trees", response_model=list[TreeVersionOut])
async def list_trees(session: AsyncSession = Depends(get_session)) -> list[TreeVersionOut]:
    return [_tree_out(v) for v in await admin_svc.list_trees(session)]


@router.get("/trees/{key}")
async def get_tree(
    key: str,
    version: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await admin_svc.get_tree(session, key, version)
    except admin_svc.AdminError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class SaveTreeIn(BaseModel):
    tree: dict


@router.post("/trees/{key}/draft", response_model=TreeVersionOut)
async def save_tree_draft(
    key: str, body: SaveTreeIn, session: AsyncSession = Depends(get_session)
) -> TreeVersionOut:
    try:
        version = await admin_svc.save_tree_draft(session, key=key, tree_json=body.tree)
    except admin_svc.AdminError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _tree_out(version)


@router.post("/trees/{key}/publish", response_model=TreeVersionOut)
async def publish_tree(
    key: str,
    version: int = Query(...),
    session: AsyncSession = Depends(get_session),
) -> TreeVersionOut:
    try:
        published = await admin_svc.publish_tree(session, key=key, version=version)
    except admin_svc.AdminError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _tree_out(published)


class TestRunIn(BaseModel):
    tree: dict
    answers: dict


class TestRunOut(BaseModel):
    path: list[str]
    complete: bool
    red_flags: list[dict]
    error: str | None = None


@router.post("/trees/test-run", response_model=TestRunOut)
async def test_run(body: TestRunIn) -> TestRunOut:
    result = admin_svc.test_run(body.tree, body.answers)
    return TestRunOut(
        path=result.path,
        complete=result.complete,
        red_flags=result.red_flags,
        error=result.error,
    )


# -- editor: price book -------------------------------------------------------


class PriceRowOut(BaseModel):
    id: uuid.UUID
    provider: str
    model: str
    unit: str
    price_inr: str
    effective_from: date
    notes: str | None


@router.get("/price-book", response_model=list[PriceRowOut])
async def price_book(session: AsyncSession = Depends(get_session)) -> list[PriceRowOut]:
    rows = await analytics.price_rows(session)
    return [
        PriceRowOut(
            id=r.id,
            provider=r.provider,
            model=r.model,
            unit=r.unit,
            price_inr=str(r.price_inr),
            effective_from=r.effective_from,
            notes=r.notes,
        )
        for r in rows
    ]


class PriceRowIn(BaseModel):
    provider: str
    model: str
    unit: PriceUnit
    price_inr: str
    effective_from: date
    notes: str | None = None


@router.post("/price-book", response_model=PriceRowOut)
async def add_price_row(
    body: PriceRowIn, session: AsyncSession = Depends(get_session)
) -> PriceRowOut:
    try:
        price = Decimal(body.price_inr)
    except InvalidOperation as exc:
        raise HTTPException(status_code=422, detail="price_inr must be a decimal") from exc
    try:
        row = await admin_svc.add_price_row(
            session,
            admin_svc.PriceEdit(
                provider=body.provider,
                model=body.model,
                unit=body.unit,
                price_inr=price,
                effective_from=body.effective_from,
                notes=body.notes,
            ),
        )
    except admin_svc.AdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PriceRowOut(
        id=row.id,
        provider=row.provider,
        model=row.model,
        unit=row.unit.value,
        price_inr=str(row.price_inr),
        effective_from=row.effective_from,
        notes=row.notes,
    )


# -- cost guard ---------------------------------------------------------------


class CostGuardChannelOut(BaseModel):
    channel: str
    spent_inr: str
    budget_inr: str | None
    fraction: float | None
    override_tier: str | None
    status: str  # ok | approaching | breached | uncapped


class CostGuardOut(BaseModel):
    enabled: bool
    channels: list[CostGuardChannelOut]


@router.get("/costguard", response_model=CostGuardOut)
async def cost_guard_status(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CostGuardOut:
    """Today's spend vs budget per channel, plus any forced tier (doc 03 §11 strip).

    Spend is read against the request session so the number matches the rest of
    the dashboard; the current override comes from the live guard's store when the
    process has one (it does in production; not under the test transport).
    """
    guard = get_guard()
    # A transient guard just to reuse day_start + spend_today against this session;
    # the real guard (if any) owns the override store we read below.
    view = CostGuard(
        session_factory=None,  # type: ignore[arg-type]  # unused: we call spend_today directly
        store=None,  # type: ignore[arg-type]
        budgets=settings.daily_budget_inr,
        alert_fraction=settings.cost_guard_alert_fraction,
        enabled=settings.cost_guard_enabled,
    )
    channels: list[CostGuardChannelOut] = []
    for channel in Channel:
        budget = settings.daily_budget_inr.get(channel.value)
        spent = await view.spend_today(session, channel)
        override = await guard._store.get(channel) if guard else None  # noqa: SLF001
        if budget is None or Decimal(budget) <= 0 or not settings.cost_guard_enabled:
            status = "uncapped"
            fraction = None
        else:
            fraction = float(spent / Decimal(budget))
            if fraction >= 1:
                status = "breached"
            elif fraction >= settings.cost_guard_alert_fraction:
                status = "approaching"
            else:
                status = "ok"
        channels.append(
            CostGuardChannelOut(
                channel=channel.value,
                spent_inr=str(spent),
                budget_inr=str(budget) if budget is not None else None,
                fraction=round(fraction, 4) if fraction is not None else None,
                override_tier=override.value if override else None,
                status=status,
            )
        )
    return CostGuardOut(enabled=settings.cost_guard_enabled, channels=channels)


@router.post("/costguard/{channel}/clear")
async def cost_guard_clear(channel: Channel, principal: Principal = Depends(require_admin)) -> dict:
    """Resume normal service on a channel (doc 02 §8 admin 'clear').

    Clears the forced tier so sessions run at their configured ceiling again.
    Needs the live guard's override store, which only exists in a running process
    — under the test transport there is none and this reports 503.
    """
    guard = get_guard()
    if guard is None:
        raise HTTPException(status_code=503, detail="cost guard is not running in this process")
    await guard.clear(channel)
    return {"channel": channel.value, "cleared": True}


# -- message templates (read-only registry) -----------------------------------


class TemplateOut(BaseModel):
    name: str
    lang: str
    category: str
    body: str
    variables: list[str]


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates() -> list[TemplateOut]:
    """The WhatsApp template registry (doc 03 §10), read-only.

    Code-defined (a Meta submission has to match the repo), so the console shows
    completeness across the four languages rather than editing it; a DB-backed
    editable registry is S18-late/S15 work.
    """
    return [
        TemplateOut(
            name=t.name,
            lang=t.lang.value,
            category=t.category,
            body=t.body,
            variables=list(t.variables),
        )
        for t in wa_templates.all_templates()
    ]


# -- voice-pack coverage ------------------------------------------------------


class VoicePackClipOut(BaseModel):
    tree_key: str
    node_id: str
    lang: str
    clip_name: str | None
    recorded: bool


@router.get("/voice-packs", response_model=list[VoicePackClipOut])
async def voice_packs() -> list[VoicePackClipOut]:
    return [
        VoicePackClipOut(
            tree_key=c.tree_key,
            node_id=c.node_id,
            lang=c.lang,
            clip_name=c.clip_name,
            recorded=c.recorded,
        )
        for c in admin_svc.voice_pack_coverage()
    ]


# -- deferred panels (S15 / S17) ----------------------------------------------


@router.get("/protocol-templates")
async def protocol_templates(session: AsyncSession = Depends(get_session)) -> dict:
    """The **live** protocol bank, rendered for reading (doc 03 §10).

    What a plan drafted right now would use: the published row if there is one,
    `seeds/protocols.json` otherwise. This is the summary view — families, rungs,
    counts. The editor works on the document itself (`/admin/protocol-banks`),
    because the validator's guarantees are properties of the whole bank.
    """
    bank = await checkin_store.resolve_bank(session)
    published = await checkin_store.published_bank(session)
    return {
        "version": bank.version,
        "editable": True,
        "source": "protocol_banks" if published is not None else "seeds/protocols.json",
        "protocols": [
            {
                "key": protocol.key,
                "label": protocol.label[Lang.EN],
                "cycle_days": protocol.cycle_days,
                "precedence": protocol.precedence,
                "matches": {
                    "drug_classes": sorted(protocol.drug_classes),
                    "keywords": list(protocol.keywords),
                },
                "checkins": [
                    {
                        "day_offset": rung.day_offset,
                        "question_set": rung.question_set,
                        "asks_about": bank.question_set(rung.question_set).title[Lang.EN],
                        "questions": len(bank.question_set(rung.question_set).questions),
                        "grading_rules": len(bank.question_set(rung.question_set).grading),
                    }
                    for rung in protocol.checkins
                ],
            }
            for protocol in sorted(bank.protocols.values(), key=lambda p: -p.precedence)
        ],
        "question_sets": [
            {
                "key": qset.key,
                "title": qset.title[Lang.EN],
                "questions": [
                    {"id": question.id, "type": question.type, "prompt": question.prompt[Lang.EN]}
                    for question in qset.questions
                ],
                "grading": [
                    {"id": rule.id, "grade": str(rule.grade), "reason": rule.reason}
                    for rule in qset.grading
                ],
            }
            for qset in sorted(bank.question_sets.values(), key=lambda s: s.key)
        ],
    }


class BankVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    published_at: datetime | None
    notes: str | None
    protocol_count: int
    question_set_count: int


def _bank_out(v: admin_svc.BankVersion) -> BankVersionOut:
    return BankVersionOut(
        id=v.id,
        version=v.version,
        status=v.status,
        published_at=v.published_at,
        notes=v.notes,
        protocol_count=v.protocol_count,
        question_set_count=v.question_set_count,
    )


@router.get("/protocol-banks", response_model=list[BankVersionOut])
async def list_protocol_banks(session: AsyncSession = Depends(get_session)) -> list[BankVersionOut]:
    return [_bank_out(v) for v in await admin_svc.list_protocol_banks(session)]


@router.get("/protocol-banks/document")
async def get_protocol_bank(
    version: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The stored bank JSON — what the editor loads, edits and posts back."""
    try:
        return await admin_svc.get_protocol_bank(session, version)
    except admin_svc.AdminError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class SaveBankIn(BaseModel):
    bank: dict
    notes: str | None = None


@router.post("/protocol-banks/draft", response_model=BankVersionOut)
async def save_protocol_bank_draft(
    body: SaveBankIn, session: AsyncSession = Depends(get_session)
) -> BankVersionOut:
    try:
        version = await admin_svc.save_protocol_bank_draft(
            session, bank_json=body.bank, notes=body.notes
        )
    except admin_svc.AdminError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _bank_out(version)


@router.post("/protocol-banks/{version}/publish", response_model=BankVersionOut)
async def publish_protocol_bank(
    version: int, session: AsyncSession = Depends(get_session)
) -> BankVersionOut:
    try:
        published = await admin_svc.publish_protocol_bank(session, version=version)
    except admin_svc.AdminError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _bank_out(published)


# -- the switchboard: channels + provider credentials (S-GL.1) -----------------


class ChannelStateOut(BaseModel):
    """One channel's row in the Channels tab.

    `enabled` and `ready` are separate fields, not one `open`, because they are
    different problems with different fixes — and `open` is derived rather than
    sent so the console cannot drift from the gate's own answer.
    """

    channel: str
    enabled: bool
    ready: bool
    open: bool
    reason: str
    ladder: list[str]
    max_concurrent: int
    #: A caveat about an open channel — today, that it is running a fake provider.
    note: str


class ChannelsOut(BaseModel):
    channels: list[ChannelStateOut]
    kiosk_voice_profile: str
    voice_profiles: list[VoiceProfileOut]
    max_oss_sessions: int
    campaign_mix: dict[str, int]
    #: True when what is in force comes from `config/tiers.yaml` because nothing
    #: has been published. The console says so — an admin should know whether
    #: they are looking at a file or at a decision somebody made.
    from_file: bool
    version: int | None


class VoiceComponentOut(BaseModel):
    component: str
    provider: str
    model: str
    configured: bool
    tested: bool
    healthy: bool
    detail: str


class VoiceProfileOut(BaseModel):
    name: str
    active: bool
    ready: bool
    reason: str
    components: list[VoiceComponentOut]


class ChannelVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    published_at: datetime | None
    notes: str | None
    enabled: dict[str, bool]


class SaveChannelsIn(BaseModel):
    config: dict
    notes: str | None = None


def _channel_version_out(v: admin_svc.ChannelConfigVersionOut) -> ChannelVersionOut:
    return ChannelVersionOut(
        id=v.id,
        version=v.version,
        status=v.status,
        published_at=v.published_at,
        notes=v.notes,
        enabled=v.enabled,
    )


@router.get("/channels", response_model=ChannelsOut)
async def channels(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)
) -> ChannelsOut:
    """What is actually open right now — the gate's own view, not the document's.

    Readiness is resolved against the *effective* settings, so a credential set in
    this console a moment ago is reflected here without a restart.
    """
    effective = await runtime.effective_settings(session, settings)
    config = await channel_svc.resolve_config(session)
    published = await channel_svc.published_config(session)
    versions = await admin_svc.list_channel_configs(session)
    live = next((v for v in versions if v.status == "published"), None)
    profiles = await admin_svc.voice_profile_statuses(
        session, active=config.kiosk_voice_profile, settings=settings
    )

    return ChannelsOut(
        channels=[
            ChannelStateOut(
                channel=state.channel.value,
                enabled=state.enabled,
                ready=state.ready,
                open=state.is_open,
                reason=state.reason,
                ladder=list(state.ladder),
                max_concurrent=state.max_concurrent,
                note=state.note,
            )
            for state in channel_svc.channel_states(config, effective)
        ],
        kiosk_voice_profile=config.kiosk_voice_profile.value,
        voice_profiles=[
            VoiceProfileOut(
                name=profile.name,
                active=profile.active,
                ready=profile.ready,
                reason=profile.reason,
                components=[
                    VoiceComponentOut(
                        component=component.component,
                        provider=component.provider,
                        model=component.model,
                        configured=component.configured,
                        tested=component.tested,
                        healthy=component.healthy,
                        detail=component.detail,
                    )
                    for component in profile.components
                ],
            )
            for profile in profiles
        ],
        max_oss_sessions=config.max_oss_sessions,
        campaign_mix={c.value: pct for c, pct in config.campaign_mix.items()},
        from_file=published is None,
        version=live.version if live is not None else None,
    )


@router.get("/channels/document")
async def channel_document(
    version: int | None = Query(default=None), session: AsyncSession = Depends(get_session)
) -> dict:
    """The document the editor loads — a stored version, or the file's own."""
    try:
        return await admin_svc.get_channel_config(session, version)
    except admin_svc.AdminError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/channels/versions", response_model=list[ChannelVersionOut])
async def channel_versions(
    session: AsyncSession = Depends(get_session),
) -> list[ChannelVersionOut]:
    return [_channel_version_out(v) for v in await admin_svc.list_channel_configs(session)]


@router.post("/channels/draft", response_model=ChannelVersionOut)
async def save_channel_draft(
    body: SaveChannelsIn, session: AsyncSession = Depends(get_session)
) -> ChannelVersionOut:
    try:
        version = await admin_svc.save_channel_config_draft(
            session, config=body.config, notes=body.notes
        )
    except admin_svc.AdminError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Committed here rather than left to the session dependency: a `yield`
    # dependency's cleanup runs *after* the response is sent, so a client that
    # publishes the version it was just handed can beat its own draft to the
    # database. Every write on this tab is followed by another request.
    await session.commit()
    return _channel_version_out(version)


@router.post("/channels/{version}/publish", response_model=ChannelVersionOut)
async def publish_channels(
    version: int,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ChannelVersionOut:
    """Open or close channels. Live on the next intake, with no deploy."""
    try:
        published = await admin_svc.publish_channel_config(
            session, version=version, settings=settings
        )
    except admin_svc.AdminError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # See `save_channel_draft`: the caller's next request is usually "is it open
    # now?", and it must not be answered from before this publish.
    await session.commit()
    return _channel_version_out(published)


class ProviderCredentialOut(BaseModel):
    """Deliberately without a field for the credentials themselves. There is no
    read path here to grow a "reveal" button onto later — see the model docstring
    on `ProviderSecret`."""

    provider: str
    configured: bool
    missing: list[str]
    source: str
    updated_at: datetime | None
    last_test: dict
    derived_key: bool
    unreadable: bool
    #: Which fields this vendor takes, so the console renders the right form
    #: without hardcoding a vendor's shape.
    fields: list[str]


class SaveCredentialsIn(BaseModel):
    #: Field name → value. Unknown keys are dropped, not stored: a console may
    #: supply this vendor's credentials and nothing else.
    values: dict[str, str]


def _credential_out(status: admin_svc.ProviderCredentialStatus) -> ProviderCredentialOut:
    return ProviderCredentialOut(
        provider=status.provider,
        configured=status.configured,
        missing=status.missing,
        source=status.source,
        updated_at=status.updated_at,
        last_test=status.last_test,
        derived_key=status.derived_key,
        unreadable=status.unreadable,
        fields=list(runtime.CREDENTIAL_FIELDS[status.provider]),
    )


@router.get("/providers/credentials", response_model=list[ProviderCredentialOut])
async def provider_credentials(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)
) -> list[ProviderCredentialOut]:
    """Whether each vendor is configured, and how it was last tested. Never what."""
    return [_credential_out(s) for s in await admin_svc.provider_credentials(session, settings)]


@router.put("/providers/{name}/credentials", response_model=ProviderCredentialOut)
async def set_provider_credentials(
    name: str,
    body: SaveCredentialsIn,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: Principal = Depends(require_admin),
) -> ProviderCredentialOut:
    """Store a vendor's credentials, encrypted. Write-only: the response says
    whether they are complete, never what was stored."""
    try:
        status = await admin_svc.save_provider_credentials(
            session,
            provider=name,
            values=body.values,
            actor_id=principal.id,
            settings=settings,
        )
    except (admin_svc.AdminError, runtime.UnknownProviderSecret) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return _credential_out(status)


@router.delete("/providers/{name}/credentials", status_code=204)
async def clear_provider_credentials(
    name: str,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> None:
    """Remove stored credentials — the box falls back to `.env`."""
    try:
        await admin_svc.clear_provider_credentials(session, provider=name, actor_id=principal.id)
    except (admin_svc.AdminError, runtime.UnknownProviderSecret) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@router.post("/providers/{name}/test")
async def test_provider(
    name: str,
    component: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """One real round-trip against the vendor, reporting the vendor's own error.

    Always 200, even when the vendor rejects us: "Meta says the token expired" is
    a successful test with a failing result, and a 4xx here would make the console
    render its own generic error instead of the one sentence that matters.
    """
    try:
        result = await admin_svc.test_provider_credentials(
            session, provider=name, component=component, settings=settings
        )
    except runtime.UnknownProviderSecret as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return result


# -- people: staff onboarding (S-GL.2) ----------------------------------------
#
# Not versioned, unlike every other editor on this router: see the note at the top
# of `app.people`. Hiring somebody is not authored content with a review cycle,
# and a draft→publish flow would only add a way to leave it half-done.
#
# Every write commits explicitly, for the reason the channel routes discovered in
# S-GL.1: a `yield` dependency's cleanup runs *after* the response is sent, and
# this console's flow is a write immediately followed by a read of what it wrote
# (create a doctor → import their roster → generate their slots).


class PersonOut(BaseModel):
    user_id: uuid.UUID
    name: str
    phone: str
    role: str
    lang: str
    active: bool
    last_login_at: datetime | None
    doctor_id: uuid.UUID | None
    reg_no: str | None
    qualification: str | None
    department_code: str | None
    department_name: str | None
    clinics: int
    upcoming_appointments: int


def _person_out(person: people.Person) -> PersonOut:
    return PersonOut(
        user_id=person.user_id,
        name=person.name,
        phone=person.phone,
        role=str(person.role),
        lang=str(person.lang),
        active=person.active,
        last_login_at=person.last_login_at,
        doctor_id=person.doctor_id,
        reg_no=person.reg_no,
        qualification=person.qualification,
        department_code=person.department_code,
        department_name=person.department_name,
        clinics=person.clinics,
        upcoming_appointments=person.upcoming_appointments,
    )


class BookedOut(BaseModel):
    appointment_id: uuid.UUID
    patient_name: str
    patient_phone: str
    at: datetime
    slot_type: str | None


def _booked_out(row: people.BookedAppointment) -> BookedOut:
    return BookedOut(
        appointment_id=row.appointment_id,
        patient_name=row.patient_name,
        patient_phone=row.patient_phone,
        at=row.at,
        slot_type=row.slot_type,
    )


class DepartmentOut(BaseModel):
    code: str
    name: str
    #: The raw stored value rather than a capabilities object, and this is the
    #: one surface where that is right: the admin console's job is to *show and
    #: edit* the system of medicine (doc 24 §7), so here it is the data, not a
    #: thing to branch on. Every other consumer gets flags.
    care_system: CareSystem


@router.get("/people", response_model=list[PersonOut])
async def list_people(session: AsyncSession = Depends(get_session)) -> list[PersonOut]:
    return [_person_out(p) for p in await people.list_people(session)]


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(session: AsyncSession = Depends(get_session)) -> list[DepartmentOut]:
    """The department list the create-a-doctor form picks from — so a console can
    never invent a department code that routing would then fail to resolve."""
    rows = (
        await session.execute(
            select(Department)
            .where(Department.deleted_at.is_(None), Department.active.is_(True))
            .order_by(Department.name)
        )
    ).scalars()
    return [DepartmentOut(code=d.code, name=d.name, care_system=d.care_system) for d in rows]


class CreateUserIn(BaseModel):
    name: str
    phone: str
    role: Role
    lang: Lang = Lang.HI


@router.post("/people", response_model=PersonOut, status_code=201)
async def create_user(
    body: CreateUserIn, session: AsyncSession = Depends(get_session)
) -> PersonOut:
    try:
        user = await people.create_user(
            session, name=body.name, phone=body.phone, role=body.role, lang=body.lang
        )
    except people.PeopleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return _person_out(await _person(session, user.id))


class CreateDoctorIn(BaseModel):
    name: str
    phone: str
    department_code: str
    reg_no: str
    qualification: str | None = None
    lang: Lang = Lang.HI


@router.post("/people/doctors", response_model=PersonOut, status_code=201)
async def create_doctor(
    body: CreateDoctorIn, session: AsyncSession = Depends(get_session)
) -> PersonOut:
    """A login and a clinical profile, in one transaction. Bookable the moment a
    clinic and its slots exist — which is the roster half below."""
    try:
        doctor = await people.create_doctor(
            session,
            name=body.name,
            phone=body.phone,
            department_code=body.department_code,
            reg_no=body.reg_no,
            qualification=body.qualification,
            lang=body.lang,
        )
    except people.PeopleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return _person_out(await _person(session, doctor.user_id))


class InviteOut(BaseModel):
    sent: bool
    to: str
    detail: str


@router.post("/people/{user_id}/invite", response_model=InviteOut)
async def invite(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> InviteOut:
    """ "This number can now sign in" — an SMS, and nothing minted. The OTP login
    is the credential, so there is no invite token here to expire or leak."""
    try:
        result = await people.send_invite(session, user_id=user_id, settings=settings)
    except people.PeopleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return InviteOut(sent=result.sent, to=result.to, detail=result.detail)


class DeactivationImpactOut(BaseModel):
    user_id: uuid.UUID
    name: str
    role: str
    is_doctor: bool
    active_clinics: int
    open_future_slots: int
    booked: list[BookedOut]
    needs_a_decision: bool


@router.get("/people/{user_id}/deactivation-impact", response_model=DeactivationImpactOut)
async def deactivation_impact(
    user_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> DeactivationImpactOut:
    """Step one of two. What deactivating this person would leave behind —
    including, by name, the patients already booked with them."""
    try:
        impact = await people.deactivation_impact(session, user_id=user_id)
    except people.PeopleError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DeactivationImpactOut(
        user_id=impact.user_id,
        name=impact.name,
        role=str(impact.role),
        is_doctor=impact.is_doctor,
        active_clinics=impact.active_clinics,
        open_future_slots=impact.open_future_slots,
        booked=[_booked_out(b) for b in impact.booked],
        needs_a_decision=impact.needs_a_decision,
    )


class DeactivateIn(BaseModel):
    #: The admin saying "yes, I have seen those patients and we will ring them".
    #: Without it the route refuses while anybody is booked.
    acknowledge: bool = False


class DeactivateOut(BaseModel):
    user_id: uuid.UUID
    name: str
    clinics_retired: int
    slots_blocked: int
    appointments_left: list[BookedOut]


@router.post("/people/{user_id}/deactivate", response_model=DeactivateOut)
async def deactivate(
    user_id: uuid.UUID,
    body: DeactivateIn,
    session: AsyncSession = Depends(get_session),
) -> DeactivateOut:
    try:
        result = await people.deactivate(session, user_id=user_id, acknowledge=body.acknowledge)
    except people.PeopleError as exc:
        # 409, not 422: the request is well-formed and the state is the problem,
        # and the console distinguishes "fix your input" from "confirm this".
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return DeactivateOut(
        user_id=result.user_id,
        name=result.name,
        clinics_retired=result.clinics_retired,
        slots_blocked=result.slots_blocked,
        appointments_left=[_booked_out(b) for b in result.appointments_left],
    )


@router.post("/people/{user_id}/activate", response_model=PersonOut)
async def activate(user_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> PersonOut:
    """Let somebody back in. Their clinic does not come back with them — see
    `app.people.activate`."""
    try:
        person = await people.activate(session, user_id=user_id)
    except people.PeopleError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return _person_out(person)


async def _person(session: AsyncSession, user_id: uuid.UUID) -> people.Person:
    found = next((p for p in await people.list_people(session) if p.user_id == user_id), None)
    if found is None:  # pragma: no cover - the row was just written
        raise HTTPException(status_code=404, detail="no such person")
    return found


# -- the facility: hospital identity + departments (AYUR-1, doc 24 §7) ---------
#
# The two facts a hospital owns about itself that were previously only editable
# by editing `seeds/hospital.json` on the box: what it is called, and which
# departments it runs. Both are thin over `app.facility`; both audit.
#
# `GET /departments` above is left exactly as it was — active-only, feeding the
# create-a-doctor picker. `GET /facility` is the editor's read, and it is the
# one that sees the closed departments, because opening them is what it is for.


class HospitalOut(BaseModel):
    hospital_id: uuid.UUID
    code: str
    #: Printed on the prescription letterhead and on the intake boarding pass.
    #: Renaming the hospital here is how "Ayurveda Hospital" reaches paper.
    name: str
    city: str | None
    district: str | None
    default_lang: Lang


def _hospital_out(row: facility.HospitalIdentity) -> HospitalOut:
    return HospitalOut(
        hospital_id=row.hospital_id,
        code=row.code,
        name=row.name,
        city=row.city,
        district=row.district,
        default_lang=row.default_lang,
    )


class DepartmentRowOut(BaseModel):
    department_id: uuid.UUID
    code: str
    name: str
    icon: str | None
    #: The raw stored value, for the same reason `DepartmentOut` carries it: this
    #: console's job is to show and edit the system of medicine (doc 24 §7), so
    #: here it *is* the data. Every other consumer gets capability flags.
    care_system: CareSystem
    active: bool
    doctors: int
    published_trees: int
    #: False means activating this department would send a patient into an error
    #: rather than into questions — the console disables the toggle on it.
    has_intake: bool


def _department_row_out(row: facility.DepartmentRow) -> DepartmentRowOut:
    return DepartmentRowOut(
        department_id=row.department_id,
        code=row.code,
        name=row.name,
        icon=row.icon,
        care_system=row.care_system,
        active=row.active,
        doctors=row.doctors,
        published_trees=row.published_trees,
        has_intake=row.has_intake,
    )


class FacilityOut(BaseModel):
    hospital: HospitalOut
    departments: list[DepartmentRowOut]


@router.get("/facility", response_model=FacilityOut)
async def facility_overview(session: AsyncSession = Depends(get_session)) -> FacilityOut:
    """The hospital and every department it runs, open or closed."""
    return FacilityOut(
        hospital=_hospital_out(await facility.identity(session)),
        departments=[_department_row_out(d) for d in await facility.list_departments(session)],
    )


class HospitalPatch(BaseModel):
    """Every field optional — an absent one is left alone, not blanked."""

    name: str | None = None
    city: str | None = None
    district: str | None = None
    default_lang: Lang | None = None


@router.patch("/hospital", response_model=HospitalOut)
async def patch_hospital(
    body: HospitalPatch, session: AsyncSession = Depends(get_session)
) -> HospitalOut:
    try:
        updated = await facility.update_identity(
            session,
            name=body.name,
            city=body.city,
            district=body.district,
            default_lang=body.default_lang,
        )
    except facility.FacilityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return _hospital_out(updated)


class CreateDepartmentIn(BaseModel):
    code: str
    name: str
    icon: str | None = None
    #: Absent means allopathy, exactly as an unstated `care_system` does in a
    #: seed file. Parsed by `app.facility` through `care_system_of`, so a
    #: misspelling is a 422 rather than a department quietly practising the
    #: wrong system of medicine.
    care_system: str | None = None
    #: Closed by default. A department created a second ago has no intake tree,
    #: and `app.facility` refuses to open one that has nothing to ask.
    active: bool = False


@router.post("/departments", response_model=DepartmentRowOut, status_code=201)
async def create_department(
    body: CreateDepartmentIn, session: AsyncSession = Depends(get_session)
) -> DepartmentRowOut:
    try:
        created = await facility.create_department(
            session,
            code=body.code,
            name=body.name,
            icon=body.icon,
            care_system=body.care_system,
            active=body.active,
        )
    except facility.FacilityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return _department_row_out(created)


class CapabilityChangeOut(BaseModel):
    flag: str
    before: str
    after: str
    #: The sentence an administrator reads. Comes from `app.care_system`, the
    #: only module that knows what a flag means.
    label: str


class CareSystemImpactOut(BaseModel):
    code: str
    name: str
    from_system: CareSystem
    to_system: CareSystem
    is_a_change: bool
    changes: list[CapabilityChangeOut]
    doctors: int
    published_trees: int
    active: bool


def _care_system_impact_out(impact: facility.CareSystemImpact) -> CareSystemImpactOut:
    return CareSystemImpactOut(
        code=impact.code,
        name=impact.name,
        from_system=impact.from_system,
        to_system=impact.to_system,
        is_a_change=impact.is_a_change,
        changes=[
            CapabilityChangeOut(
                flag=change.flag,
                before=str(change.before),
                after=str(change.after),
                label=change.label,
            )
            for change in impact.changes
        ],
        doctors=impact.doctors,
        published_trees=impact.published_trees,
        active=impact.active,
    )


@router.get("/departments/{code}/care-system-impact", response_model=CareSystemImpactOut)
async def department_care_system_impact(
    code: str,
    to: str = Query(description="the system of medicine being considered"),
    session: AsyncSession = Depends(get_session),
) -> CareSystemImpactOut:
    """What would change if this department practised `to` instead (doc 24 §7).

    Read before the write, the way `deactivation-impact` precedes a
    deactivation. The list of changes is *derived* from the two capability rows,
    so it cannot describe a state of affairs that stopped being true.
    """
    try:
        impact = await facility.care_system_impact(session, code=code, to=to)
    except facility.FacilityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _care_system_impact_out(impact)


class PatchDepartmentIn(BaseModel):
    name: str | None = None
    icon: str | None = None
    care_system: str | None = None
    active: bool | None = None
    #: Required to change the system of medicine, and ignored otherwise. The
    #: console sets it after showing the impact above; a script that sets it
    #: without reading one has still had to say the word.
    acknowledge: bool = False


@router.patch("/departments/{code}", response_model=DepartmentRowOut)
async def patch_department(
    code: str, body: PatchDepartmentIn, session: AsyncSession = Depends(get_session)
) -> DepartmentRowOut:
    try:
        updated = await facility.update_department(
            session,
            code=code,
            name=body.name,
            icon=body.icon,
            care_system=body.care_system,
            active=body.active,
            acknowledge=body.acknowledge,
        )
    except facility.FacilityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return _department_row_out(updated)


# -- the roster: slot templates + import (S-GL.2) ------------------------------
#
# This replaces S18E's `{"deferred": true, "arrives_in": "S15"}` marker, which was
# the console's last honest placeholder.


class ClinicOut(BaseModel):
    template_id: uuid.UUID
    doctor_id: uuid.UUID
    doctor_name: str
    reg_no: str
    department_code: str
    weekday: int
    weekday_name: str
    start: str
    end: str
    slot_minutes: int
    capacity: int
    slot_type: str
    active: bool
    slots_per_week: int
    future_slots: int
    future_booked: int
    #: The next three dates this clinic actually runs — a weekday name is not a
    #: date, and an admin checking their work wants the date.
    next_dates: list[date]


def _clinic_out(clinic: roster.Clinic) -> ClinicOut:
    return ClinicOut(
        template_id=clinic.template_id,
        doctor_id=clinic.doctor_id,
        doctor_name=clinic.doctor_name,
        reg_no=clinic.reg_no,
        department_code=clinic.department_code,
        weekday=clinic.weekday,
        weekday_name=clinic.weekday_name,
        start=clinic.start_time.strftime("%H:%M"),
        end=clinic.end_time.strftime("%H:%M"),
        slot_minutes=clinic.slot_minutes,
        capacity=clinic.capacity,
        slot_type=str(clinic.slot_type),
        active=clinic.active,
        slots_per_week=clinic.slots_per_week,
        future_slots=clinic.future_slots,
        future_booked=clinic.future_booked,
        next_dates=roster.upcoming_dates(clinic.weekday),
    )


@router.get("/slot-templates", response_model=list[ClinicOut])
async def slot_templates(
    include_retired: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> list[ClinicOut]:
    """The weekly clinic grid (doc 03 §10). Built in S-GL.2; before that this
    route answered with a deferral marker."""
    clinics = await roster.list_clinics(session, include_retired=include_retired)
    return [_clinic_out(c) for c in clinics]


class ClinicIn(BaseModel):
    doctor_id: uuid.UUID
    weekday: int = Field(ge=0, le=6)
    start: str
    end: str
    slot_type: SlotType = SlotType.FOLLOW_UP
    capacity: int = Field(default=1, ge=1)
    slot_minutes: int = Field(default=15, ge=1)
    acknowledge: bool = False

    def to_write(self) -> roster.ClinicWrite:
        return roster.ClinicWrite(
            doctor_id=self.doctor_id,
            weekday=self.weekday,
            start_time=_hhmm(self.start),
            end_time=_hhmm(self.end),
            slot_type=self.slot_type,
            capacity=self.capacity,
            slot_minutes=self.slot_minutes,
        )


def _hhmm(value: str) -> time:
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{value!r} is not a time (HH:MM)") from exc


class ChangeImpactOut(BaseModel):
    template_id: uuid.UUID
    label: str
    empty_future_slots: int
    booked: list[BookedOut]
    needs_a_decision: bool


def _impact_out(impact: roster.ChangeImpact) -> ChangeImpactOut:
    return ChangeImpactOut(
        template_id=impact.template_id,
        label=impact.label,
        empty_future_slots=impact.empty_future_slots,
        booked=[_booked_out(b) for b in impact.booked],
        needs_a_decision=impact.needs_a_decision,
    )


@router.get("/slot-templates/{template_id}/impact", response_model=ChangeImpactOut)
async def clinic_impact(
    template_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ChangeImpactOut:
    """Who is booked into this clinic's future slots — asked before an edit or a
    retirement, so the console shows the patients rather than a warning."""
    try:
        return _impact_out(await roster.change_impact(session, template_id=template_id))
    except roster.RosterError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/slot-templates", response_model=ClinicOut, status_code=201)
async def create_clinic(body: ClinicIn, session: AsyncSession = Depends(get_session)) -> ClinicOut:
    try:
        template, _ = await roster.save_clinic(session, write=body.to_write())
    except roster.RosterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return await _clinic(session, template.id)


@router.put("/slot-templates/{template_id}", response_model=ClinicOut)
async def update_clinic(
    template_id: uuid.UUID, body: ClinicIn, session: AsyncSession = Depends(get_session)
) -> ClinicOut:
    """Edit a clinic, reconciling the inventory it already made (`app.roster`)."""
    try:
        template, _ = await roster.save_clinic(
            session,
            write=body.to_write(),
            template_id=template_id,
            acknowledge=body.acknowledge,
        )
    except roster.RosterError as exc:
        status = 409 if "booked into" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    await session.commit()
    return await _clinic(session, template.id)


@router.delete("/slot-templates/{template_id}", response_model=ChangeImpactOut)
async def retire_clinic(
    template_id: uuid.UUID,
    acknowledge: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> ChangeImpactOut:
    """Stop a clinic. Soft — the template deactivates and its empty future slots
    block; the booked ones stand and come back in the response."""
    try:
        impact = await roster.retire_clinic(
            session, template_id=template_id, acknowledge=acknowledge
        )
    except roster.RosterError as exc:
        status = 409 if "booked into" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    await session.commit()
    return _impact_out(impact)


async def _clinic(session: AsyncSession, template_id: uuid.UUID) -> ClinicOut:
    found = next(
        (
            c
            for c in await roster.list_clinics(session, include_retired=True)
            if c.template_id == template_id
        ),
        None,
    )
    if found is None:  # pragma: no cover - just written
        raise HTTPException(status_code=404, detail="no such clinic")
    return _clinic_out(found)


class PlannedClinicOut(BaseModel):
    line: int
    doctor_label: str
    doctor_name: str | None
    department_code: str | None
    weekday_name: str
    start: str
    end: str
    slot_type: str
    capacity: int
    slot_minutes: int
    slots_per_week: int
    action: str
    error: str | None


class RosterPlanOut(BaseModel):
    """The dry run. `ok` is false if **any** row failed, because the import is
    all-or-nothing — a half-applied roster cannot be re-uploaded safely."""

    ok: bool
    counts: dict[str, int]
    rows: list[PlannedClinicOut]


class ImportResultOut(BaseModel):
    created: int
    updated: int
    unchanged: int
    slots_generated: int
    disturbed: list[BookedOut]


def _plan_out(plan: roster.RosterPlan) -> RosterPlanOut:
    return RosterPlanOut(
        ok=plan.ok,
        counts=plan.counts(),
        rows=[
            PlannedClinicOut(
                line=row.line,
                doctor_label=row.doctor_label,
                doctor_name=row.doctor_name,
                department_code=row.department_code,
                weekday_name=row.weekday_name,
                start=row.start,
                end=row.end,
                slot_type=row.slot_type,
                capacity=row.capacity,
                slot_minutes=row.slot_minutes,
                slots_per_week=row.slots_per_week,
                action=row.action,
                error=row.error,
            )
            for row in plan.rows
        ],
    )


@router.post("/roster/import")
async def import_roster(
    file: UploadFile = File(...),
    dry_run: bool = Query(default=True),
    generate: bool = Query(default=True),
    acknowledge: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Upload a roster. **Dry run by default** — nothing is written unless the
    caller asks, and never if any row failed.

    Returns the plan either way, so the console shows the same table before and
    after and an admin can see that what was previewed is what happened.
    """
    content = await file.read()
    try:
        rows = roster.read_rows(content, file.filename or "")
    except roster.RosterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    plan = await roster.plan_roster(session, rows)
    out: dict = {"plan": _plan_out(plan).model_dump(), "applied": None}
    if dry_run:
        return out

    try:
        result = await roster.apply_roster(
            session, plan, generate=generate, acknowledge=acknowledge
        )
    except roster.RosterError as exc:
        status = 409 if "booked into" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    await session.commit()
    out["applied"] = ImportResultOut(
        created=result.created,
        updated=result.updated,
        unchanged=result.unchanged,
        slots_generated=result.slots_generated,
        disturbed=[_booked_out(b) for b in result.disturbed],
    ).model_dump()
    return out


@router.get("/roster/sample.csv")
async def roster_sample() -> Response:
    """A working example to start from, rather than a column list in a paragraph."""
    return Response(
        content=roster.sample_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="roster-sample.csv"'},
    )


class GenerateIn(BaseModel):
    doctor_id: uuid.UUID | None = None
    start: date | None = None
    days: int = Field(default=60, ge=1, le=366)


class GenerateOut(BaseModel):
    created: int
    start: date
    days: int


@router.post("/slots/generate", response_model=GenerateOut)
async def generate_slots(
    body: GenerateIn, session: AsyncSession = Depends(get_session)
) -> GenerateOut:
    """Materialise bookable slots from the templates. Idempotent — pressing it
    twice creates nothing the second time and never resets a booking."""
    try:
        result = await roster.generate(
            session, doctor_id=body.doctor_id, start=body.start, days=body.days
        )
    except roster.RosterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return GenerateOut(created=result.created, start=result.start, days=result.days)
