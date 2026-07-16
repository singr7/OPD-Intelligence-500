# HANDOFF — after Session 5

**Repo state:** branch `main`, last code commit `514f433` (session-close commit follows this
file). `make test` green (backend **486**, voice-gw 1, web typecheck+lint). `make dev` brings
up 11 healthy services. Postgres is on host port **5433** — read the first bullet under
"Watch out for" before anything else.

**One paragraph:** The intake is now a running engine. `app/intake/` is one `IntakeEngine`
that drives an intake across the whole tier ladder — **V1** (a Gemini Live session bridge,
audio streamed out through a passthrough hook for voice-gw), **V2** (an STT→LLM→TTS turn
pipeline), **V3** (the deterministic walker + pre-recorded audio, offline) — all calling the
**same four-tool contract over one `Walk`** via `ToolDispatcher`, so the answers JSONB is
identical from every tier by construction. `SessionState` (Redis in prod, in-memory local)
stores the **answers, not a cursor**, plus the configured and active tier; a provider failure
or a cost-guard breach downgrades a rung and rebuilds the walk from those answers, losing
nothing (both paths tested). The summariser produces doc 03 §4's contract plus a
patient-language read-back — on the LLM for V1/V2, on a deterministic template for V3 (so a
completed intake never needs a network), and the red flags are always the rule engine's, never
the model's. `finalize_cost` sums the intake's `usage_events` onto `Intake.cost_inr` and
reconciles to the paisa. What the engine is **not** yet is connected to anything: no route, no
websocket. S6 builds the first channel — the kiosk — on top of it.

## Next session (S6 — Kiosk PWA part 1: flow + design system)

- Objective: design tokens + component library (OptionCard, FacesScale, BodyMap, Stepper,
  AssistantAvatar, AudioBar); the kiosk flow screens — language → caregiver toggle → voice
  chief complaint (Web Speech + server STT toggle) → tree questions with auto-read-aloud →
  summary read-back + confirm → token screen; Playwright screenshot suite + a self-critique
  pass per **docs/04-UIUX-GUIDE.md §5** (mandatory — frontend session, anti-generic clause
  applies to every screen).
