# HANDOFF — after Session S9 (doctor console + summary view)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`. Local voice is **done**:
> `POST /kiosk/tts` + the Kokoro container (`deploy/tts-kokoro/`, doc 10 §6) are
> live; the branded-Dhara Voicebox clone is a reserved later iteration.
>
> **Two tracks are open besides the main line. Neither is merged to `main`:**
>
> 1. **S-ADAPT (adaptive intake) — `feat/adaptive-intake`.** V1 (answer any tap node
>    by voice) and V2 (one spoken turn also fills *other* nodes; opt-in `Node.adaptive`
>    sub-questions; per-node telemetry on `Intake.adaptive_events`) are **both built and
>    neither has run on the omen box**. Design: **[docs/11-ADAPTIVE-INTAKE.md](docs/11-ADAPTIVE-INTAKE.md)**.
>    Logs: `sessions/SESSION-ADAPT-1.md`, `-2.md`. ⚠️ **Branch-only until proven on omen
>    (operator instruction)** — `main` is what the pilot deploys from. The operator's
>    stated plan is to **club the omen validation with the next "fully conversational"
>    step** rather than validate V1/V2 on their own.
> 2. **S9 (this session) — `feat/doctor-console`,** built off `main`. See below.

**Repo state:** branch **`feat/doctor-console`** (3 commits, off `main` @ `04fe8c7`).
`make test` green: backend **603** (was 581), voice-gw 1, web typecheck+lint clean,
48 conformance. **No migration this session.** Postgres on host port **5433**;
voice-gw on 8090.

