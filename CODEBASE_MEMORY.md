# OPD Intelligence Platform — Codebase Memory

Last reviewed: 2026-07-27

This is durable orientation for future work. It complements, but does not replace,
`HANDOFF.md` (the next-session brief), `STATE.md` (the living implementation map),
the numbered design documents, or the append-only session logs.

## Product Target

The immediate target is a safe, working oncology OPD MVP on the local Omen server:

1. The kiosk pathway works end to end on the real kiosk, network, GPU, screen, audio,
   and printer.
2. Queue board, coordinator, doctor, admin, and patient-facing records work locally.
3. External pathways remain code-ready and can be enabled by adding reviewed
   credentials/configuration and restarting services.
4. Provider failure degrades explicitly to a safe lower tier or a staffed path. It
   must never fabricate a successful interaction.

The architecture is deliberately a modular monolith for the pilot. Do not split it
into services without measured operational need.

## Start And Close Ritual

At the start of a substantive session:

1. Read `HANDOFF.md`.
2. Read the current sections of `STATE.md`.
3. Read the relevant numbered design document and latest related session log.
4. For frontend work, read `docs/04-UIUX-GUIDE.md`.
5. Inspect the working tree and preserve unrelated user changes.
6. Establish a baseline with the smallest relevant tests; use `make test` for shared
   contracts or release-facing work.

At the end:

1. Run focused tests plus the appropriate broad gate.
2. Record important evidence, decisions, and remaining debt in a session log.
3. Update `STATE.md` when the implementation map changed.
4. Replace `HANDOFF.md` with the next actionable brief.
5. Update this file only when architecture, invariants, pathway maturity, or the
   operating model materially changes.

## System Map

- `backend/`: FastAPI, SQLAlchemy/asyncpg, Alembic, Celery, deterministic intake
  engine, queue, clinical summaries/dictation/Rx, appointments, check-ins, analytics,
  provider adapters, audit, and RBAC.
- `voice-gw/`: Exotel/Pipecat-facing voice runtime. It imports the shared backend
  intake contracts while remaining a separate process for crash isolation.
- `web/`: one Next.js application serving `/kiosk`, `/board`, `/coordinator`,
  `/doctor`, and `/admin`; offline kiosk state uses Dexie/service worker.
- `android/`: Kotlin/Compose patient app, currently validated mainly through JVM
  tests rather than a signed real-device release.
- `seeds/`: authored clinical trees and demo data. Trees are runtime content and are
  mounted read-only into backend containers.
- `docker-compose.yml`: local application/observability stack. Omen GPU model
  containers are currently managed separately, which is an operational weakness.
- `infra/`: Terraform for the cloud-shaped deployment option, not the current Omen
  primary.
- `docs/`: numbered product/architecture/runbook documents.
- `docs/sessions/`: append-only implementation history and rationale.

## Pathway Maturity

### Kiosk

The deterministic intake, multilingual shells, offline token blocks, reconciliation,
service worker, server STT/TTS switches, and browser/ESC-POS printing code exist.
It is the intended first live pathway.

Launch is still gated by oncologist review of the tree bank, native and clinical
review of patient-facing Marathi/Telugu, the missing Surgical Oncology new-lump
tree, real Omen acceptance, real printer output, and downtime/recovery drills.

### Queue, Board, And Coordinator

Queue state transitions, token board, WebSocket updates, downtime sheets, and
coordinator controls exist. The public board is intentionally minimal.

The coordinator now has truthful live metrics for waiting, urgent, called,
in-consultation, and active departments. Metrics that require unavailable data
(throughput, idle rooms, appointment arrivals, offline debt, bottlenecks) remain
future backend work. Public screens do not expose clinical red-flag reasons.

### Doctor And Prescription

The doctor worklist, priority ordering, summary provenance, dictation review,
medication validation, signature boundary, prescription snapshot/PDF, and audit
trail are implemented. Safety-critical behavior is strong.

The worklist is queue-only, scheduled appointments do not yet become queue visits,
and access-token refresh is not used by web clients. The console now has a denser
split worklist, state-aware actions, visible document status, and a prominent signed
prescription. Room context, tasks, and labs/vitals require real backend data.

### Admin And Clinical Content

Admin surfaces cover people, trees, protocols, channels, analytics, prices, cost
guard, and audit-oriented workflows. Editable clinical content is versioned.

