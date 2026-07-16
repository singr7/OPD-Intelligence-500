# HANDOFF — after Session 4

**Repo state:** branch `main`, last code commit `3225cc3` (session-close commit follows this
file). `make test` green (backend **466**, voice-gw 1, web typecheck+lint). `make dev` brings
up 11 healthy services. Postgres is on host port **5433** — read the first bullet under
"Watch out for" before anything else.

**One paragraph:** The clinical content exists and is data. `app/trees/` is the engine:
doc 03 §3's node schema with a validator strict enough that a parsed tree is safe to ask a
patient, a deterministic red-flag rule language no model participates in, and `Walk` — one
patient's position in one tree, **derived from their answers rather than stored**, which is
what will make S5's mid-session tier downgrade lossless and what makes an amendment drop the
answers stranded on the abandoned branch. `seeds/trees/` holds 11 authored trees (en+hi, 89
nodes, 40 red flags) covering all nine departments, seeded as **draft** because publishing is
an oncologist's act, not a seed script's. `app/routing.py` turns a chief complaint into a
department around the existing `routing@v1` prompt, distrusting everything the model says —
an invented department, a shy confidence, junk JSON, or an outage all route to triage with a
human. **The one AC not met is the classifier's ≥85%**, which cannot be measured without a
vendor key; the set, harness and gate are built and waiting for one live run.

## Next session (S5 — Intake Engine (all tiers) + session state)

- Objective: `IntakeEngine` exposing the shared tool contract; V1 Gemini Live session
  manager (function-call loop + audio passthrough hooks); V2 pipeline loop (STT→Flash/
  gpt-4o-mini→TTS) on the same tools; V3 deterministic walker + pre-recorded audio manifest;
  Redis session state incl. active tier; automatic downgrade on provider failure OR
  cost-guard **preserving answers**; summarizer producing doc 03 §4's contract + a
  patient-language read-back; per-intake cost finalised on completion.
- Start notes:
  - **`Walk` is already your V3 tier, and the engine under all three.** `walk.current` is
    `get_next_node`, `walk.save(...)` is `save_answer`, `walk.red_flags()` is
    `check_red_flags`, `walk.is_complete` is when `finish_and_summarize` becomes legal. Build
    the tools as a thin dispatcher over it rather than a second implementation.
  - **The downgrade AC is nearly free if you keep position derived.** Store the answers
    (`walk.to_json()`) in Redis, not a cursor; on downgrade, `Walk.from_json(tree, answers)`
    on the new tier resumes at the same question. Adding a cursor to Redis "for speed" is
    exactly the bug the design avoids — it disagrees with the answers precisely when a
    provider is failing over.
  - `Walk.to_json()` is already `Intake.answers`'s shape ({node_id: {value, text, text_en,
    at, lang}}); `text_en` is yours to fill for the doctor screen.
  - Load trees with `app.trees.bank.get(key)` / `for_department(code)` — files, not rows, so
    the draft/published status does not block you. `app.routing.classify_department()` gives
    you the department; honour `needs_human` rather than routing on a 0.3 confidence.
  - `RealtimeVoiceProvider` is still **interface + fake only** — the Gemini Live impl is
    yours (S3 left `REALTIME_PROVIDER=gemini-live` raising rather than pretending).
  - Red flags are recomputed from answers on every call, deterministically, on every tier.
    Do not cache them onto the session, and do not let the summarize prompt invent one — it
    repeats what it is given (doc 02 §5). That boundary is what lets S21 sign the rules off.
- Exact first commands:
  1. `make dev` (baseline; 11 services healthy)
  2. `make migrate && make seed` (idempotent; now also loads the 11 trees as draft)
  3. `make test`

## Watch out for

- **Port 5433, and a native Postgres on 5432.** This machine runs its own Postgres on
  127.0.0.1:5432 which *wins over Docker's bind*, so `localhost:5432` reaches the wrong
  database and fails with `role "opd" does not exist`. Compose publishes ours on **5433**;
  don't revert it. In-cluster URLs stay `postgres:5432`. pytest defaults to
  `localhost:5433/opd_test`. voice-gw is on 8090 (8080 taken). Others: web 3000, api 8000,
  grafana 3001, uptime-kuma 3002, loki 3100, redis 6379.
- **`.env` is gitignored and yours is stale.** S3 added ~30 keys; `make .env` only copies
  when the file is *missing*. Everything defaults to `fake`, so a stale `.env` runs fine and
  silently ignores any vendor you configure.
