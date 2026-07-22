# SESSION-ADAPT-2 — Adaptive intake turn V2 (enrichment + adaptive follow-ups)

**Date:** 2026-07-22 · **Scope ref:** docs/11-ADAPTIVE-INTAKE.md §3 (S-ADAPT.2) ·
**Branch:** `feat/adaptive-intake`

> ⚠️ **BRANCH-ONLY (operator instruction).** S-ADAPT stays on `feat/adaptive-intake`
> and is **not** merged to `main`. The operator chose to keep building V2 rather
> than deploy V1 to omen first — so V2 lands ahead of the on-box validation. Deploy
> the branch to omen, prove V1+V2 on Qwen3, then fast-forward `main` only on the
> operator's explicit go-ahead.

## What was built (doc 11 §3)
V2 lets one spoken turn fill more than the asked node, and lets an opt-in node ask
a bounded sub-question — all through the same `walk.save` validator and rule engine
V1 uses (doc 11 §5 invariants intact).

- **Enrichment.** The interpreter returns `extra: [(node_id, value)]` for OTHER
  nodes the patient volunteered ("bukhaar hai, aur dard 8"). The route
  (`_stash_enrichment`) validates each against its node and stashes it in
  `SessionState.pending_prefills`. The **dispatcher auto-applies** a pre-fill via
  `walk.save` the instant the walk *reaches* that node (`_drain_prefills` in
  `app/intake/dispatch.py`) — so the node is skipped (auto-answered), never
  re-asked; an enrichment for a branch never taken stays inert and is pruned. Result
  is byte-for-byte the answers JSONB a pure-tap walk produces (doc 11 §5 inv 4).
- **Adaptive micro-follow-up.** `Node.adaptive: bool` (schema + `_NODE_KEYS` + parse
  + `to_json` round-trip). `interpret_answer` **v2** prompt receives the flag +
  the enrichment targets, and may ask ONE bounded sub-question on an adaptive node;
  non-adaptive nodes behave exactly as V1. The V1 one-clarify budget caps it.
- **Skip-logic** = realised through enrichment (a volunteered later node is skipped
  by the deterministic walker, which still validates) — no separate skip primitive
  that could leave an unasked hole.
- **Telemetry report.** Each interpret turn is recorded on `SessionState.adaptive_turns`
  ({node_id, outcome, enriched, at}) — `interpreted | clarify | exhausted` (one LLM
  call each) and `prefilled | prefill_rejected` (auto-applies, no call) — persisted
  to `Intake.adaptive_events` (JSONB; migration `a1b2c3d4e5f6`). `app/intake/
  adaptive_report.py` aggregates per-node clarify_rate / mismap_rate / enrichment_hits
  and **reconciles** the LLM-call turns to the intake's `INTAKE_TURN` usage_events
  (pre-fills excluded by design — the doc 11 §3 AC).
- **Interpreter.** `interpret(node, utterance, lang, *, others=())`; `LLMInterpreter`
  loads v2 (latest) and passes `adaptive` + `other_nodes`; `FakeInterpreter` enriches
  deterministically by matching other nodes' options in the utterance (only when the
  primary maps). `Interpretation.extra` (reserved in V1) is now live.
- **Web.** Enrichment/adaptive follow-ups are server-driven — the kiosk transparently
  gets the next *unfilled* node and the existing clarify/re-listen UI. Added
  `adaptive?: boolean` to the TS `TreeNode` for wire parity; the offline walker
  ignores it. Regenerated `walk-conformance.json` (tree JSON gained `adaptive`).

## Tests (all green)
- `tests/test_intake.py` — FakeInterpreter enrichment (+ only-when-primary-maps);
  LLMInterpreter passes adaptive/others into the v2 prompt; dispatcher pre-fill
  auto-apply (skips node, fires red-flag rule, records telemetry) + reject-drop;
  **report reconciliation** on a seeded replay (4 LLM turns == 4 usage_events).
- `tests/test_kiosk.py` — route enrichment: one voice turn pre-fills a later node,
  which is then not re-asked.
- `make test`: backend **599** green; ruff clean; `alembic check` clean;
  web tsc + eslint clean; **48** walker conformance green (fixtures regenerated).

## AC status (doc 11 §3)
- ✅ one spoken turn fills two nodes, each validated; the second not re-asked.
- ✅ `adaptive:true` node may ask one bounded sub-question; non-adaptive = V1.
- ✅ enriched/skipped path ends with the same answers JSONB as pure-tap.
- ✅ telemetry report reconciles to usage_events on a seeded replay.
- ⏳ on-box Qwen3 tuning of clarify/mis-map rates — do on omen with the report.

## Next
- **Deploy the branch to omen** (still pending from V1): `INTAKE_ADAPTIVE=1`,
  `NEXT_PUBLIC_KIOSK_ADAPTIVE=1`, mark a couple of nodes `adaptive: true` in the
  live trees, run scripted hi/en; read `adaptive_report` to tune wording/adaptivity.
- Optional S18 surface: an HTTP route over `adaptive_report` for the tree editor
  (function is ready; route deferred to S18's authenticated console).
