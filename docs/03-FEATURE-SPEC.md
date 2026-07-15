# 03 — Feature Spec (module by module)

Every module lists: capabilities, key behaviors, edge cases, acceptance criteria (AC). Sessions in doc 06 reference these by section number.

---

## §1 Patient Intake — shared engine

- One `IntakeEngine` with a three-tier fallback ladder (doc 02 §5): **V1 Gemini Live** native speech-to-speech (conversational, function-calls into the tree engine) → **V2 STT→LLM→TTS pipeline** (Sarvam + Gemini Flash / gpt-4o-mini — the cost-optimal conversational tier) → **V3 rule-based + pre-recorded voices / Web Speech** (zero-AI, offline-capable on kiosk). Downgrade triggers: provider failure, latency breach, or daily cost-guard cap.
- Languages: en, hi, mr, te. Language chosen once, switchable any time ("भाषा बदलें" persistent control).
- Every intake ends with an **AI/voice read-back of the summary in the patient's language** and an explicit confirm ("Yes that's right / I want to change something"). Unconfirmed intakes are flagged on the doctor screen.
- Red-flag rules (config, not code) evaluated on every answer. Oncology starters: fever ≥38°C within 14 days of chemo; active bleeding; severe vomiting >24h; chest pain/breathlessness; new confusion. Red flag → visit priority=urgent, nurse alert.
- Caregiver mode: one toggle "I am answering for the patient" — recorded on the intake.

**AC:** same `answers JSONB` shape produced by all tiers and all channels; tier downgrade is automatic on provider failure mid-session without losing collected answers.

## §1a Kiosk / Tab intake

- Landscape 10–11" tab, guided by a friendly on-screen assistant persona ("Dhara").
- **Q1 = chief complaint by voice**: big mic button, patient speaks freely; live waveform; STT shown as simple words + read back ("Aapne kaha ki…"). Then NLP (Gemini Flash) extracts department + seeds the tree. Tap-to-type fallback and "call staff" button always visible.
- Subsequent questions: **tap options with friendly icons, read aloud by default** (auto-play question audio; replay button). Max 3–5 options/screen; body-map picker for pain location; big numeric stepper for duration; visual severity scale (faces).
- V3 tier: identical UX but all prompts are **pre-recorded human voice files** per language, tree walked deterministically; works with server-only (no AI APIs) and, for cached trees, fully offline.
- Summary screen: icon-chip summary + full audio read-back + confirm.
- Ends with big token number + printed slip (ESC/POS thermal printer via kiosk print bridge) + WhatsApp send option.
- Idle reset 90s with "are you still there?" audio at 60s. Sanitization-friendly: gloves-friendly hit areas ≥64px.

## §1b Telephony intake (Exotel)

- Outbound D-1 campaign + inbound "press 1 to tell us your symptoms before you come."
- Exotel Voicebot Applet: bidirectional audio WS → `voice-gw` → **V1: bridged to Gemini Live session** (audio passthrough + function-call loop) or **V2: STT stream → IntakeEngine → TTS stream** per tier config/cost-guard. Per-call tier + cost recorded on the intake.
- Barge-in supported (stop TTS when caller speaks). DTMF fallback for yes/no if STT confidence <0.5 twice ("press 1 for yes").
- Call context: patient looked up by CLI; if known, conversation is longitudinal ("Since your second cycle on 3rd July, how has the vomiting been?").
- Max 8 min; graceful exit saves partial intake; retry policy 2 attempts then WhatsApp fallback message.
- Consent line at call start, recorded.

## §1c Android app (Phase 1) / iOS (Phase 2)

Why a patient installs it (the persuasion set — build these, they are the point):
1. **"My Cancer Care File"** — every prescription, summary, and report photo in one place, works offline, shareable to any doctor as PDF. (Rural patients carry plastic bags of papers; this is the killer feature.)
2. **Talk-to-Dhara intake from home** — do tomorrow's intake tonight by voice; skip the kiosk queue, get token faster on arrival.
3. **Live queue position** — "You are 7th; leave home by 10:30" with travel-time hint. Saves hours of waiting-room time for people traveling far.
4. **Medicine reminders with voice + photos of the actual strip**; caregiver gets missed-dose ping.
5. **Chemo-cycle calendar** in plain language + what-to-expect audio clips per regimen ("after tomorrow's cycle, mouth ulcers are common — here's what to do").
6. **Family access**: caregiver phone linked, sees everything with patient consent.
7. Low-end friendly: <15MB, offline-first, SMS-based OTP login, works on Android 8+.

