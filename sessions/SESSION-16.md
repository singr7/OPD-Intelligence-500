# SESSION-16 — Android app

**Date:** 2026-07-25/26 · **Scope ref:** docs/06-BUILD-PLAN.md → S16 · doc 03 §1c, doc 04 §3

## Acceptance criteria checklist

- [x] **Kotlin/Compose app, minSdk 26, <15MB** — release APK is **1.53 MB**, gated in CI.
- [x] **OTP login** — `/auth/patient/otp/{request,verify}`, patient-scoped JWT, single-use rotating refresh, profile switching.
- [x] **My Cancer Care File, offline, shareable as PDF** — Room-backed, ETag-conditional sync, `PdfDocument` share sheet.
- [x] **Talk-to-Dhara home intake (native speech)** — device `SpeechRecognizer` + `TextToSpeech`, over the same four-tool contract every channel walks.
- [x] **Live queue position** — ahead-count in the queue's own order, wait range, "leave home by" with a patient-set travel time.
- [x] **Medicine reminders (WorkManager, exact alarms)** — plus the caregiver missed-dose ping, server-side over the provider layer.
- [x] **Chemo calendar with what-to-expect audio** — spoken by the device's TTS from `seeds/regimen_notes.json` in all four languages (see Deviations).
- [x] **Caregiver link** — `caregiver_links` with consent as a state, re-read on every request.
- [x] **Instrumented tests for offline file + reminders** — 6 green on a real emulator (`opd_pilot`, API 35).
- [x] **Full home-intake flow on emulator** — sign-in → complaint → question → read-back → confirm, ending on "no token yet".
- [x] **APK size check in CI** — `checkApkSize` fails the build over 15MB; `make android-apk` locally.

## What was built

**Backend — the patient's own surface (`/patient/*`, `/auth/patient/*`)**

- `app/patient_app.py` — profile resolution (a phone → the files it may open), care file, queue
  position, arrival check-in, reminder plan, dose recording + caregiver ping, chemo calendar,
  caregiver links. Every read takes `patient_id` as a keyword argument.
- `app/routes/patient.py` — 18 endpoints, none of which takes a patient id from the request.
- Patient identity: `create_patient_access_token` (`kind: "patient"` claim), `current_patient` /
  `require_patient_self` in `app/auth/rbac.py`, `check_code` extracted from `verify_otp` so both
  audiences share one set of OTP protections.
- Models: `CaregiverLink`, `DoseEvent`, `Intake.caregiver_answered`, and `refresh_tokens` gaining
  `patient_id` + `subject_phone` under a `CHECK ((user_id IS NULL) <> (patient_id IS NULL))`.
  Migration `e108276e7d43`.
- `app/routes/kiosk.py` grew `next_node_impl` / `answer_impl` / `finish_impl` so the app walks the
  *kiosk's own handler bodies* behind its own login rather than a second implementation.
- `seeds/regimen_notes.json` (4 languages), `backend/scripts/seed_app_demo.py`.

**Android (`android/`)** — 24 Kotlin files, one activity, four tabs.

- `data/` — `ApiClient` (OkHttp + kotlinx.serialization, 401 → one serialised rotation),
  `TokenStore` (DataStore), `PatientRepository` (per-feature offline policy), `local/Db.kt` (Room).
- `ui/` — `theme/Theme.kt` (doc 04 §1 tokens verbatim), `Components.kt` (breathing Dhara,
  train-board token numeral, 64dp targets), `Speech.kt` (device STT/TTS), 8 screens.
- `reminders/` — `DoseScheduler` (pure occurrence policy + exact alarms), `DoseAlarmReceiver`
  (lock-screen taken/missed actions), `DoseReportWorker`, `SyncWorker`, `BootReceiver`.
- `make android-test` (in `make test`), `android-test-device`, `android-apk`, `android-emulator`,
  `android-install`, `app-demo`; a CI job that runs the JVM tests, the size gate, and uploads the APK.

## Decisions made

- **A patient id is never taken from a request.** It comes from the token, and the router has no
  `patient_id` parameter anywhere, so the mistake is not available to a later session.
- **`patients.caregiver_phone` is not a login.** It is a contact captured at a registration desk;
  access is exactly the `caregiver_links` the patient approved. Do not "helpfully" merge them.
- **Consent is re-read per request**, so revoking ends the caregiver's session at her next screen
  refresh rather than at token expiry. Rotation re-resolves it too.
- **A caregiver may not run the patient's intake** (`_forbid_caregiver_write`). Second-hand symptoms
  recorded as the patient's own are a clinical falsehood; the kiosk's caregiver mode is the honest path.
