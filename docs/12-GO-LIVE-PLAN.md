# 12 — Go-live plan (operator questions, 2026-07-26)

Answers six operator questions against **what is actually in the repo today**, then sequences
the work as sessions in doc 06's format. Written after S18-late; repo at `dce2269`.

**The headline, first: "live in a couple of days" is achievable for one shape of go-live and
not for another.** A **kiosk-first pilot on the 4090, tap + local voice, WhatsApp and
telephony dark** is three sessions away, because that is close to what is deployed now.
A **fully conversational speech-to-speech telephony pilot with vendor realtime, robust VAD and
barge-in** is not two days' work under any honest estimate: the realtime adapters do not
exist, the VAD is an energy proxy, and neither has ever met a real phone call. §7 splits the
plan on exactly that line. Nothing below is padding — the cuts are named so you can overrule
them with your eyes open.

---

## 1. Channel mix — "50% kiosk, 25% app, rest telephony, or 100% kiosk"

**Not configurable today, and the percentage framing does not map onto the system.** What
exists is `config/tiers.yaml`: a per-channel *tier* ladder (which speech stack each channel
prefers) plus `max_oss_sessions`. There is no per-channel enable/disable and no volume split.

The reason a percentage dial cannot exist as stated: **a channel is chosen by the patient, not
by us.** Someone who walks up to the kiosk is a kiosk intake; we cannot make her 25% an app
intake. Three things *are* genuinely controllable, and together they give you everything the
question is reaching for:

1. **Which channels are open at all.** "100% kiosk" is real and useful — it means WhatsApp,
   telephony and app intake are switched off, and a patient who tries one is told the desk
   will help her rather than dropped into a half-configured flow. This is the single most
   valuable missing switch, because it is also how you go live before Exotel and Meta are
   provisioned (§4).
2. **Which channel we *invite* patients on.** The D-1 outbound campaign (S15) already decides
   who gets a call and who gets a WhatsApp message. A weighted mix here is meaningful — "call
   30% of tomorrow's list, WhatsApp the rest" — and it is the only place a percentage is a
   real instruction rather than a wish.
3. **Capacity, per channel.** `max_oss_sessions` caps concurrent local voice sessions. A
   per-channel share of the GPU (phone never eats more than N of the 12 seats, so a kiosk
   patient standing in the room is never starved by a phone queue) is a small extension of the
   existing `AdmissionController` and is worth having on one box.

Built in **S-GL.1**.

## 2. Full streaming Gemini V2V for telephony — is the adapter there?

**No.** `RealtimeVoiceProvider` is an interface with a **fake implementation only**.
`REALTIME_PROVIDER=gemini-live` deliberately raises `UnknownProvider` — the registry refuses to
name a tier that cannot run (`backend/app/providers/registry.py:144`). The voice gateway's V1
path is wired end to end and tested against `FakeRealtimeProvider`; no vendor has ever been on
the other end of it.

The V2 path (STT → LLM → TTS) **is** real and is what telephony runs on today, including on
local providers.

**VAD and barge-in, precisely:**

- **Barge-in: implemented, at the right layer.** `PlaybackPump` streams assistant audio one
  small frame at a time and yields between frames, and a caller frame triggers `barge_in()`
  which sends Exotel a `clear` to flush playback (`voice-gw/gw/call.py:231-267`). The
  mechanism is sound.
- **VAD: not real.** End-of-utterance is a **peak-amplitude energy threshold** — the code says
  so itself: *"near-silence ends an utterance (a stand-in for real VAD, tunable later)"*
  (`voice-gw/gw/call.py:21`, `:87`). On a noisy OPD line this will cut people off mid-sentence
  and hold the line open on background noise. Silero VAD + smart-turn endpointing is specified
  (doc 08 §2, S-OSS.2) and unbuilt.
- Neither has been tuned against real Alwar telephony — a carried item since S14.

So the honest statement is: **telephony today is a working V2 pipeline with real barge-in and a
placeholder turn detector, and no speech-to-speech option at all.**

App intake is a third shape again: the Android app uses **device** STT/TTS against the kiosk's
handler bodies (S16). It is not a V2V client and would need the same realtime work plus an
audio transport the app does not have.

## 3. Realtime adapters for Gemini *and* OpenAI, with intake guardrails and a 6-minute cap

