# 02 — Architecture

## 1. Sizing reality check

500 patients/day ≈ 60–80 concurrent active sessions at peak (9–12am), a few thousand API req/min, tens of concurrent audio streams. This is **small**. One well-configured EC2 instance with docker compose handles it with headroom. Do not build for imaginary scale; build for reliability and graceful degradation.

## 2. Stack (decided — do not relitigate in sessions)

| Layer | Choice | Why |
|---|---|---|
| Backend | **Python 3.12 + FastAPI**, single monolith, modular packages | Async fits telephony/websockets; one deployable; fastest to build with LLM assistance |
| DB | **PostgreSQL 16** (container, EBS-backed, WAL archiving to S3) | JSONB for intake payloads + relational core; pilot-scale fine in-container |
| Cache/queues | **Redis 7** | Queue-board pubsub, Celery broker, rate limits |
| Background jobs | **Celery + Celery Beat** | Check-in scheduling, reminder dispatch, outbound call campaigns |
| Web frontends | **Next.js 14 (App Router) PWAs** — kiosk, queue board, doctor console, coordinator console, admin | Offline-first via service worker + IndexedDB (Dexie); one codebase, four build targets by route group |
| Android app | **Kotlin + Jetpack Compose** | Native speech UX, offline, small APK (<15MB target for low-storage phones) |
| iOS | Phase 2 (SwiftUI); API is platform-agnostic from day 1 | |
| WhatsApp | **Meta WhatsApp Cloud API** (voice notes + interactive buttons + flows) | No BSP lock-in; voice notes cover "WhatsApp calling" use case pragmatically |
| Telephony | **Exotel** — Voicebot Applet (bidirectional audio streaming over WebSocket) for conversational calls; ExoML/IVR for fallback DTMF flows | Indian numbers, compliance, proven streaming |
| Voice Tier V1 (primary) | **Gemini Live (gemini-live-3.1)** — native bidirectional speech-to-speech over WebSocket, with function calling into the IntakeEngine (tree state, red-flag checks, save-answer tools) | Lowest turn latency, natural barge-in, one hop instead of three; strong Indic speech |
| Voice Tier V2 (cost-optimal pipeline) | **STT → LLM → TTS**: Sarvam Saarika STT (hi/mr/te/en, telephony-tuned, cheap) → **Gemini Flash** (primary) / **OpenAI gpt-4o-mini** (fallback) for dialogue turns → Sarvam Bulbul TTS. Google STT/TTS as secondary fallbacks | Tried-and-tested, per-component swappable, ~3–5× cheaper per voice-minute than V1; also serves channels where Live API isn't a fit (WhatsApp voice notes) |
| Voice Tier V3 (zero-AI) | Rule-based tree + **pre-recorded human voice packs**; **Web Speech API** on kiosk for the minimalistic version | Warmest voice, works offline, zero API cost; the permanent downtime/cost floor |
| Tier routing | Config per channel + automatic downgrade V1→V2→V3 on provider failure or **cost-guard breach** (daily budget caps per channel, see §9) | Cost and resilience use the same ladder |
| LLM (non-voice) | **Gemini Flash** for classification/summaries/dictation-mapping; **OpenAI** as configured fallback; batch/context-caching wherever latency allows. All prompts vendor-neutral in `prompts/` | Cost-optimal; provider abstraction makes vendor choice config |
| Auth | JWT + role claims; doctor login via phone OTP (MSG91/Exotel SMS); staff via username+TOTP option | |
| Observability | Loki + Grafana + Uptime Kuma (all containers); Sentry (SaaS free tier) | One-box friendly |

## 3. Component diagram