iOS Phase 2: same API; SwiftUI; add HealthKit weight/temperature logging.

## §1d WhatsApp bot

- Meta Cloud API. Entry points: QR poster in OPD, reminder messages, missed-call-back link.
- Accepts **voice notes** (→ STT → engine) and interactive buttons/lists; replies with text + optional voice note (TTS) — this pragmatically delivers "WhatsApp calling" value on day 1; native WA calling API can be added when GA.
- Flows: intake, book/reschedule/cancel appointment, "my token status", check-in responses, prescription re-send.
- Session windows handled correctly (template messages outside 24h window; templates pre-approved list in repo).

## §2 Appointment management (voice inbound + messaging)

- Inbound Exotel number → AI receptionist (same voice stack). Intents: book, reschedule, cancel, status, human.
- Slot inventory per doctor/dept (admin-configured), capacity-aware; oncology slot types: new consult / follow-up / chemo review.
- Confirmations: WhatsApp interactive message + SMS fallback (MSG91). One-tap confirm/cancel in WhatsApp; cancellations release slots and notify waitlist.
- Human handoff: transfer to coordinator queue with whisper summary ("Kamla Devi, wants to move Thursday review").

**AC:** end-to-end call books a real slot in <3 min; every booking generates WhatsApp+SMS; double-booking impossible (DB constraint).

## §3 Question-tree bank

Trees are JSONB data with this node schema:

```json
{"id":"onc.pain.location","type":"single|multi|scale|number|body_map|free_voice",
 "text":{"en":"Where is the pain?","hi":"दर्द कहाँ है?","mr":"...","te":"..."},
 "audio":{"hi":"onc_pain_loc_hi.mp3", "...": "..."},
 "options":[{"id":"abdomen","icon":"belly","text":{...},"flag":false}],
 "next":{"default":"onc.pain.duration","abdomen":"onc.pain.abdo.detail"},
 "red_flag_if":{"op":"and","rules":[...]}, "adaptive_hints":"probe radiation to back"}
```

Pilot bank (author in seed data; clinically reviewed before go-live):
- **Medical Oncology** (primary): new-patient onco intake; between-cycle symptom review (CTCAE-lite: nausea, vomiting, mucositis, neuropathy, fever, fatigue, appetite, bowel); pain assessment.
- **Radiation Oncology**: on-treatment review (skin, swallowing, urinary per site).
- **Surgical Oncology**: post-op review; new lump/lesion intake.
- **Palliative care**: symptom burden (ESAS-lite).
- General support trees: **General Medicine, Gynecology (gynae-onc), ENT (head & neck), Pulmonology, Dermatology** — thinner trees for routing walk-ins.
- Admin **tree builder UI**: visual editor, per-language text+audio upload, versioning, draft→publish, test-run mode.

## §4 Summarization for doctor screen

- Output contract (strict): Chief concern · HPI (3–5 lines) · Symptoms table w/ duration+severity · Red flags (top, highlighted) · Relevant history & current meds · Since-last-visit delta (returning patients) · "Patient's own words" one quote.
- Generated in English for doctor; patient-language version for read-back. ≤150 words body. Confidence notes where STT was unsure ("[unclear: medicine name]") — never silently guess.
- Regenerates if coordinator edits answers. Immutable once consult starts (audit).

## §5 Doctor login & console

- Phone OTP login; day list of appointments+walk-ins; patient card = summary (§4) + intake answers accordion + past visits timeline + check-in trendline (symptom sparkline across cycles).
- One-tap actions: call next token, mark no-show, send to lab & re-queue, start dictation.

## §6 Queue management & board

