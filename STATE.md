# STATE

**Current release priority (2026-07-28):** CLOUD1's GPU-free AWS standby controls are
built on `aws-gpu-free-standby` and focused local infrastructure gates are green.
Nothing was provisioned: real AWS/DNS/Omen access is still required for ECR push,
TLS, backup/restore, cloud-voice, failover/failback, and measured RPO/RTO evidence.
The user authorized proceeding to ANDROID1 while these external gates stay explicit.

**Built (SESSION-CLOUD1):** Terraform defines one encrypted gp3 EC2/Compose box with
only 80/443 inbound, SSM rather than SSH, immutable scan-on-push ECR repos, a
versioned/encrypted backup bucket, least-privilege runtime access, log groups, and
instance/disk/public-health/backup-age/provider/cost alarms. The standalone AWS
Compose file is CPU-only and private behind host nginx. Full-SHA multi-architecture
image publishing records ECR digests and retains the previous manifest. Runtime
scripts fetch one allow-listed Secrets Manager object into a root-only `0600` file,
issue/test TLS before HSTS, deploy/migrate/rollback without `latest`, back up every
15 minutes, verify an isolated restore daily, and enforce single-writer promotion
with PostgreSQL `default_transaction_read_only`. A disposable PostgreSQL test proved
demotion rejects writes and promotion restores them; live AWS/Omen proof remains open.

**Built (SESSION-VOICE1):** Kiosk voice has exactly three operator-selectable profiles:
`local_oss`, `openai_cloud`, and `sarvam_cloud`. An intake snapshots exact STT/LLM/TTS
providers and models; every later voice turn resolves from that snapshot. OpenAI uses
`gpt-4o-mini-transcribe` + `gpt-5.6-luna` + `gpt-4o-mini-tts`; Sarvam uses `saaras:v3`
+ `sarvam-30b` + `bulbul:v2`; local uses Whisper + vLLM + configured local TTS.
No snapshotted profile appends a cross-vendor fallback, and exhaustion returns the
unchanged deterministic node to taps. `usage_events.voice_profile` records non-PHI
attribution. Encrypted write-only `vendor:openai` / `vendor:sarvam` rows share one key
across their components; the Channels console exposes configured/source/test/health/model
metadata, per-component tests, a new-intake-only selector, and a publish gate requiring
all three cloud components to have passed after the latest credential change. Local
gates: backend 1,261, voice-gw 25, conformance 48, Android, language QA, production build,
migration, preflight, kiosk E2E 3, Channels E2E 4.

**Built (SESSION-UX2):** Kiosk intake now captures a normalized Unicode patient name
online and offline, retains rolling-client fallback behavior, and purges successfully
synced IndexedDB PII. Optional tree `summary_role` metadata drives a presentation-only
live rail without entering traversal, routing, red flags, or clinical rules. The kiosk
uses deterministic responsive layouts with a 1280x800, 1024x768, and 800x1280 matrix at
100%/200% text scale. Prescriptions share one letterhead renderer for protected preview,
download, and print; the authenticated PDF route uses WeasyPrint with Noto Indic fonts,
repeating table headers, coherent page breaks, and complete mr/te patient strings.
`make test`, `make lang-qa`, `make preflight`, `npm run build`, kiosk E2E, and offline
sync E2E pass. Physical-device and printer acceptance remain the only release blocker.

**Built (S1):** Monorepo skeleton — `backend/` (FastAPI api + Celery worker/beat), `voice-gw/`
(FastAPI), `web/` (Next.js 14, 5 route groups, design tokens), `infra/` (Terraform pilot,
plan-only + Caddyfile). Full docker-compose stack (11 services) runs healthy via `make dev`. CI
(GitHub Actions), Makefile, pre-commit.

**Built (S2):** Full doc 02 §4 schema — 21 SQLAlchemy models + Alembic migration that round-trips
and matches the models. Phone-OTP login → JWT (access + rotating refresh with revocation), Argon2
hashing, RBAC guards. Append-only audit trail covering every clinical write. Idempotent seed
(1 hospital, 9 departments, 5 doctors + 3 staff, 50 deterministic patients).

**Built (S3):** Provider layer (doc 02 §9) — seven interfaces, each with a fake and a real impl:
SMS (**MSG91 + Exotel, both**), LLM (Gemini Flash + OpenAI), STT (Sarvam + Google), TTS (Sarvam +
Google), Messaging (Meta WhatsApp), Telephony (Exotel); Realtime = interface + fake only.
Usage metering into `usage_events` (async, batched, priced against `price_book`), cost
computation, retry + circuit breaker, provider health registry (`GET /providers/health`),
cost-guard (budget → tier downgrade), and `prompts/` — four versioned vendor-neutral prompts +
the V1/V2 shared tool contract. 231 backend tests. `make test` green.

**Built (S4):** Question-tree engine (`app/trees/`) — doc 03 §3's node schema + a validator
that rejects unreachable nodes, cycles, incomplete languages, >5 options (doc 03 §1a) and
rules that can never fire; a deterministic red-flag rule language (`rules.py`) no model
participates in; and `Walk`, one patient's position in one tree, **derived from the answers**
(the V3 tier, and the engine under S5's four tools). 11 authored trees in `seeds/trees/`
(en+hi, 89 nodes, 40 red flags) covering all 9 departments, seeded as **draft**. Department
classifier (`app/routing.py`) around `routing@v1`, plus a 60-utterance eval set and harness
(`app/evals.py`, `make eval-routing`). 466 backend tests. `make test` green.

**Built (S5):** Intake Engine (`app/intake/`) — one `IntakeEngine` driving an intake
across the V1/V2/V3 tier ladder, all calling the same four-tool contract over one
`Walk` via `ToolDispatcher`. `SessionState` in Redis (in-memory local) stores the
**answers, not a cursor**, plus configured + active tier. V1 = Gemini Live session
bridge (audio passthrough hook for voice-gw); V2 = STT→LLM→TTS turn pipeline; V3 =
deterministic walker + `voicepack` (TTS fallback). Automatic downgrade on provider
failure OR cost-guard, rebuilding the walk from stored answers (lossless). Summarizer
(`summary.py`) = doc 03 §4 contract + patient read-back, LLM path with a deterministic
offline fallback; red flags always from the rules. `finalize_cost` sums `usage_events`
by intake_id onto `Intake.cost_inr`. `prompts/intake/v1.md` = the dialogue driver.
466→**486 tests**. Not wired to any route (channels are S6/S12/S14).

**Built (S6):** The **kiosk channel** — the intake engine's first HTTP surface.
`app/routes/kiosk.py` = thin REST mirroring the four-tool contract (start / next /
answer / finish / confirm); `app/kiosk.py` = the service (route Q1 through the
classifier honouring `needs_human` → a department chooser; create the walk-in
Visit+Intake; provisional token allocation). One `IntakeEngine` on `app.state`.
The **kiosk PWA** (`web/app/(kiosk)/kiosk/`) — a V3 client driven by taps + audio:
expanded design tokens on the doc 04 §1 palette, self-hosted Noto Sans/Devanagari,
a component library (breathing **Dhara** avatar, AudioBar, OptionCard, FacesScale,
Stepper, BodyMap, ProgressDots, MicButton, duotone icons), and the full flow
language → caregiver → voice chief complaint → chooser → tree questions
(auto-read-aloud) → read-back + confirm → **train-board token**. Audio-first, ≥64px
targets, 60s idle prompt / 90s privacy blur. Playwright suite (`web/e2e`) drives a
full hi intake welcome→token against the local stack; 11 screens in
`web/screenshots/s6/`. **492 backend tests** (486→492) + web e2e. `make test` green.

**Built (S7):** The **kiosk goes offline-first** (doc 01 §5). The tree walker + red-flag
rules are ported to TypeScript (`web/app/(kiosk)/kiosk/_lib/tree/`) so an intake completes
in the browser with no API, gated against the Python original by a golden-trace conformance
suite (`app/tree_fixtures.py` → `web/e2e/conformance.spec.ts`, regenerated + diffed in
`make test` via `make check-tree-fixtures`; mutation-tested). `Tree.to_json()` is the
canonical desugared wire shape. **Offline token blocks** (`app/offline.py`): the token line
is partitioned — online `< kiosk_offline_token_base(500)`, offline blocks `>=` — so a
collision is unrepresentable; `POST /kiosk/blocks/lease` (idempotent) + `POST /kiosk/sync`
(idempotent per `Intake.client_id`, recomputes red flags server-side). `Intake` gained
`client_id` + `tree_ref` (migration `bc2e83129ac3`); `allocate_token` refuses to cross the
base. `GET /kiosk/bundle` ships canonical trees + chooser (ETag). Web offline layer
(`_lib/offline/`: Dexie store, local intake flow, `/health` reachability monitor, the
online-or-local `flow` seam, background sync, `useOffline` lifecycle) + a shell service
worker (`kiosk-sw.js`) + a **marigold Downtime banner** (doc 04 §3). ESC/POS token-slip
print (`_lib/print.ts`) + browser fallback. Demo AC proven at the service layer
(`test_offline.py`) and in a browser (`web/e2e/offline-demo.spec.ts`). 515→**541 tests** +
48 pure-logic web tests.

**Built (S8):** The **live queue** over the tokens (doc 03 §6). `app/queue.py` = a
`QueueEntry` per visit with ordering *derived* from `(priority_rank, position,
token_no)`, so an urgent red-flag intake jumps the line by construction (severity
from the rules, never re-decided) with a reason chip; plus `call_next`, a guarded
state machine (waiting→called→in_consult→done / no-show / lab-requeue-to-back),
drag `reorder` (priority still wins), a wait estimator (observed mean consult time,
seeded), `board`/`department_queue` read models, and `paper_entry` for downtime
recovery. The kiosk **confirm** and the S7 offline **sync** now `enqueue_from_intake`
+ broadcast, so a token is on the board the instant it's issued (online or
synced-from-downtime). `app/queue_hub.py` = in-process WebSocket fan-out + the
in-memory downtime flag. `app/routes/queue.py` = board (public) + `/queue/ws`,
console + action verbs (staff), downtime get/set, a reconciliation list (offline +
paper intakes), a paper-entry form, and two print routes. `app/print_sheets.py` =
downtime paper intake forms (one fillable A4 per tree, bilingual) + a tear-off
token-block sheet, both from live data (HTML→browser-print). Web: the **TV board**
(`app/(board)/board` — train-platform numerals, next-3, wait ranges, LIVE + clock,
2-lang chime + speech announce, marigold downtime banner) and the **coordinator
console** (`app/(coordinator)/coordinator` — phone-OTP login, call-next/state/drag
reorder, downtime enter/exit repainting the app bar marigold, reconciliation table,
paper-entry form, print tab); shared `app/_lib/queue.ts` + `useQueueSocket.ts`.
541→**577 tests** + `web/e2e/queue.spec.ts` (live) + `scripts/seed_queue_demo.py`.
No migration (Queue/QueueEntry existed since S2).

