# SESSION-ADAPT-1 — Adaptive intake turn V1 (clarify-only voice answers)

**Date:** 2026-07-22 · **Scope ref:** docs/11-ADAPTIVE-INTAKE.md §2 (S-ADAPT.1) ·
**Branch:** `feat/adaptive-intake`

> ⚠️ **BRANCH-ONLY (operator instruction, 2026-07-22).** S-ADAPT stays on
> `feat/adaptive-intake` and is **not** merged to `main` until proven on the live
> `omen` box. `main` is what the pilot deploys; adaptive-intake must not disrupt
> the working local-voice kiosk. Deploy this branch to omen to validate, then
> fast-forward `main` only on the operator's explicit go-ahead.

## What was built
V1 = a patient can answer any tap node **by voice**; a vague answer earns exactly
**one** spoken clarifying question; then it falls back to taps. The tree, the
`walk.save` validator and the rule-based red-flag engine are untouched — the LLM
*proposes* a candidate value, the deterministic engine still *decides* (doc 11 §5).

- **`app/intake/interpret.py`** — new `AnswerInterpreter` layer, mirrors
  `summary.py`'s shape: `Interpretation(value|clarify, confidence, extra)`;
  `LLMInterpreter` (the `interpret_answer` prompt on the LLM chain, `with_fallback`,
  metered `INTAKE_TURN`); `FakeInterpreter` (deterministic, matches the node's own
  option labels / pulls a number — never invents an option). Unparseable model JSON
  degrades to a clarify, never crashes. `extra` reserved (unused) for V2 enrichment.
- **`prompts/interpret_answer/v1.md`** — vendor-neutral, `response_format: json`,
  vars `{question, answer_spec, utterance, lang}`; constrained to the node's own
  option ids / numeric range, "clarify when in doubt", clarify in the patient's lang.
- **Backend seam — extended `POST /kiosk/{sid}/answer`** (no new route): when
  `value is null` + `raw_text` present + an interpreter is wired, it interprets
  against the **current** node. Candidate → validated by `validate_answer`
  (out-of-spec ⇒ clarify, never an error) → normal `save_answer`. Clarify →
  `AnswerOut(ok=false, clarify=…)` on the same node. Budget spent (`attempt>=1`) or
  a rejected candidate ⇒ `adaptive_exhausted=true` → the kiosk keeps its taps. Taps
  (`value` set) skip all of it — the unchanged path. Per-node outcome is logged
  (`adaptive_intake node=… outcome=…`) as the V2 telemetry signal; the LLM call is
  the priced `usage_event`.
- **Gate:** `settings.intake_adaptive` (default off); the engine builds the
  interpreter only when the flag is on **and** `llm_provider != "fake"`
  (`main.py`). `IntakeEngine(adaptive=…, interpreter=…)` — `interpreter` lets a
  test inject `FakeInterpreter`. `engine.answer_interpreter()` returns `None` when
  off ⇒ the route never interprets ⇒ byte-for-byte today's tap flow.
- **Frontend:** `kioskAdaptiveEnabled()` (needs `NEXT_PUBLIC_KIOSK_ADAPTIVE=1` +
  server-STT + recorder). `AdaptiveVoiceAnswer` sits **above** the taps on every
  tap node (never free_voice): records → `/kiosk/stt` → posts the utterance to
  `/answer` with `value:null` + `attempt`; a `clarify` is spoken (Kokoro `speak`)
  and shown, the mic re-opens; `adaptive_exhausted`/second-vague ⇒ drop the voice
  loop, taps remain. `clarify`/`voiceAttempt` reset when the node advances.
- **Build flags plumbed:** `NEXT_PUBLIC_KIOSK_ADAPTIVE` (Dockerfile + compose arg +
  .env.example) and `INTAKE_ADAPTIVE` (.env.example; api reads it via `env_file`).

## Tests (all green)
- `tests/test_intake.py` — FakeInterpreter maps single/scale, clarifies when vague,
  never-invent property (round-trips `validate_answer`); LLMInterpreter value /
  clarify / degrade-on-garbage.
- `tests/test_kiosk.py` — voice answer maps + advances; first vague ⇒ one clarify
  (same node); second vague ⇒ `adaptive_exhausted` (taps); rejected candidate does
  not advance/500; flag-off ⇒ no interpretation.
- `make test`: backend **592** green (incl. 11 new adaptive tests), ruff clean;
  web `tsc --noEmit` + eslint clean.

## AC status (doc 11 §2)
- ✅ voice → correct value on the fake (deterministic); on-box Qwen3 pending omen.
- ✅ exactly one clarify, in-language; second vague ⇒ taps (no infinite loop).
- ✅ interpreter can't produce a value the node rejects (validated → clarify).
- ✅ red flags still rule-derived (unchanged invariant tests pass).
- ✅ flag off / no LLM / offline ⇒ today's tap flow.
- ⏳ on-box scripted Qwen3 run — do on omen (needs live local vLLM).

## Next
- **Deploy this branch to omen**, set `INTAKE_ADAPTIVE=1` +
  `NEXT_PUBLIC_KIOSK_ADAPTIVE=1` (server-STT already on), run scripted hi/en answers
  through Qwen3; watch the `adaptive_intake` outcome logs = the V2 telemetry.
- **S-ADAPT.2 (V2)** — enrichment + adaptive follow-ups; scope finalised from V1
  clarify/mis-map rates (doc 11 §3). `Interpretation.extra` is already reserved.
