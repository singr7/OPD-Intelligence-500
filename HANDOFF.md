# HANDOFF — after Session 3

**Repo state:** branch `main`, last code commit `c132441` (session-close commit follows this
file). `make test` green (backend **231**, voice-gw 1, web typecheck+lint). `make dev` brings up
11 healthy services. Postgres is on host port **5433** — read the first bullet under "Watch out
for" before anything else.

**One paragraph:** Every external dependency now sits behind an interface with a fake, and
metering is no longer something a provider can forget: `Provider._invoke` (app/providers/base.py)
is the only path to a vendor, and it times, meters, prices, retries and health-tracks every call.
Costs land in `usage_events` priced against `price_book` (seeded, 19 rows), attributed to an
intake by a contextvar rather than by threading ids through every signature. The cost guard turns
a daily budget breach into a tier downgrade — degrade, never deny. `prompts/` holds four
versioned vendor-neutral prompts plus the four-function tool contract that will make V1/V2 tier
switching lossless. **The SMS decision is resolved as instructed: MSG91 and Exotel are both
implemented and both config-switchable, so the pick is now `SMS_PROVIDER=` and nothing else.**
Still no trees, no intake logic, no real UI — S4 starts the clinical content.

## Next session (S4 — Question-tree engine + oncology tree bank v1)
- Objective: tree JSONB schema + validator, deterministic tree-walker, red-flag rule evaluator,
  **author the actual trees** (med-onc new patient, between-cycle CTCAE-lite, pain, radiation
  review, surgical post-op, palliative ESAS-lite, 5 routing trees) in en+hi, plus the
  dept-classification prompt with a 60-utterance eval set.
- Start notes:
  - **The routing prompt already exists** — `backend/prompts/routing/v1.md`, loaded via
    `app.prompts.load("routing")`. S4 writes the eval set and the classifier around it; if the
    prompt needs changing, add `v2.md` (published versions are immutable — see the loader docstring).
  - `question_trees` is migrated with **no writers**; the tree JSONB schema is S4's to build.
    `TreeStatus` (draft/published) already exists.
  - Call the LLM through `app.providers.get_llm_provider()` / `llm_chain()` + `with_fallback` —
    never a vendor SDK. `FakeLLMProvider.queue(FakeLLMScript(text=...))` scripts deterministic
    replies; the eval set must not make live calls.
  - Wrap classifier calls in `usage_scope(...)` with `purpose=UsagePurpose.ROUTING`, so the S18
    dashboard can separate routing cost from intake-turn cost. It is one kwarg now and
    unrecoverable later.
  - The **tool contract** (`app/prompts/tools.py`) already declares `get_next_node` /
    `save_answer` / `check_red_flags` / `finish_and_summarize`. S4's walker is what those tools
    call in S5 — build it to that shape. Changing the contract means a new version, not an edit.
  - Red-flag rules are **deterministic and run on every answer regardless of tier** (doc 02 §5).
    The summarize prompt repeats the flags it is given verbatim and never invents one — keep that
    boundary; it is what lets an oncologist sign off on the rules (S21).
- Exact first commands:
  1. `make dev` (baseline; 11 services healthy)
  2. `make migrate && make seed` (seed is idempotent; now also loads price_book)
  3. `make test`

## Watch out for
- **Port 5433, and a native Postgres on 5432.** This machine runs its own Postgres on
  127.0.0.1:5432 which *wins over Docker's bind*, so `localhost:5432` reaches the wrong database
  and fails with `role "opd" does not exist`. Compose publishes ours on **5433**; don't revert it.
  In-cluster URLs stay `postgres:5432`. pytest defaults to `localhost:5433/opd_test`. Port 8080
  is still taken, so voice-gw is still on 8090. Others: web 3000, api 8000, grafana 3001,
  uptime-kuma 3002, loki 3100, redis 6379.
