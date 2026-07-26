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

The remaining deferred panel (slot templates → S15) answers 200 with
`{"deferred": true, "arrives_in": "S15"}` rather than 404, so the console renders
an explicit "arrives with S15" placeholder instead of a broken link.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import admin as admin_svc
from app import analytics
from app.auth.rbac import Principal, require_admin
from app.checkins import store as checkin_store
from app.config import Settings, get_settings
from app.db import get_session
from app.models.enums import Channel, IntakeTier, Lang, PriceUnit, UsagePurpose
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


@router.get("/slot-templates")
async def slot_templates() -> dict:
    """Deferred to S15 (slot inventory). Marker, not 404."""
    return {
        "deferred": True,
        "arrives_in": "S15",
        "reason": "slot templates need the appointment slot inventory (telephony part 2)",
    }
