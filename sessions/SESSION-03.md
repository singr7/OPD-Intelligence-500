# SESSION-03 — Provider layer, usage metering + prompt library

**Date:** 2026-07-15 · **Scope ref:** docs/06-BUILD-PLAN.md → S3

## Acceptance criteria checklist
- [x] **Contract tests pass against fakes** — all seven interfaces
      (RealtimeVoice/LLM/STT/TTS/SMS/Messaging/Telephony) have a fake; contract
      tests cover health, breaker, retry, fallback and failure semantics.
- [x] **Every fake call produces a priced usage_event** —
      `test_every_fake_call_is_metered_and_priced` drives one call per interface
      and asserts a priced row for each. Also verified end-to-end against the
      real stack (see Evidence).
- [x] **`providers/health` reports each provider** — plus breaker state, and an
      `unpriced` list (usage we metered but could not price).
- [x] **Cost-guard breach flips tier flag in config** — breach forces a tier
      override the intake engine reads; per channel, IST-daily, ratcheted.
- [x] **Provider swap is config-only** —
      `test_swapping_the_sms_vendor_is_config_only` proves it for the vendor
      decision that is actually open.
- [x] **`prompts/` with vendor-neutral versioned prompts** — routing, summarize,
      dictation_map, checkin_personalize + the V1/V2 shared tool contract.

## What was built
- `app/providers/base.py` — `Provider`, health, `with_fallback`. Metering is
  **structural**: `_invoke()` is the only way to reach a vendor, and it times,
  meters, prices, retries and health-tracks every call. Impls write `_send`/
  `_complete` and report usage on a `MeterCall`; they never touch the recorder,
  so they cannot forget it (doc 02 §9: "a provider without metering fails review").
- `app/providers/metering.py` — buffered, batched `usage_events` writer +
  `usage_scope()` contextvar for attribution (an intake's cost is a GROUP BY on
  `intake_id`, and no provider signature had to learn about intakes to make that
  true). Drops oldest under back-pressure rather than blocking a live call.
- `app/providers/pricing.py` — `price_book` lookup + cost computation. Tokens and
  chars priced per 1,000; `*` wildcard rows for flat per-provider rates.
- `app/providers/resilience.py` — retry (shallow, jittered) + circuit breaker.
  `ProviderUnavailable` is the single "fall back / downgrade tier" signal.
- `app/providers/costguard.py` — budgets → verdicts → tier override
  (`TierOverrideStore`: in-memory local, Redis in prod). **Degrades, never denies.**
