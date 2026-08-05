"""The intake summary (doc 03 §4) — what the doctor reads, what the patient hears.

> "Summarizer (Gemini Flash): summary_md in English + patient language; read-back
> script for confirmation." — doc 02 §5

Two audiences, one object. `IntakeSummary` is doc 03 §4's structured contract for
the doctor screen (S9 renders it); `readback` is the plain-language script the
assistant speaks to the patient to confirm before finishing (doc 03 §1). They are
produced together because they describe the same intake, but they are written
very differently — the prompt (`prompts/summarize`) is explicit about that.

## Two summarizers, one interface — because V3 has no model

`LLMSummarizer` is the V1/V2 path: the summarize prompt, run on Gemini Flash /
gpt-4o-mini, with the deterministic red flags handed in so the model repeats
rather than invents them (doc 02 §5). `TemplateSummarizer` is the V3 path: a
deterministic summary assembled from the answers and the tree text, no vendor at
all — because V3 is the offline, zero-AI tier and an intake that completes on it
must not need a network to produce its summary. The engine also falls back to the
template if the LLM is down: degrade, never deny (doc 02 §5).

**The red flags never come from the summarizer.** On both paths they are computed
by the rule engine (`Walk.red_flags`) and passed in; the LLM path forbids the
model from adding or dropping one, and the template path just lists them. That is
the boundary S21 signs the rules off against.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.intake.state import SessionState
from app.languages import script_problem
from app.models.enums import Lang, UsagePurpose
from app.prompts import load
from app.providers import LLMProvider, LLMRequest, with_fallback
from app.trees.schema import NodeType, SummaryRole, Tree
from app.trees.walker import RedFlagHit, Walk

logger = logging.getLogger(__name__)

#: For the readback prompt variable and the template path's fixed phrasing.
LANG_NAMES: dict[str, str] = {
    Lang.EN: "English",
    Lang.HI: "Hindi",
    Lang.MR: "Marathi",
    Lang.TE: "Telugu",
}

#: The alphabet each language is read in here, named for the prompt (summarize
#: v3). "Hindi" alone is not enough: Hindi and Urdu are one spoken language in two
#: scripts, and a model told only "Hindi" answers in Perso-Arabic often enough
#: that the pilot met it on day one. The prompt says it; `app.languages` enforces
#: it, because a prompt is a request and a guard is a guarantee.
SCRIPT_NAMES: dict[str, str] = {
    Lang.EN: "Latin",
    Lang.HI: "Devanagari",
    Lang.MR: "Devanagari",
    Lang.TE: "Telugu",
}

#: The template summarizer's read-back, per language — everyday words, no medical
#: vocabulary, ending in a yes/no confirm (doc 03 §1: many patients cannot read,
#: this is the only check they get). The LLM path writes a richer one; this is the
#: offline floor, authored not model-generated. All four pilot languages (S13);
#: {concern}, {facts} and {flags} arrive already translated (tree text, flag
#: instruction) — this template never composes a clinical sentence of its own.
_READBACK_TEMPLATE: dict[str, str] = {
    Lang.EN: (
        "You told me: {concern}.\n{facts}{flags}Is that right? Say yes, or tell me what to change."
    ),
    Lang.HI: ("आपने बताया: {concern}।\n{facts}{flags}क्या यह सही है? हाँ कहिए, या बताइए क्या बदलना है।"),
    Lang.MR: (
        "तुम्ही सांगितलं: {concern}.\n{facts}{flags}हे बरोबर आहे का? होय म्हणा, किंवा काय बदलायचं ते सांगा."
    ),
    Lang.TE: ("మీరు చెప్పారు: {concern}.\n{facts}{flags}ఇది సరైనదేనా? అవును అనండి, లేదా ఏం మార్చాలో చెప్పండి."),
}

#: "And you also said:" — introduces the patient's own closing words in the
#: read-back, so the last thing they volunteered is the last thing they hear
#: repeated (S-UX.6: the closing answer must reach the doctor, and the patient
#: must be able to catch it being wrong).
_READBACK_OWN_WORDS: dict[str, str] = {
    Lang.EN: "In your own words: {words}.",
    Lang.HI: "आपके अपने शब्दों में: {words}।",
    Lang.MR: "तुमच्या स्वतःच्या शब्दांत: {words}.",
    Lang.TE: "మీ సొంత మాటల్లో: {words}.",
}


@dataclass(frozen=True, slots=True)
class IntakeSummary:
    """doc 03 §4's contract, structured. `readback` is the patient-facing script.

    Built only through `parse` (LLM path) or `TemplateSummarizer` — both go
    through `_validate`, so an `IntakeSummary` in hand has the required shape. The
    S5 AC ("summary matches contract schema") is that structural guarantee.
    """

    chief_concern: str
    readback: str
    hpi: tuple[str, ...] = ()
    symptoms: tuple[dict[str, str], ...] = ()
    red_flags: tuple[str, ...] = ()
    history_meds: tuple[str, ...] = ()
    since_last_visit: tuple[str, ...] = ()
    patient_words: dict[str, str] = field(default_factory=dict)
    unclear: tuple[str, ...] = ()

    def to_structured(self) -> dict[str, Any]:
        return {
            "chief_concern": self.chief_concern,
            "hpi": list(self.hpi),
            "symptoms": [dict(row) for row in self.symptoms],
            "red_flags": list(self.red_flags),
            "history_meds": list(self.history_meds),
            "since_last_visit": list(self.since_last_visit),
            "patient_words": dict(self.patient_words),
            "readback": self.readback,
            "unclear": list(self.unclear),
        }

    def to_markdown(self) -> str:
        """The English doctor-screen summary (`Intake.summary_md`).

        Deliberately plain markdown, not a template engine: S9 renders the
        structured fields into the real UI; this is the human-readable fallback
        and what a coordinator or an export (S21) reads without the app.
        """
        lines = [f"**{self.chief_concern}**", ""]
        if self.red_flags:
            lines += ["**Red flags:**", *[f"- ⚠️ {flag}" for flag in self.red_flags], ""]
        if self.hpi:
            lines += ["**History:**", *[f"- {item}" for item in self.hpi], ""]
        if self.symptoms:
            lines.append("**Symptoms:**")
            for row in self.symptoms:
                parts = [row.get("symptom", "")]
                if row.get("duration"):
                    parts.append(f"for {row['duration']}")
                if row.get("severity"):
                    parts.append(f"severity {row['severity']}")
                lines.append(f"- {', '.join(p for p in parts if p)}")
            lines.append("")
        if self.history_meds:
            lines += ["**History / meds:**", *[f"- {item}" for item in self.history_meds], ""]
        if self.since_last_visit:
            lines += ["**Since last visit:**", *[f"- {item}" for item in self.since_last_visit], ""]
        if quote := self.patient_words.get("quote"):
            gloss = self.patient_words.get("english")
            lines.append(f'> "{quote}"' + (f" — *{gloss}*" if gloss else ""))
        if self.unclear:
            lines += ["", "*Unclear (please confirm): " + "; ".join(self.unclear) + "*"]
        return "\n".join(lines).strip()

    @classmethod
    def parse(cls, payload: Any) -> IntakeSummary:
        """Build from the model's JSON, validating the contract. Raises on drift."""
        return _validate(payload)