```
                        ┌─────────────────────────── EC2 (docker compose) ───────────────────────────┐
 Patients/Caregivers    │                                                                             │
 ┌───────────┐  PSTN    │  ┌─────────┐   ┌──────────────────────────────┐   ┌──────────┐             │
 │ Phone call├──Exotel──┼──► voice-gw │   │        api (FastAPI)         │   │ Postgres │             │
 └───────────┘  WS      │  │(audio WS)│──►│ intake · queue · appts ·     │◄──►          │             │
 ┌───────────┐          │  └─────────┘   │ doctor · rx · checkins ·     │   └──────────┘             │
 │ WhatsApp  ├──Meta────┼────────────────► admin · webhooks             │   ┌──────────┐             │
 └───────────┘ webhook  │                └───────┬───────────┬─────────┘   │  Redis   │             │
 ┌───────────┐          │                        │           │              └──────────┘             │
 │Android app├──HTTPS───┼───────────────┐        │      ┌────▼─────┐       ┌──────────┐             │
 └───────────┘          │  ┌─────────┐  │        │      │  celery  │       │ Caddy    │ TLS         │
 Kiosk/Board/Doctor ────┼──► web     │◄─┘        │      │ + beat   │       │ (edge)   │             │
 (Next.js PWAs)         │  │(Next.js)│           │      └──────────┘       └──────────┘             │
                        │  └─────────┘     ┌─────▼─────┐                                             │
                        │                  │ providers │→ Claude · Sarvam · Google · Exotel · Meta   │
                        └──────────────────┴───────────┴─────────────────────────────────────────────┘
                                     S3: audio recordings, Rx PDFs, DB WAL archive, backups
```

## 4. Core domain model (Postgres)

```
patients(id, mrn, name, phone, alt_phone, age, sex, lang, village, district, caregiver_name, caregiver_phone, created_at)
visits(id, patient_id, date, dept_id, doctor_id, token_no, status[registered|intake_done|in_queue|in_consult|done|no_show], channel[kiosk|phone|whatsapp|app|paper])
intakes(id, visit_id, tier[conversational|rule_based|prerecorded|paper], lang, transcript JSONB, answers JSONB, chief_complaint, red_flags JSONB, summary_md, summary_lang_versions JSONB, confirmed_by_patient bool, created_at)
departments(id, name, icon, active)
question_trees(id, dept_id, version, lang, tree JSONB, status[draft|published])   -- trees are DATA
appointments(id, patient_id, dept_id, doctor_id, slot_at, status[booked|confirmed|rescheduled|cancelled|arrived], source, reminders JSONB)
queues(id, dept_id, doctor_id, date) / queue_entries(queue_id, visit_id, token_no, priority[routine|semi|urgent], state, called_at, started_at, ended_at)
doctors(id, name, dept_id, phone, reg_no, otp auth fields)
dictations(id, visit_id, doctor_id, audio_url, transcript, structured JSONB{diagnosis, plan, meds[], advice, follow_up, treatment_events[]}, status[draft|signed])
prescriptions(id, visit_id, dictation_id, meds JSONB, pdf_url, delivered_via JSONB)
checkin_plans(id, patient_id, visit_id, protocol_key, schedule JSONB[{day_offset, channel, question_set}], approved_by, status)
checkins(id, plan_id, due_at, channel, sent_at, responses JSONB, grade[green|amber|red], escalated_to, resolved_at)
offline_token_blocks(kiosk_id, date, start_no, end_no, used_up_to)
usage_events(id, at, minute_bucket, session_id, intake_id, visit_id, channel, tier, provider, model, purpose[intake_turn|summary|routing|dictation|checkin|other], tokens_in, tokens_out, cached_tokens, audio_seconds, characters, unit_cost_ref, computed_cost_inr, latency_ms)
price_book(provider, model, unit[token_in|token_out|audio_sec|call_min|msg|char], price_inr, effective_from)   -- editable in admin; costs recomputable
audit_log(actor, action, entity, entity_id, at, meta JSONB)
```

Notes:
- All patient free-text/audio in original language + English translation stored side by side.
- `question_trees.tree` JSONB schema is defined in doc 03 §4 — versioned, published/draft, editable via admin.
- Soft deletes only; `audit_log` on every clinical write.
- `price_book.unit` is how the *vendor* bills, not how we measure: TTS is priced per `char` (both Sarvam Bulbul and Google bill per character, not per second of audio produced), telephony per `call_min`, STT per `audio_sec`. `token_in|token_out|char` prices are **per 1,000 units**; the rest are per single unit. A provider is priced by `audio_sec` **or** `call_min`, never both. See `backend/app/providers/pricing.py`.

