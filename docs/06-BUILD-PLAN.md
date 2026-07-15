# 06 — Build Plan: 22 sessions

Each session = one focused Claude Opus working session (~2–4h human time), sized to fit comfortably in one context window with room for iteration. Every session ends with green tests, commits, `sessions/SESSION-NN.md`, and an updated `HANDOFF.md` (see doc 07).

**Format:** Goal → Context to load → Build → Acceptance criteria (AC). "Spec §" = doc 03 sections.

---

### Phase A — Foundation (S1–S4)

**S1 — Repo, infra skeleton, CI**
- Load: docs 02 §2–3, 05.
- Build: monorepo layout (`backend/ web/ android/ infra/ seeds/`); FastAPI app factory + health route; Next.js app with route groups (kiosk/board/doctor/coordinator/admin); docker-compose (all services incl. postgres/redis/caddy); Terraform per doc 05 §4 (plan-only, not applied); GitHub Actions (lint+test+build); Makefile (`dev`, `test`, `deploy`); pre-commit hooks.
- AC: `make dev` brings the full stack up locally; CI green; `terraform validate` passes.

**S2 — Data model, auth, RBAC, audit**
- Load: docs 02 §4, 03 §5/§10 (skim).
- Build: SQLAlchemy models + Alembic for full schema (doc 02 §4); JWT auth; phone-OTP flow (SMS provider stubbed behind interface); roles; append-only audit middleware; seed script (1 hospital, 8 departments, 5 doctors, 50 fake patients).
- AC: pytest CRUD+auth suite green; audit row on every clinical write; seeds load idempotently.

**S3 — Provider layer, usage metering + prompt library**
- Load: docs 02 §5/§8/§9.
- Build: `RealtimeVoiceProvider(GeminiLive)`, `LLMProvider(GeminiFlash primary, OpenAI fallback)`, `STTProvider(Sarvam→Google)`, `TTSProvider(Sarvam→Google)`, `SMSProvider`, `MessagingProvider(Meta)`, `TelephonyProvider(Exotel)` interfaces + concrete impls + a `FakeProvider` for each (deterministic, used in tests); **usage metering decorator emitting `usage_events` from every wrapper (async, batched)** + `price_book` table & cost computation; retry/circuit-breaker + provider health registry + **cost-guard skeleton** (budget caps → tier downgrade signal); `prompts/` directory with vendor-neutral versioned prompts (routing, summarize, dictation-map, checkin-personalize) + the V1/V2 shared tool contract (get_next_node/save_answer/check_red_flags/finish_and_summarize).
- AC: contract tests pass against fakes; every fake call produces a priced usage_event; `providers/health` reports each provider; cost-guard breach flips tier flag in config; provider swap is config-only.

**S4 — Question-tree engine + oncology tree bank v1**
- Load: doc 03 §1/§3.
- Build: tree JSONB schema + validator; deterministic tree-walker; red-flag rule evaluator; **author the actual trees**: med-onc new patient, between-cycle review (CTCAE-lite), pain; radiation review; surgical post-op; palliative ESAS-lite; 5 thin routing trees — in en+hi first (mr/te text in S13); dept-classification prompt (chief complaint → dept) with eval set of 60 labeled utterances.
- AC: tree validator rejects malformed trees; walker unit tests cover branching+red flags; classifier ≥85% on eval set.

### Phase B — Intake core & kiosk (S5–S8)

**S5 — Intake Engine (all tiers) + session state**
- Load: docs 02 §5, 03 §1.
- Build: `IntakeEngine` exposing the shared tool contract; **V1 Gemini Live session manager** (function-call loop, audio passthrough hooks for voice-gw); **V2 pipeline loop** (STT→Flash/gpt-4o-mini dialogue→TTS) using the same tools; **V3** deterministic walker + pre-recorded audio manifest; Redis session state incl. active tier; automatic downgrade on provider failure OR cost-guard preserving answers; summarizer producing doc 03 §4 contract + patient-language read-back script; per-intake cost attribution finalized on completion.
- AC: scripted e2e drives a full intake through V1 (fake Live), V2, and V3 via fakes; mid-session provider kill downgrades tier without data loss; completed intake carries an accurate cost total; summary matches contract schema.