class SummaryError(ValueError):
    """The summarizer produced something that is not doc 03 §4's contract."""


def _validate(payload: Any) -> IntakeSummary:
    if not isinstance(payload, Mapping):
        raise SummaryError(f"summary must be a JSON object, got {type(payload).__name__}")

    concern = payload.get("chief_concern")
    if not isinstance(concern, str) or not concern.strip():
        raise SummaryError("summary is missing a chief_concern")
    readback = payload.get("readback")
    if not isinstance(readback, str) or not readback.strip():
        raise SummaryError("summary is missing a patient read-back script")

    def str_list(key: str) -> tuple[str, ...]:
        value = payload.get(key) or []
        if not isinstance(value, Sequence) or isinstance(value, str):
            raise SummaryError(f"summary field {key!r} must be a list of strings")
        return tuple(str(item) for item in value)

    symptoms_raw = payload.get("symptoms") or []
    if not isinstance(symptoms_raw, Sequence) or isinstance(symptoms_raw, str):
        raise SummaryError("summary field 'symptoms' must be a list")
    symptoms = tuple(
        {k: str(v) for k, v in row.items()} for row in symptoms_raw if isinstance(row, Mapping)
    )

    words_raw = payload.get("patient_words") or {}
    words = {k: str(v) for k, v in words_raw.items()} if isinstance(words_raw, Mapping) else {}

    return IntakeSummary(
        chief_concern=concern.strip(),
        readback=readback.strip(),
        hpi=str_list("hpi"),
        symptoms=symptoms,
        red_flags=str_list("red_flags"),
        history_meds=str_list("history_meds"),
        since_last_visit=str_list("since_last_visit"),
        patient_words=words,
        unclear=str_list("unclear"),
    )


