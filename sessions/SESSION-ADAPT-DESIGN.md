# SESSION-ADAPT-DESIGN — Local voice completion + S-ADAPT adaptive-intake design

**Date:** 2026-07-22 · **Scope ref:** docs/06-BUILD-PLAN.md → S-OSS (voice) + new
S-ADAPT track · **Branch:** `feat/adaptive-intake`

> ⚠️ **BRANCH-ONLY WORKFLOW (operator instruction, 2026-07-22).** All S-ADAPT work
> lives on **`feat/adaptive-intake`** and is **NOT** merged to `main` until the
> branch is **proven stable on the `omen` box**. Rationale: `main` is what the live
> pilot deploys from (omen `git pull`s it); adaptive-intake must not disrupt the
> working local-voice kiosk. Build here, deploy this branch to omen to validate,
> and only fast-forward `main` once it holds up in real use. Do not `git merge` to
> main without the operator's explicit go-ahead.

## What was built (this session)
- **Local read-aloud, on-box (doc 10 §6).** `POST /kiosk/tts` (kiosk.py) mirroring
  `/kiosk/stt` — TTS chain inside `usage_scope(KIOSK)`, 24 kHz, base64 wav; frontend
  `speak()` gains a server path (`speakServer` → `/kiosk/tts`, plays the clip) with
  the browser voice as fallback; `NEXT_PUBLIC_KIOSK_SERVER_TTS` build flag
  (Dockerfile + compose + .env.example). *(committed on main earlier)*
- **Kokoro `/tts` container — the "default voice now" path.** `deploy/tts-kokoro/`
  (FastAPI wrapper on Kokoro-82M, `POST /tts → {base64 wav}`, en+hi, per-language
  voice resolution, GPU/CPU auto). Runs as a standalone `opd-tts` container on
  `opd_default`, a peer of opd-vllm/opd-stt. Voicebox/Dhara-clone reserved for
  later. *(committed on main earlier)*
- **`/finish` 500 fix.** `finish_and_summarize` returned `IntakeSummary.red_flags`
  (human strings) into `FinishOut.red_flags` (typed `list[dict]`) — 500 the moment
  a real LLM emitted a flag. Now returns rule-engine `{id, severity}` dicts (same
  shape as /answer, /confirm); regression test added. *(committed on main earlier)*
- **S-ADAPT design (this branch).** doc 11 (adaptive intake turn, V1→V2), S-ADAPT
  track in doc 06, HANDOFF updated. Design only — no S-ADAPT code yet.

## Decisions made
- **Adaptive intake is the next build, not full-duplex V2V.** The local LLM
  answer-interpreter (voice→node value + one clarify) is the intelligence layer;
  streaming V2V (S-OSS.2) is a later transport that wraps it, and belongs on the
  phone channel more than the touchscreen. (doc 11 §4)
- **V1 = clarify-only; V2 scoped from V1 telemetry.** Build the cheap turn-based
  slice first; enrichment/adaptive-follow-ups (V2) are designed but finalised from
  V1 data. (doc 11 §2/§3)
- **Guardrails are non-negotiable** (doc 11 §5): rules decide red flags; every
  candidate value passes the existing `walk.save()`; taps stay the zero-AI floor.
- **Default voice via Kokoro, branded Dhara via Voicebox later.** No cloning on the
  critical path to first voice. (doc 10 §6)
- **Branch-only until stable on omen** (see the box above).

## Deviations from spec
- New parallel track **S-ADAPT** added to doc 06 (peer of the V-OSS track). doc 11
  is new. No changes to docs 01–05.

## Tests & evidence
- `make test` / backend suite: **581 passed** (after the /finish fix + its
  regression test). Web `next build` clean with the TTS flag; tsc + lint clean.
- New tests: `/kiosk/tts` synth + empty-text (test_kiosk.py); `/finish` red-flag
  dict-shape regression (test_intake.py).
- Live evidence: Kokoro voice confirmed speaking on omen; `/finish` fixed live.

## Known gaps / stubs introduced
- S-ADAPT is **design only** — `AnswerInterpreter`, `prompts/interpret_answer/`,
  the `/answer` voice path, and the web flag are specified (doc 11) but unbuilt.
- Kokoro Hindi quality is the bake-off follow-up (S-OSS.1); mr/te unsupported by
  Kokoro (400 → browser voice).

## Commits
(local-voice + /finish fixes committed on `main` earlier this session)
- 3bdbf38 — docs: design the S-ADAPT adaptive-intake track (V1→V2) + integrate
