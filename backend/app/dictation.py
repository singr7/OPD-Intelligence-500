"""Doctor dictation → structured fields (doc 03 §7).

> "LLM maps transcript → structured: diagnosis/impression, treatment_events,
> meds[] (validated against a formulary list w/ fuzzy match; unknowns flagged,
> never auto-corrected), advice, follow_up. Doctor reviews mapped fields
> (diff-style, tap to fix), **signs**." — doc 03 §7

The doctor speaks Hinglish at the end of a consult; a model turns that into
fields; the doctor reads the fields and signs them. This module owns the middle
and, more importantly, owns what the model is *not* allowed to decide.

## Three things the model does not get to decide

1. **Whether a drug exists.** The prompt asks for a `known` flag so the model
   thinks about it, and then `validate_meds` throws that answer away and asks
   `app.formulary` — exact match only. A model's `known: true` on a hallucinated
   drug is exactly the failure mode that ends up in a prescription.
2. **What a drug is called.** `name` is carried verbatim to the signed record.
   Nothing in this file rewrites it; the formulary's near-misses ride alongside
   as `suggestions`, for the review screen. And because a rename happens inside
   the model where we cannot see it, every name is checked back against the
   doctor's own words — see `_was_said`, which is the only thing in this system
   that can catch "Vinblastin" having become "vinblastine" on the way in.
3. **What the record says.** `mapped` (the model's output) is written once and
   never edited. The doctor's corrections go into `fields`, with an append-only
   `edits` trail. The console diffs the two — that is what makes the review
   "diff-style" rather than "here is some text, trust it".

## The adapter seam

`DictationMapper` takes an `LLMProvider` chain and nothing else, exactly like
`LLMSummarizer` (S5). So the pilot's cloud Gemini Flash and the on-prem Qwen3
served by vLLM are the same code path, chosen by `LLM_PROVIDER` — `gemini` or
`local_vllm` (S-OSS.0, doc 08). Dictation is the most private text in the
system; being able to move it onto the box by changing one setting is the point.

## Why there is no offline fallback here

The intake summarizer degrades to a deterministic template when the model is
down, because an intake must complete without a network (V3, doc 02 §5). Mapping
a free-form Hinglish paragraph into drug lines has no deterministic floor — a
template that guessed would be inventing a prescription. So when the chain is
down, mapping fails loudly, the transcript stays safe on the draft, and the
doctor retries or types. Degrade where there is something honest to degrade to.

## Signing

Signing locks the record: `status=signed`, and every mutating entry point in
this module refuses afterwards. It also requires that every flagged med — one
the formulary does not recognise, or one whose name does not appear in what the
doctor actually said (`_was_said`) — has been **acknowledged**. That is not a
formality. An incomplete formulary is normal, so "unknown" must stay signable;
but it has to be an act, not a default. A flag that can be cleared by not
noticing it is a flag that trains people to ignore flags.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import formulary as formulary_mod
from app.care_system import DEFAULT_CAPABILITIES, CareSystemCapabilities
from app.models.clinical import Dictation, Intake, Visit
from app.models.enums import DictationStatus, UsagePurpose
from app.models.org import Doctor
from app.models.patient import Patient
from app.prompts import load_packed
from app.providers import LLMProvider, LLMRequest, ProviderError, with_fallback

logger = logging.getLogger(__name__)

#: Bump when the shape of `Dictation.structured` changes. Stored on the row so a
#: reader (S11's prescription, S21's export) can tell an old record from a new one.
STRUCTURED_VERSION = 1

#: Pinned, not "latest": a prompt edit must not quietly change how live
#: dictations map (see `app.prompts.loader`).
PROMPT_VERSION = 1

#: The fields "tap to fix" may replace. `assessment` and `pathya_apathya` are
#: here because they are *only* ever written this way — no model produces them
#: (doc 24 §6.1), so the doctor typing is the only path in.
_EDITABLE_TOP_LEVEL = {
    "diagnosis",
    "advice",
    "follow_up",
    "treatment_events",
    "meds",
    "unclear",
    "assessment",
    "pathya_apathya",
}


class DictationError(Exception):
    """The caller may not do this to this dictation."""


class DictationLocked(DictationError):
    """It is signed. A signed clinical record does not change (doc 03 §7)."""


class MappingUnavailable(DictationError):
    """The LLM chain is down. The transcript is kept; nothing is invented."""


# -- the structured contract --------------------------------------------------


@dataclass(frozen=True, slots=True)
class MedLine:
    """One prescribed drug. `name` is what was said, forever.

    `known` / `generic` / `suggestions` / `ambiguous` are this system's verdict
    from `app.formulary`, not the model's claim. `acknowledged` is the doctor's
    explicit "yes, I meant that, it is not on our list" — required to sign.
    """

    name: str
    dose: str | None = None
    route: str | None = None
    freq: str | None = None
    duration: str | None = None
    as_spoken: str = ""
    known: bool = False
    generic: str | None = None
    drug_class: str | None = None
    ambiguous: bool = False
    suggestions: tuple[dict[str, Any], ...] = ()
    #: The name does not appear in what the doctor said — the model either
    #: renamed the drug or invented it. See `_was_said`.
    unsaid: bool = False
    acknowledged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dose": self.dose,
            "route": self.route,
            "freq": self.freq,
            "duration": self.duration,
            "as_spoken": self.as_spoken,
            "known": self.known,
            "generic": self.generic,
            "drug_class": self.drug_class,
            "ambiguous": self.ambiguous,
            "suggestions": [dict(s) for s in self.suggestions],
            "unsaid": self.unsaid,
            "acknowledged": self.acknowledged,
        }


@dataclass(frozen=True, slots=True)
class TreatmentEvent:
    """A chemo cycle as dictated. `as_spoken` keeps the doctor's own phrasing so
    a resolved date ("14 tareekh" → 2026-08-14) stays auditable."""

    cycle: int | None = None
    regimen: str = ""
    date: str | None = None
    next_due: str | None = None
    as_spoken: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "regimen": self.regimen,
            "date": self.date,
            "next_due": self.next_due,
            "as_spoken": self.as_spoken,
        }


@dataclass(frozen=True, slots=True)
class FollowUp:
    when: str | None = None
    as_spoken: str = ""
    instructions: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"when": self.when, "as_spoken": self.as_spoken, "instructions": self.instructions}


@dataclass(frozen=True, slots=True)
class AyurvedaAssessment:
    """The doctor's own clinical judgement, in their own words (doc 24 §6.1).

    Five optional free-text lines that an ayurveda OPD records and an allopathic
    one does not: prakriti (constitution), vikriti (the dosha involvement in
    *this* illness), agni (digestive fire), koshtha (bowel tendency) and nidana
    (what is aggravating it).

    **Nothing here is ever machine-decided.** The prompts are told in as many
    words never to infer a prakriti or a dosha, and there is no code path that
    writes these from a model's output: they arrive only through
    `apply_corrections`, which is the doctor typing. That is the same rule doc 24
    §4 states for dosha language generally — presentation and record, never
    triage — and it is why these are plain strings with no vocabulary attached.
    A constitution is decided by examining a patient, and the model has not.

    Free text rather than an enum of the seven prakriti types on purpose: an
    enum here would be a dropdown a doctor has to make their judgement fit, and
    the honest answers include "vata-pitta, pitta rising" and "unclear today".
    """

    prakriti: str = ""
    vikriti: str = ""
    agni: str = ""
    koshtha: str = ""
    nidana: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "prakriti": self.prakriti,
            "vikriti": self.vikriti,
            "agni": self.agni,
            "koshtha": self.koshtha,
            "nidana": self.nidana,
        }

    @property
    def recorded(self) -> bool:
        """Whether the doctor wrote anything at all. An assessment nobody filled
        in is not rendered, in the console or on the print — an empty labelled
        block on a prescription reads as a finding of "normal"."""
        return any((self.prakriti, self.vikriti, self.agni, self.koshtha, self.nidana))

    @classmethod
    def parse(cls, payload: Any) -> AyurvedaAssessment:
        if not isinstance(payload, Mapping):
            return cls()
        return cls(
            prakriti=_text(payload.get("prakriti")),
            vikriti=_text(payload.get("vikriti")),
            agni=_text(payload.get("agni")),
            koshtha=_text(payload.get("koshtha")),
            nidana=_text(payload.get("nidana")),
        )


@dataclass(frozen=True, slots=True)
class DictationMapping:
    """doc 03 §7's structured contract. Built only through `parse`."""

    diagnosis: str | None = None
    treatment_events: tuple[TreatmentEvent, ...] = ()
    meds: tuple[MedLine, ...] = ()
    advice: tuple[str, ...] = ()
    follow_up: FollowUp = field(default_factory=FollowUp)
    unclear: tuple[str, ...] = ()
    #: doc 24 §6.1. Empty for every allopathic consult, which is what makes this
    #: additive: the console does not render it, `to_dict` writes an empty block,
    #: and no existing note changes shape in a way anything reads.
    assessment: AyurvedaAssessment = field(default_factory=AyurvedaAssessment)
    #: doc 24 §6.2 — the diet & lifestyle lines, `advice`-shaped and kept apart
    #: from it because they print under their own heading and in an ayurveda
    #: consult they are half of the treatment rather than a footnote to it.
    pathya_apathya: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis": self.diagnosis,
            "treatment_events": [e.to_dict() for e in self.treatment_events],
            "meds": [m.to_dict() for m in self.meds],
            "advice": list(self.advice),
            "follow_up": self.follow_up.to_dict(),
            "unclear": list(self.unclear),
            "assessment": self.assessment.to_dict(),
            "pathya_apathya": list(self.pathya_apathya),
        }

    @property
    def unknown_meds(self) -> tuple[MedLine, ...]:
        return tuple(m for m in self.meds if not m.known)

    @property
    def meds_needing_attention(self) -> tuple[MedLine, ...]:
        """What signing refuses on until acknowledged: a drug the formulary does
        not know, or one whose name the doctor is not recorded as having said."""
        return tuple(m for m in self.meds if (not m.known or m.unsaid) and not m.acknowledged)

    @classmethod
    def parse(cls, payload: Any) -> DictationMapping:
        """Model JSON (or a stored dict) → the contract. Tolerant of shape, strict
        about drug names: a med with no name at all is dropped, because a nameless
        line in a prescription is not recoverable by a doctor scanning a diff."""
        if not isinstance(payload, Mapping):
            raise DictationError("mapping payload must be an object")

        diagnosis = payload.get("diagnosis")
        diagnosis = str(diagnosis).strip() if diagnosis else None

        events = tuple(
            TreatmentEvent(
                cycle=_int_or_none(row.get("cycle")),
                regimen=_text(row.get("regimen")),
                date=_text(row.get("date")) or None,
                next_due=_text(row.get("next_due")) or None,
                as_spoken=_text(row.get("as_spoken")),
            )
            for row in _rows(payload.get("treatment_events"))
        )

        meds = tuple(
            med for row in _rows(payload.get("meds")) if (med := _parse_med(row)) is not None
        )

        follow_raw = payload.get("follow_up")
        follow = FollowUp(
            when=_text(follow_raw.get("when")) or None if isinstance(follow_raw, Mapping) else None,
            as_spoken=_text(follow_raw.get("as_spoken")) if isinstance(follow_raw, Mapping) else "",
            instructions=(
                _text(follow_raw.get("instructions")) if isinstance(follow_raw, Mapping) else ""
            ),
        )

        return cls(
            diagnosis=diagnosis,
            treatment_events=events,
            meds=meds,
            assessment=AyurvedaAssessment.parse(payload.get("assessment")),
            pathya_apathya=_str_tuple(payload.get("pathya_apathya")),
            advice=_str_tuple(payload.get("advice")),
            follow_up=follow,
            unclear=_str_tuple(payload.get("unclear")),
        )