class Summarizer(Protocol):
    async def summarize(self, state: SessionState, tree: Tree, walk: Walk) -> IntakeSummary: ...


def render_answers(tree: Tree, walk: Walk, lang: Lang | str) -> str:
    """The answered nodes as readable lines for the summarize prompt.

    Question in English (the doctor's language, doc 03 §4) with the patient's own
    words attached — the prompt is told to quote them and to mark anything it
    cannot read as `[unclear: ...]` rather than guess.
    """
    lines: list[str] = []
    for node_id in walk.path():
        answer = walk.answers.get(node_id)
        if answer is None:
            continue
        node = tree.node(node_id)
        question = node.ask(Lang.EN)
        said = answer.text or _value_text(node, answer.value, lang)
        lines.append(f"- {question} → {said} (value={answer.value!r})")
    return "\n".join(lines) or "- (no answers recorded)"


def answered_rows(tree: Tree, walk: Walk, lang: Lang | str) -> list[tuple[Any, str, str]]:
    """Every answered node as `(node, question in `lang`, answer in `lang`)`.

    One traversal, shared by the read-back (patient's language), the structured
    symptom rows and the live rail — so those three can never disagree about what
    the patient said, which is the failure the doctor notices first.
    """
    rows: list[tuple[Any, str, str]] = []
    for node_id in walk.path():
        answer = walk.answers.get(node_id)
        if answer is None:
            continue
        node = tree.node(node_id)
        said = answer.text or _value_text(node, answer.value, lang)
        if not str(said).strip():
            continue
        rows.append((node, node.ask(lang), str(said).strip()))
    return rows


def final_free_text(tree: Tree, walk: Walk) -> str:
    """The patient's closing free-text answer ("anything else?"), or "".

    Last free-text node on the walked path, not the last node overall: some trees
    ask for a medicine name in free text mid-walk, and the closing account is the
    one the doctor most wants and the summariser most easily drops.
    """
    for node_id in reversed(walk.path()):
        answer = walk.answers.get(node_id)
        if answer is None:
            continue
        if tree.node(node_id).type is not NodeType.FREE_VOICE:
            continue
        text = (answer.text or str(answer.value or "")).strip()
        if text:
            return text
    return ""


def _value_text(node, value: Any, lang: Lang | str) -> str:
    """Human-readable rendering of a stored value, for when there is no raw text.

    Always in the language asked for, with English as the fallback. The
    single-select branch used to hardcode English regardless of `lang`, which the
    doctor's summary never noticed — it asks for English — but the patient's
    spoken read-back did: it told a Hindi speaker "You told me: My periods are
    irregular", which is not a sentence she can confirm or correct.
    """

    def label(option) -> str:
        return option.text.get(str(lang)) or option.text.get(Lang.EN) or option.id

    if isinstance(value, list):
        labels = [label(opt) for item in value if (opt := node.option(item)) is not None]
        return ", ".join(labels) if labels else str(value)
    if isinstance(value, str) and (opt := node.option(value)) is not None:
        return label(opt)
    return str(value)