Clinical publication is not a substitute for clinical review. Model-drafted content
must not be treated as patient-ready merely because it passes structural tests.

### External Channels

WhatsApp, SMS, phone intake, receptionist, campaigns, and provider adapters have
substantial contracts, fakes, and tests. They are not all literally
"credentials + restart": each first live vendor path still requires webhook/TLS
configuration, template approval where applicable, real-provider acceptance,
latency/error tuning, and monitoring.

### Patient App And Check-ins

The Android app, caregiver links, records, reminders, cycle/check-in logic, grading,
and escalation pathways exist. Real handset, signing, notification, battery, and
network acceptance remain. The check-in protocol bank requires oncologist review.

## Non-Negotiable Invariants

- Red flags, intake traversal, check-in grading, and escalation are deterministic.
  A model may interpret or summarize; it may not decide clinical urgency.
- The walker position is derived from persisted answers. Do not introduce a mutable
  cursor. Python-to-TypeScript conformance fixtures guard online/offline equivalence.
- Clinical writes are structurally audited. The audit log is append-only.
- Dictation never silently corrects or invents a drug. Prescription output exists
  only after doctor review/signature and is snapshotted.
- Dose times are never inferred from incomplete instructions.
- Editable trees/protocols are versioned and published explicitly.
- Monetary values use Decimal semantics. Provider usage is metered structurally.
- Appointment capacity uses database-backed seats/constraints to prevent duplicate
  booking.
- Downgrade degrades capability; it never denies care or reports a fake success.
- A missing provider must produce a visible unavailable/degraded state.

## Omen Operating Facts

- The pilot Omen currently uses `ENV=local` because the production assertion rejects
  any fake provider. That also bypasses production secret/OTP safety checks and must
  be replaced by a mixed-provider-safe `pilot` posture before public exposure.
- API/web/voice/Postgres/Redis and observability ports are currently published on
  all interfaces by Compose. Bind internal services to localhost or remove their
  host ports; expose only the intended Caddy routes.
- Local vLLM/STT/TTS containers are outside Compose and can be disconnected by
  `docker compose down`. The Omen runbook therefore forbids casual `down`.
- `make deploy` does not run migrations, verify schema head, start/reconnect GPU
  models, or perform pathway-level smoke tests.
- `/health` is liveness only despite being used as readiness. Worker and beat have
  no effective health contract.
- Omen requires scheduled encrypted off-box database backups and a tested restore.
  Upgrade checkpoints are not a backup schedule.
- `NEXT_PUBLIC_*` values are build-time values. Credential/config changes that
  affect browser behavior may require a web rebuild, not merely a container restart.

## Current Priority Order

1. Validate `uiux-enterprise-revamp` on the local stack and physical Omen pathways;
   finish S-UX.5 hardening before merging to `main`.
2. Approve clinical content and complete S-GL.3 on the real Omen/kiosk hardware.
3. Add a safe pilot environment profile, network exposure controls, real readiness,
   migration gating, worker/beat checks, and backup/restore automation.
4. Make Omen startup one deterministic command, including GPU services and a
   kiosk/doctor/queue smoke test.
5. Complete appointment arrival-to-visit flow and web token refresh.
6. Prove one real external channel at a time; then make each an explicit,
   observable configuration switch.

## Known Documentation And Tooling Debt

- `STATE.md` contains valuable detail but has accumulated stale tail entries and is
  no longer a one-page map. Reconcile stale claims rather than trusting the last
  occurrence of a topic.
- `HANDOFF.md` has grown beyond the protocol's intended compact brief.
- Automatic CI triggers are disabled. The manual voice-gateway image build uses the
  wrong Docker context.
- `make lint` ignores voice-gateway lint failure and uses a Python environment
  without Ruff on the current machine.
- Python and container dependency installation is not fully locked; preflight
  import checks exist because image/runtime drift has already caused boot failures.
- The API service currently lacks the `./config:/config:ro` Compose mount required
  by `app.tiers` in the backend image. A fresh Omen kiosk start can therefore fail
  with `tiers config not found at /config/tiers.yaml` despite a green `/health`.
- Staff login and semantic tokens are shared. Large component-local CSS strings
  remain in doctor/coordinator/admin and should move to scoped modules during
  S-UX.5 without changing behavior.
