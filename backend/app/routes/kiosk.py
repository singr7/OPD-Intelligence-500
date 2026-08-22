"""Kiosk intake HTTP surface (doc 03 §1a) — REST over the intake tool contract.

The intake engine had no channel until now (STATE.md: "not wired to any route").
This is the first one. It is deliberately **thin REST that mirrors the four-tool
contract** rather than a websocket, for one reason the HANDOFF called out: keep
the wire shape the same as the tool contract so S14's telephony and S12's WhatsApp
reuse the vocabulary. One request = one tool call over the dispatcher:

    POST /kiosk/start    -> route Q1, create the visit, get_next_node (first screen)
    GET  /kiosk/{sid}/next   -> get_next_node (re-render / resume)
    POST /kiosk/{sid}/answer -> save_answer, returns the next node
    POST /kiosk/{sid}/finish -> finish_and_summarize (the read-back screen)
    POST /kiosk/{sid}/confirm -> mark confirmed, allocate token, finalize cost

The kiosk is a V3 client (taps, no model in the walk); the one model call is Q1's
department classifier, and `needs_human` is honoured — `/start` then returns a
department chooser instead of a session, and the kiosk re-calls `/start` with the
chosen `dept_key`.

## What is and is not authenticated here

The **patient's** intake is not, and must stay that way: a kiosk is a public
terminal, the visit is an anonymous walk-in, and no route the patient drives may
require or return a credential. No PII in a path.

Since AR2 there is one authenticated cluster — the coordinator's *staff strip* on
the last screen (`/staff/holders`, `/staff/unlock`, `/{sid}/strip`,
`/{sid}/assign`). It exists because the pilot runs a single kiosk with a single
coordinator standing at it, and it is guarded by `require_kiosk_staff`, which
accepts only the narrow `kiosk_staff` token a PIN mints — never an ordinary staff
session (`app.auth.kiosk_pin` explains why that separation is the whole design).

`/start` now takes an optional phone/UHC ID and *may* find a prior patient, but
the lookup's result is never returned to the terminal: it is recorded on the visit
as a candidate for the coordinator to confirm behind the PIN. A public screen that
prints a named oncology history to whoever types ten digits is the failure this
arrangement exists to prevent, so the boring rule survives in the form that
matters — nothing patient-identifying leaves these routes unauthenticated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import allergies as allergy_svc
from app import assignment as assignment_svc
from app import facility
from app import facility as facility_svc
from app import kiosk as kiosk_svc
from app import offline as offline_svc
from app import queue as queue_svc
from app.auth.rbac import Principal, require_kiosk_staff
from app.channels import require_open, resolve_config
from app.config import Settings, get_settings
from app.db import get_session
from app.intake import IntakeEngine, Interpreter, SessionState, ToolError
from app.languages import script_problem
from app.models.clinical import Visit
from app.models.enums import CareSystem, Channel, Lang, UsagePurpose
from app.models.org import Doctor
from app.models.patient import Patient
from app.providers.audio import AudioClip
from app.providers.base import ProviderBadRequest, ProviderError, with_fallback
from app.providers.metering import get_meter, usage_scope
from app.providers.profiles import resolve_profile, snapshot_profile
from app.providers.registry import stt_chain, tts_chain
from app.providers.runtime import effective_settings
from app.queue_hub import QueueHub
from app.trees import bank
from app.trees import store as tree_store
from app.trees import visibility as tree_visibility
from app.trees.schema import Node
from app.trees.walker import AnswerError, validate_answer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kiosk", tags=["kiosk"])


# -- dependency ---------------------------------------------------------------


def get_engine(request: Request) -> IntakeEngine:
    """The one process-wide `IntakeEngine`, built on the lifespan (it holds no
    per-intake state; the session store does)."""
    engine = getattr(request.app.state, "intake_engine", None)
    if engine is None:  # pragma: no cover - lifespan always sets it
        raise HTTPException(status_code=503, detail="intake engine not ready")
    return engine


async def _load_state(
    engine: IntakeEngine,
    session_id: str,
    *,
    expected: tuple[Channel, ...] = (Channel.KIOSK,),
) -> SessionState:
    """The session, if it belongs to a channel this caller may advance.

    A phone session must never be advanced by taps, which is what `expected`
    guards. The patient app (S16) passes `(Channel.APP,)` and walks the same
    three verbs through its own authenticated routes — one walker, one wire
    shape, two front doors with different locks on them.
    """
    state = await engine.store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="no such intake session")
    if state.channel not in expected:
        raise HTTPException(status_code=409, detail="session is not a kiosk session")
    return state


# -- wire models --------------------------------------------------------------


class StartIn(BaseModel):
    lang: Lang
    chief_complaint: str = Field(min_length=1, max_length=2000)
    caregiver: bool = False
    patient_name: str | None = Field(default=None, max_length=200)
    #: The S-UX.6 registration facts, collected on the kiosk's details screen.
    #: All optional and normalised server-side — a typo must not refuse an intake.
    patient_age: int | None = Field(default=None, ge=0, le=200)
    patient_sex: str | None = Field(default=None, max_length=16)
    patient_phone: str | None = Field(default=None, max_length=24)
    #: The health ID the patient already carries — the pilot site's UHC ID/MRN,
    #: which may be printed on a card or a discharge summary they are holding.
    #: Optional in the strictest sense: it never gates an intake or a token, and a
    #: patient who does not have one, cannot read one, or declines is unaffected.
    patient_external_id: str | None = Field(default=None, max_length=64)
    #: A confirmed department (staff- or patient-picked from the chooser). When
    #: present the classifier is skipped entirely.
    dept_key: str | None = None


class NodeOut(BaseModel):
    id: str
    type: str
    text: str
    options: list[dict[str, Any]]
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    audio: str | None = None
    summary_role: str | None = None
    #: How many questions remain on this tree's default path, counting this one
    #: (S-UX.6). The kiosk uses it for an honest progress bar and to decide where
    #: the microphone belongs — speaking is offered on the closing questions,
    #: where the patient has something to add, not on every yes/no tap.
    remaining: int | None = None
    #: True when this node invites a spoken answer. Derived, never authored: a
    #: free-text node always does; a tap node does only in the closing pair.
    voice_input: bool = False


class DeptOut(BaseModel):
    key: str
    name: str
    #: The raw stored value (doc 24 §5). The kiosk uses it to style the
    #: department's card — an icon and a card treatment that say "this is the
    #: ayurveda clinic" — which is presentation keyed on identity, not a
    #: behaviour branch. `web/app/_lib/careSystem.ts` is where it is read.
    #:
    #: **Nothing about traversal, routing or red flags may consult it.** Doc 24
    #: §4: a wellness framing must never soften an emergency, so the rule engine
    #: sees answer IDs and nothing else, in either system of medicine.
    care_system: CareSystem


class StartOut(BaseModel):
    #: "routed" — a session started; "needs_department" — show the chooser.
    status: str
    session_id: str | None = None
    lang: Lang | None = None
    tier: str | None = None
    department: DeptOut | None = None
    tree_key: str | None = None
    node: NodeOut | None = None
    complete: bool = False
    #: Populated only on "needs_department": the chooser's options + why.
    departments: list[DeptOut] = Field(default_factory=list)
    reason: str | None = None


class AnswerIn(BaseModel):
    node_id: str
    value: Any = None
    raw_text: str | None = None
    #: How many times this node has already been re-asked by voice (S-ADAPT.1, doc
    #: 11 §5). The kiosk increments it on each clarify; the server refuses to
    #: clarify a second time and falls back to taps — no infinite clarify loop.
    attempt: int = 0


class AnswerOut(BaseModel):
    ok: bool
    node_id: str
    complete: bool
    #: Present when the answer did not fit the node — the kiosk re-asks.
    error: str | None = None
    red_flags: list[dict[str, Any]] = Field(default_factory=list)
    #: The next screen (None once the tree completes).
    node: NodeOut | None = None
    #: S-ADAPT.1 (doc 11 §2): one spoken clarifying question when a voice answer was
    #: too vague to map. The kiosk speaks it (Kokoro) and re-opens the mic on the
    #: *same* node. Null with `ok=False` and `adaptive_exhausted` set means the
    #: clarify budget is spent — the kiosk keeps the node's taps.
    clarify: str | None = None
    #: True when adaptive voice gave up on this node and the patient should tap
    #: (flag off, no interpreter, second vague answer, or a rejected value).
    adaptive_exhausted: bool = False
    #: The value the deterministic walker actually accepted. This lets the
    #: kiosk render the corresponding displayed label even when voice
    #: interpretation mapped the patient's words to an option id.
    accepted_value: Any = None


class FinishOut(BaseModel):
    readback: str
    summary_md: str | None
    red_flags: list[dict[str, Any]]
    complete: bool


class ConfirmOut(BaseModel):
    token_no: int | None
    department: DeptOut | None
    red_flags: list[dict[str, Any]]
    cost_inr: str | None


# -- routes -------------------------------------------------------------------


@router.post("/start", response_model=StartOut)
async def start(
    payload: StartIn,
    engine: IntakeEngine = Depends(get_engine),
    session: AsyncSession = Depends(get_session),
) -> StartOut:
    """Route the chief complaint, open the intake, return the first question.

    Honours the classifier's `needs_human`: an uncertain route yields
    `status="needs_department"` and the chooser, not a guessed session.

    Gated on the kiosk being open (S-GL.1). The gate is on *start* and not on the
    per-question routes on purpose: a patient half way through her intake when an
    admin closes the channel finishes it. Closing a channel means "take no new
    ones", not "abandon whoever is mid-sentence".
    """
    channel_config = await resolve_config(session)
    settings = await effective_settings(session, get_settings())
    require_open(channel_config, Channel.KIOSK, lang=payload.lang)
    try:
        routed = await kiosk_svc.route_complaint(
            session,
            complaint=payload.chief_complaint,
            lang=payload.lang,
            dept_key=payload.dept_key,
        )
    except kiosk_svc.KioskError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if routed.needs_department:
        departments = await kiosk_svc._departments(session)
        return StartOut(
            status="needs_department",
            departments=[
                DeptOut(key=d.code, name=d.name, care_system=d.care_system) for d in departments
            ],
            reason=routed.guess.reason or "Let's confirm the right doctor for you.",
        )

    assert routed.department is not None and routed.tree is not None
    try:
        walk_in = await kiosk_svc.create_walk_in(
            session,
            department=routed.department,
            lang=payload.lang,
            tree=routed.tree,
            caregiver=payload.caregiver,
            patient_name=payload.patient_name,
            patient_age=payload.patient_age,
            patient_sex=payload.patient_sex,
            patient_phone=payload.patient_phone,
            patient_external_id=payload.patient_external_id,
        )
    except kiosk_svc.KioskError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    state = await engine.start_session(
        tree=routed.tree,
        channel=Channel.KIOSK,
        lang=payload.lang,
        configured_tier=kiosk_svc.KIOSK_TIER,
        intake_id=walk_in.intake.id,
        visit_id=walk_in.visit.id,
        chief_complaint=payload.chief_complaint,
        voice_profile=snapshot_profile(channel_config.kiosk_voice_profile, settings),
        # Pinned for the life of the intake (doc 24 §5) — `routed.tree` was
        # already pruned to these, and every later turn reloads the tree from
        # the bank and must prune it the same way.
        open_departments=sorted(await tree_store.active_department_codes(session)),
        # The register the intake summary is written in, pinned for the walk's
        # life like the line above (doc 24 §6.4). From the tree's department, not
        # from the request.
        care_system=await facility_svc.care_system_of_department(session, routed.tree.department),
    )

    dispatcher = engine.dispatcher(state, routed.tree)
    first = await dispatcher.get_next_node()
    return StartOut(
        status="routed",
        session_id=state.session_id,
        lang=state.lang,
        tier=state.active_tier.value,
        department=DeptOut(
            key=routed.department.code,
            name=routed.department.name,
            care_system=routed.department.care_system,
        ),
        tree_key=routed.tree.key,
        node=_node_out(first),
        complete=first.get("complete", False),
    )


async def next_node_impl(
    engine: IntakeEngine, session_id: str, *, expected: tuple[Channel, ...] = (Channel.KIOSK,)
) -> Any:
    state = await _load_state(engine, session_id, expected=expected)
    dispatcher = engine.dispatcher(state)
    result = await dispatcher.get_next_node()
    node = _node_out(result)
    return node.model_dump() if node else {"complete": True, "node": None}


@router.get("/{session_id}/next", response_model=NodeOut | dict)
async def next_node(
    session_id: str,
    engine: IntakeEngine = Depends(get_engine),
) -> Any:
    """The current question — for a resumed kiosk (idle reset) or a re-render."""
    return await next_node_impl(engine, session_id)


#: The clarify budget per node (doc 11 §5: "one clarify, then fall back"). The
#: first vague voice answer earns one follow-up; a second falls back to taps.
_MAX_CLARIFY_ATTEMPTS = 1


@router.post("/{session_id}/answer", response_model=AnswerOut)
async def answer(
    session_id: str,
    payload: AnswerIn,
    engine: IntakeEngine = Depends(get_engine),
) -> AnswerOut:
    """Record one tap/answer, then hand back the next screen."""
    return await answer_impl(engine, session_id, payload)


async def answer_impl(
    engine: IntakeEngine,
    session_id: str,
    payload: AnswerIn,
    *,
    expected: tuple[Channel, ...] = (Channel.KIOSK,),
) -> AnswerOut:
    """One answer, saved and validated — the body both front doors share.

    A `Walk.save` prunes answers stranded on an abandoned branch, so the next node
    and the red flags are recomputed here from the fresh walk — never cached on the
    client (STATE.md invariant).

    S-ADAPT.1 (doc 11 §2): a *voice* answer arrives as `value=null` + `raw_text`.
    When adaptive intake is on, the answer interpreter maps the words onto the
    current node's own allowed answers; a vague answer earns one spoken clarifying
    question (`clarify`) before falling back to taps. A tapped `value` skips all of
    this — the unchanged, zero-AI path (doc 04 law 8).
    """
    state = await _load_state(engine, session_id, expected=expected)
    dispatcher = engine.dispatcher(state)

    value = payload.value
    interpreter = engine.answer_interpreter(state)
    is_voice_answer = value is None and bool(payload.raw_text)
    if is_voice_answer and interpreter is not None:
        try:
            outcome = await _interpret_voice_answer(interpreter, engine, dispatcher, state, payload)
        except ProviderError as exc:
            # Provider exhaustion is an operational downgrade, not a patient
            # error. Keep the same unanswered node and return its deterministic
            # taps; log only profile/session identifiers, never the utterance.
            logger.warning(
                "kiosk voice profile=%s session=%s component=llm outcome=taps error=%s",
                state.voice_profile.name if state.voice_profile else "legacy",
                state.session_id,
                type(exc).__name__,
            )
            return await _exhausted(payload.node_id, dispatcher)
        if outcome.reply is not None:
            # A clarify or an exhausted-budget fallback — return without advancing;
            # the kiosk re-asks or shows taps on the *same* node.
            return outcome.reply
        # The interpreter proposed a candidate the node's spec allows; it still has
        # to pass `walk.save` below (doc 11 §5 — the rules decide, not the model).
        value = outcome.value

    try:
        saved = await dispatcher.save_answer(payload.node_id, value, raw_text=payload.raw_text)
    except ToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not saved["ok"]:
        return AnswerOut(
            ok=False,
            node_id=payload.node_id,
            complete=False,
            error=saved.get("error"),
            node=_node_out(await dispatcher.get_next_node()),
        )

    nxt = await dispatcher.get_next_node()
    return AnswerOut(
        ok=True,
        node_id=payload.node_id,
        complete=saved["complete"],
        accepted_value=saved.get("value"),
        red_flags=saved.get("red_flags", []),
        node=_node_out(nxt),
    )


@dataclass(slots=True)
class _VoiceOutcome:
    """Either a terminal `AnswerOut` (clarify / fall-back-to-taps), or a candidate
    `value` still to be validated by `walk.save`. Exactly one is set."""

    reply: AnswerOut | None = None
    value: Any = None


#: How many other nodes to offer the interpreter as enrichment targets (doc 11
#: §3). A bound on prompt size, not clinical: the patient rarely volunteers facts
#: for more than a couple of upcoming questions in one breath.
_MAX_ENRICH_TARGETS = 12


def _enrich_targets(dispatcher: Any, current_id: str) -> list[Node]:
    """The other askable, mappable nodes a patient might volunteer facts for.

    Only option/number nodes (the interpreter can only return a value those accept)
    that are not yet answered and are not the current question. Capped for prompt
    size; order is the tree's own, so it is deterministic."""
    answered = set(dispatcher.walk.answers)
    targets = [
        node
        for node in dispatcher.tree.nodes.values()
        if node.id != current_id
        and node.id not in answered
        and (node.type.wants_options or node.type.wants_range)
    ]
    return targets[:_MAX_ENRICH_TARGETS]