## 5. Speech & LLM pipeline (shared engine, many channels)

One **Intake Engine** service class consumed by all channels:

```
Channel adapter (kiosk WS / exotel WS / whatsapp webhook / app API)
  → SessionState (redis)                       # language, dept, tree position, answers so far, active tier
  → Tier V1: Gemini Live session; the model NEVER free-styles clinically — it drives the intake
    via function calls: get_next_node(), save_answer(), check_red_flags(), finish_and_summarize().
    Audio in/out streams pass through voice-gw with usage metering taps.
  → Tier V2: STT stream → dialogue LLM (Gemini Flash / gpt-4o-mini) with same tool contract → TTS stream
  → Tier V3: deterministic tree walker; NLP only for chief-complaint→dept (Flash classify); pre-recorded audio
  → RedFlagRules (deterministic, config JSONB) run on every answer regardless of tier
  → Summarizer (Gemini Flash): summary_md in English + patient language; read-back script for confirmation
```

Latency budgets: V1 target <1.5s p90 turn (native S2S); V2 STT ≤1.2s + LLM ≤1.2s (Flash, short max_tokens, streaming) + TTS ≤1.0s → turn <3.5s p90; pre-recorded fillers mask V2 latency. The **same function-call tool contract** across V1/V2 is what makes tier switching mid-session lossless.

**Usage metering (built into voice-gw and every provider):** each provider call/stream emits a `usage_events` row — session_id, intake_id, provider, model, tier, channel, tokens_in/out, audio_seconds, cached_tokens, unit_cost, computed_cost, latency_ms, minute_bucket. This is the raw feed for the Cost & Analytics dashboard (§9) and the cost-guard.

## 6. Queue system

- Token issuance is server-authoritative; kiosks hold pre-allocated offline blocks (doc 01 §5).
- Queue board subscribes over WebSocket (Redis pubsub fan-out); reconnect-and-replay on drop; board caches last state in IndexedDB and enters Downtime Mode on >60s disconnect.
- Wait-time estimate: rolling median consult duration per doctor (last 20 consults) × position; clamp and show ranges ("~40–55 min"), never false precision.

## 7. Security & compliance (pilot-appropriate, DPDP-aware)

- TLS everywhere (Caddy auto-certs); JWT short-lived + refresh; RBAC (patient/caregiver, coordinator, nurse, doctor, admin).
- PII encryption at rest: EBS + S3 SSE; phone numbers hashed in analytics tables.
- Consent capture at registration (audio consent line for phone intake, recorded).
- Data residency: ap-south-1 (Mumbai) for everything incl. S3.
- Audit log immutable (append-only table + daily S3 export).
- Retention: raw audio 90 days, transcripts/summaries per hospital policy.

## 8. Cost & Usage Analytics (first-class subsystem)

- Every provider wrapper meters usage into `usage_events` (async, batched, never blocks the call path). Prices live in `price_book` (admin-editable), so historical cost is recomputable when vendors change pricing.
- Rollups (Celery, per-minute + per-day materialized views): tokens/min, audio-min/min, cost/min per provider·model·channel·tier; **cost per intake** (fully attributed: all events sharing intake_id), cost per completed vs abandoned intake, cost per check-in, cost per dictation, cost per booked appointment.
- **Cost-guard**: daily budget caps per channel/tier; approaching cap (80%) → alert; breach → automatic tier downgrade (V1→V2→V3) with banner in coordinator console. Guard rules are config.
- Dashboard is part of the admin console (spec in doc 03 §11).

## 9. Provider abstraction (hard rule)

Every external dependency sits behind an interface in `backend/app/providers/`: `RealtimeVoiceProvider` (Gemini Live), `LLMProvider` (Gemini Flash primary, OpenAI fallback), `STTProvider`, `TTSProvider`, `TelephonyProvider`, `MessagingProvider`, `SMSProvider`. Sessions must never call vendor SDKs directly from feature code, and **every provider wrapper must emit usage_events** — a provider implementation without metering fails review. This is what makes fallback tiers, vendor swaps, and cost attribution cheap.
