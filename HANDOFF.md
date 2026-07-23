# HANDOFF — after Session S11 (digital prescription)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`. Local voice is **done**:
> `POST /kiosk/tts` + the Kokoro container (`deploy/tts-kokoro/`, doc 10 §6) are
> live; the branded-Dhara Voicebox clone is a reserved later iteration. Also fixed
> live: `/finish` 500 (rule-flag dicts vs summary strings, `dispatch.py`).
>
> **⚠️ CI is off (2026-07-23, operator).** `.github/workflows/ci.yml` was burning
> the account's free Actions minutes. The pipeline is **fully intact** — only its
> triggers are commented out and replaced by `workflow_dispatch`. Run it by hand
> from the Actions tab or `gh workflow run ci.yml`; re-enable by uncommenting the
> `push`/`pull_request` block. **Nothing is checking your pushes now — `make test`
> locally is the only gate.**
>
> **S-ADAPT.1 (V1) and S-ADAPT.2 (V2) are BOTH BUILT** and now on `main` (the
> branch is merged and deleted; session logs: sessions/SESSION-ADAPT-1.md,
> sessions/SESSION-ADAPT-2.md). **V1:**
> answer any tap node by voice; one spoken clarify then taps. **V2:** one spoken
> turn also fills *other* nodes it volunteers (enrichment → `pending_prefills`,
> auto-applied by the dispatcher through the unchanged `walk.save` when the walk
> reaches them), an opt-in `Node.adaptive` may ask one bounded sub-question, and
> per-node telemetry lands on `Intake.adaptive_events` with a reconciling report
> (`app/intake/adaptive_report.py`). New `app/intake/interpret.py`, prompts
> `interpret_answer/v1+v2`, extended `POST /kiosk/{sid}/answer`, migration
> `a1b2c3d4e5f6`. Gated on `INTAKE_ADAPTIVE` + a real LLM and
> `NEXT_PUBLIC_KIOSK_ADAPTIVE=1`. Design + guardrails:
> **[docs/11-ADAPTIVE-INTAKE.md](docs/11-ADAPTIVE-INTAKE.md)**.
> (Session log: sessions/SESSION-ADAPT-DESIGN.md.)
>
> **🚩 S-ADAPT IS MERGED TO `main` BUT NOT YET PROVEN ON THE BOX (2026-07-23).**
> The branch-only rule was **lifted by the operator** partway through the rollout:
> omen entered a maintenance window after the deploy landed and before the adaptive
> flags were ever turned on, and blocking S11 behind the box was the worse trade.
> What replaced the branch as the guard is the **flag**: `INTAKE_ADAPTIVE=0` and
> `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the defaults in `.env.example`, so `main` on
> omen behaves exactly as it did before the merge until a human flips them. Do not
> read the merge as validation — see **"Owed on omen"** below, which is the real
> outstanding work and must be done before adaptive is enabled for any patient.
>
> **Rollout status (2026-07-23):**
> 1. ✅ **Deployed.** `main` was merged into `feat/adaptive-intake` (the branch was
>    cut before S9/S10, so deploying it unmerged would have taken the doctor console
>    *off* the box and stamped the DB at `a1b2c3d4e5f6`, a revision `main`'s code
>    cannot locate). Branch deployed to omen with both flags `0`; migration applied.
> 2. ✅ **Sanity pass, flags off** — full kiosk intake welcome→token unchanged, all
>    `usage_events` on `local-vllm`/`local-whisper`/`local-tts` (no `fake`),
>    `adaptive_events = []` on every intake, zero `INTAKE_TURN` rows. The deployed
>    code demonstrably does not change the kiosk while the flags are off.
> 3. ⏳ **Adaptive on — NOT DONE** (maintenance window). See "Owed on omen".
> 4. ⏳ **Doctor console on-box — NOT CONFIRMED.** Never reported as run; assume not.
> 5. ✅ **`main` fast-forwarded** to the merged branch on the operator's explicit
>    go-ahead, ahead of 3 and 4, so S11 starts from one line.
>
> **Owed on omen (do this before adaptive faces a patient):**
> - Set `OTP_DEBUG_ECHO=true` + `OTP_RESEND_COOLDOWN_SECONDS=0` in `.env`,
>   `docker compose up -d api` (env-only, no rebuild) — `FakeSMSProvider` logs an
>   OTP's *length*, never its body, so without the echo no one can log in. Turn it
>   back off afterwards (`ENV=local` is the only reason it is permitted at all).
> - **Doctor console + consult note:** `/doctor`, `+915550001001` (Dr. Anil Gupta,
>   MEDONC). Day list, red-flag stamps, **N** call-next repaints `/board`, then **D**
>   → dictate Hinglish with a deliberately misspelt drug and confirm it is *flagged,
>   never silently corrected*; signing refuses until acknowledged. Signing is
>   terminal — use a throwaway visit. Expect a `DICTATION` usage_event on `local-vllm`.
> - **Adaptive:** flags to `1`, mark 1–2 live-tree nodes `adaptive: true`, re-seed,
>   `docker compose up -d --build api web` (**web rebuild required** —
>   `NEXT_PUBLIC_KIOSK_ADAPTIVE` is a build arg). Provoke a vague answer (one
>   clarify then taps), a volunteered extra fact (later node pre-filled, not
>   re-asked), and an unmappable answer (falls to taps, never guesses). Then read
>   `app/intake/adaptive_report.py` and tune node wording from the clarify/mis-map
>   rates. **Rollback is the flags back to `0` + a web rebuild — never a redeploy.**
> - While on the box: `make eval-dictation` (backlog below) wants the same session.

**Repo state:** **`main`** — everything is on one line. `feat/doctor-console` (S9 +
S10) and `feat/adaptive-intake` (S-ADAPT V1 + V2) were both merged and the branches
deleted on 2026-07-23; S11 built straight on `main`. **Start S12 from `main`.**
`make test` green: backend **781** (726 → 781), voice-gw 1, web typecheck+lint clean,
48 conformance. **No migration in S11** (`Prescription` has existed since S2 and
`meds`/`delivered_via` are JSONB); head is still S-ADAPT's `a1b2c3d4e5f6`. Postgres on
host port **5433**; voice-gw on 8090.

⚠️ `make lint` is **failing on 11 pre-existing unformatted files**, none of them S11's
(`ruff format --check`). It is not part of `make test`, so it has been red for a while
without anyone noticing. Worth one `ruff format .` commit before it grows.

**One paragraph:** S11 gave the signature its consequence. doc 03 §7 always said signing
generates the prescription; S10 deliberately emitted nothing rather than write a
half-shaped row, and `app/prescription.py` is now what it emits — generated *inside*
`dictation.sign`, so a prescription cannot exist without a signature and there is no
`POST /prescriptions`. The interesting problem turned out not to be the PDF but the
**dosing schedule**, because the patient copy's pictograms are read by someone who cannot
read the caption under them: an icon *is* the instruction. So `parse_schedule` keeps the
*slots* a dictation names ("1-0-1", "subah aur raat") strictly apart from a bare *count*
("BD"), and refuses the conventional reading — BD is morning-and-night in Indian practice,
and encoding that convention would print a time of day no clinician wrote. A count draws
tablet glyphs and no sun; "SOS" or "alternate days" draws nothing and prints the doctor's
words. The S10 boundary continues onto paper: a drug the doctor acknowledged to sign still
prints flagged, because the acknowledgement was about their intent and the pharmacist
never saw the console.

## Next session — S12 (WhatsApp channel)
- Objective: the intake engine's second channel — Meta WhatsApp inbound/outbound over
  `MessagingProvider`, the 24h window + registered template registry, voice notes via
  `download_media`. Load doc 03 §1d.
- **The provider and its fake already exist** (S3). What does not: the template registry
  S11's delivery hooks also need, and any webhook.
- **S11 left a customer waiting for it** — `POST /prescriptions/{id}/deliver` sends free
  text, which Meta only accepts inside the 24h window. Out-of-window needs a registered
  template, so S12 should make the prescription send template-aware rather than leaving
  a hook that works only in tests.
- **Start from `main`.**
- Exact first commands:
```
make dev && make migrate && make seed && make test    # expect 781 backend green
```

## Run the S10/S11 consult demo (needs a live api)
```
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  OTP_DEBUG_ECHO=true OTP_RESEND_COOLDOWN_SECONDS=0 ENV=local \
  .venv/bin/uvicorn app.main:app --port 8123
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  .venv/bin/python -m scripts.seed_doctor_demo     # re-run before every e2e run
cd web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8123 npx next dev -p 3210
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
  npm run e2e:dictation                            # the S10 AC + screenshots