async def _interpret_voice_answer(
    interpreter: Interpreter,
    engine: IntakeEngine,
    dispatcher: Any,
    state: SessionState,
    payload: AnswerIn,
) -> _VoiceOutcome:
    """Map a spoken answer onto the current node (doc 11 §2/§3), metered per turn.

    Returns a candidate value to save, or a terminal reply — one spoken clarify /
    adaptive follow-up (first attempt) or a fall-back-to-taps once the budget is
    spent (doc 11 §5). Any facts the patient volunteered for OTHER nodes are
    validated and stashed as pending pre-fills (V2 enrichment); the interpreter
    never advances the walk — only `walk.save` does.
    """
    node = dispatcher.walk.current
    if node is None or node.id != payload.node_id:
        # The client is a screen behind (idle reset, double-post). Don't interpret a
        # stale utterance against a different question — let the kiosk re-render.
        return _VoiceOutcome(reply=await _exhausted(payload.node_id, dispatcher))

    others = _enrich_targets(dispatcher, node.id)
    with usage_scope(
        session_id=state.session_id,
        intake_id=state.intake_id,
        visit_id=state.visit_id,
        channel=Channel.KIOSK,
        tier=state.active_tier,
        voice_profile=state.voice_profile.name.value if state.voice_profile else None,
    ):
        interpretation = await interpreter.interpret(
            node, payload.raw_text or "", state.lang, others=others
        )

    if interpretation.has_value:
        try:
            # The interpreter cannot produce a value the node rejects (doc 11 §5):
            # validate the candidate here, and an out-of-spec proposal becomes a
            # clarify rather than an error. The real save still re-validates.
            validate_answer(node, interpretation.value)
        except AnswerError:
            logger.info(
                "adaptive_intake node=%s outcome=rejected value=%r session=%s",
                node.id,
                interpretation.value,
                state.session_id,
            )
        else:
            enriched = _stash_enrichment(dispatcher, interpretation.extra, current_id=node.id)
            dispatcher._record_adaptive_turn(node.id, "interpreted", enriched=enriched)
            return _VoiceOutcome(value=interpretation.value)

    # A vague answer, an adaptive follow-up, or a candidate the node rejected.
    # One clarify, then taps (doc 11 §5) — never an invented option, never a loop.
    if payload.attempt >= _MAX_CLARIFY_ATTEMPTS or not interpretation.clarify:
        dispatcher._record_adaptive_turn(node.id, "exhausted")
        await engine.store.save(state)
        return _VoiceOutcome(reply=await _exhausted(node.id, dispatcher))
    dispatcher._record_adaptive_turn(node.id, "clarify")
    await engine.store.save(state)
    return _VoiceOutcome(
        reply=AnswerOut(
            ok=False,
            node_id=node.id,
            complete=False,
            clarify=interpretation.clarify,
            node=_node_out(await dispatcher.get_next_node()),
        )
    )