- **`.env` is gitignored and yours is now definitely stale.** S3 added ~30 keys (the `*_PROVIDER`
  selectors, vendor creds, `DAILY_BUDGET_INR`). `make .env` only copies when the file is
  *missing*, so append the new block from `.env.example` by hand. Everything defaults to `fake`,
  so a stale `.env` still runs — it just silently ignores any vendor you configure.
- **Metering is silent by design.** `record()` swallows everything and drops rows under
  back-pressure rather than blocking a patient-facing call. If usage_events look thin, check
  `meter.dropped` and `/providers/health`'s `unpriced` list before assuming the call path broke.
  In tests there is no drain task: `await meter.flush()` explicitly (the `meter` fixture writes
  into the test's rolled-back transaction).
- **A new provider needs a price_book row or it silently costs ₹0** — which the cost guard reads
  as "budget to spare". `/providers/health` → `unpriced` is the tripwire. Add rows to
  `seeds/price_book.json` (natural key provider+model+unit+effective_from; `model: "*"` for flat
  rates). **Never edit a rate in place** — add a row with a later `effective_from`, or you
  silently re-interpret every historical cost.
- **The enum columns have no CHECK constraint.** `app/models/enums.py` claims "VARCHAR + CHECK",
  but `enum_type` sets `native_enum=False` and SQLAlchemy 2.0 defaults `create_constraint=False`,
  so nothing at the DB level rejects a bad enum value. Found in S3 (it made adding
  `PriceUnit.CHAR` free). Not fixed: the docstring is wrong, not the schema. Backlogged.

## Decisions needed from the human
- ~~Ratify `PriceUnit.CHAR` + `usage_events.characters`.~~ **Ratified** ("bill per character is
  fine"). Doc 02 §4 now lists `char` and `characters`, with a note on units/quanta. Settled — do
  not reopen.
- **The SMS pick is now unblocking but not urgent** (both vendors work; `fake` runs locally). One
  thing to weigh when you do pick: Exotel is already the telephony vendor, so choosing it for SMS
  means one outage takes down SMS *and* the phone intake channel together — MSG91 keeps those
  failure domains apart. Either way the DLT template must declare variables named `otp` and
  `minutes` (see `.env.example`).
- **Are the seeded price-book rates close enough to your real contracts?** They are public list
  prices converted at ~₹84/USD, rounded *up*. Every unit-economics number from here — cost per
  intake, the S18 dashboard, the cost-guard thresholds — is built on them.
- **Nothing blocking S4.**

## Backlog additions
- **WhatsApp is metered per message; Meta bills per 24h conversation** — over-counts. Fix where
  the window state lives — **S12**. Must close before S18's invoice reconciliation is honest.
- **Cached tokens are priced at the full `token_in` rate** (vendors discount ~25%). Over-estimates
  deliberately. Wants a `token_cached` unit — **S18**, with the price-book editor.
- **Nothing schedules `CostGuard.evaluate()`** — on-demand only, so in production the guard would
  never actually fire. Needs a Celery beat job — **S17**, when beat gets real work.
- **Sarvam STT reports no confidence**, so doc 03 §4's `[unclear: ...]` contract leans on Google's.
  Revisit when Saarika exposes one — **S13**, with the language QA harness.
- **Enum CHECK constraints don't exist** (see "Watch out for") — fix the docstring or add the
  constraints — **S18/S20**.
- **`/providers/health` is unauthenticated.** It reports vendor names and health only, never keys,
  but it should get auth or be edge-restricted before the box is public — **S19/S20**.
- Realtime/Gemini Live impl — **S5** (session manager) / **S14** (voice-gw bridge).
- Carried from S2: staff username+TOTP login (S18); rate-limit OTP verify by IP (S20); prune
  `otp_codes` (S17); audit log daily S3 export + retention (S19); soft-delete query filtering
  (S8+).
- Carried from S1: provision Grafana datasource + dashboards (S19); pin exact dependency versions.
