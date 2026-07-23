# HANDOFF — after Session S10 (dictation → structured mapping)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`. Local voice is **done**; the
> branded-Dhara Voicebox clone is a reserved later iteration.
>
> **⚠️ CI is off (2026-07-23, operator).** `.github/workflows/ci.yml` was burning
> the account's free Actions minutes. The pipeline is **fully intact** — only its
> triggers are commented out and replaced by `workflow_dispatch`. Run it by hand
> from the Actions tab or `gh workflow run ci.yml`; re-enable by uncommenting the
> `push`/`pull_request` block. **Nothing is checking your pushes now — `make test`
> locally is the only gate.**
>
> **Two tracks are open besides the main line. Neither is merged to `main`:**
>
> 1. **S-ADAPT (adaptive intake) — `feat/adaptive-intake`.** V1 and V2 both built,
>    neither has run on the omen box. Design: **[docs/11-ADAPTIVE-INTAKE.md](docs/11-ADAPTIVE-INTAKE.md)**.
>    Logs: `sessions/SESSION-ADAPT-1.md`, `-2.md`. ⚠️ **Branch-only until proven on
>    omen (operator instruction).** The stated plan is to club the omen validation
>    with the next "fully conversational" step.
> 2. **`feat/doctor-console` — now carries BOTH S9 and S10** (7 commits off `main`
>    @ `04fe8c7`). S10 was built here on the operator's instruction rather than
>    branching off `main`.

**Repo state:** branch **`feat/doctor-console`**, last commit `S 10: session close`.
`make test` green: backend **708** (was 603), voice-gw 1, web typecheck+lint clean,
48 conformance. **No migration in S10** (`Dictation` has existed since S2 and
`structured` is JSONB). Postgres on host port **5433**; voice-gw on 8090.

⚠️ **If the baseline starts red with ~259 DB errors,** `opd_test` is stamped at the
S-ADAPT migration (`a1b2c3d4e5f6`), which exists only on `feat/adaptive-intake`.
Switching between that branch and anything off `main` does this. Fix:
```
docker compose exec -T postgres psql -U opd -d opd_test \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

**One paragraph:** S10 turned the doctor's voice into a signed record (doc 03 §7).
`app/dictation.py` maps a Hinglish consult note to structured fields on the LLM
chain — **`LLM_PROVIDER=local_vllm` runs the whole thing on the box's Qwen3, no code
change**, which is the point for the most private text in the system. What the module
really owns is *what the model does not get to decide*: `validate_meds` throws away
the model's `known` flag and asks `app/formulary.py`, where **`known` is exact-match
only and there is no code path from a fuzzy score to a written name**. Writing the ten
Hinglish fixtures exposed the hole a formulary cannot cover — when the doctor says
"Vinblastin" and the model helpfully writes "vinblastine", the result is a real drug
and every check passes — so every name is also checked back against the doctor's own
words (`_was_said`). On the fixtures it fires exactly twice: the helpful correction
and the hallucination. Signing locks the record and refuses while any flagged drug is
unacknowledged; off-formulary stays signable, but as an act. Web: the consult note on
the console stage, where every written value hangs under the phrase it came from —
**the diff is speech against record, not form-v1 against form-v2** — and the row steps
out of alignment in danger-red when the two cannot be reconciled.

## Next session — S11 (Digital prescription)
- Objective: Rx PDF (letterhead + large-type pictogram patient copy); print endpoint;
  WhatsApp/SMS delivery hooks via the provider layer; Rx history on the patient file.
  Load doc 03 §8.
- **This is where signing finally does something.** doc 03 §7 says signing generates
  the prescription; S10 deliberately emits nothing (see below). The `Prescription`
  model already exists (S2 — `visit_id`, `dictation_id`, `meds`, `pdf_url`,
  `delivered_via`), so S11 should hook `app.dictation.sign` rather than add a verb.
- **Reuse:** `app/print_sheets.py` (S8) is the HTML→browser-print pattern and the
  closest precedent for a letterhead.
- **The meds are already prescription-shaped:** `structured["fields"]["meds"]` carries
  name / dose / route / freq / duration plus the formulary verdict. The `known: false`
  ones are *acknowledged*, not resolved — decide how the printed Rx shows that.
- **Decide first:** merge order (see "Decisions needed").
- Exact first commands:
```
docker compose exec -T postgres psql -U opd -d opd_test \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"   # only if the baseline is red
make dev && make migrate && make seed && make test
```

## Run the S10 dictation demo (needs a live api with S10 code)
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
console, pick a patient and press **D**.

## Watch out for
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
- **Merge order for three live branches.** `main` carries the deployed pilot;
  `feat/doctor-console` now carries **S9 + S10**; `feat/adaptive-intake` carries
  S-ADAPT V1+V2. S9+S10 remain additive and low-risk — no migration, no changes to
  existing routes or models, and the only shared file touched is `FakeLLMProvider`
  (a test/demo fake). **Recommend: merge `feat/doctor-console` to `main` now**, since
  S11 builds directly on the signed dictation and a third session stacked on an
  unmerged branch gets harder to land each time. S-ADAPT stays gated on omen.
- **Should a real Qwen3 run score the ten fixtures?** They currently gate *our* layer
  with the model faked, which is the safety property. What they do not measure is how
  often the box's Qwen3 renames a drug in the first place — i.e. how often `_was_said`
  will fire in real use. That is a half-day eval (`make eval-dictation`, mirroring
  `eval-routing`) and it needs the omen box.
- When the GPU box work resumes, S-OSS.1 is unblocked and unchanged.

## Backlog additions
- **`make eval-dictation`** — score a real model against the ten fixtures on the omen
  box: rename rate, hallucination rate, dose-inference rate. Turns `_was_said`'s
  firing rate from a guess into a number. (S18, or alongside the omen validation.)
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
  as S-ADAPT V1+V2 on its branch, awaiting omen validation**.