def _parse_med(row: Mapping[str, Any]) -> MedLine | None:
    name = _text(row.get("name"))
    if not name:
        return None
    suggestions_raw = row.get("suggestions")
    return MedLine(
        name=name,
        dose=_text(row.get("dose")) or None,
        route=_text(row.get("route")) or None,
        freq=_text(row.get("freq")) or None,
        duration=_text(row.get("duration")) or None,
        as_spoken=_text(row.get("as_spoken")),
        # Deliberately NOT read from the payload on the model path — `validate`
        # overwrites it. Read here only so a stored record round-trips.
        known=bool(row.get("known", False)),
        generic=_text(row.get("generic")) or None,
        drug_class=_text(row.get("drug_class")) or None,
        ambiguous=bool(row.get("ambiguous", False)),
        suggestions=tuple(dict(s) for s in _rows(suggestions_raw)),
        unsaid=bool(row.get("unsaid", False)),
        acknowledged=bool(row.get("acknowledged", False)),
    )


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# -- formulary validation -----------------------------------------------------


def _was_said(med: MedLine, transcript: str) -> bool:
    """Does this drug name actually appear in what the doctor said?

    This is the only check that can catch the session's worst failure. Nothing
    downstream of the model can tell that "vinblastine" in `name` started life as
    "Vinblastin" in the doctor's mouth — by then the substitution has already
    happened, invisibly, inside the model. What we *can* do is hold the name up
    against the doctor's own words: the prompt requires `as_spoken` to carry the
    phrase the line came from, and the verbatim transcript is on the row.

    So: take the drug word (the first token of the normalised name) and look for
    it in the normalised spoken text. Present ⇒ the model transcribed. Absent ⇒
    either it renamed the drug or it invented one, and both are the doctor's
    business before they sign.

    It is a heuristic and it is allowed to be: a false positive costs one
    acknowledgement tap, and it fires on exactly the two cases in the fixture set
    that must never sail through — the helpful correction and the hallucination.
    `as_spoken` is preferred over the transcript because it is the line's own
    provenance; the transcript is the fallback when the model omits it.
    """
    spoken = formulary_mod.normalise(med.as_spoken or transcript)
    key = formulary_mod.normalise(med.name)
    if not key:
        return True  # no name to check; `parse` already dropped nameless lines
    if not spoken:
        return False  # nothing to check it against, so it is not evidenced
    return key.split()[0] in spoken.split()