**None of it exists.** No Gemini Live adapter, no OpenAI Realtime adapter, and no realtime
prompt. What *does* exist and is directly reusable:

- **The shared tool contract** (`app/prompts/tools.py`: `get_next_node` / `save_answer` /
  `check_red_flags` / `finish_and_summarize`) — versioned, and already what V1/V2/V3 all drive.
  A realtime adapter is a function-call loop over this, not a new engine.
- **`prompts/intake/v1.md`** — the dialogue driver for the V2 turn loop. It is written for
  turn-based use; a realtime prompt needs the interruption, silence and scope-recovery
  language a duplex model needs, which is genuinely different text.
- **A call cap**: `MAX_CALL_SECONDS = 8 * 60` for intake, `5 * 60` for the receptionist, with a
  **graceful partial** on timeout (answers are persisted per turn, so a cut call keeps
  everything answered so far). Your 6 minutes is a constant change, not a build.

The guardrails you describe — stay on the intake, decline unrelated conversation, return
gracefully, never diagnose, never decide a red flag — are exactly the shape of the existing
prompt discipline, but they need writing **and an eval**, because a duplex model wanders in
ways a turn-based one does not. A prompt with no eval set is an assertion, not a guardrail.

Built in **S-GL.4** (Gemini) and **S-GL.5** (OpenAI + the choice being config-only).

## 4. Running all of this on the omen / 4090 box

What already works there: the whole app stack, plus Whisper + Qwen3 + Kokoro TTS, live at
`opd.radpretation.ai` (doc 10). The problems I can see:

- **Nothing lets you enable a vendor without editing `.env` and restarting.** Provider
  selection, Meta credentials and Exotel credentials are all boot-time settings. Your "simple
  way to set it up when it arrives" does not exist. This is the §1 switchboard's second half
  and is built in **S-GL.1**.
- **A half-configured channel fails badly rather than staying shut.** With no
  `EXOTEL_CHECKIN_APPLET_URL`, the check-in ladder correctly skips the voice rung; but there is
  no equivalent for "WhatsApp is not provisioned" — the bot would try and fail per message.
  Channel enablement fixes this properly.
- **GPU contention between voice and everything else is untested** (carried since S-OSS). One
  4090 running Whisper + Qwen3 + Kokoro, with kiosk read-aloud and phone calls competing, has
  no measured concurrency envelope. `max_oss_sessions: 12` is an engineering estimate from doc
  08, not a measurement on your box.
- **Realtime V2V would not run locally at all.** Gemini/OpenAI realtime are cloud services —
  turning them on means per-minute vendor cost and an internet dependency on a box whose whole
  design point is zero cloud AI. That is a legitimate choice for telephony specifically, but it
  should be a deliberate per-channel one, which the tier ladder already expresses.
- **Single box, single point of failure** — which is question 5.

## 5. AWS as a no-GPU fallback that degrades to offline speech + lean summarisation

This is the best-shaped of the six, because **most of it already exists**:

- **The kiosk already has a zero-AI floor**: browser `SpeechRecognition` + pre-recorded/TTS
  packs, the offline tree walker in TypeScript, Dexie caching, offline token blocks, and
  Downtime Mode with sync/reconciliation (S6/S7). It genuinely completes intakes with no API.
- **Summarisation already has a no-vendor path**: `TemplateSummarizer` produces a deterministic
  doctor summary and read-back from the answers plus the rule-decided flags. Your "lean NLP
  fallback" is built, and honest about being thin.
- **The engine already downgrades rather than denies** — a provider outage lowers the tier and
  never blocks an intake.

What is missing is that this has **never been assembled as a cloud deployment**. Specifically:
a GPU-free AWS profile that boots with every AI provider off and every ladder ending at `v3`;
a `assert_production_safe`-style check that refuses to boot such a profile with a cloud AI key
accidentally set; DNS/traffic switching between box and cloud; and a documented, *rehearsed*
failover with a restore. Also, LLM-dependent features (dictation mapping, check-in
personalisation, the receptionist, adaptive intake) must degrade to manual entry **visibly** —
a doctor needs to see "dictation mapping is offline, type it" rather than a spinner.

Built in **S-GL.6**, which replaces doc 06's S19 for this purpose.

