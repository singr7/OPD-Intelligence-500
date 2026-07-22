# 11 — Adaptive intake turn (local-LLM answer interpreter), V1 → V2

**Status as of 2026-07-22:** design, not yet built. The pilot runs a **local**
STT + LLM + TTS stack live (doc 10): the kiosk records voice, transcribes on-box
(Whisper), routes/summarises on-box (Qwen3), and reads aloud on-box (Kokoro, doc
10 §6). Today the *per-question* flow is still pure **V3 taps** — the patient taps
one of 3–5 options. This track adds an **LLM answer-interpreter** so a patient can
*answer each question by voice*, and the system asks a **clarifying follow-up**
when the answer is vague — turn-based, on the stack we already deployed.

It is deliberately the **intelligence layer under any future full-duplex V2V**
(S-OSS.2 / Pipecat streaming): build the dialogue smarts cheaply in a
request/response harness first; a streaming transport later just wraps them. It
also directly attacks the **operator-flagged routing/adaptivity debt** (HANDOFF)
by producing the telemetry that says *which* questions are poorly worded.

---

## 1. The one idea

Intake nodes are **structured tap targets** — `single | multi | scale | number |
body_map` (doc 03 §1a; there are no free-text nodes). `walk.save()` already
validates every value against the node. So the interpreter's job is narrow and
safe:

> Given a node (its question + its allowed answers) and a patient's spoken
> utterance (already transcribed by local Whisper), produce **either** a *candidate
> value the node accepts*, **or** *one short clarifying question* in the patient's
> language.

The candidate value is then passed through the **existing `walk.save()`**
validation and the **existing rule-based red-flag evaluator** — unchanged. The LLM
*proposes*; the deterministic engine still *decides*. This is the whole safety
story (see §5).

```
patient speaks ─▶ local Whisper (/kiosk/stt, already live)
                     │  raw utterance
                     ▼
             AnswerInterpreter.interpret(node, utterance, lang)   ← new, local LLM
                     │
      ┌──────────────┼─────────────────────────┐
      ▼              ▼                           ▼
  candidate      clarify: one follow-up      (V2) enrich: extra facts
  value            question (spoken via         volunteered for OTHER
      │            Kokoro), then re-listen      nodes
      ▼
  walk.save(node, value)   ← UNCHANGED validator + red-flag rules
      │
      ▼
  next node (deterministic tree — the floor)
```

**Taps never touch the LLM.** A tapped `value` on `/kiosk/{sid}/answer` behaves
exactly as it does today — deterministic, offline-capable, zero-AI floor (doc 04
law 8). The interpreter runs **only** when an utterance arrives without a tap and
the adaptive flag is on. Flag off / offline / no LLM ⇒ the kiosk shows taps.

---

## 2. V1 — clarify-only voice answers (the first shippable slice)

**Goal:** the patient can answer any node by voice; a vague answer earns exactly
**one** spoken clarifying question; the tree, the validator and the red-flag rules
are untouched. Scope is intentionally minimal so tree authority stays absolute.

### Build
1. **`app/intake/interpret.py` — `AnswerInterpreter`** (mirrors `Summarizer`'s
   shape: an `LLMInterpreter` using `llm_chain()` + `with_fallback`, and a
   deterministic `FakeInterpreter` for tests).
   - `interpret(node, utterance, lang) -> Interpretation` where `Interpretation`
     is `value: Any | None`, `clarify: str | None`, `confidence: float`.
   - Prompt gets the node's **question, type, and allowed answers** (option ids +
     patient-language labels, or min/max/unit for number/scale) and the utterance.
     It returns JSON: either `{"value": <option-id | number | [ids]>}` or
     `{"clarify": "<one short question in <lang>>"}`.
   - **Never invents options.** The prompt is constrained to the node's own option
     ids / numeric range; anything else ⇒ it must `clarify`.
2. **`prompts/interpret_answer/v1.md`** — vendor-neutral, versioned (doc 02 §2),
   `response_format: json`, variables `{question, answer_spec, utterance, lang}`.
   Logged onto the LLM call like every other prompt (`prompt_ref`).