def validate_meds(
    mapping: DictationMapping,
    *,
    transcript: str = "",
    check_unsaid: bool = True,
    scope: str = formulary_mod.DEFAULT_SCOPE,
) -> DictationMapping:
    """Replace every drug verdict with this system's own (doc 03 §7).

    The model's `known` is discarded rather than trusted-and-checked: a flag that
    is right 95% of the time is worse than no flag, because the 5% arrives
    looking exactly like the 95%. `name` is untouched — that is the invariant
    `test_dictation.py` asserts on every fixture.

    `check_unsaid=False` is for the typed note (plan §5.3a), where nothing was
    said and no model has been near the text. `_was_said` asks "did a model
    rename this drug on the way in?", and on a note the doctor typed themselves
    the honest answer is that the question does not apply — the alternative is
    every line on every typed note arriving flagged, which is a flag that means
    nothing and trains people to clear flags without reading them. The formulary
    check is untouched and still blocks the signature; that one is about the drug,
    not about who wrote it down.

    `scope` is the department's `capabilities.formulary_scope` (doc 24 §6.3) —
    which shelf of the book counts as known here. It defaults to allopathy so
    that every caller predating doc 24 gets exactly today's verdicts; the live
    paths pass the scope derived from the note's own department, never from a
    flag on the request, for the same reason `check_unsaid` is derived from the
    record below.
    """
    book = formulary_mod.get_formulary(scope=scope)
    checked = []
    for med in mapping.meds:
        verdict = book.lookup(med.name)
        checked.append(
            MedLine(
                name=med.name,  # verbatim, always
                dose=med.dose,
                route=med.route,
                freq=med.freq,
                duration=med.duration,
                as_spoken=med.as_spoken,
                known=verdict.known,
                generic=verdict.generic,
                drug_class=verdict.drug_class,
                ambiguous=verdict.ambiguous,
                suggestions=tuple(verdict.to_dict()["suggestions"]),
                unsaid=check_unsaid and not _was_said(med, transcript),
                acknowledged=med.acknowledged,
            )
        )
    return DictationMapping(
        diagnosis=mapping.diagnosis,
        treatment_events=mapping.treatment_events,
        meds=tuple(checked),
        advice=mapping.advice,
        follow_up=mapping.follow_up,
        unclear=mapping.unclear,
        # Carried through untouched. This function's whole job is the drug
        # verdicts; a field it rebuilt without copying would be silently erased
        # every time a doctor corrected an unrelated line.
        assessment=mapping.assessment,
        pathya_apathya=mapping.pathya_apathya,
    )


