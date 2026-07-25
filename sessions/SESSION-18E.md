# SESSION-18E — Admin console + Cost & Usage Analytics (early / scoped)

**Date:** 2026-07-25 · **Scope ref:** docs/06-BUILD-PLAN.md → S18 (pulled ahead of S14 as **S18-early**)

Built ahead of S14 at the operator's request. S18-early covers every S18 panel
whose backing model exists today; the two that need models S15/S17 will settle
(protocol templates, slot templates) are deferred as explicit in-console
placeholders rather than built against a schema that would then change. Remaining
scope is **S18-late** (below).

## Acceptance criteria checklist
- [x] Non-technical user edits a tree and **publishes → live on the kiosk with no deploy.**
      The intake path now reads `app.trees.store.resolve_tree` (DB-published, disk-bank
      floor); publishing writes the row, the next intake serves it. Proven by
      `test_publish_makes_the_edit_live_on_the_intake_path`.
- [x] **Dashboard reconciles to `usage_events` exactly on a seeded replay day.**
      `test_reconciles_to_usage_events_exactly`: time-series total == breakdown total ==
      raw `Σ computed_cost_inr`, to the paisa; `%` of spend partitions to 100.
- [x] **Cost per intake visible per channel & tier** (median + p90) — unit-economics table.
- [x] **What-if recompute matches a hand calculation** — `test_what_if_matches_a_hand_calculation`
      (zero a provider → delta is exactly minus its contribution).
- [x] Every editor write is **audited** (`audit.record_admin_action`; these tables aren't
      `Clinical`) and validated (a bad tree is refused, `test_save_draft_rejects_an_invalid_tree`).
- [x] Deferred panels render an explicit **"arrives with S15/S17"** marker, not a broken link.
- [x] `make test` green (backend **840**, was 826; voice-gw 1; web typecheck+lint+48 conformance);
      `make lang-qa` clean across [en, hi, mr, te].

## What was built
- **`backend/app/analytics.py`** — the read-only analytics service over `usage_events` +
  domain tables: `time_series` (minute/day, IST day boundary), `breakdown` (provider→model→
  purpose, % of spend), `unit_economics` (₹/completed intake median+p90 by channel×tier,
  ₹/abandoned, ₹/dictation), `what_if` (edited-price-book recompute), `live_strip`,
  `anomalies` (cost/intake spike, runaway session), `ops_metrics` (intake funnel, downgrade
  proxy, by-lang). `Filters` is the five `usage_events` dimensions — new channels flow in
  with no code change.
- **`backend/app/trees/store.py`** — `resolve_tree(session, dept_key)`: latest published
  `question_trees` row for the department, disk-bank fallback; `pick()` shared with the disk
  selector. Wired into `kiosk.route_complaint`. `kiosk.select_tree` stays disk-only for the
  offline pack builder.
- **`backend/app/admin.py`** — tree draft (new version)/publish (one live version per key,
  demotes siblings)/test-run (dry walk), price-book add (versioned + cache invalidate),
  voice-pack coverage. Every mutation self-audits.
- **`backend/app/routes/admin.py`** — `require_admin` on the whole router; analytics reads +
  editor writes + cost-guard view/clear + read-only template registry + deferred markers.
  Money is `str` on the wire.
- **`backend/app/audit.py`** — `record_admin_action` for non-`Clinical` content edits.
- **`web/app/(admin)/admin/`** — full six-tab console (Cost & tokens, Operations, Trees,
  Price book, Templates & voice, Coming soon) + login gate reusing the coordinator token.

## Decisions made
- **Kiosk now serves trees from the DB, not disk** (`resolve_tree`), with the bank as the
  floor. This is what makes "publish → live" true; before S18 the kiosk never read the table
  the editor writes. No cache — one indexed query per intake start beats a stale-cache bug,
  and "live immediately" is the feature.
- **One published version per tree key.** Publish demotes every sibling to draft, so the
  resolver has an unambiguous answer and rollback (publish an older version) works.
- **What-if is the *edited-price-book* recompute, not tier-mix.** It re-scales stored per-row
  cost by a provider/model factor — exactly hand-checkable (`Σ cost·(factor−1)`). Tier-mix
  ("if phone ran V2 not V1") needs a cross-tier provider mapping and is deferred with S14.
- **Templates + voice packs are read-only** this session (registry is code-defined; packs are
  empty/TTS). Editable registry + pack upload are S18-late/S7.

## Deviations from spec
- Doc 03 §10 lists a "voice-pack manager (upload/re-record)"; shipped as a **coverage view**
  only — the pack storage format is S7's and unbuilt, so an uploader would invent a layout.
- Doc 03 §11 "abandonment points by question node" not built — needs per-node answer
  timestamps (the tree-improvement report, deferred with the visual tree editor).
- Doc 03 §11 latency-degradation anomaly not built — that series is S19's provider-health
  telemetry (Grafana), not in-app.

## Tests & evidence
- `make test`: backend **840 passed** (was 826, +14); voice-gw 1; web typecheck + lint +
  **48 conformance** green. `make lang-qa` clean.
- New tests: `tests/test_analytics.py` (6 — reconciliation, filters, what-if hand-calc, unit
  economics splits, live strip), `tests/test_admin.py` (9 — publish→live, invalid-tree
  rejection, publish audit, price add audit+cache-invalidate, duplicate refusal, test-run,
  role guard 401/403, deferred markers).
- No screenshots this session (no live stack brought up on this box); the console is
  typechecked + linted, not yet visually verified on a screen — see HANDOFF "Owed on omen".

## Known gaps / stubs introduced (mirror into STATE.md → Stubs & fakes)
- **Tree editor is version-list + publish + JSON-inspect**, not a visual node editor. On-box
  editing today is "edit the JSON / re-seed a draft, publish" — the AC path works, the WYSIWYG
  builder is S18-late.
- **Cost-guard `clear` needs the live guard process** (Redis override store); under the test
  transport it 503s. Fine in prod/`make dev`.
- **Voice-pack manager, template registry, protocol/slot editors** — read-only / deferred as above.

## Commits
- 2b05b92 — S18E: analytics service + admin console backend — reconciles to usage_events
- 0770bfb — S18E: the admin console — cost dashboard, editors, and deferred placeholders
- (session close — this file + HANDOFF)