- Public board (TV via cheap Android stick / any browser): current tokens per room, next 3, est. wait; gentle chime + audio announcement of token in 2 languages; high-contrast, viewable at 8m (spec in doc 04).
- Coordinator console: drag to reprioritize, urgent-flag insertion (red-flag intakes auto-jump with visual reason), room reassignment, downtime mode entry/exit, downtime reconciliation screen.
- Patient-facing status: WhatsApp "token status" query + app live position + SMS at "3 away".
- Full Downtime Protocol behaviors per doc 01 §5. **AC:** kill the API container for 10 min during demo; kiosk still issues tokens, board still advances, everything reconciles on restart with zero duplicate tokens.

## §7 Doctor dictation → structured fields

- Web Speech API for capture in doctor console (Chrome), with raw-audio record fallback → server STT for accuracy pass.
- LLM maps transcript → structured: diagnosis/impression, treatment_events (chemo cycle #, regimen, date, next-due), meds[] (name, dose, route, freq, duration — validated against a formulary list w/ fuzzy match; unknowns flagged, never auto-corrected), advice, follow_up.
- Doctor reviews mapped fields (diff-style, tap to fix), **signs**; signing generates prescription (§8) and check-in plan draft (§9).
- Hinglish/mixed-language dictation explicitly supported in prompts.

## §8 Digital prescription

- PDF (hospital letterhead template) — printed at desk + WhatsApp to patient/caregiver; large-type patient copy option with icons (morning/afternoon/night pictograms) for low literacy.
- Option B (photo of handwritten Rx → extraction) is Phase 2; schema supports it.

## §9 Post-treatment check-ins / chronic care continuation

- On dictation sign: `treatment_events` + protocol templates (per regimen family, admin-editable) → LLM personalizes a check-in plan (days, channels, question sets) referencing the doctor's actual notes; doctor approves in one tap (edit optional).
- Delivery ladder per patient preference/reachability: WhatsApp → AI voice call → SMS. Celery beat scheduler; quiet hours 21:00–08:00.
- Responses graded green/amber/red (deterministic rules first, LLM assist for free text); amber → nurse review queue; red → immediate call task + doctor notification; all visible on patient timeline.
- Also handles **next-cycle reminders** (D-2 and D-0 morning) with confirm/reschedule buttons.

## §10 Admin

- Departments, doctors, rooms, slot templates; question-tree builder; protocol templates; red-flag rule editor; message template registry; voice-pack manager (upload/re-record prompts); price book editor; cost-guard config; downtime drill trigger.

## §11 Cost & Usage Analytics dashboard

Audience: founder/ops + hospital admin. Two tabs.

**Tab 1 — Cost & tokens**
- Live strip: tokens/min and ₹/min right now (per provider·model), active voice sessions by tier, today's spend vs budget bar with cost-guard status.
- Time series (1-min granularity, zoomable to day/week): tokens in/out, cached tokens, audio-minutes, computed ₹ — filterable by channel (kiosk/phone/whatsapp/app), tier (V1/V2/V3), purpose (intake/summary/dictation/check-in/routing), model.
- **Unit economics cards**: ₹ per completed intake (median + p90, split by channel & tier), ₹ per abandoned intake, ₹ per check-in, ₹ per dictation→Rx, ₹ per booked appointment, ₹ per patient-day. Trend arrows week-over-week.
- Breakdown table: provider → model → purpose with tokens, audio-sec, calls, ₹, % of spend; export CSV.
- What-if panel: recompute yesterday's cost under a different tier mix or edited price book ("if phone intake ran V2 instead of V1: −₹X/day").
- Anomaly flags: cost/intake > 2× 7-day median, runaway session (>threshold tokens), provider latency degradation.

**Tab 2 — Intake & operations metrics** (doc 01 §7 made live)
- Intake funnel per channel: started → completed → confirmed-by-patient → summary-opened-by-doctor; abandonment points by question node (find the tree nodes where people quit).
- Median intake duration per channel/tier/language; STT low-confidence rate per language; tier-downgrade events count.
- Queue: wait-time estimate error, tokens/hour, urgent insertions; Doctor: summary open rate, dictation usage, Rx generated; Check-ins: response rate by day-offset & channel, amber/red escalations & resolution time.

**AC:** every number traceable to `usage_events`/domain tables (no hand-maintained figures); dashboard loads <2s on a week range; cost per intake accurate to ±2% against provider invoices in a monthly reconciliation view.