# -- the mapper (the provider-chain adapter) ----------------------------------


@dataclass(frozen=True, slots=True)
class MapResult:
    mapping: DictationMapping
    model: str
    prompt_ref: str


class DictationMapper:
    """`dictation_map` on the LLM chain — Gemini Flash, OpenAI, or local Qwen3.

    Config-only, like every other provider call in this codebase: the chain comes
    from `registry.llm_chain(settings)`, so `LLM_PROVIDER=local_vllm` runs the
    whole thing on the box with no code change (doc 08).
    """

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        *,
        prompt_version: int | None = PROMPT_VERSION,
        capabilities: CareSystemCapabilities | None = None,
    ):
        self._providers = list(providers)
        # The department's system of medicine decides two things here and nothing
        # else: which register the prompt is written in, and which shelf of the
        # formulary a dictated name is checked against (doc 24 §6.3, §6.4).
        # Defaulted rather than required so a caller with no department in
        # hand — a script, a test written before doc 24 — gets exactly today's
        # behaviour; the route derives it from the visit.
        self._caps = capabilities or DEFAULT_CAPABILITIES
        self._prompt = load_packed("dictation_map", self._caps.prompt_pack, prompt_version)

    async def map(self, transcript: str, *, patient: str, context: str) -> MapResult:
        """One transcript → validated structured fields. Raises `MappingUnavailable`."""
        if not transcript.strip():
            raise DictationError("nothing to map: the transcript is empty")

        rendered = self._prompt.render(
            transcript=transcript,
            formulary_hint=formulary_mod.get_formulary(
                scope=self._caps.formulary_scope
            ).prompt_hint(),
            patient=patient,
            context=context,
        )
        request = LLMRequest(
            prompt=rendered,
            system=self._prompt.system,
            prompt_ref=self._prompt.ref,
            json_output=True,
            # Near-zero: this is transcription-shaped work, not writing. A
            # creative temperature here invents a dose.
            temperature=0.0,
            max_tokens=1200,
        )
        try:
            result = await with_fallback(
                self._providers,
                lambda provider: provider.complete(request, purpose=UsagePurpose.DICTATION),
            )
        except ProviderError as exc:
            raise MappingUnavailable(str(exc)) from exc

        mapping = validate_meds(
            DictationMapping.parse(result.json()),
            transcript=transcript,
            scope=self._caps.formulary_scope,
        )
        return MapResult(mapping=mapping, model=result.model, prompt_ref=self._prompt.ref)