**S6 — Kiosk PWA part 1 (flow + design system)**
- Load: doc 04 (full), 03 §1a. Reference `smart-opd-intake_1.html` for feel only.
- Build: design tokens + component library (OptionCard, FacesScale, BodyMap, Stepper, AssistantAvatar, AudioBar); kiosk flow screens: language → caregiver toggle → voice chief complaint (Web Speech + server STT toggle) → tree questions with auto-read-aloud → summary read-back + confirm → token screen; Playwright screenshot suite + self-critique pass per doc 04 §5.
- AC: full kiosk intake in hi+en against local stack; every screen passes the audio-first laws checklist; screenshots reviewed in session log.

**S7 — Kiosk part 2: offline-first, T3 voices, printing**
- Load: docs 01 §5, 03 §1a, 04 §3.
- Build: service worker + Dexie caching (trees, audio packs, session state); offline token blocks (server allocation API + kiosk consumption); Downtime Mode UI + auto-detection + sync/reconciliation; ESC/POS print bridge for token slip; voice-pack manifest format + generate placeholder TTS-rendered packs (real human recordings swapped later via admin).
- AC: demo script: kill API 10 min → kiosk completes 3 offline intakes with valid tokens → restart → all sync, zero collisions.

**S8 — Queue service + board + coordinator console**
- Load: doc 03 §6, 04 §3, 01 §5.
- Build: queue APIs, token issuance, priority/urgent insertion, wait-time estimator; WebSocket fan-out; TV board (train-board aesthetic, audio announcements 2 langs); coordinator console (drag reorder, downtime enter/exit, reconciliation screen, downtime paper-entry form); printable daily paper-token-block + paper intake sheets (PDF generated from trees).
- AC: 3 browsers (kiosk/board/coordinator) live-sync; urgent red-flag intake jumps queue with reason chip; downtime drill passes end-to-end.

### Phase C — Doctor loop (S9–S11)

**S9 — Doctor console + summary view**
- Load: doc 03 §4/§5, 04 §3.
- Build: OTP login; day list; patient card (summary hero, red-flag strip, answers accordion, visit timeline, symptom sparklines); call-next / no-show / lab-requeue actions wired to queue; keyboard shortcuts.
- AC: doctor completes a full morning simulation on seed data; summary renders ≤20s-scannable per checklist; every action audited.

**S10 — Dictation → structured mapping**
- Load: doc 03 §7.
- Build: dictation capture (Web Speech + audio record fallback → server STT); mapping prompt → structured JSONB; formulary fuzzy-match validation (seed Indian oncology formulary ~300 drugs); review/diff UI; sign flow.
- AC: 10 sample Hinglish dictations (write them as fixtures) map with zero silent drug substitutions; unknown drugs flagged; signing locks record.

**S11 — Digital prescription**
- Load: doc 03 §8.
- Build: Rx PDF (letterhead template + large-type pictogram patient copy); print endpoint; WhatsApp/SMS delivery hooks (via providers, template registered); Rx history on patient file.
- AC: signed dictation → PDF in <3s; pictogram copy passes low-literacy checklist; delivery recorded per channel.

### Phase D — Channels (S12–S16)

**S12 — WhatsApp bot**
- Load: doc 03 §1d/§2, 04 §3.
- Build: Meta webhook + session windows; intake via buttons and voice notes; token status; Rx re-send; template registry + seed templates; voice-note replies (TTS).
- AC: e2e via Meta test number: voice-note intake completes; buttons flow completes; out-of-window template path works.

**S13 — Multilingual completion (mr, te) + language QA harness**
- Load: doc 03 §1, tree bank.
- Build: mr/te text for all trees, UI strings, summaries, WhatsApp templates; language QA harness (round-trip STT/TTS smoke per language, glossary consistency check); font/line-height audit per doc 04 §4.
- AC: full kiosk + WhatsApp intake in all 4 languages; QA harness in CI.

**S14 — Telephony voice gateway (Exotel) part 1: pipeline**
- Load: docs 02 §5, 03 §1b.
- Build: `voice-gw` service: Exotel Voicebot WS ↔ **V1 Gemini Live bridge** (audio relay + tool loop) with **V2 STT↔TTS pipeline** path per tier config; per-minute audio metering into usage_events; barge-in; DTMF fallback; consent line; call state persistence; local test harness replaying recorded audio as a fake Exotel client.
- AC: fake-client e2e intake completes on both V1 and V2 paths (V1 <1.5s, V2 <3.5s p90 turn latency, measured); barge-in works; partial saves on hangup; call cost recorded per intake.

