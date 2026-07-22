"""The intake tool dispatcher — the four tools, run over one `Walk`.

> "Build the tools as a thin dispatcher over [Walk] rather than a second
> implementation." — HANDOFF (S5 start notes)

`app.prompts.tools` declares the contract (the JSON Schema the model sees);
`app.trees.walker.Walk` is the whole of the clinical logic. This class is the
seam between them, and deliberately nothing more:

    get_next_node       -> walk.current
    save_answer         -> walk.save(...)
    check_red_flags     -> walk.red_flags()
    finish_and_summarize-> summarizer + walk.is_complete gate

The same dispatcher instance backs all three tiers. V1 (Gemini Live) and V2
(STT→LLM→TTS) call it with the model's function-call arguments; V3 (the kiosk)
calls it directly from taps. That single implementation is what makes doc 03 §1's
"same answers JSONB from every tier" true by construction rather than by three
teams remembering the same shape.

Every mutation persists the session immediately, because position is derived from
the answers (`app.intake.state`): the stored answers *are* the intake, and a
crash between a `save_answer` and its persist would lose the last thing the
patient said.
"""

from __future__ import annotations

import logging
from typing import Any

from app.intake.state import SessionState, SessionStatus, SessionStore
from app.intake.summary import IntakeSummary, Summarizer
from app.models.enums import Lang
from app.prompts.tools import INTAKE_TOOLS_BY_NAME, tool
from app.trees.schema import Tree
from app.trees.walker import AnswerError, Walk

logger = logging.getLogger(__name__)


class ToolError(ValueError):
    """A tool was called wrongly — bad name, missing argument, wrong session.

    Distinct from `AnswerError` (a patient answer that needs re-asking): a
    `ToolError` is a caller/model bug, and the tiers surface it as a re-prompt to
    the *model*, not to the patient.
    """