# -- the record ---------------------------------------------------------------


def empty_structured() -> dict[str, Any]:
    return {
        "version": STRUCTURED_VERSION,
        "mapped": None,
        "fields": None,
        "edits": [],
        "model": None,
        "prompt_ref": None,
        "mapping_error": None,
        "mapped_at": None,
    }


def current_mapping(dictation: Dictation) -> DictationMapping | None:
    """What the record currently says — the doctor's version if they edited it."""
    fields = (dictation.structured or {}).get("fields")
    return DictationMapping.parse(fields) if isinstance(fields, Mapping) else None


async def get_draft(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor
) -> Dictation | None:
    """This visit's dictation, if it has one. A visit has at most one."""
    await assert_visit_scope(session, visit_id=visit_id, doctor=doctor)
    return await session.scalar(
        select(Dictation)
        .where(Dictation.visit_id == visit_id, Dictation.deleted_at.is_(None))
        .order_by(Dictation.created_at.desc())
        .limit(1)
    )


async def start(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor, transcript: str = ""
) -> Dictation:
    """Open (or reopen) the draft for a visit and store the transcript.

    Idempotent per visit: pressing D twice does not start a second note. Once
    signed, it refuses — a correction to a signed record is an amendment, and
    this system has no amendment anywhere yet (S18).
    """
    existing = await get_draft(session, visit_id=visit_id, doctor=doctor)
    if existing is not None:
        if existing.status is DictationStatus.SIGNED:
            raise DictationLocked("this consult note is signed and cannot be re-dictated")
        if transcript:
            existing.transcript = transcript
        await session.flush()
        return existing

    dictation = Dictation(
        visit_id=visit_id,
        doctor_id=doctor.id,
        transcript=transcript or None,
        structured=empty_structured(),
        status=DictationStatus.DRAFT,
    )
    session.add(dictation)
    await session.flush()
    return dictation


