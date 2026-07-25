# HANDOFF — after Session S16 (Android app)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`.
>
> **⚠️ CI is off (2026-07-23, operator).** `.github/workflows/ci.yml` is intact but
> its `push`/`pull_request` triggers are commented out. Run it by hand:
> `gh workflow run ci.yml`. **`make test` locally is the only gate** — plus `make lang-qa`.
>
> **🚩 Adaptive intake (S-ADAPT) is on `main` but NEVER PROVEN with its flags on.**
> `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the defaults. Unchanged by S16.

**Repo state:** **`main`**, last commit `S 16: session close`. `make test` green: backend
**932** (was 907), voice-gw **22**, web typecheck+lint+**48** conformance, **Android 6 JVM**
(`make test` now includes `android-test`). `make lang-qa` clean across [en,hi,mr,te].
**Migration `e108276e7d43`** (caregiver links, dose events, patient refresh sessions) — run
`make migrate` before anything. Postgres on host port **5433**; voice-gw on **8090**.
**The mainline sequence resumes at S17.**

⚠️ `make lint` still fails on the **same pre-existing unformatted files** (none of S16's — new
Python is `ruff format`-clean). Not in `make test`. Still worth one `ruff format .` commit.

**One paragraph:** S16 put the platform in a patient's hand. The backend grew its first
*patient* identity — a phone resolves to the care files it may open (her own, plus anyone who
granted that number caregiver access), and `/patient/*` scopes every read on the token, with no
`patient_id` parameter anywhere in the router to get wrong. `caregiver_links` is an access grant
and deliberately not inferred from `patients.caregiver_phone`, which is a contact number a
registration desk wrote down; consent is re-read on every request, so revoking ends a caregiver's
session at her next screen refresh. The app itself (`android/`, Kotlin/Compose, **1.53 MB** of the
15MB budget) renders the care file from Room whether or not there is a signal, refuses to put an
alarm on a dose time the doctor never stated, shows a stale queue position with its age rather than
as the truth, and issues **no token** for an intake done at home — `/patient/arrive` does that,
when she is actually at the hospital. Proven on a real emulator: six instrumented tests plus a
screen walk against the live local stack signed in as a seeded patient.

## Next session — S17 (Check-in engine)
- Objective: doc 03 §9 — protocol templates per regimen family, plan generation from a signed
  dictation (LLM personalise + doctor one-tap approve), scheduler, delivery ladder
  WhatsApp→voice→SMS, grading rules + LLM assist, nurse review queue, next-cycle reminders.
- **Load:** doc 03 §9.
- **AC:** sign a fixture dictation → correct plan drafted → a simulated D+2 red answer escalates
  within 1 min; quiet hours respected.
- **What S16 gives it:** `CheckinPlan` / `Checkin` have existed since S2 and are still empty. The
  app is a **fourth delivery channel** for a check-in, and `DoseEvent` is the first adherence data
  the grading rules could read. The chemo calendar currently counts cycles from the patient's own
  `chemo_review` appointments — S17's protocol templates are what should replace that.
- **Start from `main`.** First commands:
```
make dev && make migrate && make seed && make slots   # 12 slot templates -> ~800 slots
make test                                             # 932 backend / 22 voice-gw / 48 web / 6 android
make lang-qa                                          # expect clean across [en,hi,mr,te]
```

## Watch out for (S16 fragile edges)
- **`make test` now needs a JDK 17 and the Android SDK.** `ANDROID_JAVA_HOME` defaults to
  `/opt/homebrew/opt/openjdk@17`; `android/local.properties` (gitignored) points at the SDK. On a
  box without them, `make test-backend test-voicegw test-web` is the Python/web-only gate.
- **A patient token must never reach a staff route.** `current_principal` refuses any token whose
  `kind` claim is not `"user"`, and `current_patient` refuses the reverse. Both directions are
  tested (`test_a_staff_token_cannot_open_a_patient_file_and_vice_versa`); do not "simplify" the
  claim away.
- **The app walks the kiosk's own handler bodies** (`kiosk.answer_impl` et al.) behind its own
  login, because an app session hangs off a named patient's record while a kiosk session is an
  anonymous walk-in. Do not point the app at `/kiosk/*` directly, and do not fork the walker.
- **Instrumented tests are a local gate only.** CI runs the JVM tests and the APK size gate; there
  is no KVM runner. Run `make android-emulator` then `make android-test-device` before trusting a
  change to the offline store, the alarms, or the intake screens.
- **`Intake.caregiver_answered` is new and the kiosk now sets it.** The S6 workaround (writing a
  marker into `Patient.caregiver_name`) is still there for walk-ins; on a *known* patient it would
  overwrite a real caregiver's name, which is why the column exists.

## Decisions needed from the human
- **A live Exotel number + creds** — still blocking *two* proofs: the S14 intake bridge and the
  S15 receptionist/campaign. Everything is fake-client-proven only. *(carried, unchanged)*
- **Who is the coordinator on `COORDINATOR_PHONE`**, and does the Exotel console have a whisper
  applet? *(carried)*
- **Does the app go on the Play Store, or sideload at the OPD desk?** It is unsigned today
  (`assembleRelease` produces an unsigned APK the size gate measures). A Play listing needs a
  signing key in CI, a privacy policy URL, and a data-safety declaration — none of which exist. A
  QR-code sideload at registration is the smaller path and may be the right one for the pilot.
- **mr/te still need a native + clinical review before a patient reads them** (S21). S16 adds ~90
  app strings and the chemo-calendar what-to-expect lines to that pile.

## Owed on omen (before adaptive / mr-te / the phone path face real use)
- **The app on a real handset** — everything is proven on an emulator. Wanted: a low-end Android 8
  phone, the sideloaded APK, an intake over 2G, and one alarm firing overnight with the screen off
  (Doze is the risk the emulator cannot show). *(new)*
- **Live Exotel smoke, both applets** — one Voicebot applet at `wss://…/exotel/voicebot` (intake),
  one at `wss://…/exotel/receptionist` (appointments). *(carried)*
- **Turn the campaign on for one evening, on real numbers.** *(carried)*
- **Phone-on-GPU contention** — the phone's `[v_oss, v2, v3]` ladder shares the kiosk's
  `max_oss_sessions: 12` pool. *(carried)*
- **Admin console visual pass** (S18E) — walk the six tabs, publish a tree edit. *(carried)*
- **Telugu kiosk render** — తెలుగు glyphs, ≥1.6 line-height at 200%. *(carried)*
- **Adaptive on** — flags to `1`, mark 1–2 live-tree nodes `adaptive: true`, re-seed, rebuild. *(carried)*
- **Doctor console + consult note on-box** — real-Qwen3 dictation `_was_said` pass. *(carried)*

## Backlog additions (S16)
- **Report photos in the care file** — doc 03 §1c.1 says "every prescription, summary, **and report
  photo**". The app has no camera path and the server has no attachment model. Wants an
  `Attachment` table + object storage; suggest S18-late.
- **Booking from the app** — `/patient/appointments/{slots,book,cancel}` exist and are tested; the
  UI lists only. Slot choice deserves the receptionist's phrasing.
- **Play Store signing + release lane** — see the decision above.
- **An app tab for check-in answers** — the moment S17 exists, the app is the cheapest channel for
  a D+2 check-in and the one that already knows the patient.
- **`TokenStore` at rest** — plain DataStore today (rationale in the file). If the pilot ever holds
  more than one patient's session on a shared handset, revisit.
- Carried, unchanged: `make lint` red on pre-existing files; mr/te unreviewed (S21); Telugu never
  seen rendered; admin console never seen on a screen (S18E); appointment waitlist (S18-late);
  language detection from the caller's greeting; admin slot-template editor; campaign
  observability; tier-mix what-if; V1 continuous caller-audio streaming; surface STT confidence
  instead of the energy proxy; tune VAD/DTMF thresholds on real Alwar telephony.