- **Home intake issues no token.** A token minted the night before is called while she is still
  travelling. `/patient/arrive` is what turns last night's answers into a place in the queue.
- **The phone owns alarms; the server owns consequences.** A dose report is queued locally first and
  sent second, and `(prescription_id, med_index, scheduled_for)` is the natural key — so a flaky
  connection cannot ping a caregiver twice.
- **No Kotlin tree walker.** The kiosk earned its offline walker with a golden-trace conformance
  suite; a third implementation of the same clinical logic is a liability. The app needs a signal to
  *do* an intake (done indoors, the evening before) and none to *read* the file.
- **Audit actor for a patient is `actor_id = NULL` + a labelled role.** `audit_log.actor_id` is a FK
  into `users`; writing a patient id there would one day match somebody.

## Deviations from spec

- **doc 03 §1c.5 "what-to-expect audio clips"** ships as text the device reads aloud rather than
  recorded clips: a tenth of the bytes, works offline, in the patient's language, and the same
  strings the language QA harness checks. Recorded clips would need a voice artist per language.
- **doc 04 §1's self-hosted Noto** is not bundled: Android already ships Devanagari and Telugu, and
  three font files would cost ~4MB of a 15MB budget. doc 04's note is written for the web surfaces,
  which have no system font to fall back on.
- **Light theme only** — a second palette to keep WCAG-AA in both directions, for a surface used
  outdoors. Deliberate, not forgotten.
- **Appointments are read-only in the app.** The `/patient/appointments` booking endpoints exist and
  are tested; the UI lists but does not book, because slot choice deserves the receptionist's
  phrasing and S16 was already large.

## Tests & evidence

- `make test`: **932 backend** (was 907) · **22 voice-gw** · **48 web conformance** · **6 Android JVM**. Green.
- `make lang-qa`: clean across [en, hi, mr, te].
- `make android-test-device`: **6 instrumented tests green** on `opd_pilot` (API 35, arm64):
  `OfflineCareFileTest` (2), `RemindersTest` (3), `HomeIntakeFlowTest` (1).
- `make android-apk`: **APK size OK: 1.53 MB of 15MB**.
- New backend tests: `tests/test_patient_app.py` (25) — mostly boundary: whose file a token opens,
  what a caregiver may not do, that a revoked link dies mid-session, that a staff token is refused
  on `/patient` and a patient token on `/queue/console`.
- Fixed a pre-existing S15 flake: `test_worker.py` built "tomorrow" from the UTC clock where the
  campaign means the hospital's — it failed only between 00:00 and 05:30 IST.

**Screenshots** (`sessions/screenshots/s16/`, taken on the emulator against the live local stack,
signed in as a real seeded patient):

- `01-03 onboarding` — three spoken screens, one idea each. *Critique: the first cut pinned the
  content to the top and left a third of the screen empty; the idea now sits in the optical centre.*
- `04-06 sign-in` — phone, then code. *Honest failure copy ("That code did not work. Let's try once
  more") is visible in 06 because an early tap submitted an empty field — kept, it is the real state.*
- `07-home` — greeting, Talk to Dhara, arrive. *Critique: shipped with two full-width marigold
  buttons stacked, reading as two primary actions; arrival is now the quiet one.*
- `08-file` — real prescriptions and a Hindi visit summary. *Critique: showed a duplicate
  prescription, which was the demo seeder searching for one visit channel and creating another —
  fixed, and it is now idempotent.*
- `09-queue`, `10-medicines` — *Critique: the "SOS" line was rendered with the danger stamp; red is
  reserved for red flags, so a drug with no stated time is now a plain informational card.*
- `12-calendar` — cycle 1 with Hindi what-to-expect and Listen/Stop.

## Known gaps / stubs introduced

- `TokenStore` is plain DataStore, not EncryptedSharedPreferences (rationale in the file).
- The chemo calendar counts cycles from the patient's own `chemo_review` appointments; a real
  regimen protocol arrives with S17's check-in engine.
- Report **photos** (doc 03 §1c.1 "report photo") are not capturable yet — the file holds
  prescriptions and summaries only.
- Instrumented tests are a local gate; CI runs the JVM tests and the size gate only (no KVM runner).
- mr/te app strings are machine-written and owed the same native + clinical review as the tree bank (S21).

## Commits

- `3486963` — S 16: a patient can log in — identity, consent, and the /patient surface
- `919667d` — S 16: the Android app — a patient's file, her queue, and her medicines