## 6. Doctor onboarding and roster import

**Not in the admin console at all.** Doctors, staff users and slot templates come from
`seeds/doctors.json` and `seeds/slot_templates.json` via `make seed`. There is no route to
create a user, no invite flow, and the slot-template editor is still S18-late's one honest
deferred placeholder. Onboarding a doctor today means editing a seed file and re-running the
seed on the box — not something a hospital administrator can do, and a real blocker for going
live with more than the five seeded doctors.

Built in **S-GL.2**.

---

## 7. The sequence

Each session is doc 06 format, and **the numbering is the execution order** — S-GL.1, then
S-GL.2, then S-GL.3, and so on. Three phases:

| | | |
|---|---|---|
| **Phase 1 — go live, kiosk-first** | S-GL.1 → S-GL.2 → S-GL.3 | Now. Every path in it is built; what they lack is a real patient. |
| **Phase 3 — conversational voice + resilience** | S-GL.4 → S-GL.5 → S-GL.6 → S-GL.7 | After go-live. Needs vendor credentials and real-call tuning; do not compress. |
| **Phase 4 — second platform** | S-P4.1 → S-P4.2 | iOS, moved out of the doc 06 Phase 2 backlog (§9). |

(Phase 2 remains doc 06's unscheduled product backlog — handwritten-Rx OCR, WhatsApp native
calling, FHIR export, multi-site tenanting, the analytics warehouse. It is numbered below
Phase 3 but sequenced after it: those are "someone asks for this" items, whereas Phase 3 is
finishing what the pilot started. Opening WhatsApp and telephony on the **existing V2
pipeline** is not in any of these phases — it needs no build at all once credentials exist,
just the S-GL.1 switch, and should happen whenever Meta and Exotel are provisioned.)

### S-GL.1 — The switchboard: channel enablement, capacity, and runtime provider config
**BUILT — 2026-07-26. Session log: `sessions/SESSION-GL1.md`.** Every item below shipped except
the seat share's *live wiring* into the voice path, which stays S-OSS.2 (the document, the cap,
the console and the tests are all here; routing an over-share call down its ladder is not). Two
things the plan did not anticipate and the session decided: the shipped `config/tiers.yaml`
changes nothing (channels stay open, the mix stays commented out — the go-live act is three taps
in the console, and the readiness rule already darkens the vendor channels on a box with no
account), and a `fake` provider counts as ready on a local box **but says so**, because the pilot
box runs `ENV=local` and would otherwise have shown a false green.
- **Load:** doc 02 §9, doc 08 §3/§5, `config/tiers.yaml`, `app/tiers.py`, `app/config.py`,
  `app/providers/registry.py`, `sessions/SESSION-18L.md`.
- **Build:**
  - `channels` table (or a `runtime_config` document, versioned + published like the tree and
    protocol banks — reuse that pattern rather than inventing a third): per channel, `enabled`,
    `tier_ladder`, `max_concurrent`. Seeded from `config/tiers.yaml` so nothing changes on day
    one; the file stays the floor.
  - A **hard gate at each channel's entry point** — `routes/kiosk.py`, `routes/whatsapp.py`,
    voice-gw's applets, `routes/patient.py` intake — that returns a civil "this channel is not
    open; please see the desk" rather than a 500 or a half-flow. Disabled must be *quiet*.
  - Extend `AdmissionController` with a **per-channel seat share** so phone traffic cannot
    starve a patient standing at the kiosk.
  - **Runtime provider credentials**: a `provider_settings` store (encrypted at rest) that
    overlays `.env` for `MESSAGING_PROVIDER`/Meta and `TELEPHONY_PROVIDER`/Exotel, with a
    `POST /admin/providers/{name}/test` that does one real round-trip and reports the vendor's
    own error. Credentials are **write-only** over the wire: set and test, never read back.
  - Admin console **Channels** tab: a switch per channel, its ladder, its cap, and a
    provider-credentials card per vendor with the test button.
  - Campaign **channel-mix weights** (doc 12 §1.2): "call X%, WhatsApp the rest" of tomorrow's
    list, deterministic per patient id so a re-plan does not reshuffle who gets what.
