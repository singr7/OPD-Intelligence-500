# HANDOFF — after Session S8 (queue + board + coordinator console)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`. Local voice is **done**:
> `POST /kiosk/tts` + the Kokoro container (`deploy/tts-kokoro/`, doc 10 §6) are
> live; the branded-Dhara Voicebox clone is a reserved later iteration. Also fixed
> live: `/finish` 500 (rule-flag dicts vs summary strings, `dispatch.py`).
>
> **Next build decided: S-ADAPT.1 — adaptive intake turn V1** (answer questions by
> voice + one clarifying follow-up, on the live local stack). Full V1→V2 design,
> sequencing, seams and guardrails in **[docs/11-ADAPTIVE-INTAKE.md](docs/11-ADAPTIVE-INTAKE.md)**;
> integrated into the build plan as the **S-ADAPT** track (doc 06). S9 (doctor
> console) remains the main-line build; S-ADAPT runs as a parallel track like V-OSS.



**Repo state:** branch `main`. `make test` green: backend **577** (was 541),
voice-gw 1, web typecheck+lint clean, **48** conformance. Postgres on host port
**5433**; voice-gw on 8090. **No migration this session** — `Queue`/`QueueEntry`
existed since S2 (only a new env-only setting, `queue_default_consult_minutes`).
`make dev`'s api image predates this session; rebuild it (or run a local uvicorn)
to serve the `/queue/*` routes and `/queue/ws`.

**One paragraph:** S8 turned the tokens into a live queue (doc 03 §6). `app/queue.py`
is the service — a `QueueEntry` per visit, ordering derived from
`(priority_rank, position, token_no)` so an urgent red-flag intake *jumps the line
by construction* (severity from the rules, not re-decided) with a reason chip;
plus `call_next`, a guarded state machine, drag `reorder`, a wait estimator, and
`board`/`department_queue` read models. The kiosk confirm and the S7 offline sync
now `enqueue_from_intake` + broadcast, so a token hits the board the instant it's
issued — online or synced-from-downtime. `app/queue_hub.py` is an in-process WS
fan-out + the in-memory downtime flag; `app/routes/queue.py` exposes a public
board + `/queue/ws`, staff console + actions, downtime, a reconciliation list
(offline + paper intakes), a paper-entry form, and two print routes
(`app/print_sheets.py`: fillable intake forms + a tear-off token block, HTML→
browser-print). Web: the **TV board** (`app/(board)/board`, train-platform
numerals + chime + 2-lang announce + marigold downtime banner) and the
**coordinator console** (`app/(coordinator)/coordinator`, phone-OTP login,
call-next/state/reorder, downtime toggle, reconciliation, paper entry, print).
The three-browser live-sync + urgent-jump AC is proven live in
`web/e2e/queue.spec.ts` (project `queue`).

## Next session — S9 (Doctor console + summary view)
- Objective: OTP login; day list; patient card (summary hero, red-flag strip,
  answers accordion, visit timeline, symptom sparklines); call-next / no-show /
  lab-requeue wired to the **S8 queue** (`app.queue.set_state` / `call_next`);
  keyboard shortcuts. Load docs 03 §4/§5, 04 §3.
- **Reuse, don't rebuild:** the doctor's queue actions are the same
  `app.queue` verbs the console uses; the minimal phone-OTP login
  (`web/app/(coordinator)/coordinator/_components/Login.tsx` + `_lib/session.ts`)
  was written to be lifted into S9. The summariser (S5, `app/intake/summary.py`)
  already produces the doc 03 §4 contract onto `Intake.summary_md`.
- Exact first commands: `make dev` → rebuild the api image (or local uvicorn,
  below) → `make migrate && make seed` → `make test`.

## Run the S8 board/console demo (needs a live api with S8 code)
```
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  OTP_DEBUG_ECHO=true OTP_RESEND_COOLDOWN_SECONDS=0 ENV=local \
  .venv/bin/uvicorn app.main:app --port 8123
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  .venv/bin/python -m scripts.seed_queue_demo         # deterministic demo queue
cd web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8123 npx next dev -p 3210
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
  npx playwright test --project=queue                 # or npm run e2e:queue
```
Coordinator login: phone `+915550000002` (seeded coordinator); the demo OTP code
is echoed in the response and shown as a hint on the login screen.

## Watch out for
- **A WebSocket route can't take a `Request`-typed dependency.** `/queue/ws`
  reads the hub off `ws.app.state.queue_hub` directly — `Depends(get_hub)` there
  500s the handshake (it bit us once). Any new WS route must do the same.
- **`<style>{cssText}</style>` in a client component hydrates as a mismatch**
  (quotes escape differently SSR vs client). The board/console inject CSS via
  `dangerouslySetInnerHTML`. Keep it that way; a plain text child flickers.
- **Never renumber a token.** The queue wraps `allocate_token`; it does not
  reissue. The online/offline partition (S7) is still the no-collision
  guarantee. Priority reorders the *queue*, never the number.
- **Downtime + the hub are in-memory, single-process** — correct for the one
  pilot api container; a second replica needs Redis pub/sub (same caveat as the
  cost-guard override store and the OSS AdmissionController).
- **`make test` does NOT run the `queue` e2e** (needs a live stack), same as
  `offline-demo` and `kiosk`. Run it explicitly (`npm run e2e:queue`).
- **`seed_queue_demo` hard-deletes today's demo rows** to be repeatable — it is a
  dev-only script and steps outside the soft-delete invariant on purpose.

## Decisions needed from the human
- None blocking. When the GPU box arrives, S-OSS.1 unblocks (unchanged).

## Backlog additions
- **Server-side PDF for the paper sheets** — today the print routes return HTML
  the browser prints; a real HTML→PDF with embedded Indic fonts is a deploy
  dependency decision (S19/S21).
- **Per-doctor queues + room assignment** — S8 runs one queue per department
  (`doctor_id` null); splitting by room/doctor is S9/S18.
- **Board/console localisation** — reason chip + department names are English
  until S13.
- **Staff auth hardening** — the coordinator token is localStorage, not an
  httpOnly cookie (S19/S20).
- **A `/queue/ws` unit test** — currently covered only by the live `queue` e2e.
- **Intake routing + question adaptivity — stress-test & improve (operator-flagged,
  2026-07-22).** On the live local stack the department routing (Q1 classifier) and
  the per-node question flow sometimes feel under-adapted — the tree asks a fixed
  path where a smarter follow-up/clarification would fit. Needs: (a) a routing
  stress set (varied/ambiguous/misspelt complaints in hi+en) measuring mis-route
  rate + `needs_human` calibration against Qwen3; (b) adaptive questioning without
  losing the deterministic offline floor. **(b) is now designed** as the S-ADAPT
  track — **[doc 11](docs/11-ADAPTIVE-INTAKE.md)** (V1 clarify-only voice answers →
  V2 enrichment); its per-node telemetry is what turns (a) from vibes into data.