def _stash_enrichment(
    dispatcher: Any, extra: tuple[tuple[str, Any], ...], *, current_id: str
) -> int:
    """Validate each volunteered (node_id, value) and hold it as a pending pre-fill
    (doc 11 §3). Returns how many were accepted. Nothing is written to the walk here
    — the dispatcher auto-applies a pre-fill (through `walk.save`) only when the walk
    actually reaches that node, so an enrichment for a branch never taken stays inert
    and never lands in the summary."""
    accepted = 0
    for node_id, value in extra:
        # Skip the current node (it is being answered now) and anything already
        # answered — enrichment is only for questions still to come.
        if node_id == current_id or node_id in dispatcher.walk.answers:
            continue
        target = dispatcher.tree.nodes.get(node_id)
        if target is None:
            continue
        try:
            normalized = validate_answer(target, value)
        except AnswerError:
            continue
        dispatcher.state.pending_prefills[node_id] = {"value": normalized}
        accepted += 1
    return accepted


async def _exhausted(node_id: str, dispatcher: Any) -> AnswerOut:
    """The voice path gave up on this node; the kiosk keeps its taps (doc 11 §5).

    Nothing was saved, so `get_next_node` still returns the same question — the
    kiosk re-renders it with its taps and drops the mic."""
    return AnswerOut(
        ok=False,
        node_id=node_id,
        complete=False,
        adaptive_exhausted=True,
        node=_node_out(await dispatcher.get_next_node()),
    )