3. **Backend seam — extend `POST /kiosk/{sid}/answer`** (NOT a new route):
   - Today: `{node_id, value, raw_text?}`. Add the case **`value is None` +
     `raw_text` present + adaptive enabled** → run the interpreter:
     - candidate value → feed into the **existing** `dispatcher.save_answer(...)`;
       if the validator rejects it, treat as a clarify (don't error).
     - `clarify` → return `AnswerOut(ok=False, clarify=..., node=<same node>)`
       (a new optional `clarify` field; the kiosk re-asks + re-listens, does not
       advance).
   - A tapped `value` skips all of this — unchanged path.
4. **Config gate:** backend `settings.intake_adaptive` (default off); the
   interpreter is only built/called when on **and** an LLM provider is real. Web
   build flag `NEXT_PUBLIC_KIOSK_ADAPTIVE=1` (mirrors the STT/TTS flags —
   Dockerfile + compose arg + `.env.example`).
5. **Frontend:** in the kiosk answer flow, when adaptive is on, a node offers
   "answer by voice" (reuse `recordToServer`), post the utterance to `/answer` as
   `raw_text` with `value: null`; on a `clarify` response, speak it (Kokoro
   `speak()`), show it as text, and re-open the mic. Taps remain on every screen.
6. **Metering & telemetry:** the interpret call emits a `usage_event`
   (`purpose=INTAKE_TURN`, `provider=local-vllm`) like every LLM call. Record per
   node: interpreted-first-try vs clarified vs fell-back-to-tap — this is the V2
   input and the routing-debt signal.

### Acceptance criteria (V1)
- Voice answer to a `single`/`scale`/`number` node maps to the correct value on the
  fake interpreter (deterministic) and, on the box, on Qwen3 for a scripted set.
- A vague utterance returns exactly **one** `clarify`, spoken in the patient's
  language; a second vague answer falls back to **taps** (never an infinite loop).
- The interpreter can **never** produce a value the node rejects (property test:
  candidate always passes through `walk.save`, invalid ⇒ clarify).
- Red-flag list is still rule-derived (the doc 02 §5 invariant test still passes).
- Flag off / no LLM / offline ⇒ kiosk is byte-for-byte today's tap flow.
- Backend suite green incl. a fake-interpreter test; `make test` clean.

---

## 3. V2 — enrichment + adaptive follow-ups (informed by V1)

V2 is **deliberately designed after V1 ships**, because its shape depends on V1
telemetry (which nodes clarify most, mis-map rates, how often patients volunteer
extra facts). The design below is the *intended* target; the numbers from V1 tune
it.

### Build (target)
1. **Enrichment — capture volunteered facts.** When a patient answers node A but
   also states something that belongs to node B ("bukhaar hai, aur teen din se
   khaansi bhi"), the interpreter returns, alongside A's value, a list of
   `(node_id, value)` **candidates for other nodes**. The engine **pre-fills**
   those — each still through `walk.save()` validation — and the walk simply skips
   an already-answered node when it reaches it. No node is answered *unasked*
   without validation; nothing bypasses a red-flag rule.
2. **Adaptive micro-follow-ups.** For a node the tree marks `adaptive: true`
   (schema addition, S18-editable), the interpreter may ask a **bounded** clarifying
   sub-question not in the tree (max 1, capped), to disambiguate before mapping —
   e.g. "aap dard 1 se 10 me kitna batayenge?" This is the tree-authority-bending
   part, so it is **opt-in per node** and the tree can always override.
3. **Skip-logic proposals.** The interpreter may propose skipping a node made
   redundant by an enriched answer; the **deterministic walker still decides**
   whether the skip is legal (a proposal, never a jump).
4. **Telemetry → tree improvement loop.** Ship a small report (per node: clarify
   rate, mis-map rate, enrichment hits) that feeds the S18 tree editor and closes
   the routing/adaptivity debt with data, not vibes.

### Acceptance criteria (V2)
- A single spoken turn that names two symptoms fills both nodes, each validated;
  the second node is not re-asked.
- An `adaptive: true` node asks at most one bounded sub-question; a non-adaptive
  node behaves exactly as V1.
- Every enriched/skipped path still ends with the **same answers JSONB** a pure-tap
  walk would produce for the same facts (the doc 03 §1 cross-tier invariant).
- The telemetry report reconciles to `usage_events` on a seeded replay.

### What V1 must expose so V2 is cheap
- The `Interpretation` type reserves (unused in V1) an `extra: list[(node_id,
  value)]` field, so enrichment is an additive change, not a re-shape.
- Per-node interpret outcomes are metered from V1 day one.

---

## 4. Sequencing

```
S-ADAPT.1 (V1)  clarify-only voice answers ─────────▶ ships, gathers telemetry
                                                         │
                              (V1 clarify/mis-map rates) │ tunes
                                                         ▼
S-ADAPT.2 (V2)  enrichment + adaptive follow-ups ──▶ + tree-improvement report
                                                         │
                                                         ▼
              (intelligence proven, turn-based)     S-OSS.2 full-duplex V2V
                                                     wraps this in Pipecat streaming
```

- **V1 before V2** is a hard order — V2's scope is set by V1's data.
- **Both before full streaming V2V.** Streaming (S-OSS.2) is a *transport* over the
  same interpreter; building it first would mean debugging turn-taking and dialogue
  quality simultaneously. Its natural home is also the **phone** channel (no
  screen), not the touchscreen kiosk — the kiosk keeps taps as a first-class input.

---

## 5. Invariants this track must not break (guardrails)

1. **Rules decide red flags, not the model** (doc 02 §5). The interpreter proposes
   values; flags are recomputed by `walk.red_flags()` after save, always.
2. **The tree is the floor.** Every candidate value passes the **existing**
   `walk.save()` validator. The interpreter cannot invent an option, exceed a
   range, or write an unasked node without validation.
3. **Taps are the zero-AI floor** (doc 04 law 8). Adaptive is additive and gated;
   off/offline/no-LLM ⇒ today's deterministic tap flow, unchanged.
4. **Same answers JSONB across every path** (doc 03 §1). Voice, tap, enriched or
   skipped, the stored intake is identical for identical facts.
5. **One clarify, then fall back.** No infinite clarify loop — a bounded number of
   follow-ups, then taps.
6. **Everything is metered** (doc 02 §8). Each interpret call is a priced
   `usage_event`, so the cost of "adaptive" is visible on the S18 dashboard.

---

## 6. Session build-plan integration

New **parallel track S-ADAPT** (peer of the V-OSS track), added to doc 06. It
builds on: S3 (tool contract + prompt loader), S5 (IntakeEngine + dispatcher), S6
(kiosk flow), and the now-live local stack (S-OSS.0/1, doc 10). Slots after the
local voice deploy; independent of the main S9→S22 line.

- **S-ADAPT.1 — Adaptive intake turn V1 (clarify-only voice answers).**
  Load: docs 03 §1a, 11 §2, 02 §5. Build/AC per §2 above.
- **S-ADAPT.2 — Adaptive intake turn V2 (enrichment + adaptive follow-ups).**
  Load: doc 11 §3 + the V1 telemetry report. Build/AC per §3 above; scope
  finalised from V1 data before coding.

Each session ends the usual way (doc 07): green tests, commit, `sessions/…`, and a
HANDOFF update.

## 7. Handoff notes (for whoever picks up S-ADAPT.1)

- **Read first:** this doc §2 + §5, `app/intake/dispatch.py` (`save_answer` is the
  seam), `app/intake/summary.py` (`LLMSummarizer`/`FakeSummarizer` is the exact
  shape to mirror for the interpreter), `app/prompts/loader.py` (prompt format),
  `web/app/(kiosk)/kiosk/_lib/speech.ts` (`recordToServer` + `speak` are already
  the voice-in/out you reuse).
- **Don't** add a new route or a new engine — extend `/kiosk/{sid}/answer` and add
  one `AnswerInterpreter` component. The dispatcher and walker do not change.
- **Don't** let the interpreter write flags or values that skip `walk.save()`.
- **Do** meter per-node interpret outcomes from day one — it is V2's whole input.
- The `Interpretation.extra` field is reserved in V1 (unused) so V2 enrichment is
  additive.
```