- Start notes:
  - **The kiosk is a V3 client of the engine.** It drives the tools directly from taps:
    `dispatcher = engine.dispatcher(state)`, then `get_next_node()` renders the screen (it
    returns `{node:{id,type,text,options[{id,text,icon}],min,max,unit,audio}}` already in the
    patient's language), a tap calls `save_answer(node_id, value, raw_text=...)`, and
    `finish_and_summarize` returns the read-back. You do **not** re-implement the walk — build
    the UI over `app.intake.ToolDispatcher`. See `tests/test_intake.py::
    test_dispatcher_walks_the_tree_and_finishes` for the exact call shape.
  - **Q1 (voice chief complaint) is the one place the model judges** — feed the transcript to
    `app.routing.classify_department`, honour `needs_human` (send to the desk, don't guess a
    0.3), then `app.trees.bank.for_department(guess.dept_key)` to pick the tree and
    `engine.start_session(tree=..., channel=Channel.KIOSK, lang=..., chief_complaint=...)`.
  - **The engine has no HTTP surface yet — you are adding it.** Decide the kiosk API: either
    thin REST endpoints wrapping the dispatcher (start / next / answer / finish) or a
    websocket. Keep the tool contract as the wire shape so S14's telephony reuses it. Build the
    session store with `app.intake.build_session_store(settings)` and one `IntakeEngine` per
    process (it holds no per-intake state).
  - **V3 audio is TTS today.** `voicepack.resolve` returns synthesised prompt audio (no
    recordings exist). The kiosk's auto-read-aloud can use the returned clip or the browser's
    own Web Speech; `node.audio` is authored-empty until S7/S21.
  - `Intake.answers` / `red_flags` / `summary_md` are populated by `finalize_cost` — but that
    needs an `Intake` row to exist. The kiosk creates the Visit+Intake (it knows the patient /
    walk-in) and passes `intake_id`/`visit_id` into `start_session`.
- Exact first commands:
  1. `make dev` (baseline; 11 services healthy)
  2. `make migrate && make seed` (idempotent; loads the 11 trees as draft)
  3. `make test`

## Watch out for

- **Port 5433, and a native Postgres on 5432.** This machine runs its own Postgres on
  127.0.0.1:5432 which *wins over Docker's bind*, so `localhost:5432` reaches the wrong
  database and fails with `role "opd" does not exist`. Compose publishes ours on **5433**;
  don't revert it. In-cluster URLs stay `postgres:5432`. pytest defaults to
  `localhost:5433/opd_test`. voice-gw is on 8090. Others: web 3000, api 8000, grafana 3001.
- **The V2 pipeline is turn-based, not token-streamed, and does not feed tool results back to
  the LLM mid-turn.** The `LLMProvider` interface has no tool-result message type, so the
  engine injects the current question into the per-turn prompt instead of a true tool loop.
  Fine for kiosk/WhatsApp; S14's real-time telephony will want streaming + a tool-result turn
  on `LLMProvider`, which is a versioned contract change, not an edit.
- **A downgrade rebuilds the walk from `state.answers`.** Anything the kiosk caches that is
  derived from the answers (a rendered summary, a progress bar, a red-flag banner) must be
  recomputed after every `save_answer` — `Walk.save` prunes answers stranded on an abandoned
  branch, so an amendment can *remove* an answer and its flag (tested).
- **`finalize_cost` needs the meter drained first.** In the app the lifespan drain handles it;
  in a request you may need to let the batched meter flush before the number is complete.
  Money is `Decimal` end to end — don't let a float into a cost path (S18 reconciles exactly).
- **The Hindi and the clinical content are still unreviewed** (a model wrote both). Tests
  prove structure and presence, not good Hindi or good medicine. Nothing goes near a real
  patient before S21's review pack. The kiosk read-back is the patient's only check — get
  its phrasing in front of a Hindi speaker when one is available.

## Decisions needed from the human

- **Ratify dropping `question_trees.lang`** (S4's schema change, migration `fbcaee31fa43`) —
  still open from S4. Doc 03 §3 embeds every language in the node; the per-language row in doc
  02 §4 is the casualty. If you agree, doc 02 §4's `question_trees` line should lose `lang`
  the way `PriceUnit.CHAR` was ratified in S3.
- **Five minutes with a `GEMINI_API_KEY` closes S4's open AC.** `make eval-routing` scores the
  60-utterance set and gates at 85%; nobody can honestly claim that number until a real model
  answers. The classifier is now on the kiosk's critical path (Q1), so this matters more.
- **The trees need an oncologist before go-live** — seeded `draft`, S21 builds the review
  pack, but the thresholds are worth challenging now (fever ≥38 within 14 days; pain ≥8
  urgent; vomiting >5×/day; ESAS distress ≥7). Cheaper to change before S6–S8 build on them.
- **Still open, neither blocking:** SMS vendor pick (Exotel puts SMS + phone intake in one
  failure domain, MSG91 splits them); whether the seeded price-book rates match your real
  contracts (every unit-economics number rests on them).

## Backlog additions

- **`LLMProvider` needs a tool-result message type** for a true multi-step tool loop within a
  turn (and streaming) — **S14**, when telephony's latency budget makes the turn pipeline too
  slow. Until then V2 mediates `get_next_node` by injecting the question into the prompt.
- **`Intake.answers[*].text_en` is unfilled during intake** — the doctor-screen English gloss
  per answer is left to the summariser. A per-answer translation pass wants an LLM call —
  **S9** (doctor console) or S13 (multilingual), whichever needs it first.
- **A kiosk idle-reset / abandoned-session sweeper** — `RedisSessionStore` has a TTL, but a
  walked-away kiosk intake should reset the screen too (doc 03 §1a: 90s idle) — **S6/S7**.
- **Surgical Oncology "new lump/lesion intake" tree** — doc 03 §3 lists it; a new-lump walk-in
  currently gets `surg_onc_post_op`, which asks about an operation they have not had — **S18**.
- Carried from S3/S4: red-flag `or`/`unanswered` satisfiability check needs real satisfiability
  not reachability (**S18**); shared red-flag rule library to stop per-tree drift (**S18**);
  WhatsApp per-message vs Meta's 24h conversation billing (**S12**); cached tokens priced at
  full `token_in` rate, wants a `token_cached` unit (**S18**); nothing schedules
  `CostGuard.evaluate()` so the guard never fires in production (**S17**); `/providers/health`
  unauthenticated (**S19/S20**); Sarvam STT reports no confidence (**S13**).
- Carried from S2: staff username+TOTP login (S18); rate-limit OTP verify by IP (S20); prune
  `otp_codes` (S17); audit log daily S3 export + retention (S19).
- Carried from S1: provision Grafana datasource + dashboards (S19); pin dependency versions.