class LLMSummarizer:
    """V1/V2 path — the `summarize` prompt on the LLM chain (Gemini Flash → OpenAI)."""

    def __init__(self, providers: Sequence[LLMProvider], *, prompt_version: int | None = None):
        self._providers = list(providers)
        self._prompt = load("summarize", prompt_version)

    async def summarize(self, state: SessionState, tree: Tree, walk: Walk) -> IntakeSummary:
        flags = walk.red_flags()
        rendered = self._prompt.render(
            lang=str(state.lang),
            lang_name=LANG_NAMES.get(str(state.lang), str(state.lang)),
            script_name=SCRIPT_NAMES.get(str(state.lang), "the language's own"),
            patient=state.chief_complaint or "(walk-in, details in the answers)",
            answers=render_answers(tree, walk, state.lang),
            # Handed over separately as well as inside `answers`: it is the line a
            # summariser under a word budget drops first, and it is the one the
            # doctor most wants (S-UX.6).
            final_words=final_free_text(tree, walk) or "(the patient said nothing further)",
            red_flags=_flags_for_prompt(flags, state.lang),
            history="(none recorded)",
            since_last_visit="",
        )
        request = LLMRequest(
            prompt=rendered,
            system=self._prompt.system,
            prompt_ref=self._prompt.ref,
            json_output=True,
            temperature=0.1,
            max_tokens=800,
        )
        result = await with_fallback(
            self._providers,
            lambda provider: provider.complete(request, purpose=UsagePurpose.SUMMARY),
        )
        summary = IntakeSummary.parse(result.json())
        # Trust the rules, not the model, for the flag list — even if the prompt
        # behaved, this makes the invariant true by construction (doc 02 §5).
        summary = _with_rule_flags(summary, flags, state.lang)
        # And trust the script check, not the prompt, for what the patient hears.
        return _with_safe_script(summary, state, tree, walk)


class TemplateSummarizer:
    """V3 path — a deterministic summary from the answers, no vendor.

    Honest about being thin: it is a legible record of what was asked and
    answered plus the rule-decided flags, not the LLM path's prose. The point is
    that a V3 intake (offline, zero-AI, cost-guarded) still ends with a doctor
    summary and a spoken read-back without a network.
    """

    async def summarize(self, state: SessionState, tree: Tree, walk: Walk) -> IntakeSummary:
        return self.build(state, tree, walk)

    def build(self, state: SessionState, tree: Tree, walk: Walk) -> IntakeSummary:
        flags = walk.red_flags()
        english = answered_rows(tree, walk, Lang.EN)
        spoken = answered_rows(tree, walk, state.lang)
        closing = final_free_text(tree, walk)

        primary = _role_answer(english, SummaryRole.PRIMARY_SYMPTOM)
        fallback = (
            state.chief_complaint
            or _first_answer_text(tree, walk, state.lang)
            or (tree.title.get(str(state.lang)) or tree.title.get(Lang.EN, "Intake"))
        )
        # Two audiences, two languages, one fact: the doctor's card reads English,
        # the spoken read-back has to be in the words the patient used — telling a
        # Hindi speaker "You told me: Pain" is not a check they can answer.
        concern = primary or fallback
        spoken_concern = _role_answer(spoken, SummaryRole.PRIMARY_SYMPTOM) or fallback

        hpi = [f"{question}: {said}" for _, question, said in english]
        # One row, from the roles the tree author marked. Deliberately not a row
        # per answer: `symptoms` is the doctor's at-a-glance table, and a table
        # with twelve rows is the same as no table at all.
        symptoms: tuple[dict[str, str], ...] = ()
        if primary:
            symptoms = (
                {
                    "symptom": primary,
                    "duration": _role_answer(english, SummaryRole.DURATION) or "",
                    "severity": _role_answer(english, SummaryRole.SEVERITY) or "",
                },
            )

        return IntakeSummary(
            chief_concern=concern,
            readback=_template_readback(spoken_concern, spoken, closing, flags, state.lang),
            hpi=tuple(hpi),
            symptoms=symptoms,
            red_flags=tuple(flag.name(Lang.EN) for flag in flags),
            patient_words=(
                {"quote": closing or state.chief_complaint or "", "lang": str(state.lang)}
                if (closing or state.chief_complaint)
                else {}
            ),
        )


def _role_answer(rows: Sequence[tuple[Any, str, str]], role: SummaryRole) -> str | None:
    """The most recent answer the tree author tagged with this summary role.

    `summary_role` is presentation-only metadata (STATE.md invariant): it decides
    where an answer is *shown*, never where the walk goes or whether a flag fires.
    """
    for node, _question, said in reversed(rows):
        if node.summary_role is role:
            return said
    return None


