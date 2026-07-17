# HANDOFF — after Session 6

**Repo state:** branch `main`, last code commit `115547e` (session-close commit
follows this file). `make test` green (backend **492**, voice-gw 1, web
typecheck+lint clean). `make dev` brings up the stack; Postgres on host port
**5433** (read "Watch out for"). The kiosk PWA is live at `/kiosk`.

**One paragraph:** The intake engine now has its first channel. `app/routes/kiosk.py`
is thin REST that mirrors the four-tool contract (start / next / answer / finish /
confirm) over one `IntakeEngine` on `app.state`; `app/kiosk.py` routes Q1 through
the department classifier — honouring `needs_human` by returning a **department
chooser** rather than guessing — then creates the walk-in Visit+Intake and drives
the dispatcher from taps. The kiosk is a **V3 client**: no model in the walk, the
answers JSONB identical to every other tier by construction. The **PWA**
(`web/app/(kiosk)/kiosk/`) is the first real UI — a design system on the doc 04 §1
palette, a component library (breathing Dhara avatar, faces scale, body map,
stepper, audio bar, duotone icons), and the whole flow language → caregiver → voice
chief complaint → chooser → tree questions (auto-read-aloud) → read-back + confirm →
train-board token, all audio-first with a 90s privacy blur. A Playwright suite
drives a full hi intake welcome→token against the local stack (11 screens in
`web/screenshots/s6/`). What the kiosk is **not** yet: offline. Kill the API and it
stops — that is exactly S7.

## Next session (S7 — Kiosk part 2: offline-first, T3 voices, printing)
- Objective: service worker + Dexie caching (trees, audio packs, session state);
  offline token blocks (server allocation API + kiosk consumption); Downtime Mode UI
  + auto-detection + sync/reconciliation; ESC/POS print bridge for the token slip;
  voice-pack manifest format + placeholder TTS-rendered packs. (docs 01 §5, 03 §1a,
  04 §3.)
- Start notes:
  - **The kiosk currently dies without the API.** Every screen calls
    `kioskApi.*` (`web/app/(kiosk)/kiosk/_lib/api.ts`) synchronously. S7 puts a
    service worker + Dexie in front: cache the tree bank and walk the dispatcher
    **client-side** when offline (the walker is deterministic — reimplement it in TS
    from `app/trees`, or expose it via a cached WASM/JSON contract; the V3 logic is
    small). Session state must persist to Dexie so an idle-reset or a reload resumes.
  - **Token issuance is the offline crux.** `app.kiosk.allocate_token` is a
    provisional `max+1` that needs the DB. S7's offline token *blocks* (a server
    pre-allocates a range per kiosk per day; the kiosk consumes from it offline and
    reconciles on reconnect) replace it — build the server allocation API and the
    kiosk consumption side together so the demo AC (kill API 10 min → 3 offline
    intakes → reconnect → zero collisions) passes.
  - **Server-STT toggle + `/kiosk/stt`.** S6 shipped Web Speech + tap-to-type only.
    Add a `/kiosk/stt` endpoint over `stt_chain` and a MediaRecorder client path so
    the "trouble hearing?" toggle is real (doc 06 S6 line, deferred).
  - **Voice packs.** `node.audio` is authored-empty and `voicepack.resolve` TTS-falls
    back. S7 defines the pack manifest and renders placeholder TTS packs; the kiosk's
    AudioBar should prefer `node.audio` when present over browser Web Speech.
- Exact first commands:
  1. `make dev` (stack up; if Docker is down, `open -a Docker` first — it dropped
     mid-session once)
  2. `make migrate && make seed`
  3. `make test`
  4. Kiosk live: `cd web && npm run e2e` (drives `/kiosk` against the local stack)

## Watch out for
- **Docker died mid-session once** (daemon stopped → Postgres 5433 refused → api
  500s with no CORS header → the browser reports a bogus "CORS" error). If the kiosk
  fetch fails, check `docker ps` before suspecting the code. `open -a Docker`,
  `docker compose up -d postgres redis`, wait for healthy.
- **`NEXT_PUBLIC_API_BASE` is baked at dev-server start**, not per request. If the
  kiosk calls the wrong api, the dev server was started without the env — restart it.
  Compose sets it to `http://localhost:8000`; a local uvicorn on another port needs
  the web dev server relaunched with a matching base.
- **The kiosk session store is in-memory locally** — one api process only. A second
  uvicorn worker (or a compose scale) would 404 mid-flow. Prod uses Redis.
- **`finalize_cost` in `/confirm` flushes the meter first** — keep that if you touch
  the route, or the cost reads ₹0 for a metered call.
- **Position is derived, never stored** (STATE.md) — anything the kiosk caches from
  the answers (a rendered read-back, a progress bar, the red-flag banner) is
  recomputed after every save; a downgrade/prune can *remove* an answer and its flag.

## Decisions needed from the human
- **Server-STT vendor confidence** — the kiosk read-back is the patient's only check
  on a bad transcription; the Hindi in the trees + the read-back script are still
  model-authored and unreviewed (S21). Get the read-back phrasing in front of a Hindi
  speaker when one is available.
- **Carried, still open:** ratify dropping `question_trees.lang` (S4); five minutes
  with a `GEMINI_API_KEY` to close S4's classifier ≥85% AC (now on the kiosk's Q1
  critical path — matters more); oncologist review of the tree thresholds before
  S7/S8 build further on them; SMS vendor pick; price-book rates vs real contracts.

## Backlog additions
- **`/kiosk/stt` server-STT endpoint** (MediaRecorder → `stt_chain`) + the "trouble
  hearing?" toggle — **S7**.
- **Per-node "back"/amend inside a kiosk walk** — needs a rewind/remove-answer path;
  today "change something" restarts — **S7/S9**.
- **Attribute Q1 routing cost to the intake** — the classifier runs before the
  intake_id exists, so its `usage_event` is unattributed; wrap it in
  `usage_scope(intake_id=...)` once the walk-in row is created — **S9/S18**.
- **Full custom duotone icon set** (~65 keys) + human review — **S7/S21** (S6 ships a
  branded subset + aliases + neutral fallback).
- **Kiosk department-name localisation** (hi/mr/te) — seeded names are English — **S13**.
- Carried from S5: `LLMProvider` tool-result message type for a true tool loop (S14);
  `Intake.answers[*].text_en` per-answer English gloss (S9/S13); kiosk idle-reset
  *sweeper* server-side (S6 did the client blur; a walked-away session TTL is S7).
- Carried earlier: red-flag `or`/`unanswered` real satisfiability (S18); shared
  red-flag rule library (S18); WhatsApp 24h-conversation billing (S12); `token_cached`
  price unit (S18); schedule `CostGuard.evaluate()` (S17); `/providers/health` auth
  (S19/S20); Sarvam STT confidence (S13); staff username+TOTP (S18); OTP-verify IP
  rate-limit (S20); prune `otp_codes` (S17); audit S3 export + retention (S19);
  Grafana datasource + dashboards (S19); pin dependency versions; Surgical Oncology
  "new lump/lesion" tree (S18).
