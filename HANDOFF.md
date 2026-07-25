# HANDOFF — after Session S18E (Admin console + analytics, pulled ahead of S14)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`.
>
> **⚠️ CI is off (2026-07-23, operator).** `.github/workflows/ci.yml` is intact but
> its `push`/`pull_request` triggers are commented out. Run it by hand:
> `gh workflow run ci.yml`. **`make test` locally is the only gate right now** — and
> S13 added `make lang-qa` (also a CI step and a pytest test).
>
> **🚩 Adaptive intake (S-ADAPT) is on `main` but NEVER PROVEN with its flags on.**
> `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the defaults, so `main`
> behaves as the pure-tap kiosk until a human flips them. See "Owed on omen". Unchanged by S18E.

**Repo state:** **`main`** — S18E built straight on it (two feature commits + close).
`make test` green: backend **840** (826→840), voice-gw 1, web typecheck+lint clean, 48
conformance. `make lang-qa` clean across [en, hi, mr, te]. **No migration in S18E** — the
console reuses existing tables (`question_trees`, `price_book`, `usage_events`, `audit_log`);
no schema change. Postgres on host port **5433**; voice-gw on 8090.
**Return to S14 next** (S18E was pulled ahead of it; the mainline sequence resumes at S14).

⚠️ `make lint` is still **failing on 11 pre-existing unformatted files** (none S18E's — the
new code is `ruff format`-clean). Not in `make test`, red a while. Worth one `ruff format .`
commit before it grows.

**One paragraph:** S18E is the admin console + cost/usage analytics, pulled ahead of S14 at
the operator's request (see "Why out of sequence"). It ships every S18 panel whose model
exists today and defers the two that don't (protocol templates → S17, slot templates → S15)
as honest in-console placeholders. The load-bearing win is two-sided: the **analytics
dashboard reconciles to `usage_events` to the paisa** on a seeded replay day (proven), and a
**tree publish is live on the kiosk on the next intake with no deploy** — because the intake
path now reads DB-published trees (`app.trees.store.resolve_tree`) with the disk bank as the
floor, instead of only ever reading disk. Every editor write validates and self-audits
(`audit.record_admin_action`, since these content tables aren't `Clinical`). The dashboard's
filters are the five `usage_events` dimensions, so S14/S15/S17 usage flows in as new filter
values with **no dashboard change** — the only additive step per channel is extending the
seeded replay day.

## Why out of sequence (S18E, not S18, not S14)
Built as **S18-early**: the panels backed by models that exist now. Two panels were left as
markers because building them now is rework — their models are set by later sessions:
**protocol-template editor → S17** (regimen families; only `CheckinPlan` exists), **slot-
template editor → S15** (no slot inventory yet). Those two + the visual node editor + editable
template registry + voice-pack upload are **S18-late**, to fold in after S15/S17.

## Next session — S14 (Telephony voice gateway (Exotel) part 1: pipeline)
- Objective: `voice-gw` — Exotel Voicebot WS ↔ **V1 Gemini Live bridge** + a **V2 STT↔TTS**
  path per tier config; per-minute audio metering into `usage_events`; barge-in; DTMF
  fallback; consent line; call-state persistence; a local fake-Exotel replay harness.
- **Load:** docs 02 §5, 03 §1b.
- **AC:** fake-client e2e on both V1 and V2 (V1 <1.5s, V2 <3.5s p90 turn latency, measured);
  barge-in; partial saves on hangup; call cost recorded per intake.
- **S18E gives S14 a live dashboard to watch itself land in.** As soon as voice-gw emits
  `channel=phone` usage_events, they appear under the admin dashboard's channel filter with no
  work — a free instrument for the latency/cost ACs. Extend the seeded replay day
  (`tests/test_analytics.py`) to include a phone row so the reconciliation test covers it.
- **Start from `main`.** First commands:
```
make dev && make migrate && make seed && make test    # expect 840 backend green
make lang-qa                                           # expect clean across [en,hi,mr,te]
```

## Watch out for (S18E fragile edges)
- **The kiosk reads trees from the DB now** (`store.resolve_tree`), disk bank only as
  fallback. If a box has trees in `question_trees` but none **published**, the kiosk falls
  through to disk — so on a fresh box run `make seed` (draft) then publish from the console,
  or `make seed` with `--publish-trees`. A published-but-unparseable row is skipped, not fatal.
- **Publishing demotes every other version of that key to draft** (one live version per key).
  Intended — it makes rollback work — but means "publish" is also "unpublish the old one".
- **What-if is the price-book recompute, not tier-mix.** Do not wire a tier-mix button to it;
  tier-mix is a different (harder) recompute deferred with S14.
- **Cost-guard `clear` needs the running guard process** (Redis override store) — 503s under
  the test transport / a process with no guard. Works in prod and `make dev`.
- **Money is a wire string** through the whole admin path (`Decimal` server-side). Keep it
  that way — no `Number()` arithmetic on ₹ in the browser; display only.

## Decisions needed from the human
- **mr/te still need a native + clinical review before a patient reads them** (carried from
  S13; S21). Unchanged by S18E.
- **Whoever next has the box: "Owed on omen" is still unclaimed** (below). S18E adds one item:
  the console has not been seen rendered on a screen (typecheck/lint only) — a visual pass is owed.

## Owed on omen (before adaptive / mr-te / the console face real use)
- **Admin console visual pass** — bring the stack up, sign in as admin (+915550000001), walk
  the six tabs, publish a tree edit and confirm it changes a live kiosk intake. Cheapest proof
  of the S18E headline AC on a real screen. *(new)*
- **Telugu kiosk render** — switch to తెలుగు, confirm glyphs (not tofu) and ≥1.6 line-height at
  200% font scale (doc 04 §4). Carried from S13.
- **Adaptive on:** flags to `1`, mark 1–2 live-tree nodes `adaptive: true`, re-seed,
  `docker compose up -d --build api web` (**web rebuild required** — the flag is a build arg).
  Provoke a vague answer, a volunteered fact, an unmappable answer. Rollback = flags to `0` + rebuild.
- **Doctor console + consult note on-box** (`/doctor`, `+915550001001`) — the real-Qwen3
  dictation + `_was_said` pass is still owed. `make eval-dictation` wants the same session.

## Backlog additions (S18E) → S18-late
- **Visual tree node editor** (WYSIWYG add/edit option, drag branch) — this session shipped
  version-list + publish + JSON inspect + a server `test-run` endpoint; the graphical builder is owed.
- **Editable message-template registry** (DB-backed, replacing the code registry) — pairs with S15.
- **Voice-pack upload/re-record** — needs S7's pack storage format; coverage view only today.
- **Protocol-template editor** → S17; **slot-template editor** → S15 (deferred markers live now).
- **Node-level abandonment + tree-improvement report** (doc 03 §11) — needs per-node answer
  timestamps; pairs with the visual editor.
- **Tier-mix what-if** — needs a cross-tier provider mapping (S14 gives telephony real V1/V2 shapes).
- **Latency-degradation anomaly** — S19 provider-health telemetry.
- Carried, unchanged: `make lint` red on 11 pre-existing files (one `ruff format .` clears it);
  mr/te unreviewed (S21); Telugu never seen rendered; no mr/te STT/TTS on a live vendor.