- `app/providers/registry.py` — config → instances; the config-only promise.
- Seven interfaces + fakes + real impls: **MSG91 and Exotel SMS** (both, per the
  human's decision), Gemini Flash, OpenAI, Sarvam/Google STT, Sarvam/Google TTS,
  Meta WhatsApp, Exotel telephony. Realtime = interface + fake only (see below).
- `app/prompts/` — loader (versioned, strict `{{ var }}` rendering) + `tools.py`
  (the four-function V1/V2 contract). `backend/prompts/*/v1.md` — four prompts.
- `GET /providers/health`; meter drain + cost guard on the app lifespan.
- `seeds/price_book.json` (19 rows) loaded idempotently by `make seed`.

## Decisions made
- **Both SMS vendors ship, neither is chosen.** Per the human's instruction. The
  pilot's open decision is now `SMS_PROVIDER=msg91|exotel` and nothing else, and
  a test enforces that. Whichever account clears DLT first wins; the other stays
  as failover. Noted for the human: Exotel is already the telephony vendor, so
  picking it for SMS means one outage takes SMS *and* the phone channel down
  together — MSG91 keeps those failure domains apart.
- **Metering is structural, not reviewed.** Doc 02 §9 asks reviewers to catch
  unmetered providers; a template-method base makes it unreachable instead.
- **Fakes have distinct names** (`fake-sms`, `fake-llm`, …), not a shared "fake".
  They are `usage_events.provider` values and price-book join keys.
- **The guard downgrades from where a channel *is*,** not from the top of the
  ladder — otherwise a breached channel pins at V2 and never reaches the free
  tier (see Deviations/bugs).
- **`assert_production_safe` covers every provider**, not just SMS, walking
  `PROVIDER_SETTINGS` so a new interface cannot ship pointing at its fake.
- **Realtime is interface + fake only.** The Live session manager is S5's build
  and the audio bridge S14's (doc 06). A websocket protocol written here, two
  sessions before anything drives it, would be written blind and rewritten twice.
  `REALTIME_PROVIDER=gemini-live` raises rather than promising a tier that cannot run.

## Deviations from spec
- **Added `PriceUnit.CHAR` + `usage_events.characters`** — **ratified by the human
  at session end ("bill per character is fine"), and doc 02 §4 updated to match**,
  so this is no longer a deviation. Both TTS vendors (Sarvam Bulbul, Google) bill
  **per character**, not per second of audio produced. Without a char unit, TTS
  cost is an estimate derived from output duration, and S18's AC ("dashboard
  numbers reconcile to usage_events exactly" + monthly invoice reconciliation) is
  unmeetable by construction. Migration `8d11748ba95e`; the enum itself needed no
  DDL. Doc 02 §4 also gained a note on units vs. quanta (per-1,000 for
  token/char; `audio_sec` xor `call_min`).
- **Cached tokens are priced at the `token_in` rate.** Vendors discount them
  (~25% on Gemini), so this over-estimates — deliberately: a cost-guard that
  under-reports is the failure that hurts. A `token_cached` unit belongs with the
  S18 price-book editor. Backlogged.
- **WhatsApp meters per message; Meta bills per 24h conversation.** Over-counts.
  Conversation-window state is S12's build, which is where the fix belongs.
  Backlogged — must close before S18's invoice reconciliation is honest.

## Bugs found and fixed while building
- **Audio double-billing.** `audio_seconds` is the quantity for both `audio_sec`
  and `call_min`, so any provider with rows for both units was charged twice for
  every voice minute. `price()` now resolves to exactly one audio unit
  (`call_min` wins where it exists). Regression test added.
- **The cost guard could never reach V3.** It computed `downgrade(CONVERSATIONAL)`
  on every evaluation, so a breached channel sat at V2 — still spending, still
  breaching, never reaching the free tier. Now steps down from the current tier,
  so doc 02 §8's V1→V2→V3 actually happens.
- **Fakes collided in the price book** (all named "fake"): an STT call matched
  telephony's per-minute rate. Fixed by distinct names.

## Tests & evidence
- `make test`: **green** — backend **231** (was 87), voice-gw 1, web typecheck+lint.
- New tests (144): `test_providers_contract.py` (45), `test_providers_vendors.py`
  (23), `test_prompts.py` (23), `test_costguard.py` (17), `test_providers_registry.py`
  (17), `test_providers_metering.py` (14), + 5 in `test_config.py`.
- Vendor impls are exercised through `httpx.MockTransport`: the real
  request-building and response-parsing run, and assertions are on the bytes we
  would put on the wire. No live vendor call in tests (doc 07 §4).
- **End-to-end on the real stack** (not just fixtures): `POST /auth/otp/request`
  against the running container → fake SMS → metered → drained by the background
  task → priced → row in Postgres:
  ```
   provider |  model   | purpose | computed_cost_inr | priced |     minute_bucket
   fake-sms | fake-sms | other   |            0.2000 | t      | 2026-07-15 17:02:00+00
  ```
  This exercises the lifespan, the drain task, pricing and the DB write in a real
  process — the parts a test with `ASGITransport` never touches.
- `alembic check` clean; `make seed` idempotent (19 price rows).

## Known gaps / stubs introduced
(Mirrored into STATE.md → Stubs & fakes)
- **No live vendor has accepted a call.** Every real impl is written against
  documented APIs and mock-tested; endpoints, DLT template ids, sender ids and
  auth are per-account. First live send needs a human watching a handset.
- Realtime/Gemini Live: interface + fake only (S5/S14).
- Sarvam STT reports no confidence → `confidence=None`. Doc 03 §4's
  `[unclear: ...]` contract leans on Google's confidence until that is solved.
- Cost guard is evaluated on demand only — no Celery beat schedule yet (S17
  gives beat real work). Nothing calls `evaluate()` periodically in production.
- `prompts/` are en-only prompt *text*; mr/te patient-facing strings are S13.

## Commits
- e0c00dc — S 03: add provider layer, usage metering, pricing, cost guard, prompt library
- c132441 — S 03: test the provider layer, metering, cost guard and prompt library