**S15 — Telephony part 2: inbound appointments + outbound campaigns**
- Load: doc 03 §2, 01 §4.2/4.4.
- Build: slot inventory + booking APIs (constraint-safe); inbound AI receptionist intents; human-handoff with whisper summary; D-1 outbound intake campaign (Celery beat, retry ladder, WhatsApp fallback); confirmations WhatsApp+SMS.
- AC: fake-client books/reschedules/cancels against real slots; double-booking test fails safely; campaign dry-run produces correct call list.

**S16 — Android app**
- Load: doc 03 §1c, 04 §3.
- Build: Kotlin/Compose app: OTP login, My Cancer Care File (offline), Talk-to-Dhara home intake (native speech), live queue position, medicine reminders (WorkManager, exact alarms), chemo calendar with audio clips, caregiver link; <15MB, minSdk 26.
- AC: instrumented tests for offline file + reminders; full home-intake flow on emulator; APK size check in CI.

### Phase E — Continuity & hardening (S17–S22)

**S17 — Check-in engine**
- Load: doc 03 §9.
- Build: protocol templates (regimen families: platinum-based, taxane, anthracycline, RT, post-op, palliative); plan generation from signed dictation (LLM personalize + doctor one-tap approve); scheduler; delivery ladder WhatsApp→voice→SMS; grading rules + LLM assist; nurse review queue + escalation tasks; next-cycle reminders.
- AC: sign a fixture dictation → correct plan drafted → simulated D+2 red answer escalates within 1 min; quiet hours respected.

**S18 — Admin console + Cost & Usage Analytics dashboard**
- Load: doc 03 §10/§11, 02 §8.
- Build: tree builder (visual, versioned, test-run), red-flag rule editor, protocol template editor, message template registry, voice-pack manager, slot templates, price-book editor, cost-guard config, downtime drill button; **full analytics dashboard per doc 03 §11**: rollup materialized views (per-minute + daily), live cost/token strip, time series with channel/tier/purpose/model filters, unit-economics cards (₹ per intake etc.), what-if tier-mix recompute, anomaly flags, intake funnel + operations tab, CSV export, monthly invoice reconciliation view.
- AC: non-technical user edits a tree option, publishes, sees it live on kiosk without deploy; dashboard numbers reconcile to usage_events exactly on a seeded replay day; cost per intake visible per channel & tier; what-if recompute matches hand calculation on fixture data.

**S19 — Deploy to AWS + observability**
- Load: doc 05 (full).
- Build: apply Terraform; ECR + GitHub Actions deploy pipeline; Caddy TLS; CloudWatch alarms; Loki/Grafana dashboards (turn latency, provider health, queue depth, check-in outcomes) + Grafana infra-cost panel distinct from the in-app product analytics; backup jobs + restore runbook (execute one real restore).
- AC: production URL live; alarm test fires; documented restore completed from last-night backup.

**S20 — Load, chaos & security pass**
- Load: docs 02 §6–7, 01 §5.
- Build: Locust profile for 500-patient day (peak-hour shape); chaos scripts (kill api / kill postgres / block LLM egress) verifying every fallback tier; security pass (authz matrix tests, rate limits, webhook signature verification, PII log-scrubbing, dependency audit).
- AC: peak-hour load p95 API <300ms, zero 5xx; all chaos scenarios recover per Downtime Protocol; security checklist signed off in session log.

**S21 — Pilot content & clinical review pack**
- Load: docs 01, 03 §3.
- Build: final human voice-pack integration hooks + recording script sheets for a voice artist (all prompts, 4 languages); clinical review export of every tree/red-flag/protocol (readable PDFs for the oncology team to sign off); OPD signage/QR posters; staff training one-pagers (coordinator, nurse, doctor) with screenshots; downtime laminate sheets.
- AC: review pack generated from live data (not hand-written) so it never drifts from the system.

**S22 — Pilot dress rehearsal & handover**
- Load: everything (skim), all HANDOFFs.
- Build: full-day simulation script executed end-to-end (walk-in kiosk, D-1 call, WhatsApp caregiver, doctor loop, Rx, check-in, downtime drill mid-day); fix punch list; write `OPERATIONS.md` (runbooks: deploy, restore, downtime, provider outage, on-call) and `PILOT-PLAYBOOK.md` (week-by-week rollout: week 1 one doctor one kiosk → week 3 full OPD); tag `v1.0-pilot`.
- AC: simulation passes without manual DB edits; a new engineer can deploy from OPERATIONS.md alone.

---

**Phase 2 backlog (tracked, not built):** iOS app; handwritten-Rx OCR; WhatsApp native calling API; FHIR export; urban/multi-site tenanting; analytics warehouse.