- **AC:** with every channel but kiosk disabled, a WhatsApp inbound and a phone call are both
  refused politely and audibly, nothing 500s, and the kiosk is untouched; enabling WhatsApp
  from the console (with credentials entered and tested) makes the bot answer with **no
  restart**; the seat share is proven by a test that fills phone seats and still admits a
  kiosk session; campaign dry-run at 30/70 produces the documented split.
- **Note:** treat "disabled" as the safe default for any channel with no tested credentials —
  going live should not require remembering to switch things off.

### S-GL.2 — Staff onboarding + roster (doc 03 §10's unbuilt half)
- **Load:** doc 03 §2/§10, `app/scheduling.py`, `app/models/org.py`, S18-late's editor pattern.
- **Build:** admin **People** tab — create/deactivate a user with a role, create a doctor
  against a department, and an **invite** flow that is just "this phone number can now sign in"
  (the OTP login already exists; no passwords, nothing to reset). Slot-template editor: the
  weekly clinic grid per doctor, with a **CSV/XLSX roster import** (doctor, weekday, start,
  end, slot type, capacity), a dry-run that shows what would be created before it writes, and a
  "generate slots" button over `app.scheduling.generate_slots`. Deactivation must not orphan
  booked appointments — surface them and make the admin decide.
- **AC:** a new doctor is onboarded, given a Tuesday clinic by CSV import, has slots generated,
  and appears in the receptionist's inventory and the doctor console — entirely from the
  console, on a box, with no seed run and no deploy; the import dry-run refuses a row naming an
  unknown doctor and says which row.

### S-GL.3 — Go-live hardening on the box (kiosk-first)
- **Load:** docs 09, 10, doc 01 §5, `HANDOFF.md` → "Owed on omen".
- **Build:** pay down the on-box debt that is currently owed and blocks a real patient:
  Telugu kiosk render checked on the actual screen; the admin console walked tab by tab
  against real data; a real Qwen3 run of `checkin_personalize` and `summarize` read by a human
  (that text is what a frightened person reads); one full kiosk intake per language on the box;
  the downtime drill executed on the box, not in a test; GPU contention measured — kiosk
  read-aloud during N concurrent local sessions, and `max_oss_sessions` set from the
  measurement rather than from doc 08's estimate.
- **AC:** a written record in the session log of each item observed on the box, with the
  measured concurrency envelope and the value `max_oss_sessions` was set to and why.
- **Note:** this session builds almost nothing. It exists because everything in it is a
  first-contact-with-reality item, and the pilot's first day is the wrong time for all of them.

### S-GL.4 — Gemini Live realtime adapter + a real turn detector
- **Load:** doc 02 §5, doc 03 §1b, doc 08 §2, `app/providers/realtime.py`, `voice-gw/gw/call.py`.
- **Build:** the real `GeminiLiveProvider` behind the existing `RealtimeVoiceProvider`
  interface — bidirectional audio, the function-call loop over the **unchanged** four-tool
  contract, metering per audio-minute, and the existing V1→V2 downgrade on failure.
  `prompts/intake_realtime/v1.md`: scope discipline (this is an intake, not a consultation),
  graceful return from an off-topic turn, never diagnose, never assert a red flag, hand to a
  human on distress. Replace the energy-threshold endpointer with **Silero VAD + smart-turn**
  (doc 08 §2) behind an interface so the energy proxy stays as the fallback. Timebox to the
  operator's **6 minutes**, keeping the existing graceful-partial behaviour.
- **AC:** a fake-client e2e intake completes on the real adapter's code path; an eval set of
  ~30 scripted off-topic / silent / interrupting turns shows the model returning to the tree
  every time and never emitting a red flag; p90 first-audio ≤1.5s measured; a 6-minute call
  ends with a usable partial; barge-in cuts playback within 300ms; **and the VAD change is
  measured against recorded noisy audio, not asserted**.

### S-GL.5 — OpenAI Realtime as the second adapter, and the choice made config-only
- **Load:** S-GL.4's output, doc 02 §9.
- **Build:** `OpenAIRealtimeProvider` against the same interface and the same prompt; make
  `REALTIME_PROVIDER=gemini-live|openai-realtime` a real config selector with a fallback chain,
  and expose the per-channel choice in the S-GL.1 Channels tab. Price-book rows for both, so
  the cost dashboard and the cost guard cover realtime minutes.
