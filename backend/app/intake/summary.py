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
from app.models.enums import Lang, UsagePurpose
from app.prompts import load
from app.providers import LLMProvider, LLMRequest, with_fallback
from app.trees.schema import Tree
from app.trees.walker import RedFlagHit, Walk

logger = logging.getLogger(__name__)

#: For the readback prompt variable and the template path's fixed phrasing.
LANG_NAMES: dict[str, str] = {
    Lang.EN: "English",
    Lang.HI: "Hindi",
    Lang.MR: "Marathi",
    Lang.TE: "Telugu",
}

#: The template summarizer's read-back, per language — everyday words, no medical
#: vocabulary, ending in a yes/no confirm (doc 03 §1: many patients cannot read,
#: this is the only check they get). The LLM path writes a richer one; this is the
#: offline floor, authored not model-generated. mr/te are S13 (fall back to hi).
_READBACK_TEMPLATE: dict[str, str] = {
    Lang.EN: "You told me: {concern}. {flags}Is that right? Say yes, or tell me what to change.",
    Lang.HI: "आपने बताया: {concern}। {flags}क्या यह सही है? हाँ कहिए, या बताइए क्या बदलना है।",
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


def _value_text(node, value: Any, lang: Lang | str) -> str:
    """Human-readable rendering of a stored value, for when there is no raw text."""
    if isinstance(value, list):
        labels = [
            opt.text.get(str(lang)) or opt.text.get(Lang.EN, opt.id)
            for item in value
            if (opt := node.option(item)) is not None
        ]
        return ", ".join(labels) if labels else str(value)
    if isinstance(value, str) and (opt := node.option(value)) is not None:
        return opt.text.get(Lang.EN) or opt.id
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
            patient=state.chief_complaint or "(walk-in, details in the answers)",
            answers=render_answers(tree, walk, state.lang),
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
        return _with_rule_flags(summary, flags, state.lang)


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
        concern = (
            state.chief_complaint
            or _first_answer_text(tree, walk, state.lang)
            or (tree.title.get(str(state.lang)) or tree.title.get(Lang.EN, "Intake"))
        )
        hpi = []
        for node_id in walk.path():
            answer = walk.answers.get(node_id)
            if answer is None:
                continue
            node = tree.node(node_id)
            said = answer.text or _value_text(node, answer.value, state.lang)
            hpi.append(f"{node.ask(Lang.EN)}: {said}")
        red_flag_lines = [flag.name(Lang.EN) for flag in flags]
        readback = _template_readback(concern, flags, state.lang)
        return IntakeSummary(
            chief_concern=concern,
            readback=readback,
            hpi=tuple(hpi),
            red_flags=tuple(red_flag_lines),
            patient_words=(
                {"quote": state.chief_complaint, "lang": str(state.lang)}
                if state.chief_complaint
                else {}
            ),
        )


def _first_answer_text(tree: Tree, walk: Walk, lang: Lang | str) -> str | None:
    for node_id in walk.path():
        answer = walk.answers.get(node_id)
        if answer is not None:
            return answer.text or _value_text(tree.node(node_id), answer.value, lang)
    return None


def _template_readback(concern: str, flags: Sequence[RedFlagHit], lang: Lang | str) -> str:
    template = _READBACK_TEMPLATE.get(str(lang)) or _READBACK_TEMPLATE[Lang.HI]
    # Speak the (oncologist-authored) flag instruction verbatim — never a
    # model's or a template's own reassurance.
    flag_text = (flags[0].say(lang) + " ") if flags else ""
    return template.format(concern=concern, flags=flag_text)


def _flags_for_prompt(flags: Sequence[RedFlagHit], lang: Lang | str) -> str:
    if not flags:
        return "(none)"
    return "\n".join(f"- {flag.name(Lang.EN)}: {flag.say(lang)}" for flag in flags)


def _with_rule_flags(
    summary: IntakeSummary, flags: Sequence[RedFlagHit], lang: Lang | str
) -> IntakeSummary:
    from dataclasses import replace

    return replace(summary, red_flags=tuple(flag.name(Lang.EN) for flag in flags))
