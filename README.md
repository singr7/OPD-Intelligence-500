# OPD Intelligence Platform — Oncology Pilot

AI-assisted OPD consultation platform for a **500 patients/day oncology care center**, designed rural-first for **Tier-2/Tier-3 India** (English, Hindi, Marathi, Telugu). This repo contains the complete concept, architecture, feature spec, UI/UX system, deployment plan, and a **session-by-session build plan** designed to be executed self-sufficiently by Claude Opus.

## Repo map

| File | What it is | Read when |
|---|---|---|
| `docs/01-CONCEPT-AND-WORKFLOWS.md` | Product concept, personas, end-to-end workflows, downtime protocol | Session 0, and whenever workflow questions arise |
| `docs/02-ARCHITECTURE.md` | System architecture, stack decisions, data model, integrations | Every backend session |
| `docs/03-FEATURE-SPEC.md` | Module-by-module product spec incl. question-tree bank | The sessions building that module |
| `docs/04-UIUX-GUIDE.md` | Design system, rural-first UX laws, per-surface guidance | Every frontend session — **mandatory** |
| `docs/05-DEPLOYMENT.md` | Docker, Terraform, exact EC2 config, cost model | Sessions 1, 19, 20 |
| `docs/06-BUILD-PLAN.md` | The 22-session build plan with per-session scope and acceptance criteria | Start of every session |
| `docs/07-SESSION-PROTOCOL.md` | The ritual: how every session starts, commits, logs, and hands off | **Start and end of every session** |
| `sessions/_TEMPLATE.md` | Session log + handoff template | Copied at start of each session |

## How to run a build session with Claude Opus

1. Open a fresh conversation with Claude Opus with repo access (Claude Code recommended).
2. Paste the **Session Start Prompt** from `docs/07-SESSION-PROTOCOL.md`, filling in the session number.
3. Opus reads `HANDOFF.md` (written by the previous session), the current session's entry in `docs/06-BUILD-PLAN.md`, and only the docs listed in that session's "Context to load" line — nothing else. This is the token-optimization contract.
4. Opus builds, tests, commits incrementally, writes `sessions/SESSION-NN.md` and overwrites `HANDOFF.md`, then stops.

## Non-negotiable product principles

1. **Voice-first, literacy-optional.** Every patient-facing surface must be fully operable by someone who cannot read, on a shaky 2G/3G connection.
2. **Doctors stay at the center.** AI prepares, summarizes, drafts. A human always confirms anything clinical.
3. **Degrade gracefully.** Every module has a defined behavior for offline / API-down / power-cut (see Downtime Protocol in doc 01).
4. **Oncology-first, config-driven.** Question trees, departments, and check-in protocols are data, not code, so the platform later adapts to other specialties and urban populations.
5. **Simple to deploy.** One EC2 box, docker compose, Terraform. No Kubernetes for the pilot.