@router.post("/{session_id}/finish", response_model=FinishOut)
async def finish(
    session_id: str,
    engine: IntakeEngine = Depends(get_engine),
) -> FinishOut:
    """Summarise the intake and return the patient read-back (the confirm screen).

    Does not yet allocate a token or finalise cost — the patient has not confirmed
    the read-back. That is `/confirm`.
    """
    return await finish_impl(engine, session_id)


async def finish_impl(
    engine: IntakeEngine, session_id: str, *, expected: tuple[Channel, ...] = (Channel.KIOSK,)
) -> FinishOut:
    state = await _load_state(engine, session_id, expected=expected)
    dispatcher = engine.dispatcher(state)
    result = await dispatcher.finish_and_summarize("complete")
    return FinishOut(
        readback=result["readback"],
        summary_md=result["summary_md"],
        red_flags=result["red_flags"],
        complete=result["complete"],
    )


class AllergyItemIn(BaseModel):
    """One substance, as the patient said it.

    `substance_en` is optional and rides along untranslated when the kiosk has
    nothing to put there — the doc 02 §4 convention is original beside English,
    and a kiosk that machine-translated a drug name to fill the column would be
    inventing the safer-looking half of the record.
    """

    substance: str = Field(min_length=1, max_length=200)
    substance_en: str | None = Field(default=None, max_length=200)


class AllergiesIn(BaseModel):
    """What the patient answered when the kiosk asked.

    Both fields, not one: `none_known=True` with an empty list is the patient
    saying "none", and `none_known=False` with an empty list is a client that
    should not have called this route at all. The service refuses to write the
    second as a negative statement.
    """

    none_known: bool = False
    items: list[AllergyItemIn] = Field(default_factory=list, max_length=20)


class AllergiesOut(BaseModel):
    recorded: int


@router.post("/{session_id}/allergies", response_model=AllergiesOut)
async def record_allergies(
    session_id: str,
    payload: AllergiesIn,
    engine: IntakeEngine = Depends(get_engine),
    session: AsyncSession = Depends(get_session),
) -> AllergiesOut:
    """What the patient said about allergies, on the way to the read-back.

    **Its own route rather than a tree node**, and that is the whole design
    decision. An allergy is not a department's clinical question — it is an
    identity-level fact that has to be asked of the ENT walk-in and the
    palliative review alike, on the tap-only tier, in every language, and during
    an outage. As a node it would have to be authored into all eleven trees,
    where eleven copies would drift, ten of them would be reviewed by nobody, and
    a new tree would ship without it.

    **It writes before the read-back, not at `/confirm`.** A patient who names a
    drug and then walks away from the summary screen has still told this hospital
    something about what she reacts to, and the record should keep it — she may
    be in the queue by a coordinator's hand ten minutes later. Nothing about this
    write is a clinical claim: it is a statement, stored with its source.
    """
    state = await _load_state(engine, session_id)
    if state.visit_id is None:
        # No visit yet means no patient row to hang a statement on. This is a
        # kiosk calling the route before `/start`, which is a client bug —
        # answered as a 409 rather than silently dropping what she said.
        raise HTTPException(status_code=409, detail="this session has no visit yet")

    from app.models.clinical import Intake, Visit

    visit = await session.get(Visit, state.visit_id)
    if visit is None:
        raise HTTPException(status_code=409, detail="this session has no visit yet")

    # Caregiver mode is read off the `Intake` row rather than the session state,
    # which does not carry it. It is what decides whether this is the patient
    # telling us or her son telling us, and the doctor's screen says which.
    intake = await session.get(Intake, state.intake_id) if state.intake_id else None

    written = await allergy_svc.from_intake(
        session,
        patient_id=visit.patient_id,
        visit_id=visit.id,
        caregiver=bool(intake.caregiver_answered) if intake else False,
        none_known=payload.none_known,
        substances=[item.model_dump() for item in payload.items],
    )
    await session.commit()
    return AllergiesOut(recorded=len(written))


async def _apply_destination(
    session: AsyncSession,
    *,
    engine: IntakeEngine,
    state: Any,
    visit: Any,
) -> None:
    """Move the visit to the department these answers ask for, if any.

    The decision itself is `Walk.destination` — deterministic, computed from the
    answers, and it already drops a patient's stated preference when a red flag
    fired. What is left here is the two things only a request can know: whether
    that department exists and is open, and whether it is somewhere else.

    A destination that resolves to nothing is **ignored, not an error**. A tree
    naming a department this hospital does not run (or has since closed) is a
    content problem for the tree bank tests to catch; at 9am with a patient at
    the screen, the right answer is the department she is already in, with a
    token, rather than a failed confirm.
    """
    destination = engine.dispatcher(state).walk.destination()
    if destination is None:
        return
    dept = await kiosk_svc.department_by_code(session, destination)
    if dept is None or dept.id == visit.department_id:
        if dept is None:
            logger.warning(
                "intake %s asked for department %r, which is not open; leaving the visit where "
                "it is",
                state.intake_id,
                destination,
            )
        return
    visit.department_id = dept.id
    await session.flush()


@router.post("/{session_id}/confirm", response_model=ConfirmOut)
async def confirm(
    session_id: str,
    request: Request,
    engine: IntakeEngine = Depends(get_engine),
    session: AsyncSession = Depends(get_session),
) -> ConfirmOut:
    """The patient confirmed the read-back: allocate a token, finalise the cost,
    and put the visit in its department's queue (S8).

    The token screen is the kiosk's last screen (doc 03 §1a). Cost finalisation
    sums this intake's `usage_events` (the classifier's routing call, mostly) onto
    the `Intake` row. Enqueuing is what makes the token appear on the board and
    coordinator console live — an intake with a red flag lands `urgent` and jumps
    the queue (doc 03 §6), with no coordinator action.
    """
    state = await _load_state(engine, session_id)
    state.confirmed = True
    await engine.store.save(state)

    from app.models.clinical import Visit

    # Load the visit up front so `finalize_cost` → `_persist_intake` can resolve
    # `intake.visit` from the identity map without an async lazy-load (which would
    # raise MissingGreenlet). It is also the row `allocate_token` stamps.
    visit = await session.get(Visit, state.visit_id) if state.visit_id is not None else None

    # Drain the batched meter first so the cost sums a complete set of
    # usage_events — the classifier's routing call is metered async, and without a
    # flush finalize_cost would read ₹0 for a call that did cost (STATE.md).
    meter = get_meter()
    if meter is not None:
        await meter.flush()
    # Finalise before enqueuing: it persists `intake.red_flags`
    # (engine._persist_intake), the source the queue reads to decide urgency.
    cost = await engine.finalize_cost(state, session)

    token_no: int | None = None
    department: DeptOut | None = None
    enqueued = False
    if visit is not None:
        # Where these answers say this patient belongs (doc 24 §4/§5) — the
        # ayurveda OPD she asked for, or the chest clinic a TB-suspect rule
        # names. Applied *before* the token, because the number is per
        # department and a token issued in the wrong series is a queue the
        # patient is not in. Nothing is reissued and no coordinator has to
        # intervene: at this point in the walk there is no number yet.
        await _apply_destination(session, engine=engine, state=state, visit=visit)
        token_no = await kiosk_svc.allocate_token(session, visit)
        intake = await session.get(kiosk_svc.Intake, state.intake_id)
        if intake is not None:
            await queue_svc.enqueue_from_intake(session, visit=visit, intake=intake)
            enqueued = True
        dept = await session.get(kiosk_svc.Department, visit.department_id)
        if dept is not None:
            department = DeptOut(key=dept.code, name=dept.name, care_system=dept.care_system)

    if enqueued:
        await session.commit()  # commit before broadcasting so re-fetches see it
        hub: QueueHub | None = getattr(request.app.state, "queue_hub", None)
        if hub is not None:
            await hub.notify_queue_changed()

    return ConfirmOut(
        token_no=token_no,
        department=department,
        red_flags=state.red_flags,
        cost_inr=str(cost) if cost is not None else None,
    )