- **AC:** the same eval set passes on both vendors; swapping vendor is a config change with no
  code change; a realtime minute appears priced in `usage_events` and moves the tier-mix panel.

### S-GL.6 — The GPU-free cloud fallback, and a rehearsed failover
- **Load:** doc 05, doc 09 §9, this document §5.
- **Build:** an AWS profile that boots with **every AI provider off** and every ladder ending
  at `v3`, refusing to start if a cloud AI key is set (the inverse of `assert_production_safe`);
  the kiosk's offline speech path as the *primary* path in that profile, not the fallback;
  `TemplateSummarizer` as the only summariser; **visible degradation** in every LLM-dependent
  surface — dictation mapping, check-in personalisation, the receptionist, adaptive intake all
  say "offline, enter this by hand" rather than spinning; database replication box→cloud and
  the switchover runbook; CloudWatch alarms and the Grafana dashboards doc 06 S19 asks for.
- **AC:** with the box powered off, a full OPD day runs in the cloud profile — walk-in intake,
  token, queue, board, doctor console, manual consult note, printed prescription — with no LLM
  anywhere and no error state visible to a patient; a restore from last night's backup is
  performed and documented; failing back to the box loses nothing.
- **Note:** this supersedes doc 06's S19 for the pilot. Doc 06 S19 assumes AWS is the primary
  deployment; here it is the disaster-recovery profile, which changes the acceptance criteria
  but not the Terraform.

### S-GL.7 — Pilot dress rehearsal (doc 06 S22, brought forward and narrowed)
- Full-day simulation on the box with the channels you are actually opening, the failover
  drill from S-GL.6 executed mid-day, and `OPERATIONS.md` written from what actually happened.

---

## 8. Phase 1 in detail — what "go live" means and what it does not

Open **kiosk only**, on the box, with WhatsApp, telephony and app intake dark behind the
S-GL.1 switch. In order:

1. **S-GL.1 — the switchboard.** Without it there is no honest "off": a patient who messages
   the hospital's WhatsApp number today reaches a bot that will try and fail per message.
2. **S-GL.2 — staff onboarding + roster.** Without it you cannot add a doctor without editing
   a seed file on the box.
3. **S-GL.3 — the on-box reality pass.** The cheapest insurance in this document, and the only
   session that builds almost nothing.

**What a patient gets on day one:** she walks up, picks her language, describes her problem in
her own words to a local Whisper, answers a reviewed question tree by tap with everything read
aloud in Hindi/Marathi/Telugu/English, confirms a read-back, and takes a printed token. The
board calls her number. The doctor reads a summary with the red flags on top, dictates a note
that maps to structured fields with no silent drug substitution, signs it, and prints a
prescription with pictograms. If the network dies mid-morning, the kiosk keeps issuing tokens
from its offline block and reconciles when it returns.

**What is explicitly not open on day one**, and what to tell staff:

- **No phone intake, no AI receptionist, no outbound campaign** — no Exotel number yet, and the
  turn detector is not ready for a real line either way (§2).
- **No WhatsApp** — no approved Meta templates. Check-ins therefore have no first rung: the
  delivery ladder would fall to SMS, so **hold check-in plans in draft** (they need a doctor's
  tap anyway) or leave `CHECKINS_ENABLED=false` until WhatsApp is live.
- **No app intake** — the Android APK is unsigned and has never run on a real handset.
- **Adaptive voice answering stays off** (`INTAKE_ADAPTIVE=0`) — built, never proven with the
  flag on.
- **One box, no failover** — until S-GL.6 a hardware fault is a paper day. The Downtime
  Protocol covers this and should be drilled in S-GL.3, not discovered.

**Two things worth deciding before day one, not during it:** who is on `COORDINATOR_PHONE`,
and whether an oncologist has reviewed the **tree bank** — the trees a kiosk patient answers
are model-drafted and clinically unreviewed, exactly like the check-in protocol bank. The
kiosk-first pilot makes the trees the *only* clinical content in front of a patient, which
raises rather than lowers how much that review matters.

## 9. Phase 3 — conversational voice and resilience

Unchanged from the sessions above (**S-GL.4** Gemini Live + real VAD, **S-GL.5** OpenAI
Realtime as the second adapter, **S-GL.6** the GPU-free cloud DR profile, **S-GL.7** the dress
rehearsal), with one sequencing rule: **telephony opens the day the VAD is measured on real
Alwar calls, not the day the adapter compiles.**