⚠️ **The baseline started red, and not because of this branch.** `opd_test` was stamped at
revision `a1b2c3d4e5f6` — the **S-ADAPT migration**, which exists only on
`feat/adaptive-intake` — so `alembic upgrade head` could not compute a path and all 259
DB-backed tests errored. The `_schema` fixture only upgrades, it never drops. **Switching
between the adaptive track and anything off `main` will do this again.** Fix:
```
docker compose exec -T postgres psql -U opd -d opd_test \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

**One paragraph:** S9 turned the queue into a doctor's morning (doc 03 §4/§5).
`app/doctor.py` is deliberately **two reads and no writes**: `day_list` (the doctor's own
department queue, in the queue's own urgent-first order, with the patient behind each
token) and `patient_card` (the stored doc 03 §4 summary, the rule engine's red flags, the
answers rendered against the tree in `tree_ref`, the visit timeline, the check-in
trendline). `app/routes/doctor.py` exposes `GET /doctor/day` + `GET /doctor/patients/{visit_id}`,
both `require_doctor` — a tighter guard than the coordinator's `require_staff`, because this
is the one surface returning name + phone + answers + history together. **S9 added no action
endpoints**: call-next / no-show / lab-requeue are the S8 `/queue/*` verbs, called with the
doctor's own token, so there stays one state machine and one audit trail. Web:
`app/(doctor)/doctor` — the day list as a vertical clinical spine (the patient in the room is
the one filled marigold node), and a card ordered by clinical urgency: red-flag stamps
carrying the rule's own instruction ("patient was told: …"), then chief concern + a compact
symptoms table, then everything else collapsed. N calls the next patient; D says honestly
that dictation is S10. The full-morning AC is proven in `web/e2e/doctor.spec.ts` (project
`doctor`).

## Next session — S10 (Dictation → structured mapping)
- Objective: dictation capture (Web Speech + audio-record fallback → server STT); mapping
  prompt → structured JSONB; formulary fuzzy-match validation (seed an Indian oncology
  formulary, ~300 drugs); review/diff UI; sign flow. Load doc 03 §7.
- **Reuse, don't rebuild:** the `Dictation` model already exists (S2 — `transcript`,
  `structured`, `status`, `signed_at`/`signed_by`). The STT chain is built and live
  (`POST /kiosk/stt` → `stt_chain` → local Whisper on the box). The **D shortcut and its
  toast are already wired** in `web/app/(doctor)/doctor/_components/Console.tsx` — replace
  `setDictationNote(true)` with the real entry point.
- **Decide first:** whether S10 lands on `main` or continues on `feat/doctor-console`
  (S9 is unmerged — see "Decisions needed").
- Exact first commands:
```
docker compose exec -T postgres psql -U opd -d opd_test \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"   # only if the baseline is red
make dev && make migrate && make seed && make test
```

## Run the S9 doctor console demo (needs a live api with S9 code)
```
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  OTP_DEBUG_ECHO=true OTP_RESEND_COOLDOWN_SECONDS=0 ENV=local \
  .venv/bin/uvicorn app.main:app --port 8123
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  .venv/bin/python -m scripts.seed_doctor_demo        # 5 MEDONC walk-ins, one urgent
cd web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8123 npx next dev -p 3210
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
  npm run e2e:doctor                                  # the full-morning AC + screenshots
```
Doctor login: `+915550001001` (seeded Dr. Anil Gupta, MEDONC); the OTP is echoed in the
response and shown as a hint on the login screen.

## Watch out for
- **`make dev`'s api image predates S9** (it predated S8 too). Rebuild it, or run the local
  uvicorn above, or `/doctor/*` 404s.
- **The doctor console must never grow its own queue verbs.** Call-next / no-show /
  lab-requeue are `app.queue`'s, reached through `/queue/*`. A `/doctor/call-next` gives the
  board and the console two state machines that disagree the moment one is patched — and two
  audit trails to reconcile. Same reason the card reads `Intake.red_flags` rather than
  recomputing flags.
- **The queue state machine is strict and the UI has to follow it.** `called → done` is
  **illegal**: a called patient goes `called → in_consult → done`, and `no_show` is only
  legal straight off `called`. This bit the e2e first time round. A lab-requeued patient
  stays on the worklist by design (`lab_requeue → waiting | done`).
- **`source_node` is not enough to highlight the answers behind a red flag.** It is only
  populated for node-level sugar; the clinically interesting multi-node rules (`fever ≥38
  AND ≤14 days since chemo`) carry none, so the febrile-neutropenia patient's fever went
  unhighlighted at first. `app.doctor._flagged_nodes` reads the fired rule's own `when`
  condition instead. If you touch it: it reads which nodes a rule was *about*; it never
  re-evaluates whether the rule fired.
- **`make test` does NOT run the `doctor` e2e** (needs a live stack + `seed_doctor_demo`),
  same as `queue`, `offline-demo` and `kiosk`. Run `npm run e2e:doctor` explicitly.

## Decisions needed from the human
- **Merge order for three live branches.** `main` carries the deployed pilot;
  `feat/doctor-console` (S9) and `feat/adaptive-intake` (S-ADAPT V1+V2) are both unmerged.
  S9 is additive and low-risk — no migration, no changes to existing routes or models, and
  it only *reads* what the queue and intake already wrote — so it can fast-forward to `main`
  once you have clicked through the console. S-ADAPT is still gated on omen validation.
  **Recommend: merge S9 first, keep S-ADAPT gated.**
- When the GPU box work resumes, S-OSS.1 is unblocked and unchanged.

## Backlog additions
- **Appointments in the doctor's day list** — doc 03 §5 says "appointments+walk-ins"; today
  the worklist is the S8 queue only, because `Appointment` has no check-in flow until S15.
  Deliberately not faked. (S15/S18.)
- **Push the doctor console over `/queue/ws`** — it refetches after its own mutations, so a
  coordinator moving the same line elsewhere is not reflected until the doctor acts. The hub
  already exists to subscribe to. (S18.)
- **Per-node amend + summary regeneration** — doc 03 §4 wants the summary regenerated if a
  coordinator edits answers, and immutable once the consult starts. Neither exists: there is
  still no rewind or amend anywhere in the system. (S18.)
- **Real §4 summaries in the demo seed** — `seed_doctor_demo`'s structured summaries are
  authored fixtures (the V3 `TemplateSummarizer` emits no symptom table); its answers and
  red flags *are* genuinely derived. A live Qwen3 run on omen would produce real ones.
- Carried over from S8, unchanged: server-side PDF for the paper sheets (S19/S21);
  per-doctor queues + room assignment (S18); board/console localisation (S13); staff auth
  hardening (S19/S20); a `/queue/ws` unit test.
- **Intake routing + question adaptivity — stress-test & improve (operator-flagged,
  2026-07-22).** (a) a routing stress set (varied/ambiguous/misspelt complaints in hi+en)
  measuring mis-route rate + `needs_human` calibration against Qwen3; (b) adaptive
  questioning without losing the deterministic offline floor — **(b) is built as S-ADAPT
  V1+V2 on its branch, awaiting omen validation**; its per-node telemetry is what turns (a)
  from vibes into data.