async def compose(session: AsyncSession, *, dictation: Dictation, doctor: Doctor) -> Dictation:
    """Open the editable field set with no model in the loop (plan §5.3a).

    This is what "Type note" is: the same `Dictation` row, the same `fields` /
    `edits` history, the same signature boundary and the same `blocking_meds`
    refusal — with `mapped` left null, because no model produced anything to diff
    against. It is deliberately *not* a second way to make a prescription. A
    parallel writer around the signature is how the drug-safety validation gets
    bypassed two quarters from now, so the typed note joins the existing path at
    the earliest possible point instead: an empty mapping that `apply_corrections`
    can then patch, exactly like a mapped one.

    Idempotent, and it never clears fields that already exist — a doctor who
    presses "Type note" on a mapped draft is asking to edit it, not to lose it.
    """
    _assert_unsigned(dictation)
    structured = dict(dictation.structured or empty_structured())
    if isinstance(structured.get("fields"), Mapping):
        return dictation
    structured["fields"] = DictationMapping().to_dict()
    structured["edits"] = list(structured.get("edits") or [])
    dictation.structured = structured
    await session.flush()
    return dictation


async def map_transcript(
    session: AsyncSession, *, dictation: Dictation, doctor: Doctor, mapper: DictationMapper
) -> Dictation:
    """Run the mapping and store both the model's version and the working copy.

    `mapped` is written once per mapping run and is what the review screen diffs
    against; `fields` starts as a copy and is where the doctor's edits land.
    Re-mapping (the doctor re-dictates) resets both and clears the edit trail —
    the trail describes edits to *a* mapping, and keeping it across a new one
    would attribute corrections to text nobody said.
    """
    _assert_unsigned(dictation)
    if not (dictation.transcript or "").strip():
        raise DictationError("nothing to map: the transcript is empty")

    patient_line, context_line = await _prompt_context(session, dictation=dictation)
    structured = dict(dictation.structured or empty_structured())
    try:
        result = await mapper.map(
            dictation.transcript or "", patient=patient_line, context=context_line
        )
    except MappingUnavailable as exc:
        # The transcript is the irreplaceable half — it is the doctor's voice and
        # they have moved on to the next patient. Record the failure on the draft
        # and let them retry; never drop the note because a vendor was down.
        #
        # And open the fields (plan §5.2): a failed mapping must be a recoverable
        # state, not a dead end. The doctor keeps their recording *and* keeps a
        # route to a signature — the model being down is not a reason a patient
        # leaves without a prescription. Existing fields are never overwritten,
        # so a retry that fails cannot wipe what the doctor has already typed.
        structured["mapping_error"] = str(exc)
        if not isinstance(structured.get("fields"), Mapping):
            structured["fields"] = DictationMapping().to_dict()
        dictation.structured = structured
        await session.flush()
        raise

    payload = result.mapping.to_dict()
    structured.update(
        {
            "version": STRUCTURED_VERSION,
            "mapped": payload,
            "fields": payload,
            "edits": [],
            "model": result.model,
            "prompt_ref": result.prompt_ref,
            "mapping_error": None,
            "mapped_at": _now().isoformat(),
        }
    )
    dictation.structured = structured
    await session.flush()
    return dictation