Entry conditions, so this phase does not start half-blind:

- A live Exotel number and credentials, and at least one Meta-approved WhatsApp template.
- Phase 1 complete, with the S-GL.3 concurrency measurement in hand — a realtime tier's seat
  budget is decided against that number, not against doc 08's estimate.
- A decision on **who pays for realtime minutes**: Gemini Live and OpenAI Realtime are cloud
  services on a box built for zero cloud AI (§4). The tier ladder already expresses "phone is
  allowed to be expensive, the kiosk is not"; that choice should be made explicitly and priced
  in the cost guard before the first minute.

Two things I would refuse to drop from Phase 3 if it is compressed: **the S-GL.4 eval sets** (a
duplex model with no eval is an unreviewable clinical surface) and **the rehearsed restore in
S-GL.6** (an untested backup is not a backup).

## 10. Phase 4 — iOS

Moved here from doc 06's Phase 2 backlog. Doc 03 §1c specifies it as "same API; SwiftUI; add
HealthKit weight/temperature logging", which understates the work in one important way and
overstates it in another.

**Why it is smaller than it looks:** every `/patient/*` route, the patient-token identity
model, the caregiver grant, the dose-event adherence keying and the offline care-file contract
were built platform-neutrally in S16. iOS is a client against a proven API, not a new backend.

**Why it is not a port:** three Android affordances the app leans on do not exist on iOS.

- **Exact alarms.** S16's dose reminders use Android exact alarms and the invariant that *the
  phone never invents a dose time*. iOS has no exact-alarm equivalent — `UNCalendarNotification`
  is best-effort and the system may defer it. The reminder feature is therefore **weaker on
  iOS, and must say so** rather than quietly drifting; a dose reminder that arrives forty
  minutes late without explanation is worse than one that admits it is approximate.
- **Background sync.** `BGAppRefreshTask` is scheduled at the system's discretion, so the care
  file syncs on foreground rather than overnight. Fine — the file is small and ETag-conditional.
- **Distribution.** App Store review is days, not minutes, and the first submission needs an
  Apple Developer Program enrollment (an organisation account needs a D-U-N-S number, which can
  itself take a week), a privacy policy URL, and privacy-nutrition-label declarations covering
  health data. **Start the enrollment before the code**, because it is the long pole and it is
  pure paperwork.

**S-P4.1 — iOS client: care file, queue, login.**
- Load: doc 03 §1c, `backend/app/routes/patient.py`, `android/` for the contract it proved.
- Build: SwiftUI app, phone-OTP login against the shared `/auth/patient/*` flow with the
  `kind: "patient"` token claim; SwiftData-backed offline **My Cancer Care File** with
  ETag-conditional sync and PDF share; live queue position with the "leave home by" hint;
  family access via the existing `caregiver_links` grant, re-read per request.
- AC: the care file opens and shares in airplane mode; a revoked caregiver link closes the file
  at the next screen; the app never sends a `patient_id` (identity comes from the token).

**S-P4.2 — iOS: home intake, reminders, chemo calendar, and the store.**
- Load: S-P4.1's output, doc 03 §1c.4/5, doc 04 §3.
- Build: Talk-to-Dhara home intake over `SFSpeechRecognizer` + `AVSpeechSynthesizer` against
  the same kiosk handler bodies the Android app uses; dose reminders on
  `UNUserNotificationCenter` with **honest approximate-timing copy** and the same refusal to
  invent a time the doctor did not state; chemo calendar with spoken what-to-expect text;
  HealthKit weight/temperature logging (doc 03 §1c, read-write, opt-in, and **never** a clinical
  input — it is the patient's own record, not a vital sign the system grades); App Store
  submission with privacy labels and a TestFlight build for the OPD staff.
- AC: a full home intake completes on a real device; a reminder fires for a dose time the
  doctor actually stated and none fires for a bare "BD"; the build passes App Store review, or
  the rejection reasons are logged and addressed.

**Do not start Phase 4 until the Android app has run on a real handset for a fortnight.** Its
open questions — Doze behaviour overnight, whether patients install anything at all — are
answers Phase 4 should be built on, not guesses it should repeat.