class ToolDispatcher:
    """Runs the intake tool contract against one session's walk.

    Bound to one `SessionState` and its `Tree`. The `Walk` is rebuilt from the
    state's stored answers, so a dispatcher constructed fresh on a downgraded tier
    resumes at exactly the same question (STATE.md invariant: position is derived,
    never stored).
    """

    def __init__(
        self,
        state: SessionState,
        tree: Tree,
        store: SessionStore,
        summarizer: Summarizer,
    ) -> None:
        self.state = state
        self.tree = tree
        self._store = store
        self._summarizer = summarizer
        self.walk = Walk.from_json(tree, state.answers)

    # -- the contract entry point ---------------------------------------------

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one tool by name, as the model asked. Validates the call first.

        Returns a JSON-serialisable dict — this is fed straight back to Gemini
        Live / the V2 LLM as the tool result, so it must contain no dataclasses,
        UUIDs or datetimes.
        """
        spec = INTAKE_TOOLS_BY_NAME.get(name)
        if spec is None:
            raise ToolError(
                f"unknown tool {name!r}; the contract declares {sorted(INTAKE_TOOLS_BY_NAME)}"
            )
        for required in tool(name).required():
            if required not in arguments:
                raise ToolError(f"{name}: missing required argument {required!r}")
        self._check_session(arguments.get("session_id"))

        match name:
            case "get_next_node":
                return await self.get_next_node()
            case "save_answer":
                return await self.save_answer(
                    arguments["node_id"],
                    arguments.get("value"),
                    raw_text=arguments.get("raw_text"),
                    lang=arguments.get("lang"),
                )
            case "check_red_flags":
                return await self.check_red_flags()
            case "finish_and_summarize":
                return await self.finish_and_summarize(arguments.get("reason", "complete"))
        raise ToolError(f"tool {name!r} is declared but not dispatched")  # pragma: no cover

    def _check_session(self, session_id: Any) -> None:
        if session_id is not None and session_id != self.state.session_id:
            raise ToolError(
                f"tool call names session {session_id!r} but this dispatcher is "
                f"bound to {self.state.session_id!r}"
            )

    # -- the four tools -------------------------------------------------------

    async def get_next_node(self) -> dict[str, Any]:
        await self._drain_prefills()
        node = self.walk.current
        if node is None:
            return {"complete": True, "node": None}
        lang = self.state.lang
        return {
            "complete": False,
            "node": {
                "id": node.id,
                "type": node.type.value,
                "text": node.ask(lang),
                "options": [
                    {
                        "id": opt.id,
                        "text": opt.text.get(str(lang)) or opt.text.get(Lang.EN, opt.id),
                        "icon": opt.icon,
                    }
                    for opt in node.options
                ],
                "min": node.min,
                "max": node.max,
                "unit": node.unit,
                "audio": node.audio_clip(lang),
            },
        }

    async def save_answer(
        self,
        node_id: str,
        value: Any,
        *,
        raw_text: str | None = None,
        lang: str | None = None,
    ) -> dict[str, Any]:
        try:
            answer = self.walk.save(
                node_id,
                value,
                text=raw_text,
                lang=lang or self.state.lang,
            )
        except AnswerError as exc:
            # A patient answer that does not fit — the model should re-ask, not
            # crash. Reported as a structured result, not an exception, so the
            # tier loop can hand it back to the model verbatim.
            return {"ok": False, "error": str(exc), "node_id": node_id}

        if raw_text:
            self.state.record_turn("patient", raw_text, lang=answer.lang or self.state.lang)

        # Position, answers and flags all move together; recompute, never cache
        # (STATE.md: anything derived from walk.answers is recomputed after a save).
        self.state.answers = self.walk.to_json()
        flags = self.walk.red_flags()
        self.state.red_flags = [flag.to_json() for flag in flags]
        await self._store.save(self.state)

        return {
            "ok": True,
            "node_id": node_id,
            "value": answer.value,
            "complete": self.walk.is_complete,
            "red_flags": [self._flag_brief(flag) for flag in flags],
        }

    async def _drain_prefills(self) -> None:
        """Auto-answer any node the walk has reached that a patient already
        volunteered an answer for (S-ADAPT.2 enrichment, doc 11 §3).

        This is what makes an enriched walk "skip" a question: when the current
        node has a pending prefill, apply it — through the SAME `walk.save`
        validator and red-flag recompute a tap would hit — and move on, so the
        node is never re-asked. Nothing here bypasses the tree: an invalid or
        no-longer-reachable prefill is dropped, and the node is asked normally.
        The result is byte-for-byte the answers JSONB a pure-tap walk would
        produce for the same facts (doc 11 §5 invariant 4).
        """
        prefills = self.state.pending_prefills
        if not prefills:
            return
        changed = False
        # Bounded by the number of pending prefills — each iteration consumes one.
        for _ in range(len(prefills)):
            node = self.walk.current
            if node is None or node.id not in prefills:
                break
            spec = prefills.pop(node.id)
            changed = True
            try:
                self.walk.save(
                    node.id,
                    spec.get("value"),
                    text=spec.get("text"),
                    lang=spec.get("lang") or self.state.lang,
                )
            except AnswerError:
                # A fact that no longer fits (an amendment rerouted the branch, or
                # the model proposed something the node rejects). Drop it and ask.
                self._record_adaptive_turn(node.id, "prefill_rejected")
                break
            self._record_adaptive_turn(node.id, "prefilled")
        if changed:
            self.state.answers = self.walk.to_json()
            self.state.red_flags = [flag.to_json() for flag in self.walk.red_flags()]
            await self._store.save(self.state)

    def _record_adaptive_turn(self, node_id: str, outcome: str, *, enriched: int = 0) -> None:
        """Append one adaptive-intake telemetry event (doc 11 §3, §6)."""
        from datetime import UTC, datetime

        self.state.adaptive_turns.append(
            {
                "node_id": node_id,
                "outcome": outcome,
                "enriched": enriched,
                "at": datetime.now(UTC).isoformat(),
            }
        )

    async def check_red_flags(self) -> dict[str, Any]:
        flags = self.walk.red_flags()
        return {
            "any": bool(flags),
            "priority": self.walk.priority().value,
            "red_flags": [
                {
                    "id": flag.id,
                    "severity": flag.severity.value,
                    "label": flag.name(self.state.lang),
                    "instruction": flag.say(self.state.lang),
                }
                for flag in flags
            ],
        }

    async def finish_and_summarize(self, reason: str = "complete") -> dict[str, Any]:
        summary = await self._summarizer.summarize(self.state, self.tree, self.walk)
        self._apply_summary(summary)
        self.state.status = (
            SessionStatus.COMPLETE
            if reason == "complete" and self.walk.is_complete
            else SessionStatus.HANDOFF
            if reason == "handoff"
            else SessionStatus.ENDED
        )
        self.state.record_turn("assistant", summary.readback, lang=self.state.lang)
        await self._store.save(self.state)
        return {
            "ok": True,
            "reason": reason,
            "complete": self.walk.is_complete,
            "readback": summary.readback,
            "summary_md": self.state.summary_md,
            # The rule-engine flags as {id, severity} dicts — the same shape
            # save_answer/confirm return and FinishOut is typed for. NOT
            # summary.red_flags, which are human-readable strings for the doctor
            # summary_md (a real LLM emits them; the fake in tests emits none,
            # which is why the string-vs-dict mismatch only bit on the live box).
            "red_flags": [self._flag_brief(flag) for flag in self.walk.red_flags()],
        }

    def _apply_summary(self, summary: IntakeSummary) -> None:
        self.state.summary_md = summary.to_markdown()
        self.state.summary_lang_versions[str(self.state.lang)] = {
            "structured": summary.to_structured(),
            "readback": summary.readback,
        }

    @staticmethod
    def _flag_brief(flag) -> dict[str, Any]:
        return {"id": flag.id, "severity": flag.severity.value}