async def apply_corrections(
    session: AsyncSession,
    *,
    dictation: Dictation,
    doctor: Doctor,
    patch: Mapping[str, Any],
    capabilities: CareSystemCapabilities | None = None,
) -> Dictation:
    """The doctor's "tap to fix" (doc 03 §7).

    Whole-field replacement, not a JSON merge: the fields are small and a merge
    of `meds[2].dose` against a list the doctor just reordered is a silent
    corruption. Every accepted patch is re-validated against the formulary, so a
    doctor typing a drug name gets the same verdict the model's output got — and
    the same refusal to be helpfully corrected.

    "The same verdict" is why `capabilities` is here: the mapping was validated
    against the department's formulary shelf, so a correction validated against a
    different one would flag a preparation the model was allowed to write, and
    the doctor would be looking at a flag that appeared because they touched the
    field. The route derives it from the visit, the same as the mapping did.
    """
    _assert_unsigned(dictation)
    structured = dict(dictation.structured or empty_structured())
    before = structured.get("fields")
    if not isinstance(before, Mapping):
        raise DictationError("nothing to correct: this dictation has not been mapped yet")

    unknown = set(patch) - _EDITABLE_TOP_LEVEL
    if unknown:
        raise DictationError(f"not editable: {sorted(unknown)}")

    merged = {**before, **patch}

    # `assessment` is the one field merged key-by-key rather than replaced, and
    # the exception is deliberate rather than a softening of the rule above.
    #
    # The rule exists for `meds`: merging `meds[2].dose` into a list the doctor
    # just reordered is a silent corruption, because the index means something
    # different than it did. A flat map of five independent strings has no such
    # hazard — but it does have the opposite one. The five lines are edited as
    # five fields and travel as one object, so a client that commits Agni while
    # holding a copy of Prakriti from before the last round trip replaces the
    # whole object and erases a sibling nobody touched. That is not theoretical:
    # it is what a doctor filling the fields faster than the network does, and it
    # is what `ayurveda-console.spec.ts` caught the first time it ran.
    #
    # Merging by key makes each of the five its own whole-field replacement,
    # which is what they always were on screen.
    if isinstance(patch.get("assessment"), Mapping):
        existing = before.get("assessment")
        merged["assessment"] = {
            **(existing if isinstance(existing, Mapping) else {}),
            **patch["assessment"],
        }
    transcript = dictation.transcript or ""
    after = validate_meds(
        DictationMapping.parse(merged),
        transcript=transcript,
        # Nothing was said, so there is nothing a model could have renamed. See
        # `validate_meds`. Derived from the record rather than from a flag the
        # client sets: a note with no transcript cannot have been mapped, because
        # `map_transcript` refuses an empty one.
        check_unsaid=bool(transcript.strip()),
        scope=(capabilities or DEFAULT_CAPABILITIES).formulary_scope,
    ).to_dict()

    edits = list(structured.get("edits") or [])
    for key in sorted(patch):
        if before.get(key) != after.get(key):
            edits.append(
                {
                    "at": _now().isoformat(),
                    "by": str(doctor.id),
                    "field": key,
                    "from": before.get(key),
                    "to": after.get(key),
                }
            )
    structured["fields"] = after
    structured["edits"] = edits
    dictation.structured = structured
    await session.flush()
    return dictation