**Built (S9):** The **doctor console** (doc 03 §4/§5). `app/doctor.py` is two reads and
no writes: `day_list` (the doctor's own department queue, in the queue's own urgent-first
order, with the patient behind each token) and `patient_card` (the stored doc 03 §4
summary, the rule engine's red flags, the answers rendered against the tree in `tree_ref`,
the visit timeline, and the check-in trendline). `app/routes/doctor.py` exposes
`GET /doctor/day` + `GET /doctor/patients/{visit_id}`, both `require_doctor`. **S9 added no
action endpoints** — call-next / no-show / lab-requeue are the S8 `/queue/*` verbs the
coordinator console already drives. Web: `app/(doctor)/doctor` — phone-OTP login lifted
from S8, the day list as a **vertical clinical spine** (tokens as stations, the patient in
the room the one filled marigold node, urgent tokens ringed in danger), and the patient
card ordered by clinical urgency: red-flag **stamps** carrying the rule's own instruction,
then chief concern + compact symptoms table, then everything else collapsed. Keyboard
shortcuts N (call next) and D (dictation → honest "S10"). 581→**603 tests** +
`web/e2e/doctor.spec.ts` (project `doctor`, the session AC as a test) +
`scripts/seed_doctor_demo.py`. No migration.

**Built (S10):** **Dictation → structured fields** (doc 03 §7). `app/formulary.py` +
`seeds/formulary.json` (189 generics / 617 dictatable names, chemo through supportive
care): `known` is set by **exact match only**, fuzzy matching produces advisory
`suggestions` and an `ambiguous` flag, and there is no code path from a score to a
written name. `app/dictation.py` = the doc 03 §7 contract, `validate_meds` (which
discards the model's own `known` claim), `_was_said` (a drug name absent from the
doctor's own words is flagged — the only way to catch a model that *renamed* a drug
into another real one), `DictationMapper` (the `dictation_map@v1` prompt on the LLM
chain, so `LLM_PROVIDER=local_vllm` runs it on the box's Qwen3 unchanged), and the
record's state machine `start → map → correct → sign`. `mapped` is frozen and `fields`
carries the doctor's corrections with an append-only `edits` trail, which is what makes
the review diff-style. Signing locks the record and refuses while any flagged drug is
unacknowledged. `app/routes/dictation.py` = six routes, all `require_doctor`. Web:
`_components/DictationPanel.tsx` — the consult note on the console stage, where every
written value hangs under the phrase it came from. 603→**708 tests** +
`web/e2e/dictation.spec.ts` (project `dictation`) + `backend/tests/fixtures/dictations.json`
(ten Hinglish notes). No migration.

**Built (S11):** **Digital prescription** (doc 03 §8) — what a signed note becomes.
`app/prescription.py` is generated *inside* `dictation.sign` (idempotent per dictation),
so a prescription cannot exist without a signature and there is no `POST /prescriptions`.
The one piece of interpretation on the page is the dosing schedule, and it has the hard
rule: `parse_schedule` reports **only what the doctor's words state**, keeping *slots*
("1-0-1", "subah aur raat") apart from a bare *count* ("BD") — a count-without-a-time
renders as N tablet glyphs, never a sun and a moon, and an unreadable frequency ("SOS",
"alternate days") returns `None` and prints the doctor's words with no icon at all.
`app/rx_sheets.py` renders the snapshot twice: a letterhead **clinical copy** (drug table,
`as_spoken` under each line, signature block naming whoever *signed*) and a large-type
**patient copy** (one band per drug, morning/afternoon/night pictograms, en+hi). A flagged
drug prints flagged on both — `RxLine.flagged` deliberately does not mirror
`meds_needing_attention`, because acknowledgement unlocked signing and did not make the
drug known. `app/routes/prescription.py` = read / history / print / deliver, all
`require_doctor`; delivery goes through the provider layer and a vendor outage is
**recorded as `failed`, not raised**. Web: `_components/RxPanel.tsx` under the signed note,
previewing the schedule with the same three branches the sheet uses. 726→**781 tests**.
No migration (`Prescription` has existed since S2; `meds`/`delivered_via` are JSONB).

**Built (S12):** The **WhatsApp bot** (doc 03 §1d) — the intake engine's **second
channel**, sibling of the kiosk. `app/whatsapp/`: `bot.py` (`WhatsAppBot`) drives one
Meta webhook message at a time over the *unchanged* `IntakeEngine` / four-tool contract
/ tree walker / red-flag rules — language → chief complaint (typed **or a voice note →
STT**) → department chooser (only when the classifier is unsure) → the tree as **reply
buttons (≤3) or a list (4–5)** → read-back → confirm → token, ending in a Visit +
QueueEntry on `Channel.WHATSAPP`. `conversation.py` = `Conversation` state keyed by
`wa_id` (Redis/in-memory, like `SessionState`): the wa_id → session mapping, the
pre-intake step, and the **24h window** (`last_inbound_at`) that is WhatsApp's alone.
`templates.py` = the pre-approved template registry ("templates in repo", bilingual,
variable counts validated at import + before the wire; seeded `intake_invite`,
`token_status`, `prescription_ready`). `render.py` = Node → interactive. Two
patient-initiated commands short-circuit at any idle point — **token status** and
**resend prescription** — both free-text (a patient who messaged is in-window by
definition). `app/routes/whatsapp.py` = the GET verify handshake + the POST inbound
webhook (**app-secret signature** verified, verify-token checked, always 200s, exact
redeliveries dropped by message id). The **bot never sends** — `handle` returns messages
and the webhook does the sending + the one commit, so it is a pure function of (state,
inbound) the tests drive without a live Meta. `MetaWhatsAppProvider` gained
`upload_media` (voice-note replies go by uploaded id) + an interactive-list payload.
S11 Rx `deliver` is now **window-aware** (out-of-window → the `prescription_ready`
template). Voice-note replies (TTS) behind `WHATSAPP_VOICE_NOTES` (default off). New
config `META_VERIFY_TOKEN` / `META_APP_SECRET` / `WHATSAPP_VOICE_NOTES`. 781→**816
tests**. No migration.

**Built (S13):** **Multilingual completion** (doc 03 §1) — the pilot now speaks all
**four** languages, not two. mr + te fill every patient-facing surface: the whole tree
bank (11 trees, every node/option/title/red-flag — 258 unique strings applied by a
single `en→(mr,te)` map so a repeated phrase reads identically everywhere), the kiosk
shell (`web/.../i18n.ts`, `KioskLang` widened so tsc fails on a missing language), the
three WhatsApp templates, and the offline read-back. `app/languages.py` holds
`PILOT_LANGUAGES = (en,hi,mr,te)` as the **one source of truth** the seed, the tests
and the harness share, plus `looks_like_script` (Devanagari for hi/mr, Telugu for te).
The **language QA harness** (`app/lang_qa.py`, `make lang-qa`, a named CI step) is the
S13 deliverable: completeness across surfaces the tree validator never sees, a
script/no-English-leak check, glossary consistency (`seeds/glossary.json` fixes 11 core
symptom words; the bank may not drift to a synonym), and an STT/TTS round-trip + real
BCP-47 mapping per language. Font audit (doc 04 §4): **Noto Sans Telugu** self-hosted at
build alongside Devanagari, font-family falls through Latin→Deva→Telugu, ≥1.6 line-height
now covers mr + te. 816→**826 tests**. No migration. **mr/te are model-drafted, pending
native clinical review at S21** (same stance as the hi text).

**Built (S-ADAPT.1 + .2):** **Adaptive intake** (doc 11) — a patient can answer a tap
node **by voice**, and one spoken turn can fill more than the node that was asked.
`app/intake/interpret.py` is the whole idea: given a node (its question + the answers
it accepts) and a transcribed utterance, the LLM returns **either a candidate value
the node already accepts, or one short clarifying question** — it never returns free
text, and every candidate still goes through the unchanged `walk.save()` validator and
the unchanged deterministic rule engine. **The model proposes; the engine decides.**
V2 adds *enrichment* (the interpreter returns `extra: [(node_id, value)]` for other
nodes the patient volunteered; the route validates each and stashes it in
`SessionState.pending_prefills`, and `ToolDispatcher._drain_prefills` auto-applies it
through the same `walk.save` the moment the walk *reaches* that node — so the node is
skipped, never re-asked, and an enrichment for a branch never taken stays inert and is
pruned), an opt-in `Node.adaptive` that may ask one bounded sub-question, and per-node
telemetry on `Intake.adaptive_events` (migration `a1b2c3d4e5f6`) that
`app/intake/adaptive_report.py` aggregates into clarify / mis-map / enrichment rates
and **reconciles against the intake's `INTAKE_TURN` usage_events**. Prompts
`interpret_answer/v1+v2`; extended `POST /kiosk/{sid}/answer`. Gated on
`INTAKE_ADAPTIVE` + a real LLM and `NEXT_PUBLIC_KIOSK_ADAPTIVE=1` — **both default
off**, and with them off the kiosk is byte-for-byte the pure-tap V3 flow. 599→**726
tests** (union with S9/S10). ⚠️ **Never run with the flags on against a live model
(see below).**

**Built (S-OSS.0):** The **V-OSS** software layer (doc 08) — the fully-open-source local
voice tier as ordinary provider adapters, no GPU required to build. `app/providers/local_oss/`:
`LocalLLMProvider` (vLLM, reusing the OpenAI wire, keyless), `LocalSTTProvider` (Whisper,
OpenAI-audio-compatible), `LocalTTSProvider` + `VoiceboxTTSProvider` — all config-only-selectable
(`LLM_PROVIDER=local_vllm`, `STT_PROVIDER=local_whisper`, `TTS_PROVIDER=local_tts|voicebox`) and
metering `provider=local-*`, priced from amortized `local-*` `price_book` rows. `config/tiers.yaml`
+ `app/tiers.py` = per-channel tier ladder loader (validated at boot); `AdmissionController` =
`MAX_OSS_SESSIONS` concurrency cap that routes overflow to the next tier and frees seats on crash.
492→**515 tests**. The **GPU half** (S-OSS.1 bake-off, S-OSS.2 Pipecat realtime + 12-concurrent
proof, S-OSS.3 Dhara cloning) needs the physical 24 GB box — not built here; `local-pipecat`
realtime refuses to build until then.

**Built (S15):** **Appointments** (doc 03 §2, doc 01 §4.2/4.4). `app/scheduling.py` = slot
inventory + constraint-safe booking: `SlotTemplate` (the clinic grid) → `generate_slots` →
`AppointmentSlot`, and **a seat is a row** — `appointments.UNIQUE(slot_id, seat_no)` plus
`CHECK(0 <= booked <= capacity)` and a conditional seat claim make double-booking
unrepresentable; cancelling NULLs `seat_no` to release it. `app/receptionist.py` = the inbound
AI receptionist: one distrusted model call (`prompts/receptionist/v1.md`) picks the intent
(book/reschedule/cancel/status/human), everything after it is deterministic over real inventory,
slots are chosen by keypad digit, and two failed turns (or any classifier failure) transfer to a
coordinator with a **whisper summary**. `app/notify.py` = WhatsApp **+** SMS on every booking
(template out of window, one-tap confirm/cancel buttons in window), recorded on
`Appointment.reminders` and never able to fail a booking. `app/campaign.py` + `app/worker.py` =
the **D-1 outbound campaign** as four idempotent beat jobs (plan/launch/dial/reconcile) with the
2-attempt ladder in an `outbound_calls` row and a WhatsApp last rung; `campaign_enabled` is off
by default. `app/routes/appointments.py` = staff booking REST + the **Exotel status callback**
(meters per-call minutes, always 200s). `voice-gw`: `WS /exotel/receptionist` +
`gw/reception.py` over S14's transport/pump/VAD, and the **Redis-backed `PhoneCallStore`**.
Migration `48da92857b2a`; `seeds/slot_templates.json`; `make slots`, `make campaign-dryrun`.
840→**907 backend tests**, voice-gw 15→**22**.

**Built (S16):** The **Android patient app** (doc 03 §1c) and the backend's first *patient*
identity. `app/patient_app.py` + `app/routes/patient.py` = the `/patient/*` surface, all of it
derived from rows other sessions already write (S11 prescriptions, S5 summaries, S8's queue order,
S15's `app.scheduling`) — and every read scoped on the **token's** patient id, with no
`patient_id` parameter in the router at all. Identity: `create_patient_access_token` carries a
`kind: "patient"` claim that `current_principal` refuses and `current_patient` requires;
`refresh_tokens` gained `patient_id`/`subject_phone` under `CHECK((user_id IS NULL) <> (patient_id
IS NULL))`; `app.auth.otp.check_code` is the shared code-checking both audiences use.
`caregiver_links` is an **access grant** (never inferred from `patients.caregiver_phone`, which is
a desk contact), re-read on every request so a revocation lands at the caregiver's next screen.
`dose_events` is adherence keyed `(prescription_id, med_index, scheduled_for)` so a re-report
cannot ping a caregiver twice. `app/routes/kiosk.py` grew `next_node_impl`/`answer_impl`/
`finish_impl` so the app walks the **kiosk's own handler bodies** behind its own login.
`android/` = Kotlin/Compose, minSdk 26, **1.53 MB** release APK: four tabs, Room-backed offline
care file (ETag-conditional sync, `PdfDocument` share), home intake over device STT/TTS, live
queue position with a "leave home by", WorkManager + **exact** alarms that ring only for dose
times the doctor actually stated, chemo calendar read aloud from `seeds/regimen_notes.json`, and
family access. Migration `e108276e7d43`. 907→**932 backend tests** + 6 Android JVM (in `make
test`) + 6 instrumented (emulator, `make android-test-device`).

**Built (S17):** The **check-in engine** (doc 03 §9) — what a signed note becomes *next week*.
`seeds/protocols.json` + `app/checkins/protocols.py` = the protocol bank: six regimen families
(platinum, taxane, anthracycline, radiotherapy, post-op, palliative) over seven question sets, in
four languages, whose grading rules are **the S4 red-flag rule language** validated against the
question types at load — so no model decides a check-in grade, `free_voice` is unmatchable by
construction, and **green is the absence of a fired rule** (a `green` rule is a load error).
`app/checkins/plan.py` drafts inside `dictation.sign`: the family from the formulary **class** of
the prescribed drugs plus keywords over the *structured* note (never the transcript), the days
from the protocol, and only then the LLM — `apply_personalisation` copies messages back rung by
rung, so a model that adds a day, drops one, swaps a question set or returns prose gets a plain
four-language message and no schedule change. A doctor's one tap (`POST
/checkins/plans/{id}/approve`, no body) freezes the plan and materialises `Checkin` rows carrying
`asked`, the questions **as sent**. `app/checkins/delivery.py` + `window.py` walk WhatsApp →
voice → SMS advancing on **silence** (a refused send drops at once; an accepted one waits 6h),
and defer out of 21:00–08:00 **without consuming an attempt**. `app/checkins/grading.py` grades
on every answer: a red ends the check-in on the spot and SMSes the doctor who signed plus the
coordinator, an amber waits on the nurse queue, an unreachable check-in expires with **no grade**.
The one LLM in the answer path (`prompts/checkin_triage/v1.md`) may raise green→amber over the
free-text answer and may neither produce a red nor lower anything. `app/checkins/cycles.py` sends
D-2/D-0 next-cycle reminders, reusing S15's `notify_appointment` when there is a slot to confirm.
`app/routes/checkins.py` (drafts/approve/cancel = `require_doctor`; review/resolve =
`require_clinical`), the WhatsApp bot's `ck:` branch, two beat jobs, migration `ae3caebf5e9a`,
`make checkin-demo`. `GET /admin/protocol-templates` stopped being a deferred marker.
932→**1071 backend tests**.

**Built (S18-late):** The **admin console finished** (doc 03 §10). The **visual tree editor**
(`web/.../TreeEditor.tsx`) draws a tree as a spine in ask order — branches indented under the
option that leads to them, red-flag stations stamped and tinted — with the question text, the
option labels and the red-flag label/instruction/severity editable in all four languages, a
try-it panel that dry-walks the *edited* JSON through the real walker, and save→publish as two
deliberate taps. It edits **words, not shape**: adding, deleting or rewiring a question stays in
`seeds/trees/*.json` and a pull request, where the validator's unreachable-question and cycle
checks are read by a person. The **check-in protocol bank moved into a table** the way S4's trees
did — `protocol_banks` holds versions, `app/checkins/store.py` `resolve_bank()` prefers the
newest published row that parses and falls back to `seeds/protocols.json`, and every check-in
entry point (drafting, approval, the plan view) resolves through it; the bank is versioned as
**one document** because `protocols.parse` cross-checks the whole thing (no orphan set, no tied
precedence), and `parse()` is still the only constructor. That editability forced
`Checkin.grading_rules`: a grade is recomputed on every answer, so rules read live would let an
afternoon's publish re-decide Tuesday's answers — the rules are now frozen onto the row beside
the questions `asked` already froze. `analytics.tier_mix` is doc 03 §11's other what-if,
**measured not modelled** (medians this hospital booked; it refuses rather than pricing phone V2
off kiosk intakes). Migration `cb011d62f829`. 1071→**1082 backend tests** +
`web/e2e/admin.spec.ts` (project `admin`, `npm run e2e:admin`, 4 tests — the session AC among
them) + `web/screenshots/s18l/`.

**Built (S-GL.1):** The **switchboard** (doc 12 §1/§4) — an honest "off". A **channel document**
(`channel_configs`, `app/channels/store.py`) is the third instance of the versioned
draft→publish→resolve pattern, with `config/tiers.yaml` as the floor, carrying per-channel
`enabled` / `ladder` / `max_concurrent` plus the campaign mix. Two facts are kept apart
(`app/channels/state.py`): the **switch** is the operator's decision and lives in the document;
**readiness** — whether Meta or Exotel is actually provisioned — is computed from settings and
cannot be asserted from a console, so a hospital that forgets to close WhatsApp still has a closed
WhatsApp. Gates sit at each channel's entry point on **start** and never mid-flow (kiosk + app 503
with the line in her own language, the Meta webhook still 200s and runs no bot logic, both voice-gw
applets answer/speak/hang up without taking consent). **Runtime vendor credentials**
(`app/providers/{secrets,runtime,probe}.py`, `provider_secrets`) are Fernet-encrypted, write-only
over the wire, restricted to their own vendor's fields, with `.env` as the floor and a
`POST /admin/providers/{name}/test` that reports the vendor's own error. `AdmissionController`
gained per-channel GPU seat shares. Admin **Channels** tab. Migration `2c978d44c900`; new
dependency **`cryptography`**. 1082→**1156 backend tests**, voice-gw 22→25.

**Built (S-GL.2):** **Staff onboarding + the roster** (doc 03 §2/§10) — the console's last two
unbuilt panels, and the end of "hiring a doctor is a deploy". `app/people.py`: create a user or a
doctor (a `User` login identity and a `Doctor` clinical profile in one transaction), an **invite**
that mints nothing (the OTP login already *is* the credential, so it is an SMS saying the number
works — no token to leak or expire), `normalise_phone` (the login looks up `users.phone` by exact
match, so any other shape is an account that silently cannot sign in), and **two-step
deactivation** — `deactivation_impact` lists the booked patients by name and `deactivate` refuses
without an acknowledgement, then blocks the empty future slots and leaves the booked ones standing.
`app/roster.py`: the weekly clinic grid, a **CSV/XLSX roster import** that is all-or-nothing and
dry-run-first (every row's errors at once, reported against the line number in the administrator's
own spreadsheet), and `_reconcile` — the one piece of real engineering here, because
`generate_slots` dedupes on `(doctor, instant)` **regardless of `blocked`**, so blocking a moved
template's slots and regenerating would empty the clinic forever. `scheduling._instants` became
public `instants_on` so generation and reconciliation cannot drift. Admin **People & roster** tab
(the week as a timetable; an ungenerated clinic drawn hollow). **No migration.** 1156→**1212
backend tests** + `web/e2e/people.spec.ts` (project `people`, `npm run e2e:people`, 5 tests — the
session AC among them) + `web/screenshots/sgl2/`.

**Not built yet:** the real Exotel vendor WS + a live number (S14/S15 are proven against the
fake client; `transfer_call`'s whisper applet is unproven against the vendor); the real Gemini
Live vendor impl (S14 wired the bridge, the vendor is still fake); an appointment **waitlist**
(S15 releases the seat, notifies nobody); real voice packs / the voice-pack manifest +
`/kiosk/stt` (S7 carryover → backlog); the V-OSS **GPU half** (S-OSS.1/.2/.3 — needs the GPU
box).

## How to run
```
make dev                 # full stack (11 services)
make migrate             # apply migrations to the local DB
make seed                # load the pilot dataset + price book + trees (idempotent)
make test                # backend + voice-gw pytest, web typecheck/lint, android JVM tests
make preflight           # build the api+voice-gw IMAGES and prove they import — run before any
                         #   box deploy. `make test` runs in the venvs (pyproject); the images
                         #   install backend/requirements.txt, and the two have drifted twice.
make migration m="..."   # autogenerate a revision from model changes
make eval-routing        # score the routing classifier (needs a real LLM key to mean anything)
make app-demo            # give the first seeded patient a prescription, a cycle, a caregiver
make checkin-demo        # sign a chemo note, approve the plan, answer D+2 red (S17)
make android-emulator    # boot the pilot AVD headless
make android-install     # install the debug app, pointed at http://10.0.2.2:8000
make android-test-device # instrumented tests (needs a booted emulator)
make android-apk         # release APK + the 15MB size gate
```
Android app (S16): `android/`, package `ai.radpretation.opd`. Needs a **JDK 17**
(`ANDROID_JAVA_HOME`, default `/opt/homebrew/opt/openjdk@17`) and an SDK path in
`android/local.properties` (gitignored). Demo login: `make app-demo`, then sign in on the
emulator as `+915551900001`; with `OTP_DEBUG_ECHO=true` the code comes back in the
`/auth/patient/otp/request` response, so read it from the api log or re-issue the request with
curl. The linked caregiver is `+915551900099`.
Queue board + coordinator console (S8): served at `/board` (public TV) and
`/coordinator` (staff, phone-OTP). The board holds a WebSocket to `/queue/ws` and
re-fetches on every change ping. Live demo (needs a live api with S8 code — the
dockerised image predates it): run a local uvicorn with `OTP_RESEND_COOLDOWN_SECONDS=0`,
`python -m scripts.seed_queue_demo` for a deterministic demo queue, then
`npm run e2e:queue`. Coordinator login: `+915550000002` (seeded coordinator); the
OTP is echoed on the login screen locally. See HANDOFF.md for the exact commands.
Kiosk PWA: `web/app/(kiosk)/kiosk`, served at `/kiosk` (web on :3000, api on :8000;
`NEXT_PUBLIC_API_BASE` points the browser at the api). The Playwright screenshot
suite runs against a live stack: `cd web && npm run e2e` (needs `make dev` + a
seeded dev DB; drives welcome→token, writes `web/screenshots/s6/`). The kiosk is a
V3 client — the fake classifier always triages, so Q1 lands on the department
chooser locally; pick a department to proceed.
Local login: `POST /auth/otp/request {"phone": "+915550001001"}` (seeded doctor) returns
`debug_code` when `OTP_DEBUG_ECHO=true`; POST it to `/auth/otp/verify` for a JWT.
Provider status: `GET /providers/health` (unauthenticated; names + health only, never keys).

## Environment gotchas
- **The current Compose file does not mount `./config` into the API container.**
  `app.tiers` resolves the backend-image path as `/config/tiers.yaml`, so a fresh
  Omen API can raise `TierConfigError` on `POST /kiosk/start` even while `/health`
  is green. Until the repository fix lands, the `api` service needs
  `./config:/config:ro` alongside `./seeds:/seeds:ro`, followed by an API-only
  recreate. This was observed on the Omen upgrade on 2026-07-27; it is unrelated
  to nginx/Caddy/CORS and must be fixed in source before the next deploy.
- **Postgres: host port 5433**, not 5432 — a native Postgres owns 127.0.0.1:5432 on this dev
  machine and wins over Docker's bind, so 5432 silently reaches the wrong database. In-cluster
  URLs are unchanged (`postgres:5432`). Tests default to `localhost:5433/opd_test`
  (`TEST_DATABASE_URL` to override); `ALEMBIC_DATABASE_URL` overrides for alembic by hand.
- **voice-gw on host port 8090** (8080 taken by another local project).
- **`.env` is gitignored and does not auto-update.** `make .env` only copies `.env.example` when
  the file is missing — after a session that adds keys, append them by hand. S3 added ~30. All
  providers default to `fake`, so a stale `.env` runs fine but ignores any vendor you configure.
- `terraform` is not on PATH (brew blocked by old Xcode); CI covers `terraform validate`.
- Tests require a real Postgres and build the schema via `alembic upgrade head` — SQLite would
  not have JSONB or the audit triggers.

## Env vars
See `.env.example` (authoritative). Notable: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET` (≥32
chars), `OTP_DEBUG_ECHO` (local only), and the S3 provider block — one `*_PROVIDER` selector per
interface (`fake` by default), optional `*_FALLBACK_PROVIDER` chains, vendor credentials, and
`DAILY_BUDGET_INR` (per-channel cost-guard caps; a channel with no entry is uncapped). **V-OSS
(doc 08):** `local_vllm|local_whisper|local_tts|voicebox` are valid provider selectors backed by
`LOCAL_VLLM_BASE_URL`, `LOCAL_STT_URL`, `LOCAL_TTS_URL`, `VOICEBOX_URL` (a base URL is all a local
provider needs to count as configured — no key); per-channel ladder + `max_oss_sessions` live in
`config/tiers.yaml`, not env. **Offline kiosk (S7):** `KIOSK_OFFLINE_TOKEN_BASE` (default 500 —
online tokens stay below it, offline blocks at/above) and `KIOSK_OFFLINE_BLOCK_SIZE` (default 50).
**Adaptive intake (S-ADAPT):** `INTAKE_ADAPTIVE` (backend gate; needs a real,
non-fake `LLM_PROVIDER`) + `NEXT_PUBLIC_KIOSK_ADAPTIVE` (**build-time** — the web
image must be rebuilt to change it). Both default `0` = today's pure-tap kiosk.
**Queue (S8):** `QUEUE_DEFAULT_CONSULT_MINUTES` (default 6) — the wait-estimator seed before
a department has any completed consults to measure; no other queue config (downtime is an
in-memory flag, not env).
**WhatsApp (S12):** `MESSAGING_PROVIDER=fake|meta`, `META_WHATSAPP_TOKEN`,
`META_PHONE_NUMBER_ID`, plus webhook auth `META_VERIFY_TOKEN` (GET handshake) +
`META_APP_SECRET` (POST signature — signature checking is skipped only when it is empty).
`WHATSAPP_VOICE_NOTES` (default `false`) attaches a synthesized voice note to each reply.
The 24h window + wa_id→session state is Redis/in-memory, not env.
**Appointments + campaign (S15):** `CAMPAIGN_ENABLED` (default `false` — nothing dials until an
operator turns it on), `CAMPAIGN_HOUR` (default 18, hospital-local, doc 01 §4.2's evening slot),
`SLOT_GENERATION_HORIZON_DAYS` (default 60), `EXOTEL_APPLET_URL` (the Voicebot applet Exotel runs
when a campaign call is answered), `EXOTEL_STATUS_CALLBACK_URL` + `EXOTEL_WEBHOOK_TOKEN` (the
per-call cost callback and its shared secret — `assert_production_safe` refuses to boot with the
campaign on and the token empty), `COORDINATOR_PHONE` (where a receptionist handoff transfers).
**Check-ins (S17):** `CHECKINS_ENABLED` (default `true` — unlike the campaign, nothing goes out
except on a plan a doctor approved one tap at a time; turn it off on a box being restored or
replayed), `CHECKIN_SEND_HOUR` (default 10, hospital-local), `CHECKIN_QUIET_START_HOUR` /
`CHECKIN_QUIET_END_HOUR` (21 / 8, doc 03 §9), `EXOTEL_CHECKIN_APPLET_URL` (empty — the voice rung
has no voice-gw handler yet, see Stubs). The protocol bank is `seeds/protocols.json`, not env.
Web: `NEXT_PUBLIC_PRINT_BRIDGE_URL` (a kiosk's local thermal-print daemon; absent = browser print
fallback).

Admin console (S18E → S-GL.2): served at `/admin` (staff, phone-OTP; **admin** role only —
seeded Priya Sharma `+915550000001`). **Eight** tabs, in the order an operator needs them:
channels (S-GL.1 — "can a patient reach us at all"), people & roster (S-GL.2), cost & tokens,
operations, trees (the visual editor), price book, templates & voice, protocols & slots. Needs a
live api with S-GL.2 code:
```
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  OTP_DEBUG_ECHO=true OTP_RESEND_COOLDOWN_SECONDS=0 \
  JWT_SECRET=local-dev-secret-padded-to-32-chars-plus .venv/bin/python -m uvicorn app.main:app --port 8123
cd web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8123 npx next dev -p 3210
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
  npm run e2e:admin       # the S18 AC    + screenshots -> web/screenshots/s18l/
  npm run e2e:channels    # the S-GL.1 AC + screenshots -> web/screenshots/sgl1/
  npm run e2e:people      # the S-GL.2 AC + screenshots -> web/screenshots/sgl2/
```
⚠️ All three of these really write. `e2e:admin` publishes a new version of
`general_medicine_routing`; `e2e:channels` publishes channel documents (a failed run mid-suite can
leave channels shut); `e2e:people` creates a doctor and a clinic (timestamp-suffixed, so re-runs
do not collide, and the rows stay — deactivation is not deletion). Dev boxes only; **never point
any of them at the pilot.**

Doctor console (S9) + consult note (S10): served at `/doctor` (staff, phone-OTP). Needs a
live api with S9/S10 code and a seeded demo morning. **Re-seed before every e2e run** —
signing a note is terminal, and the seed hard-deletes its own dictations to stay repeatable:
```
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  .venv/bin/python -m scripts.seed_doctor_demo      # 5 MEDONC walk-ins, one urgent
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
  npm run e2e:doctor                                # the full-morning AC + screenshots
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
  npm run e2e:dictation                             # the S10 AC + screenshots
```
Doctor login: `+915550001001` (seeded Dr. Anil Gupta, MEDONC); the OTP is echoed locally.
In the console, pick a patient and press **D** for the consult note.

CI (GitHub Actions) is **manual-only** since 2026-07-23 (operator: it was burning free
Actions minutes). `.github/workflows/ci.yml` is intact; only its `push`/`pull_request`
triggers are commented out. `gh workflow run ci.yml` to run it. **`make test` locally is
the only gate right now.**

## Invariants (don't quietly break these)
- **Anything patient-affecting subclasses `Clinical`** (`app/models/base.py`) — that alone makes
  writes audited, via a `before_flush` hook on `AuditedSession`. There is no per-route audit call.
- **`audit_log` is append-only in the database** — triggers reject UPDATE/DELETE/TRUNCATE for
  every client, including psql. Pruning requires an explicit migration that drops them.
- **Audit records that a field changed, never the PII** (`REDACTED_FIELDS` in `app/audit.py`).
- **Soft deletes only** on clinical tables; set `deleted_at`, never DELETE.
- **Money is `Numeric`/`Decimal`, never float** — costs must reconcile exactly against
  `usage_events` (S18 AC).
- **New model ⇒ import it in `app/models/__init__.py`**, or it is silently missing from migrations.
- **Every external call behind a provider interface**, each with a fake (doc 02 §9). Feature code
  must never import a vendor SDK, and never name a vendor — ask `app.providers.get_*_provider()`.
- **Metering is not optional.** `Provider._invoke` is the only way to reach a vendor; impls
  implement the private verb and report usage on the `MeterCall`. Don't add a public method that
  bypasses it.
- **Never edit a `price_book` rate in place** — add a row with a later `effective_from`. Editing
  silently re-interprets every historical cost computed at the old rate.
- **A patient id comes from the token, never from a request** (S16) — `app/routes/patient.py` has
  no `patient_id` path or body parameter anywhere, so "forgot to scope this query" is not a
  mistake that can be made there. A caregiver's token names the *patient*, not the caregiver.
- **Caregiver access is a consented grant, re-read per request** (S16) —
  `patients.caregiver_phone` is a contact number a registration desk wrote down and grants
  nothing; `caregiver_links` in state `active` is the only thing that opens a file, and
  `current_patient` re-checks it on every call so revoking is immediate.
- **The phone never invents a dose time** (S16) — `DoseScheduler` arms an alarm only where the
  server returned a clock time, which S11's `parse_schedule` sets only when the doctor's own words
  named one. A "BD" with no slots rings for nothing and says why. Same refusal as the paper copy.
- **No model decides a check-in grade** (S17, `app.checkins.grading`) — the same boundary as
  the red flags, and literally the same rule language and evaluator. `grade()` is a pure
  function of the answers and the frozen question snapshot; the LLM assist may only raise a
  green to an amber over a `free_voice` answer, may never produce a red, and may never lower
  anything. Every reason carries `source` so the nurse queue never shows a model's opinion
  dressed as a protocol.
- **The check-in personalisation writes wording, never schedule** (S17, `app.checkins.plan`) —
  `apply_personalisation` matches the model's reply rung by rung against a draft the protocol
  already fixed, and never reads a day offset or a question set back out of it. Which day a
  chemotherapy patient is asked about fever is clinical policy an oncologist signs off; a plan
  the model could reschedule would put that decision inside a vendor's weights.
- **A `Checkin` is answered against `asked`, not against the bank** (S17) — the questions are
  frozen onto the row when it is created, so re-authoring a protocol cannot change what a
  patient was asked last week, or what her "2" meant. Same argument as S11's `meds` snapshot.
- **…and graded against `grading_rules`, not against the bank either** (S18-late) — the other
  half of the same snapshot, and what makes an editable protocol bank safe. A grade is
  recomputed on every answer and every correction, so rules read live would let a bank
  published on Wednesday afternoon re-decide answers given on Tuesday, in either direction and
  with nothing on the record to say so. A snapshot the validator no longer accepts grades
  **amber, by hand** — never green, and never a red nobody can explain.
- **`app.checkins.store.resolve_bank` is how every check-in entry point gets the bank**
  (S18-late) — published row first, `seeds/protocols.json` as the floor, a row that no longer
  parses skipped rather than fatal. Calling `protocols.get_bank()` directly in new code
  reintroduces "the console edits a file the engine never reads", which is the bug S18E fixed
  for trees and this fixed for protocols. `parse()` remains the only constructor for both.
- **A check-in nobody could reach expires with no grade** (S17) — `EXPIRED`, never green.
  "We could not reach her" and "she said she is fine" are different clinical facts, and a
  system that recorded the first as the second would be worse than one that never asked.
- **Quiet hours are code, not a beat schedule** (S17, `app.checkins.window`) — `opd.checkins.send`
  fires every ten minutes round the clock and the job is a no-op inside 21:00–08:00. Moving the
  rule into the crontab would make the box's timezone the only thing between a patient and a 3am
  phone call. Deferral never consumes a delivery attempt.
- **A dosing schedule is never inferred** (S11, `app.prescription.parse_schedule`) —
  the patient copy's pictograms are read by someone who cannot read the caption under
  them, so an icon *is* the instruction. Slots are drawn only when the dictation names a
  time of day; a bare count ("BD" — conventionally morning-and-night in Indian practice)
  draws tablet glyphs and no time, and an unreadable frequency draws nothing. Encoding
  the convention would put a time of day on the page that no clinician wrote.
  `lines_of` reads the stored snapshot rather than re-parsing, so tightening the parser
  can never re-interpret a prescription already in a patient's hand.
- **A prescription is created by signing, never by a client** (S11) — `generate` is
  called inside `dictation.sign` and there is no `POST /prescriptions`. A prescription
  that exists without a signature is one nobody stands behind.
- **A flagged drug prints flagged** (S11) — `RxLine.flagged` is `not known or unsaid`,
  deliberately *not* `meds_needing_attention` (which drops acknowledged drugs). The
  acknowledgement let the doctor sign; it did not make the drug known, and the
  pharmacist reading the sheet never saw the console.
- **A dictated drug name is never rewritten** (S10) — `app.formulary` sets `known` on an
  exact match alone; fuzzy neighbours are `suggestions` shown to the doctor and never a
  value in a field, and `app.dictation.validate_meds` copies `name` through verbatim.
  "Auto-apply the closest match" would delete the S10 acceptance criterion.
- **A signed dictation does not change** — every mutating entry point in `app.dictation`
  raises `DictationLocked` once `status=signed`. Correcting one is an amendment, and this
  system has no amendment anywhere yet.
- **A published prompt version is immutable** — to change a prompt, add `v<N+1>.md`. Outputs are
  traced back to `id@vN`.
- **The tool contract is versioned** (`app/prompts/tools.py`) — changing a tool's shape means a
  new version, not an edit; a half-finished intake resuming against a redefined `save_answer` is
  silent data corruption.
- **The cost guard degrades, never denies.** It may lower a tier (V1→V2→V3); it must never block
  a call, deny an intake, or force `paper` (that is a human's downtime decision).
- **A `Tree` can only be built by `app.trees.schema.parse()`** — so a `Tree` in hand is
  validated by construction. Reading `question_trees.tree` and using the dict directly skips
  every check, including the ones for unreachable questions and unfireable red flags.
- **No model ever decides a red flag**, on any tier. Rules are data in the tree, evaluated
  deterministically (`app/trees/rules.py`). A model-decided flag would answer "is this fever
  dangerous?" differently depending on which vendor was up, and would be unreviewable.
- **Rules can't match `free_voice` answers** — the validator rejects it. That text is ASR
  output; matching it makes a flag depend on the transcriber and fires "no blood in my stool"
  as bleeding.
- **The walker's position is derived from the answers, never stored.** Do not add a cursor:
  it becomes a second source of truth that disagrees exactly when a provider is failing over,
  and it is what makes a tier downgrade lossless. `Walk.save()` prunes the answers stranded
  on an abandoned branch — anything derived from `walk.answers` must be recomputed after a
  save, not cached.
- **Red flags are recomputed, never accumulated** — an amendment that removes the alarming
  answer removes the flag.
- **A published tree version is immutable in spirit** — bump `version` in the file rather than
  editing content that has been asked, or every intake citing `key@vN` silently re-reads.
- **Trees are seeded `draft`.** Publishing is a clinical act (doc 03 §3, S21), not a seed
  script's. `--publish-trees` is the explicit opt-in for a dev box.
- **A session stores answers, never a tier cursor** (`app/intake/state.py`). Position is
  derived by the walker; a downgrade rebuilds `Walk.from_json(tree, answers)` on the new
  tier and loses nothing. Adding a cursor "for speed" reintroduces the exact bug the ladder
  avoids — two sources of truth that disagree when a provider is failing over.
- **The intake summariser never decides a red flag**, on any tier. Flags come from
  `Walk.red_flags` (the rules) and are passed in; the LLM path overwrites the model's flag
  list with the rules', the template path just lists them. Same boundary as the tool loop.
- **The engine downgrades, never denies** — a provider outage or a cost-guard breach lowers
  the tier (V1→V2→V3); a completed V3 intake needs no vendor for its summary. Nothing in the
  engine may block or fail an intake for cost or an outage (that is a human's paper decision).
- **A template edit reconciles inventory; it never blocks-and-regenerates** (S-GL.2,
  `app.roster._reconcile`) — `generate_slots` dedupes on `(doctor, instant)` **regardless of
  `blocked`**, so blocking a moved template's future slots and regenerating leaves them blocked
  forever and the clinic silently empties out. Only the instants the new shape no longer runs are
  blocked; the ones it still runs are updated in place. Both sides read
  `app.scheduling.instants_on` — which is public for this reason — because two implementations of
  "what times does this clinic run" would disagree exactly when a template was edited, which is
  the only case that matters. Anything new that edits a template goes through
  `roster.save_clinic`.
- **A booked slot is a promise, and no roster or staffing edit breaks one** (S-GL.2) — retiring a
  clinic, editing one, or deactivating a doctor blocks the **empty** future slots and leaves every
  slot with a patient in it exactly as it was, returning those patients by name. Both flows refuse
  outright without an explicit acknowledgement, so an admin sees the five people affected before
  rather than discovering them after. Same soft-delete stance as `app.scheduling`: nothing here
  ever deletes.
- **A staff phone is stored in exactly one shape** (S-GL.2, `app.people.normalise_phone`) — the
  OTP flow looks up `users.phone` by exact string match, so a number written any other way is an
  account that was created successfully, audited, and cannot sign in. Every console write goes
  through the normaliser.
- **The queue never renumbers a token** (`app.queue`) — it wraps `allocate_token`, it does
  not re-issue. A token is a promise to a patient holding a slip; priority reorders the
  *queue*, never the number. The online/offline partition (S7) stays the no-collision guarantee.
- **Queue order is derived, never stored as a rank** — `(priority_rank, position, token_no)`.
  Urgent-jump falls out of the sort (severity from the rules, not re-decided, and not a
  coordinator's manual move); a drag only rewrites `position` and can never demote an urgent
  token below a routine one. The one place a human sets priority is a **paper** entry.
- **A WebSocket route reads app state directly, never via a `Request` dependency** — a WS
  scope has no `Request`, so `Depends(get_hub)` 500s the handshake. `/queue/ws` uses
  `ws.app.state.queue_hub`.
- **The doctor console owns no queue mutation** (`app.doctor` is two reads). Call-next,
  no-show and lab-requeue are the S8 `/queue/*` verbs, called with the doctor's own token.
  Adding a `/doctor/call-next` gives the board and the console two state machines that
  disagree the moment one is patched — and two audit trails to reconcile.
- **The doctor's card never re-derives clinical judgement** — red flags are read from
  `Intake.red_flags` (the rule engine) and the summary from
  `summary_lang_versions[...]["structured"]` (the summarizer). A doctor screen that
  recomputed either would show a different clinical picture than the kiosk told the
  patient and than the queue prioritised on. The one thing `app.doctor` *does* ask the
  tree is which nodes a **already-fired** rule's `when` condition referenced, to highlight
  the answers behind it — that reads the rule, it does not evaluate it.
- **The answer interpreter proposes, it never writes** (S-ADAPT, `app/intake/
  interpret.py`) — it may only return a value the node already accepts or one
  clarifying question, and every candidate (including an enrichment pre-fill) goes
  through the unchanged `walk.save()` and the unchanged rule engine. A path that let
  interpreter output reach `Intake.answers` without that validation would put a model
  inside the clinical record, and would make a red flag depend on the transcriber —
  the same boundary `free_voice` rules and the summariser already hold.
- **Client components inject CSS via `dangerouslySetInnerHTML`, not a `<style>{text}` child**
  — the text child hydrates as a mismatch (quotes escape differently SSR vs client) and
  flickers the whole subtree to client rendering.

## Stubs & fakes
- **The AWS standby is repository-complete but not provisioned** (CLOUD1) — no
  AWS account, DNS authority, Omen access, or real provider keys were available.
  Terraform apply, ECR digests, TLS/renewal, public proxy paths, S3 backup/restore,
  cloud-voice intake, controlled failover/failback, post-cutoff exclusion, and
  measured RPO/RTO remain external release evidence. Local contract tests and the
  disposable database writer-lock proof are not substitutes for that drill.
- **The check-in protocol bank is clinically unreviewed** (S17) — six regimen families, seven
  question sets, 41 grading rules in `seeds/protocols.json`, model-drafted like the tree bank
  and pending S21. It is the first content in this system that **rings a phone at a threshold
  nobody has signed off** (fever `yes`, temp ≥38, five vomits, orthopnoea). The mr/te text
  carries the same unreviewed-language caveat as everything else.
- **No check-in has ever reached a patient** (S17) — every rung is proven against the provider
  fakes, the same first-send caveat every other channel carries.
- **The voice rung of the check-in ladder has no applet behind it** (S17) — there is no voice-gw
  check-in handler (S14 built intake, S15 the receptionist), so `EXOTEL_CHECKIN_APPLET_URL` is
  empty by default, the rung records "not configured" and the ladder falls through to SMS.
  Dialling a patient into an applet that answers with silence is worse than not dialling.
- **The SMS rung of the check-in ladder is a nudge, not a questionnaire** (S17) — structured
  answers over a DLT-templated Indian SMS gateway does not work, so it says "reply on WhatsApp
  or call us". What it buys is a human knowing to ring her.
- **The "immediate call task" for a red check-in is the nurse queue plus an SMS** (S17, doc 03
  §9 says "immediate call task") — this pilot has no task table, so the call is placed by the
  human the alert reaches, and there is nowhere to record that someone rang her (only that the
  check-in was resolved). A real task model is backlog.
- **The LLM check-in assist has never run on a real model** (S17) — `FakeLLMProvider` has no
  canned `checkin_triage` reply, so on a fake stack a free-text answer gets no assist at all.
  Same for `checkin_personalize`: a local demo shows the plain fallback message, not a
  personalised one.
- **One protocol per plan** (S17) — a carboplatin/paclitaxel doublet matches two families and
  follows the higher-precedence one (taxane); the platinum GI questions are not merged in. Every
  family that matched is recorded on `CheckinPlan.personalisation.matched_protocols`.
- **The protocol bank is a table now, but nothing is published to it** (S18-late) — `make seed`
  writes v1 as a **draft**, so `resolve_bank` still serves `seeds/protocols.json`. Publishing is
  a clinical act and the bank is still clinically unreviewed (S21); the console has the button,
  and the first press should be an oncologist's. Note the other half: `Checkin.grading_rules` is
  NULL on rows created before migration `cb011d62f829`, and those grade against the live bank —
  so a publish *would* re-grade an in-flight pre-migration check-in. There are none; if the
  pilot creates any before the first publish, backfill the column.
- **The app's chemo cycles are counted from appointments, not from a regimen** (S16) —
  `chemo_calendar` numbers a patient's own `chemo_review` appointments and shows the generic
  what-to-expect text from `seeds/regimen_notes.json`. The dates are real; the advice is
  generic-but-true. **S17 did not change this**: the protocol bank now knows a patient's regimen
  family and cycle length, and `CheckinPlan.next_cycle_at` knows when the next one is due, so
  the app could read a real cycle count — but rewiring `chemo_calendar` was outside S17's scope
  and is backlog.
- **"What-to-expect audio clips" are device TTS over text** (S16, doc 03 §1c.5) — a tenth of the
  bytes, works offline, and the same strings the language QA harness checks. Recorded clips would
  need a voice artist per language.
- **`scripts/seed_app_demo.py` writes a prescription snapshot by hand** (S16) — the frozen shape
  `app.prescription` writes at signing, not the output of a real dictation. Demo furniture for a
  screen walk; the dictation path has its own fixtures (S10/S11).
- **The app stores its session in plain DataStore** (S16) — app-private storage, tokens already
  scoped to one file, and EncryptedSharedPreferences costs ~600KB plus a class of
  keystore-invalidation bugs that would lock a patient out of her prescriptions in a waiting room.
- **The app has never run on a real handset** (S16) — six instrumented tests and a full screen walk
  on an emulator (API 35). Doze behaviour overnight on a low-end Android 8 phone is the untested
  risk. Owed on omen.
- **The APK is unsigned** (S16) — `assembleRelease` produces the unsigned artifact the size gate
  measures. Play Store signing, privacy policy and data-safety declaration do not exist yet.
- **The tree editor edits wording and severity, not tree structure** (S18-late) — deliberate,
  and stated on the screen. Adding, deleting or rewiring a node happens in `seeds/trees/*.json`
  and a pull request; a drag-to-rewire builder that silently orphans a question would be a worse
  tool than the diff, because the validator's reachability and cycle checks *are* the review.
  The severity select offers exactly `urgent` and `semi` (`Priority`, minus `routine`, which the
  tree validator refuses) — offering a value the schema lacks silently rewrites a flag's
  severity on the next save.
- **The protocol editor edits the document, not fields** (S18-late) — the panel above it is the
  structured reading view; saving posts the whole bank and shows the validator's own refusal
  verbatim. A per-rung form is possible now that the table exists (backlog).
- **Admin voice-pack manager + template registry are read-only** (S18E) — the template registry
  is code-defined (`app/whatsapp/templates.py`); the voice-pack panel is a coverage checklist
  (every clip `recorded: false` → TTS) because the pack storage format is S7's. Upload/re-record
  and a DB-backed editable template registry are S18-late/S7/S15.
- **No admin route is a deferral marker any more** (S-GL.2) — `GET /admin/slot-templates` was the
  last one and now returns the live clinic grid. `GET /admin/protocol-templates` stopped being a
  marker in **S17** and became editable in **S18-late**. `seeds/{doctors,slot_templates}.json` +
  `make seed` are still the floor for a fresh box; the console is now the way to change either
  afterwards.
- **The staff invite SMS is free text, not a DLT template** (S-GL.2) — staff-facing rather than
  patient-facing, and `send_invite` records a vendor refusal rather than raising, so a rejected
  invite is visible and not fatal. On a real Indian gateway it may still be rejected in a way
  `FakeSMSProvider` never shows; **no staff invite has ever been sent for real**.
- **The roster import resolves doctors, it does not create them** (S-GL.2) — a row naming somebody
  unknown is refused by line number rather than quietly onboarding them. Creating a clinical
  identity from a spreadsheet row is not a thing to do without a person looking; a two-stage
  "these six names are new, create them?" flow is backlog.
- **A reactivated doctor's clinics do not come back with her** (S-GL.2) — deactivation retires the
  templates and blocks their empty future slots, and `activate` restores only the login. "She is
  back" and "she is back on Tuesdays at ten" are different facts; the console says so, but it means
  her clinics must be re-authored or re-imported.
- **Leave is a clinic-level act, not a slot-level one** (S-GL.2) — there is no way to block a
  single Tuesday from the console without retiring the whole weekly clinic. `AppointmentSlot.
  blocked` exists for exactly this (S15) and nothing exposes it per slot. Backlog.
- **The campaign has never dialled a real number** (S15) — `CAMPAIGN_ENABLED=false` everywhere,
  and the whole ladder is proven against `FakeTelephonyProvider`. Turning it on needs a live
  Exotel number, `EXOTEL_APPLET_URL`, `EXOTEL_STATUS_CALLBACK_URL` and `EXOTEL_WEBHOOK_TOKEN`
  (the last is enforced by `assert_production_safe` when the flag is on).
- **Exotel `transfer_call` is a second `connect`, and the whisper applet is not code** (S15) —
  Exotel has no "transfer this live leg" verb, so the handoff dials the coordinator and bridges;
  the applet that reads the whisper line to them is configured in the Exotel console. Unproven
  against the vendor.
- **No appointment waitlist** (S15) — doc 03 §2's "cancellations release slots and notify
  waitlist" is half-built: the seat is released, nobody is notified.
- **Receptionist mr/te copy is romanized placeholders** (S15) — same carry as the S14 consent
  line; native + clinical review is S21.
- **Admin what-if is two panels** (S18E + S18-late) — the edited-price-book recompute
  (re-scales stored per-row cost by a provider/model factor) and the tier-mix recompute
  (`intakes × (to_median − from_median)` over medians actually booked on that channel). Both are
  hand-checkable by construction. Tier-mix **refuses** when the target tier has never run on
  that channel: unknown renders as unchanged, never as zero saving.
- **Cost-guard `clear` from the admin console needs the running guard process** (S18E) — the
  Redis override store; 503s under the test transport / a process with no guard. Works in prod
  and `make dev`.
- **The admin console has not been seen rendered on a screen** (S18E) — typecheck + lint + 48
  conformance only; a visual pass on the box is in HANDOFF "Owed on omen".
- **No live Meta number has ever answered** (S12) — the webhook + bot are proven
  against the `FakeMessagingProvider` and a simulated Meta payload, exactly like every
  other channel's first-send caveat. The first real inbound/outbound and **template
  approval in the WhatsApp Manager** need a human on a real number. The repo template
  registry (`app/whatsapp/templates.py`) only guarantees we never send a shape or
  arg-count Meta has not seen from us — it is not proof Meta approved it.
- **WhatsApp templates are en + hi only** (S12) — `get_template` **raises** for a
  missing language rather than falling back to English (an unreadable out-of-window
  message is worse than none). mr + te bodies are S13.
- **Multi-select over WhatsApp picks one option** (S12) — a `multi`/`body_map` node
  wraps a single tap into a one-element list (`bot._parse_answer`); a Meta list reply is
  single-select too. True multi-pick is a UX decision for later.
- **A WhatsApp voice note answers only the chief complaint** (S12) — a voice note on a
  *tree* question falls back to the buttons; a spoken tree answer needs the adaptive
  interpreter (doc 11), which is flag-gated and off by default. Pairs with S-ADAPT.
- **WhatsApp billing is still per-message, not per-conversation** (S12) — the messaging
  provider meters `messages=1` per send (over-counts vs Meta's 24h-conversation billing).
  The window state now exists to fix it, but conversation attribution is deferred to
  S18's invoice reconcile (`app/providers/messaging.py` docstring).
- **The WhatsApp conversation store is Redis/in-memory, single-process** (S12) — same
  multi-replica caveat as `SessionState` and the queue hub; a second api replica would
  each hold their own threads. Fix is the shared Redis both already point at.
- **No live vendor has ever delivered a prescription** (S11/S12) — WhatsApp/SMS run on
  the provider fakes like every other channel. S12 made the WhatsApp send **window-aware**
  (out-of-window → the registered `prescription_ready` template), but no template has
  been approved at Meta. `delivered_via` records what was attempted (and which path — a
  `template:` detail means only the reply-nudge went, not the full sheet), which is real;
  the send is not.
- **The pictogram copy has never been read by a low-literacy patient** (S11) — the three
  glyphs were chosen to survive a laser printer without a webfont, and the sheet was
  self-critiqued against doc 04 §5, not tested with anyone. The low-literacy checklist in
  doc 06's S11 AC needs the S21 clinical/design review before it is really met.
- **Prescriptions print doses and durations in the doctor's own words** (S11) — a Hindi
  patient copy can carry "5 days" and "8 mg" in English, because those strings come from
  the dictation and translating them would be inventing. Per-value localisation is S13.
- **A prescription cannot be amended** (S11) — it hangs off a signature and signing is
  terminal, so the S10 amendment gap now has a printed artifact attached to it (S18/S19).
- **`Prescription.pdf_url` is unused** (S11) — nothing is archived to object storage; a
  reprint re-renders from the stored `meds` snapshot, which is why the snapshot exists
  rather than a view over the dictation.
- **Adaptive intake has never run with its flags on** (S-ADAPT, 2026-07-23). It is
  merged to `main` and deployed to omen, but only ever exercised there with
  `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` — that pass proved the *absence*
  of an effect (`adaptive_events = []`, zero `INTAKE_TURN` usage_events), which is the
  safety property, not the feature. **No real patient utterance has ever reached the
  interpreter, and no clarify/mis-map/enrichment rate quoted anywhere comes from the
  clinic** — the numbers in the tests come from `FakeInterpreter`. Turning the flags on
  is the outstanding on-box work (HANDOFF "Owed on omen"); until then treat every
  adaptive quality claim as unmeasured. The *safety* claims (nothing bypasses
  `walk.save`, no model decides a red flag) are structural and unit-tested.
- **The doctor console + consult note have not been exercised on omen** — S9/S10 are
  deployed there and green in tests, but the on-box pass (real Qwen3 mapping a real
  dictation, `_was_said` firing on a real mis-hearing) was never run. Same session as
  the adaptive validation.
- **Kiosk token issuance is `max(token_no)+1` and stays that way** —
  `app.kiosk.allocate_token` allocates below the offline base (500), guarded by the
  unique constraint. S8 did **not** replace it: the queue (`app.queue`) *wraps* it
  with a `QueueEntry` (priority/urgent insertion, wait estimate, reconciliation)
  rather than re-issuing a number, because a token is a promise to a patient holding
  a slip. The offline blocks (`app.offline`, S7) still partition the number line so
  online and offline never collide. A gap-free / reserved-number scheme is not needed.
- **`seed_doctor_demo`'s structured summaries are authored fixtures**, standing in for what
  the LLM path (doc 03 §4) writes on a box with a real model — the deterministic V3
  `TemplateSummarizer` emits only "question: answer" lines and no symptom table, which
  would under-sell a screen whose whole job is a 20-second read. The **answers and red
  flags in the same seed are genuinely derived** (a real `Walk`, real `walk.red_flags()`).
  Like `seed_queue_demo` it hard-deletes its own rows to stay repeatable.
- **`FakeLLMProvider` answers `dictation_map` with a canned, contract-shaped payload**
  (S10, `_CANNED_JSON` in `app/providers/llm.py`) — a fake that says "ok" to a
  `response_format: json` prompt can only ever demonstrate the failure path, and the
  fakes exist so whole flows can be demoed without a vendor. It is a **demo** fixture:
  tests asserting on mapped content queue their own `FakeLLMScript`, which always wins.
  The canned reply carries one off-formulary drug on purpose.
- **`_was_said` is token presence, not alignment** (S10) — a drug the doctor said in a
  *different* sentence than the one the model quoted still passes. Tighter matching needs
  word timings from the STT, which we do not store.
- ~~**Signing a dictation emits nothing** (S10)~~ — resolved: signing generates the
  prescription (S11) and now drafts the check-in plan (S17). Neither can fail the signature.
- **The formulary is a seed file read at boot**, not a table — a hospital adding a drug
  needs a deploy until S18's admin console owns it.
- **The doctor's day list has no appointments** — doc 03 §5 says "appointments+walk-ins".
  S15 gives `Appointment` real slots and a booking flow, but **no arrival/check-in step** turns
  one into a queue entry, so the worklist is still the queue only. Not faked; backlog.
- **The doctor console has no WebSocket** — it refetches after its own mutations, so a
  coordinator moving the same line elsewhere is not pushed to the doctor until they next
  act. The `/queue/ws` hub already exists to subscribe to (S18 polish).
- **The doctor's staff token is localStorage**, and deliberately the *same* key as the
  coordinator console so one staff session covers a shift — same S19/S20 httpOnly
  hardening note.
- **Symptom sparklines now have a real writer** (S17) — `Checkin.responses` is written by
  `app.checkins.grading`, so `app.doctor._trends` draws from real answers once a patient has
  answered two check-ins. It still picks out numeric values defensively and needs ≥2 points.
- **The queue + downtime flag are in-memory, single-process** (`app.queue_hub`) —
  correct for the one pilot api container; a second replica would each hold their own
  WS clients and downtime flag and miss each other's. Fix is a Redis pub/sub channel
  (S19/S20), same shape as the cost-guard override store and the OSS AdmissionController.
- **`/queue/ws` is covered by the live `queue` e2e, not a unit test** — the
  ASGITransport test client can't easily drive a WebSocket; the `QueueHub` logic
  itself is unit-tested. The route reads the hub off `ws.app.state` (a WS scope has
  no `Request`, so `Depends` on a Request-typed provider 500s the handshake).
- **The coordinator staff token is localStorage, not an httpOnly cookie** — fine for
  a pilot on a trusted LAN behind Caddy; a cookie hardening pass is S19/S20. The
  minimal phone-OTP login was lifted into S9's doctor console, which shares the key.
- **Board/console reason chips + department names render in English** — the stored
  priority reason is the English clinical label; dept-name/chip localisation is S13.
- **Downtime paper sheets are browser-printed HTML, not server PDFs** —
  `app.print_sheets` returns print-optimised HTML (A4, tick-boxes, Devanagari) that
  the browser turns into a PDF, the same fallback stance as the S7 ESC/POS bridge. A
  server-side PDF with embedded Indic fonts is a deploy dependency decision (S19/S21).
- **Offline audio is the browser's Web Speech** — the voice-pack manifest + placeholder
  TTS packs were deferred (S7 backlog). No recorded packs exist (S21); the `VoicePack`
  seam (`app.intake.voicepack`) is unchanged from S5.
- **The offline TS walker/rules are a second implementation of clinical logic** — trusted
  only because `make check-tree-fixtures` + `web/e2e/conformance.spec.ts` gate them against
  the Python original (mutation-tested). Change `app/trees/` ⇒ `make tree-fixtures`.
- **Kiosk session state is in-memory locally** (`is_local` → `InMemorySessionStore`),
  so the multi-request flow only survives within one api process. Prod is Redis. A
  second uvicorn worker locally would not share sessions. (Offline sessions live in the
  browser tab, not the server, and do not survive a tab reload mid-intake by design.)
- **Server-STT is built (`POST /kiosk/stt`)** — the kiosk records the chief complaint
  (MediaRecorder) and posts the clip to `/kiosk/stt`, which runs it through `stt_chain`
  (local Whisper on a V-OSS box → audio stays on-premises). Gated by the build-time flag
  `NEXT_PUBLIC_KIOSK_SERVER_STT=1`; **off by default**, when the kiosk uses the browser's
  Web Speech (Chrome ships that audio to a cloud recogniser). Tap-to-type is always behind
  both. The endpoint is proven with the fake STT (returns "haan"); a real transcript needs
  a live Whisper on the box. `python-multipart` was added for the upload.
- **No printer has printed a slip** — `_lib/print.ts` ESC/POS bytes are built against the
  documented 58mm command set and unit-tested; the first real slip needs a human at a
  printer. Devanagari needs the printer codepage set on the box (prints `?` until then).
- **No true "back" inside a kiosk walk** — the walk has no rewind endpoint; the
  read-back "change something" restarts the intake. S9 did not add one either (the
  doctor console is read-only over the answers), so a per-node amend — and the
  summary regeneration doc 03 §4 wants after a coordinator edit — is S18.
- **The kiosk icon set is a branded subset + aliases + a neutral fallback**, not the
  full ~65-key custom duotone set doc 04 law 4 wants; the full set + human review is
  a design-asset task (S7/S21). No option is ever iconless.
- **A pure-V3 kiosk intake finalises at ~₹0** — no metered calls happen in the walk,
  and Q1's routing-classifier cost is not attributed to the intake (routing runs
  before the intake_id exists). A `usage_scope(intake_id=...)` around the classifier
  is backlog.
- **Kiosk department names render in English** on the hi flow (seeded English names);
  dept-name localisation is S13.
- **No live vendor has ever accepted a call.** Every real impl (MSG91, Exotel SMS/telephony,
  Gemini, OpenAI, Sarvam, Google, Meta) is written against documented HTTP APIs and tested
  through `httpx.MockTransport` — real request-building and response-parsing, mocked wire.
  Endpoints, DLT template ids, sender ids and auth are per-account. **The first live send of each
  needs a human watching a real handset/number.**
- **Realtime (Gemini Live / tier V1): session manager built (S5), the Exotel↔engine bridge
  built (S14), the real Gemini Live vendor impl still fake only.** `IntakeEngine._run_v1` drives
  the `RealtimeVoiceProvider` interface and `voice-gw` (S14) now bridges the Exotel Voicebot
  websocket to it end-to-end (proven against the fake, both V1 and V2). What is still fake is the
  **vendor**: `REALTIME_PROVIDER=gemini-live` raises rather than pretending, and V1 continuous
  caller-audio streaming into a live session (the fake scripts turns from the opening kick, so
  `_pump_v1` consumes only the opening) waits on that impl.
- **V2 is a turn pipeline, not token streaming, and does not feed tool results back to the
  LLM within a turn** — the request/response `LLMProvider` has no tool-result message type, so
  the engine mediates `get_next_node` by injecting the current question into the prompt. Fine
  for kiosk/WhatsApp; S14's real-time telephony wants true streaming + a tool-result turn.
- **The intake engine drives three channels now** — kiosk (S6, HTTP), WhatsApp (S12, webhook)
  and **telephony (S14, the Exotel WS in `voice-gw`)**, all over the one service class. S14 added
  a **streaming turn-source**: `IntakeEngine.run(turn_source=…)` for a live call whose turns are
  not known up front; the fixed-`turns` path (kiosk, tests) is unchanged.
- **Telephony vendor + call-state are partly fake (S14).** The Exotel Voicebot WS protocol,
  the call driver, barge-in, DTMF, consent, partial-save and per-intake cost are built and proven
  against a fake replay client; the **real Exotel vendor WS + the status-callback webhook**
  (`TelephonyProvider.record_call_completed`) are S15, and the `PhoneCallRecord` store is
  in-memory until then. **Barge-in and the DTMF trigger key off channel-side audio energy**
  (`SILENCE_PEAK` / `UNCLEAR_PEAK`), a tunable stand-in for real VAD / STT confidence.
- **No node has real V3 audio** — `app/intake/voicepack.resolve` falls back to TTS for every
  prompt; the pack format is S7, recordings S21 (already noted below for the tree nodes).
- **`Intake.answers[*].text_en` is not filled during intake** — the per-answer English gloss
  for the doctor screen is left to the summariser; a translation pass per answer is future.
- `price_book` rates are **estimates**: public list prices at ~₹84/USD, rounded up. Admin-editable
  in S18; every unit-economics number depends on them.
- WhatsApp meters per message; **Meta bills per 24h conversation** — over-counts until S12.
- Cached tokens are priced at the full `token_in` rate (vendors discount ~25%) — over-estimates.
- **Nothing schedules `CostGuard.evaluate()`** — on-demand only; needs a beat job. S17 added
  beat jobs but only for check-ins; this one is still unscheduled (S18/S19).
- Sarvam STT reports no confidence (`confidence=None`); doc 03 §4's `[unclear: ...]` contract
  leans on Google's until that is solved.
- **The classifier's ≥85% AC (S4) is unmeasured.** The 60-utterance eval set, the harness and
  the 85% gate exist (`make eval-routing`), but the only provider available is the fake, and
  scoring it measures the harness. Needs one live run with a `GEMINI_API_KEY`. The tests
  deliberately do not fake the number.
- **The 11 trees are unreviewed clinical content**, seeded `draft`, pending S21. The hi/mr/te
  in them — and the eval set's utterances — were authored by a model, not a native speaker, and
  not collected from real patients. Tests and the `app.lang_qa` harness check the text is
  present, in the right script, and structurally sound; they cannot check it is good Marathi,
  good Telugu, or good medicine. **mr/te (S13) especially need a native + clinical read before
  a patient sees them** — same open review as the WhatsApp template bodies and the read-back.
- **No tree node has `audio`** — the field is authored-empty. V3 kiosk voice packs are S7,
  real human recordings S21; TTS covers the gap until then.
- **V-OSS is the software half only (S-OSS.0).** The local provider adapters are real HTTP
  clients tested through `httpx.MockTransport` (OpenAI-compatible vLLM/Whisper shapes, local
  TTS `/tts`, Voicebox `/api/tts`) — **no live GPU server has ever answered**; first real bring-up
  is S-OSS.1 on the box. The `local-*` `price_book` rates are **amortized placeholders** (GPU
  capital + power spread over volume, set to a tiny per-unit number), not measured — non-zero on
  purpose so the S18 dashboard shows a true V-OSS cost, admin-editable in S18.
- **`LocalPipelineVoiceProvider` (V-OSS realtime) does not exist yet** — `REALTIME_PROVIDER=local-pipecat`
  raises (needs GPU + Pipecat, S-OSS.2), the same honesty `gemini-live` keeps. V-OSS voice runs as
  the V2 pipeline backed by local providers until then.
- **`config/tiers.yaml` `ladder_for()` is not wired into the engine/voice-gw yet** — the loader,
  validation and `AdmissionController` are built and tested, but *routing* a channel down its ladder
  (and gating the live local session on admission) is S-OSS.2, when there is a live local realtime
  session to route. Today the ladder is expressed operationally via provider fallback chains
  (local primary + cloud `*_FALLBACK_PROVIDER`) plus the existing V2→V3 tier downgrade.
- **`AdmissionController` count is per-process, in-memory** — correct for the single-voice-gw pilot;
  a second voice-gw replica needs a Redis counter (noted for S-OSS.2), same shape as the cost-guard
  override store.
- **Red-flag satisfiability is only checked for `and`-rooted rules, and only in tests** —
  `or` across branches is legitimate and `unanswered` is satisfied by an off-path node, so
  a general check needs real satisfiability, not reachability (S18's editor will want it).
- **Red flags and their instruction text are per-tree** (a tree is the unit of publish and
  sign-off), so the shared ones are duplicated across the med-onc trees and can drift.
- No Surgical Oncology "new lump/lesion" tree — doc 03 §3 lists it, doc 06's S4 line did not.
  A new-lump walk-in currently gets `surg_onc_post_op`, which asks about an operation they
  have not had.
- `prompts/` text is English-only prompt *instructions* (vendor-neutral, not patient-facing);
  all patient-facing strings are now four-language (S13). The Telugu kiosk has **not been seen
  rendered on a real screen** — Noto Sans Telugu is wired and typechecks, but the visual proof
  (Telugu glyphs + ≥1.6 line-height at 200% scale, doc 04 §4) is owed on the box, like the other
  on-omen UI validation. No mr/te STT/TTS has hit a live vendor (fakes only, same as every channel).
- Enum columns have **no CHECK constraint** despite the docstring claiming so (`native_enum=False`
  + SQLAlchemy 2.0's `create_constraint=False`).
- Staff username+TOTP login is modelled on `users` but not implemented; phone-OTP is the only path.
- No IP rate limiting on OTP verify (per-challenge attempt cap only) — S20.
- `otp_codes` rows are never pruned — still unscheduled after S17 (S19/S20).
- Migrations applied by hand (`make migrate`); no auto-migrate on container start.
- worker/beat: placeholder `opd.ping` Celery task only.
- The production root is now an enterprise role gateway and the signed prescription
  is a prominent document surface. Staff tokens and login are shared; doctor,
  coordinator, and admin still retain established large scoped CSS strings pending
  S-UX.5 extraction. `/mocks` remains design reference only.
- Loki/Grafana/uptime-kuma: default config, unprovisioned.