```
Doctor login: `+915550001001` (Dr. Anil Gupta, MEDONC); the OTP is echoed. In the
console, pick a patient and press **D**. **Sign the note** and the S11 prescription
panel appears under it — print either copy, or send it over the fake WhatsApp/SMS.
The two rendered sheets are in `sessions/screenshots/s11/`.

## Watch out for
- **Nothing infers a dosing schedule, and nothing may start to.** The patient copy's
  pictograms are read by someone who cannot read the caption, so an icon is the
  instruction. `parse_schedule` draws a time of day only when the dictation names one;
  "BD" draws tablet glyphs and no sun, however conventional morning-and-night is. The
  tests that catch a regression are
  `test_prescription.py::test_a_count_without_a_time_of_day_reports_the_count_and_no_slots`
  and `::test_an_unreadable_frequency_yields_no_schedule`.
- **`lines_of` must not re-parse.** It reads the stored `meds` snapshot, so tightening
  the parser cannot re-interpret a prescription already in a patient's hand
  (`::test_the_schedule_is_not_re_derived_when_a_stored_prescription_is_read`).
- **`RxLine.flagged` is deliberately not `meds_needing_attention`.** The latter drops
  acknowledged drugs — that is what let the doctor sign. "Simplifying" the page to reuse
  it would silently un-flag every acknowledged drug on the pharmacist's copy.
- **Nothing rewrites a drug name, and nothing may start to.** `known` is exact-match
  only; `suggestions` are advice on a screen. If a future session adds "auto-apply the
  top suggestion", the S10 AC is gone — the tests that catch it are
  `test_formulary.py::test_a_near_miss_keeps_the_dictated_spelling` and
  `test_dictation.py::test_fixture_maps_without_rewriting_a_single_drug_name`.
- **`_was_said` is a heuristic and must stay noisy-side.** It is token presence in
  `as_spoken` (falling back to the transcript). Tuning it to fire less quietly
  re-opens the rename hole; a false positive costs one acknowledgement tap.
- **The doctor console's first load no longer steals the stage.** S10 fixed two races
  (auto-open only when nothing is selected; close the note only when `selected` really
  changes). Both looked random and both silently cleared a typed transcript. Do not
  "simplify" those effects back.
- **`seed_doctor_demo` must delete dictations before visits** or the second run of the
  day dies on a foreign key. The same will be true of `Prescription` in S11.
- **`make test` does NOT run the `dictation` e2e** (needs a live stack +
  `seed_doctor_demo`), same as `doctor`, `queue`, `offline-demo` and `kiosk`. Its
  signing test cannot repeat without a re-seed — signing is terminal by design; it now
  fails with a message saying so.

## Decisions needed from the human
- *(Resolved 2026-07-23 — both branches merged to `main`. `feat/doctor-console`
  (S9 + S10) first, then `feat/adaptive-intake`. The branch-only gate on S-ADAPT
  was lifted by the operator when omen went into maintenance mid-rollout; the
  default-off flags now carry the guarantee the branch used to. Nothing is in
  flight and there is no unmerged work.)*
- **Whoever next has the box: "Owed on omen" at the top is unclaimed work**, and
  it is the only remaining reason to doubt anything in `main`.
- When the GPU box work resumes, S-OSS.1 is unblocked and unchanged.

## Backlog additions
- **Server-side PDF** — both sheet families (S8 downtime, S11 prescriptions) return HTML
  the browser prints. One decision, one native dependency (WeasyPrint/pango + Indic
  fonts), S19/S21.
- **`make lint` is red on 11 pre-existing unformatted files** — one `ruff format .`
  commit clears it. It is not in `make test`, which is why it drifted.
- **The pictogram copy needs a real low-literacy review** (S21) — doc 06's S11 AC asks
  for a checklist pass; what it has had is a self-critique against doc 04 §5.
- **Prescription delivery is not template-aware** — see the S12 note above.
- **`make eval-dictation` — score a real Qwen3 against the ten fixtures (debt,
  deferred 2026-07-23 by the operator).** The fixtures gate *our* layer with the model
  faked, which is the safety property and is done. What is **not** measured is how
  often the box's Qwen3 renames or invents a drug in the first place — i.e. how often
  `_was_said` fires in real use, and therefore how much acknowledgement tapping a
  doctor actually faces. Mirrors `app/evals.py` / `make eval-routing`; needs the omen
  box. Report rename rate, hallucination rate and dose-inference rate. Until this
  runs, the flag-firing rate quoted anywhere is from fixtures, not from the clinic.
  (Alongside the S-ADAPT omen validation, or S18.)
- **Formulary in the DB + admin editing** — it is a seed file read at boot. A hospital
  adding a drug should not need a deploy, and S18's admin console is the place.
- **Amendment of a signed note** — signing is terminal and there is still no rewind or
  amend anywhere in the system (the same gap S9 logged for intake answers). A real
  clinic needs one. (S18/S19.)
- **Transcript timing for tighter provenance** — `_was_said` could align a drug to the
  *sentence* it was said in if the STT returned word timings. (S14 touches STT.)
- Carried over, unchanged: appointments in the doctor's day list (S15/S18); push the
  doctor console over `/queue/ws` (S18); per-node amend + summary regeneration (S18);
  real §4 summaries in the demo seed; server-side PDF for the paper sheets (S19/S21);
  per-doctor queues + room assignment (S18); board/console localisation (S13); staff
  auth hardening (S19/S20); a `/queue/ws` unit test.
- **Intake routing + question adaptivity — stress-test & improve (operator-flagged,
  2026-07-22).** (a) a routing stress set (varied/ambiguous/misspelt complaints in
  hi+en) measuring mis-route rate + `needs_human` calibration against Qwen3; (b)
  adaptive questioning without losing the deterministic offline floor — **(b) is built
  as S-ADAPT V1+V2 on its branch, awaiting omen validation**; its per-node telemetry
  ([doc 11](docs/11-ADAPTIVE-INTAKE.md)) is what turns (a) from vibes into data.