async def sign(session: AsyncSession, *, dictation: Dictation, doctor: Doctor) -> Dictation:
    """Sign and lock (doc 03 §7).

    Refuses while any unrecognised drug is unacknowledged. The doctor can sign
    anything — the formulary is incomplete by nature — but they have to say so.
    """
    _assert_unsigned(dictation)
    mapping = current_mapping(dictation)
    if mapping is None:
        raise DictationError("cannot sign a dictation that has not been mapped")
    if not mapping.meds and not mapping.diagnosis and not mapping.advice:
        raise DictationError("cannot sign an empty consult note")

    unacknowledged = [m.name for m in mapping.meds_needing_attention]
    if unacknowledged:
        raise DictationError(
            "these drugs are flagged and have not been acknowledged: " + ", ".join(unacknowledged)
        )

    dictation.status = DictationStatus.SIGNED
    dictation.signed_at = _now()
    dictation.signed_by = doctor.id
    await session.flush()
    # doc 03 §8: the signature is what produces the prescription, so it is
    # generated here rather than behind a verb a client could call without one.
    # Imported locally because `app.prescription` reads this module's contract —
    # a module-level import would be a cycle. doc 03 §9's check-in plan draft
    # hangs off the same moment (S17), for the same reason: a follow-up nobody
    # prescribed is a follow-up nobody stands behind.
    from app import prescription as prescription_svc
    from app.checkins import plan as checkin_plan

    await prescription_svc.generate(session, dictation=dictation, doctor=doctor)
    # Never raises — see `draft_from_dictation`. A drafted plan messages nobody
    # until a doctor approves it; drafting it here is what puts it in front of
    # them at the moment they are still thinking about this patient.
    await checkin_plan.draft_from_dictation(session, dictation=dictation, doctor=doctor)
    logger.info("dictation %s signed by doctor %s", dictation.id, doctor.id)
    return dictation


# -- helpers ------------------------------------------------------------------


def _assert_unsigned(dictation: Dictation) -> None:
    if dictation.status is DictationStatus.SIGNED:
        raise DictationLocked("this consult note is signed; signed records do not change")


async def assert_visit_scope(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor
) -> Visit:
    """Same scoping as the S9 card: your department, or an error that says so."""
    visit = await session.get(Visit, visit_id)
    if visit is None or visit.deleted_at is not None:
        raise DictationError(f"no such visit {visit_id}")
    if visit.department_id != doctor.department_id:
        raise DictationError("that patient is in another department")
    return visit


async def _prompt_context(session: AsyncSession, *, dictation: Dictation) -> tuple[str, str]:
    """The two context lines the mapping prompt takes.

    Deliberately thin: who is in the room, and what today's date is, so relative
    dates ("next Tuesday", "14 tareekh") resolve. Not the intake answers — the
    model's job is to transcribe the doctor's decisions, and feeding it the
    patient's symptoms invites it to fill gaps the doctor left on purpose.
    """
    visit = await session.get(Visit, dictation.visit_id)
    if visit is None:  # pragma: no cover - FK guarantees it
        return "(unknown patient)", "(no visit on file)"
    patient = await session.get(Patient, visit.patient_id)
    intake = await session.scalar(
        select(Intake)
        .where(Intake.visit_id == visit.id, Intake.deleted_at.is_(None))
        .order_by(Intake.created_at.desc())
        .limit(1)
    )

    if patient is None:  # pragma: no cover - FK guarantees it
        patient_line = "(unknown patient)"
    else:
        bits = [patient.name, f"{patient.age}y" if patient.age else "", str(patient.sex or "")]
        patient_line = ", ".join(b for b in bits if b)

    context = [f"visit date: {visit.date.isoformat()}"]
    if intake and intake.chief_complaint_en:
        context.append(f"chief concern at intake: {intake.chief_complaint_en}")
    return patient_line, "; ".join(context)


def _now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)