- **A tree is only as validated as `parse()`.** `Tree` objects can only be built by
  `app.trees.schema.parse`, so if you find yourself constructing one another way (or reading
  `question_trees.tree` and using the dict directly), you have skipped every check —
  including the ones that stop an unreachable question or an unfireable red flag.
- **The Hindi and the clinical content are unreviewed.** A model wrote both. The tests prove
  the text is *present* and the structure sound; they cannot prove it is good Hindi or good
  medicine. Nothing should go near a real patient before S21's review pack.
- **`Walk.save()` prunes.** Amending an early answer silently deletes the answers on the
  branch you left. That is deliberate and tested — but if S5 caches anything derived from
  `walk.answers` (a summary, a cost, a red-flag list), it must recompute after every save.

## Decisions needed from the human

- **Ratify dropping `question_trees.lang`** (S4's schema change, migration `fbcaee31fa43`).
  Doc 02 §4 sketched one tree row per language; doc 03 §3 then put every language inside the
  node (`text:{en,hi,mr,te}`). S4 kept doc 03 §3: doc 03 §1 makes language switchable
  mid-intake, so embedded text is a re-render while per-language rows are a tree swap that is
  only safe if the rows happen to share node ids and branching — and four rows means four
  copies of the branching and red flags under one clinical sign-off. If you agree, doc 02
  §4's `question_trees` line should lose `lang`, the way `PriceUnit.CHAR` was ratified in S3.
  While there: line 73 points at "doc 03 §4" for the tree schema; it is **§3**.
- **Five minutes with a `GEMINI_API_KEY` closes S4's open AC.** `make eval-routing` scores
  the 60-utterance set and gates at 85%. Nobody can honestly claim that number until a real
  model answers; if it comes in low, the fix is a `routing/v2.md` (v1 is immutable), and the
  harness prints a confusion matrix to aim it.
- **The trees need an oncologist before go-live** — they are seeded `draft` and S21 builds
  the review pack, but if a clinician can read them sooner, the content is 11 JSON files and
  the thresholds are worth challenging now (fever ≥38 within 14 days; pain ≥8 urgent;
  vomiting >5×/day; ESAS distress ≥7). Cheaper to change now than after S6–S8 build on them.
- **Still open from S3, neither blocking:** the SMS vendor pick (both work; note that Exotel
  for SMS puts SMS and the phone intake channel in one failure domain, MSG91 splits them), and
  whether the seeded price-book rates match your real contracts (they are public list prices
  at ~₹84/USD, rounded up — every unit-economics number rests on them).

## Backlog additions

- **Surgical Oncology "new lump/lesion intake" tree** — doc 03 §3 lists it; doc 06's S4 line
  did not, so it was not built. Walk-ins with a new lump currently get `surg_onc_post_op`,
  which asks about an operation they have not had. **S18** (tree builder) or sooner.
- **Red-flag satisfiability is only checked for `and`-rooted rules, in tests, not the
  validator.** `or` across branches is legitimate and `unanswered` is satisfied by a node
  being off-path, so the general check needs real satisfiability rather than reachability —
  **S18**, when non-engineers start authoring and the check has to be live.
- **Red flags and their instruction text are per-tree and duplicated** across the med-onc
  trees (a tree is the unit of publish and sign-off, deliberately), so they can drift. A
  shared rule library belongs in **S18**'s editor if it wants one.
- **No node has `audio`** — V3's kiosk voice packs are **S7**, real human recordings **S21**.
- **`app/evals.py`'s loader is generic** — S10's dictation mapping and S17's grading can reuse
  the shape — **S10/S17**.
- Carried from S3: WhatsApp metered per message vs Meta's 24h conversation (**S12**, before
  S18's invoice reconciliation is honest); cached tokens priced at full `token_in` rate, wants
  a `token_cached` unit (**S18**); nothing schedules `CostGuard.evaluate()`, so the guard would
  never fire in production (**S17**); Sarvam STT reports no confidence (**S13**); enum columns
  have no CHECK constraint despite the docstring (**S18/S20**); `/providers/health` is
  unauthenticated (**S19/S20**); Realtime/Gemini Live impl (**S5**/**S14**).
- Carried from S2: staff username+TOTP login (S18); rate-limit OTP verify by IP (S20); prune
  `otp_codes` (S17); audit log daily S3 export + retention (S19); soft-delete filtering (S8+).
- Carried from S1: provision Grafana datasource + dashboards (S19); pin dependency versions.