# -- server STT (local Whisper on a V-OSS box) --------------------------------


class SttOut(BaseModel):
    text: str
    provider: str
    lang: str
    confidence: float | None = None
    #: True when the transcript is below the confidence floor (doc 03 §4) — the
    #: kiosk should offer the tap-to-type correction rather than trust it silently.
    uncertain: bool = False


#: A kiosk chief complaint is a few seconds of audio; anything much larger is a
#: broken client or abuse, and the box's Whisper should not be handed a huge blob.
_MAX_STT_BYTES = 8 * 1024 * 1024


@router.post("/stt", response_model=SttOut)
async def stt(
    file: UploadFile = File(...),
    lang: Lang = Form(Lang.HI),
    duration_seconds: str | None = Form(default=None),
    session_id: str | None = Form(default=None),
    engine: IntakeEngine = Depends(get_engine),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SttOut:
    """Server-side speech-to-text for the kiosk chief complaint (doc 03 §1a).

    The kiosk records the spoken complaint (MediaRecorder) and posts the clip
    here; we transcribe it through the configured STT chain. On a V-OSS box that
    is `local_whisper`, so the audio never leaves the premises — unlike the
    browser Web Speech path, which ships it to a cloud recogniser. Keeping this
    boring and unauthenticated on purpose: a public terminal carries no
    credential, and the clip is an anonymous chief complaint, not a stored record.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="empty audio upload")
    if len(data) > _MAX_STT_BYTES:
        raise HTTPException(status_code=413, detail="audio clip too large")

    # The browser knows the recording length; trust it for metering (the clip is
    # webm/opus, whose duration the server can't derive without transcoding).
    # Absent or unparseable, `duration()` falls to 0 — unpriced usage is visible
    # on the S18 dashboard, an invented duration is an invented rupee amount.
    duration: Decimal | None = None
    if duration_seconds:
        try:
            duration = Decimal(duration_seconds)
        except (InvalidOperation, ValueError):
            duration = None

    clip = AudioClip(data=data, mime=file.content_type or "audio/webm", duration_seconds=duration)
    settings = await effective_settings(session, settings)
    state = await _load_state(engine, session_id) if session_id else None
    metered_profile: str | None = None
    if state is not None and state.voice_profile is not None:
        providers = list(resolve_profile(state.voice_profile, settings).stt)
        metered_profile = state.voice_profile.name.value
    elif settings.stt_provider == "fake":
        providers = stt_chain(settings)
    else:
        channel_config = await resolve_config(session)
        selected = snapshot_profile(channel_config.kiosk_voice_profile, settings)
        providers = list(resolve_profile(selected, settings).stt)
        metered_profile = selected.name.value
    try:
        with usage_scope(
            session_id=state.session_id if state else None,
            intake_id=state.intake_id if state else None,
            visit_id=state.visit_id if state else None,
            channel=Channel.KIOSK,
            voice_profile=metered_profile,
        ):
            transcript = await with_fallback(
                providers,
                lambda p: p.transcribe(clip, str(lang), purpose=UsagePurpose.INTAKE_TURN),
            )
    except ProviderBadRequest as exc:
        raise HTTPException(status_code=422, detail=f"could not read that audio: {exc}") from exc
    except ProviderError as exc:
        # The kiosk always has tap-to-type behind this (doc 04 law 8); a 503 tells
        # it to show that fallback rather than blame the patient.
        raise HTTPException(status_code=503, detail="speech recognition is unavailable") from exc

    # The script guard (doc 03 §1, `app.languages`). A recogniser handed Hindi
    # audio can return Urdu script — the same words, in an alphabet this patient
    # very likely cannot read. We may not transliterate it: inventing characters
    # over a clinical complaint is exactly what the rest of this system refuses
    # to do with drug names. So the transcript is dropped and the kiosk falls
    # back to tap-to-type, which is the deterministic floor doc 04 law 8 already
    # requires to be present on this screen.
    problem = script_problem(transcript.text, lang)
    if problem:
        logger.warning("kiosk STT rejected: %s (provider %s)", problem, transcript.provider)
        return SttOut(
            text="",
            provider=transcript.provider,
            lang=str(lang),
            confidence=transcript.confidence,
            uncertain=True,
        )

    return SttOut(
        text=transcript.text,
        provider=transcript.provider,
        lang=transcript.lang,
        confidence=transcript.confidence,
        uncertain=transcript.is_uncertain,
    )


# -- server TTS (local/Voicebox "Dhara" voice on a V-OSS box) -----------------


class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    lang: Lang = Lang.HI
    session_id: str | None = None


class TtsOut(BaseModel):
    #: base64-encoded audio (WAV from the local/Voicebox engine) for the kiosk to
    #: play. Base64 rather than raw bytes so the read-aloud carries the provider +
    #: voice alongside it (the kiosk logs which voice spoke; batch tooling reuses it).
    audio: str
    mime: str
    sample_rate: int
    provider: str
    voice: str


#: The kiosk reads a question or the summary read-back — a couple of sentences.
#: A higher rate than the 8 kHz telephony default: this is browser playback, and
#: the branded Dhara voice should sound natural, not like a phone line.
_KIOSK_TTS_SAMPLE_RATE = 24000


@router.post("/tts", response_model=TtsOut)
async def tts(
    payload: TtsIn,
    engine: IntakeEngine = Depends(get_engine),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TtsOut:
    """Server-side text-to-speech for the kiosk read-aloud (doc 03 §1a, doc 10 §6).

    Mirrors `/kiosk/stt`: on a V-OSS box the configured TTS chain is Voicebox (or a
    local `/tts` service) speaking the cloned **Dhara** voice, so the read-aloud is
    on-premises and one identity across every channel — instead of the browser's
    SpeechSynthesis. A single multilingual Dhara clone covers both English and
    Hindi (the languages the pilot reads aloud); the vendor picks the accent from
    `lang`. Unauthenticated for the same reason as STT: a public terminal carries
    no credential, and the text is a clinical prompt, not a stored record. The
    kiosk keeps the browser voice behind this (flag off / offline / on error).
    """
    settings = await effective_settings(session, settings)
    state = await _load_state(engine, payload.session_id) if payload.session_id else None
    metered_profile: str | None = None
    if state is not None and state.voice_profile is not None:
        providers = list(resolve_profile(state.voice_profile, settings).tts)
        metered_profile = state.voice_profile.name.value
    elif settings.tts_provider == "fake":
        providers = tts_chain(settings)
    else:
        channel_config = await resolve_config(session)
        selected = snapshot_profile(channel_config.kiosk_voice_profile, settings)
        providers = list(resolve_profile(selected, settings).tts)
        metered_profile = selected.name.value
    try:
        with usage_scope(
            session_id=state.session_id if state else None,
            intake_id=state.intake_id if state else None,
            visit_id=state.visit_id if state else None,
            channel=Channel.KIOSK,
            voice_profile=metered_profile,
        ):
            speech = await with_fallback(
                providers,
                lambda p: p.synthesize(
                    payload.text,
                    str(payload.lang),
                    sample_rate=_KIOSK_TTS_SAMPLE_RATE,
                    purpose=UsagePurpose.INTAKE_TURN,
                ),
            )
    except ProviderBadRequest as exc:
        raise HTTPException(
            status_code=422, detail=f"could not synthesize that text: {exc}"
        ) from exc
    except ProviderError as exc:
        # The kiosk always has the browser voice behind this (doc 04 law 1); a 503
        # tells it to fall back rather than sit silent.
        raise HTTPException(status_code=503, detail="speech synthesis is unavailable") from exc

    return TtsOut(
        audio=speech.audio.b64(),
        mime=speech.audio.mime,
        sample_rate=speech.audio.sample_rate,
        provider=speech.provider,
        voice=speech.voice,
    )


# -- offline (S7, doc 01 §5) --------------------------------------------------


class BundleTreeOut(BaseModel):
    department_key: str | None
    #: The canonical tree (`Tree.to_json`) — already validated and desugared, so
    #: the offline walker is a walker only. See app/trees/schema.py.
    tree: dict[str, Any]


class BundleHospitalOut(BaseModel):
    """What this hospital calls itself, as the kiosk must render it (AYUR-1).

    Doc 24 §3.2 says the letterhead "already reads stored hospital facts, so
    'Ayurveda Hospital' propagates for free — verify with the pass and Rx print
    tests, **don't assume**." It did not. The prescription letterhead does read
    `Hospital.name`; the kiosk's brand bar and the intake boarding pass rendered
    a four-language constant compiled into the bundle
    (`_lib/i18n.ts`, key `hospital`) that had already drifted from the seeded
    name. So an admin renaming the hospital would have changed the prescription
    and not the paper the patient is handed at the kiosk door.

    It rides on the bundle rather than on `POST /kiosk/start` because the brand
    bar is drawn before any intake begins and the pass must still print with the
    name during an outage — the bundle is the kiosk's offline memory, and this
    is a fact it needs to have cached.
    """

    #: English, and the fallback for a language with no translation.
    name: str
    #: `{lang: name}` — the whole map, not the one language this request is in,
    #: because the kiosk switches language client-side with no server round trip
    #: and must not be caught mid-outage holding only the wrong one.
    name_i18n: dict[str, str]
    city: str | None


class BundleOut(BaseModel):
    #: Changes whenever the content does; the kiosk re-downloads only on a change.
    etag: str
    generated_at: datetime
    hospital: BundleHospitalOut
    departments: list[DeptOut]
    trees: list[BundleTreeOut]


@router.get("/bundle", response_model=BundleOut)
async def bundle(
    session: AsyncSession = Depends(get_session),
    response: Response = None,  # type: ignore[assignment]
) -> BundleOut:
    """Everything the kiosk needs to run with no server (doc 01 §5).

    Fetched while the network is up and kept in IndexedDB. It is the trees plus
    the department chooser, because those are the two things an offline intake
    cannot do without: the walk is deterministic given a tree, and offline there
    is no classifier, so the patient picks the department by hand.

    The trees are the **canonical** form, not the authored one — already parsed,
    validated and desugared by `parse()`. That is what keeps the offline TS
    walker from having to re-implement the validator, which is the whole reason
    it can be trusted (see app/tree_fixtures.py).
    """
    departments = await kiosk_svc._departments(session)
    hospital = await facility.identity(session)
    # Pruned before it is packed, not filtered when it is drawn (doc 24 §5): a
    # question offering a closed department never reaches the kiosk's IndexedDB,
    # so an outage cannot surface one. `departments` is already in the ETag
    # below, which is what makes opening Ayurveda invalidate yesterday's pack.
    open_codes = {d.code for d in departments}
    trees = [
        tree_visibility.for_active(tree, open_codes).to_json()
        for tree in sorted(bank.load_bank().values(), key=lambda t: t.key)
    ]

    # Content-addressed: the kiosk sends If-None-Match and skips the download
    # when nothing changed. A tree edit (S18) or a department rename changes it.
    payload = json.dumps(
        {
            # The hospital's name is in the hash for the same reason a
            # department's is: it is drawn on the brand bar and printed on the
            # boarding pass, so a rename in the admin console must invalidate a
            # cached bundle or the kiosk keeps handing out paper with the old
            # name on it through the next outage.
            "hospital": (hospital.name, sorted(hospital.name_i18n.items()), hospital.city),
            # `care_system` is in the hash on purpose: it changes how the kiosk
            # draws the card, so a department switching system must invalidate
            # a cached bundle exactly the way a rename does.
            "departments": [(d.code, d.name, str(d.care_system)) for d in departments],
            "trees": trees,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    etag = hashlib.sha256(payload.encode()).hexdigest()[:32]
    if response is not None:
        response.headers["ETag"] = f'"{etag}"'
        # The kiosk must not serve a tree from the HTTP cache without asking —
        # a stale tree is a stale clinical question. Revalidate every time; the
        # ETag makes that nearly free, and the service worker holds the real
        # offline copy.
        response.headers["Cache-Control"] = "no-cache"

    return BundleOut(
        etag=etag,
        generated_at=datetime.now(UTC),
        hospital=BundleHospitalOut(
            name=hospital.name, name_i18n=hospital.name_i18n, city=hospital.city
        ),
        departments=[
            DeptOut(key=d.code, name=d.name, care_system=d.care_system) for d in departments
        ],
        trees=[BundleTreeOut(department_key=tree.get("department"), tree=tree) for tree in trees],
    )


class BlockOut(BaseModel):
    department: DeptOut
    start_no: int
    end_no: int
    #: The highest number the *server* knows this kiosk has issued. The kiosk's
    #: own store is ahead of this during an outage — it is a resume hint after a
    #: reboot, not an instruction.
    used_up_to: int | None
    next_free: int


class LeaseOut(BaseModel):
    kiosk_id: str
    date: str
    blocks: list[BlockOut]


class SyncIntakeIn(BaseModel):
    #: The kiosk's id for this intake; the idempotency key (see `app.offline`).
    client_id: str = Field(min_length=8, max_length=64)
    department_key: str
    tree_key: str
    lang: Lang
    token_no: int
    #: `{node_id: {value, text, text_en, lang, at}}` — the walker's shape, from
    #: the offline TS walker. The server re-walks it; red flags are recomputed
    #: here and the kiosk's own list is never read.
    answers: dict[str, Any]
    chief_complaint: str | None = None
    caregiver: bool = False
    patient_name: str | None = Field(default=None, max_length=200)
    patient_age: int | None = Field(default=None, ge=0, le=200)
    patient_sex: str | None = Field(default=None, max_length=16)
    patient_phone: str | None = Field(default=None, max_length=24)
    #: The health ID from the arrival screen (AR3). An offline kiosk cannot look
    #: up a prior file — there is no server to ask — but the patient still typed
    #: this, so it rides along and the lookup happens here, at sync.
    patient_external_id: str | None = Field(default=None, max_length=64)
    completed_at: datetime | None = None
    #: What she said when the kiosk asked about allergies during the outage
    #: (SESSION-ALLERGY): `{none_known: bool, items: [{substance, substance_en}]}`.
    #: `None` — the field absent entirely — means this kiosk build never asked,
    #: which is not the same as a patient who said none, and must not be written
    #: as one. An older kiosk that has not been updated lands here.
    allergies: dict[str, Any] | None = None


class SyncIn(BaseModel):
    kiosk_id: str = Field(min_length=1, max_length=64)
    intakes: list[SyncIntakeIn] = Field(max_length=200)


class SyncResultOut(BaseModel):
    client_id: str
    #: "synced" | "duplicate" | "rejected"
    status: str
    token_no: int | None = None
    red_flags: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class SyncOut(BaseModel):
    results: list[SyncResultOut]
    synced: int
    duplicates: int
    rejected: int


@router.post("/blocks/lease", response_model=LeaseOut)
async def lease_blocks(
    kiosk_id: str,
    session: AsyncSession = Depends(get_session),
) -> LeaseOut:
    """Lease this kiosk's offline token blocks for today (doc 01 §5).

    Called while the network is *up* — that is the whole point. The kiosk holds
    one block per department (offline it cannot classify, so the patient picks
    from the chooser and any department may be needed) and consumes them from
    IndexedDB during an outage.

    Idempotent: re-leasing returns the same ranges. It never hands out a fresh
    one, because the old one is already on paper slips in patients' hands.
    """
    try:
        blocks = await offline_svc.lease_blocks(session, kiosk_id=kiosk_id)
    except offline_svc.OfflineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return LeaseOut(
        kiosk_id=kiosk_id,
        date=offline_svc.today().isoformat(),
        blocks=[
            BlockOut(
                department=DeptOut(
                    key=block.department_key,
                    name=block.department_name,
                    care_system=block.department_care_system,
                ),
                start_no=block.start_no,
                end_no=block.end_no,
                used_up_to=block.used_up_to,
                next_free=block.next_free,
            )
            for block in blocks
        ],
    )


@router.post("/sync", response_model=SyncOut)
async def sync(
    payload: SyncIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SyncOut:
    """Take back the intakes a kiosk completed while the API was unreachable.

    Per-intake results rather than all-or-nothing: one bad intake in a batch of
    twenty must not strand the other nineteen on a kiosk, and the kiosk needs to
    know exactly which ones to stop retrying. A `duplicate` is a success — it
    means an earlier attempt landed before the network dropped again.
    """
    results: list[SyncResultOut] = []
    for item in payload.intakes:
        outcome = await offline_svc.sync_intake(
            session,
            kiosk_id=payload.kiosk_id,
            client_id=item.client_id,
            department_key=item.department_key,
            tree_key=item.tree_key,
            lang=item.lang,
            token_no=item.token_no,
            answers=item.answers,
            chief_complaint=item.chief_complaint,
            caregiver=item.caregiver,
            patient_name=item.patient_name,
            patient_age=item.patient_age,
            patient_sex=item.patient_sex,
            patient_phone=item.patient_phone,
            patient_external_id=item.patient_external_id,
            completed_at=item.completed_at,
            allergies=item.allergies,
        )
        results.append(
            SyncResultOut(
                client_id=outcome.client_id,
                status=outcome.status,
                token_no=outcome.token_no,
                red_flags=outcome.red_flags or [],
                error=outcome.error,
            )
        )

    synced = sum(1 for r in results if r.status == "synced")
    if synced:
        # A drill/outage recovery just put tokens on the record; nudge the board
        # and coordinator console so the reconciliation list and queue go live.
        await session.commit()
        hub: QueueHub | None = getattr(request.app.state, "queue_hub", None)
        if hub is not None:
            await hub.notify_queue_changed()

    return SyncOut(
        results=results,
        synced=synced,
        duplicates=sum(1 for r in results if r.status == "duplicate"),
        rejected=sum(1 for r in results if r.status == "rejected"),
    )


# -- the coordinator's staff strip --------------------------------------------
#
# The kiosk's last screen belongs to the patient: token, department, red-flag
# instruction, all spoken. These routes drive the strip *below* it, which is
# locked until a coordinator enters their PIN and relocks on idle.
#
# The pilot runs one kiosk with one coordinator standing at it, which is what
# makes a screen-side staff control workable at all. Two things are settled here
# in one action: whether this arrival is the returning patient the arrival screen
# matched, and which doctor is going to see them.


class PinHolderOut(BaseModel):
    """A coordinator who can unlock this kiosk. Name only — nothing contactable."""

    id: uuid.UUID
    name: str


class UnlockIn(BaseModel):
    user_id: uuid.UUID
    pin: str = Field(min_length=1, max_length=16)


class UnlockOut(BaseModel):
    token: str
    expires_at: datetime
    name: str


class CandidateOut(BaseModel):
    """The possible prior file. Returned **only** behind the PIN."""

    patient_id: uuid.UUID
    name: str
    mrn: str
    age: int | None = None
    sex: str | None = None
    external_id: str | None = None
    last_visit_on: date_type | None = None


class StripDoctorOut(BaseModel):
    id: uuid.UUID
    name: str
    qualification: str | None = None
    on_duty: bool


class StripOut(BaseModel):
    visit_id: uuid.UUID
    token_no: int | None = None
    department_key: str
    department_name: str
    departments: list[DeptOut]
    doctors: list[StripDoctorOut]
    #: Pre-selected when exactly one doctor is on duty; null when it is a real
    #: choice, because an unnoticed default is how a patient lands on the wrong list.
    default_doctor_id: uuid.UUID | None = None
    assigned_doctor_id: uuid.UUID | None = None
    link_state: str
    candidate: CandidateOut | None = None


class AssignIn(BaseModel):
    #: True confirms the candidate is the same person; False records that a human
    #: looked and it is not. Null leaves the question open for the console.
    link_candidate: bool | None = None
    department_key: str | None = None
    doctor_id: uuid.UUID | None = None


class AssignOut(BaseModel):
    visit_id: uuid.UUID
    department_key: str
    department_name: str
    assigned_doctor_id: uuid.UUID | None = None
    assigned_doctor_name: str | None = None
    link_state: str
    patient_name: str | None = None
    token_no: int | None = None
    #: Set when a department change reissued the number. The coordinator has to
    #: hand the patient this one — their printed slip is now stale.
    previous_token_no: int | None = None
    token_reissued: bool = False


@router.get("/staff/holders", response_model=list[PinHolderOut])
async def staff_holders(session: AsyncSession = Depends(get_session)) -> list[PinHolderOut]:
    """Who can unlock this kiosk.

    Unauthenticated because it has to be: the strip cannot ask who you are after
    you have identified yourself. It discloses staff *names* — which are on the
    badges of the people standing in the same corridor — and nothing else. No
    phone, no role, no id beyond the opaque one the unlock call needs.
    """
    from app.auth.kiosk_pin import PIN_ROLES
    from app.models.org import User

    users = await session.scalars(
        select(User)
        .where(
            User.kiosk_pin_hash.is_not(None),
            User.role.in_(tuple(PIN_ROLES)),
            User.active.is_(True),
            User.deleted_at.is_(None),
        )
        .order_by(User.name)
    )
    return [PinHolderOut(id=u.id, name=u.name) for u in users]


@router.post("/staff/unlock", response_model=UnlockOut)
async def staff_unlock(
    payload: UnlockIn,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UnlockOut:
    """Exchange a coordinator's PIN for a narrow, short-lived kiosk token."""
    from app.auth import kiosk_pin as kp
    from app.models.org import User

    user = await session.get(User, payload.user_id)
    if user is None or user.deleted_at is not None:
        # Same shape as a wrong PIN: the strip must not become a way to probe
        # which staff ids exist.
        raise HTTPException(status_code=401, detail="that PIN was not recognised")
    try:
        issued = await kp.verify_pin(session, user=user, pin=payload.pin, settings=settings)
    except kp.PinLocked as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except kp.PinError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    await session.commit()
    return UnlockOut(token=issued.token, expires_at=issued.expires_at, name=user.name)


async def _strip_visit(engine: IntakeEngine, session: AsyncSession, session_id: str) -> Visit:
    state = await _load_state(engine, session_id)
    if state.visit_id is None:  # pragma: no cover - a started session always has one
        raise HTTPException(status_code=409, detail="this session has no visit yet")
    visit = await session.get(Visit, state.visit_id)
    if visit is None or visit.deleted_at is not None:  # pragma: no cover - FK-guaranteed
        raise HTTPException(status_code=404, detail="no such visit")
    return visit


@router.get("/{session_id}/strip", response_model=StripOut)
async def read_strip(
    session_id: str,
    principal: Principal = Depends(require_kiosk_staff),
    engine: IntakeEngine = Depends(get_engine),
    session: AsyncSession = Depends(get_session),
) -> StripOut:
    """What the unlocked strip shows: the possible match, and who is in clinic."""
    visit = await _strip_visit(engine, session, session_id)
    dept = await session.get(kiosk_svc.Department, visit.department_id)
    if dept is None:  # pragma: no cover - FK-guaranteed
        raise HTTPException(status_code=404, detail="no such department")

    departments = await kiosk_svc._departments(session)
    options = await assignment_svc.assignable_doctors(session, department_id=dept.id, on=visit.date)
    default = assignment_svc.default_doctor(options)

    candidate: CandidateOut | None = None
    if visit.candidate_patient_id is not None:
        prior = await session.get(Patient, visit.candidate_patient_id)
        if prior is not None and prior.deleted_at is None:
            last = await session.scalar(
                select(func.max(Visit.date)).where(
                    Visit.patient_id == prior.id,
                    Visit.id != visit.id,
                    Visit.deleted_at.is_(None),
                )
            )
            candidate = CandidateOut(
                patient_id=prior.id,
                name=prior.name,
                mrn=prior.mrn,
                age=prior.age,
                sex=str(prior.sex) if prior.sex else None,
                external_id=prior.external_id,
                last_visit_on=last,
            )

    return StripOut(
        visit_id=visit.id,
        token_no=visit.token_no,
        department_key=dept.code,
        department_name=dept.name,
        departments=[
            DeptOut(key=d.code, name=d.name, care_system=d.care_system) for d in departments
        ],
        doctors=[
            StripDoctorOut(id=o.id, name=o.name, qualification=o.qualification, on_duty=o.on_duty)
            for o in options
        ],
        default_doctor_id=default.id if default else None,
        assigned_doctor_id=visit.doctor_id,
        link_state=str(visit.patient_link_state),
        candidate=candidate,
    )


@router.post("/{session_id}/assign", response_model=AssignOut)
async def assign_from_strip(
    session_id: str,
    payload: AssignIn,
    request: Request,
    principal: Principal = Depends(require_kiosk_staff),
    engine: IntakeEngine = Depends(get_engine),
    session: AsyncSession = Depends(get_session),
) -> AssignOut:
    """Settle identity and assignment in one action.

    Order matters: the link is resolved first, so a confirmed match means the
    doctor is assigned against the patient's real file rather than the throwaway
    walk-in row.
    """
    visit = await _strip_visit(engine, session, session_id)

    try:
        if payload.link_candidate is True:
            await assignment_svc.confirm_link(session, visit=visit)
        elif payload.link_candidate is False:
            await assignment_svc.reject_link(session, visit=visit)

        department = None
        if payload.department_key:
            department = await kiosk_svc.department_by_code(session, payload.department_key)
            if department is None:
                raise HTTPException(status_code=422, detail="no such department")

        result = await assignment_svc.assign(
            session, visit=visit, doctor_id=payload.doctor_id, department=department
        )
    except assignment_svc.AssignmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    dept = await session.get(kiosk_svc.Department, visit.department_id)
    doctor = await session.get(Doctor, visit.doctor_id) if visit.doctor_id else None
    patient = await session.get(Patient, visit.patient_id)

    await session.commit()
    # The board shows the token and the department; both may have just changed.
    hub: QueueHub | None = getattr(request.app.state, "queue_hub", None)
    if hub is not None:
        await hub.notify_queue_changed()

    return AssignOut(
        visit_id=visit.id,
        department_key=dept.code if dept else "",
        department_name=dept.name if dept else "",
        assigned_doctor_id=doctor.id if doctor else None,
        assigned_doctor_name=doctor.name if doctor else None,
        link_state=str(visit.patient_link_state),
        patient_name=patient.name if patient else None,
        token_no=result.new_token_no,
        previous_token_no=result.old_token_no if result.token_reissued else None,
        token_reissued=result.token_reissued,
    )


# -- helpers ------------------------------------------------------------------


def _node_out(result: dict[str, Any]) -> NodeOut | None:
    """The dispatcher's `get_next_node` result → the wire node, or None if done."""
    if result.get("complete") or result.get("node") is None:
        return None
    node = result["node"]
    return NodeOut(
        id=node["id"],
        type=node["type"],
        text=node["text"],
        options=node["options"],
        min=node.get("min"),
        max=node.get("max"),
        unit=node.get("unit"),
        audio=node.get("audio"),
        summary_role=node.get("summary_role"),
        remaining=node.get("remaining"),
        voice_input=bool(node.get("voice_input", False)),
    )