def _first_answer_text(tree: Tree, walk: Walk, lang: Lang | str) -> str | None:
    for node_id in walk.path():
        answer = walk.answers.get(node_id)
        if answer is not None:
            return answer.text or _value_text(tree.node(node_id), answer.value, lang)
    return None


#: How many answered questions the template read-back repeats back. Long enough
#: that a patient can catch a wrong answer, short enough to stay listenable when
#: it is spoken aloud to someone who cannot read the screen.
_READBACK_MAX_FACTS = 6


def _template_readback(
    concern: str,
    rows: Sequence[tuple[Any, str, str]],
    closing: str,
    flags: Sequence[RedFlagHit],
    lang: Lang | str,
) -> str:
    template = _READBACK_TEMPLATE.get(str(lang)) or _READBACK_TEMPLATE[Lang.HI]

    # The questions and answers in the patient's own language, as the tree wrote
    # them — this path composes no clinical sentence of its own, which is exactly
    # why it can be trusted with no model behind it.
    facts = [
        f"{question} — {said}"
        for node, question, said in rows
        if node.type is not NodeType.FREE_VOICE
    ][:_READBACK_MAX_FACTS]
    if closing:
        own = _READBACK_OWN_WORDS.get(str(lang)) or _READBACK_OWN_WORDS[Lang.HI]
        facts.append(own.format(words=closing))
    facts_text = ("\n".join(facts) + "\n") if facts else ""

    # Speak the (clinician-authored) flag instruction verbatim — never a model's
    # or a template's own reassurance.
    flag_text = (flags[0].say(lang) + "\n") if flags else ""
    return template.format(concern=concern, facts=facts_text, flags=flag_text)


def _flags_for_prompt(flags: Sequence[RedFlagHit], lang: Lang | str) -> str:
    if not flags:
        return "(none)"
    return "\n".join(f"- {flag.name(Lang.EN)}: {flag.say(lang)}" for flag in flags)


def _with_rule_flags(
    summary: IntakeSummary, flags: Sequence[RedFlagHit], lang: Lang | str
) -> IntakeSummary:
    from dataclasses import replace

    return replace(summary, red_flags=tuple(flag.name(Lang.EN) for flag in flags))


def _with_safe_script(
    summary: IntakeSummary, state: SessionState, tree: Tree, walk: Walk
) -> IntakeSummary:
    """Refuse a read-back written in a script this patient's language does not use.

    A model asked for Hindi can answer in Urdu script — the same language, a
    different alphabet — and the Alwar pilot met it on its first day. The
    read-back is *the* confirmation step for a patient who cannot read a form
    (doc 03 §1); rendering it in an alphabet they cannot read turns the one check
    they get into a shape on a screen, and they will tap yes.

    So the model does not get to decide the script. When it gets it wrong the
    authored template read-back stands in — the same deterministic string the V3
    offline floor speaks, complete in all four languages by construction and
    checked by `app.lang_qa`. Degrade where there is something honest to degrade
    to; here there is.

    The patient's quote is dropped rather than replaced, because a quote is
    supposed to be the patient's own words and there is nothing honest to put in
    its place. `chief_concern`, `hpi` and `symptoms` are the doctor's English
    card and are left alone.
    """
    from dataclasses import replace

    lang = state.lang if isinstance(state.lang, Lang) else Lang(str(state.lang))
    fixed = summary

    problem = script_problem(summary.readback, lang)
    if problem:
        logger.warning("summary read-back rejected: %s; using the template read-back", problem)
        fixed = replace(
            fixed,
            readback=_template_readback(
                _role_answer(answered_rows(tree, walk, lang), SummaryRole.PRIMARY_SYMPTOM)
                or state.chief_complaint
                or "",
                answered_rows(tree, walk, lang),
                final_free_text(tree, walk),
                walk.red_flags(),
                lang,
            ),
        )

    quote = str(fixed.patient_words.get("quote") or "")
    if quote and script_problem(quote, lang):
        logger.warning("summary patient quote dropped: wrong script for %s", lang)
        fixed = replace(fixed, patient_words={})

    return fixed
