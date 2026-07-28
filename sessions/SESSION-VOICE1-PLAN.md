# SESSION-VOICE1 — Selectable Kiosk Voice Profiles

Type: execution only  
Branch: `kiosk-voice-profiles`  
Predecessor: accepted `uiux-kiosk-rx-hardening` commit  
Outcome: local, OpenAI, and Sarvam kiosk voice profiles selectable without code edits

## Start

Follow `docs/07-SESSION-PROTOCOL.md`. Read only:

1. `HANDOFF.md`
2. `STATE.md`
3. `docs/02-ARCHITECTURE.md` sections on providers, tiers, metering, and safety
4. `docs/10-LOCAL-GPU-DEPLOYMENT-AND-TTS-NEXT.md`
5. `docs/11-ADAPTIVE-INTAKE.md`
6. `docs/16-VOICE-CLOUD-ANDROID-EXECUTION-PLAN.md`
7. this file

Before editing, confirm doc 15's physical acceptance is recorded and preserve any
unrelated dirty files. Create the branch from the accepted commit.

## Build contract

### Unit 1 — Freeze the profile contract

Add a typed profile definition with exactly these names:

- `local_oss`
- `openai_cloud`
- `sarvam_cloud`

Extend the published channel configuration with `kiosk_voice_profile`. The file
configuration remains the floor and a published database version remains the live
overlay. Unknown profiles must fail validation. An unavailable profile must not
become active merely because it was selected.

Snapshot the selected profile and exact STT/LLM/TTS model names into the intake
session at start. Do not change an active intake when another profile is published.
Include the profile in usage/health metadata without logging patient text or audio.

Commit: `S VOICE1: define versioned kiosk voice profiles`

### Unit 2 — Implement missing adapters

Keep all vendor calls behind the existing interfaces.

OpenAI:

- add an STT adapter using the transcription endpoint and configurable default
  `gpt-4o-mini-transcribe`;
- configure the existing OpenAI LLM adapter for `gpt-5.6-luna`;
- add a TTS adapter using the speech endpoint and configurable default
  `gpt-4o-mini-tts`;
- preserve usage metering, timeouts, retries, circuit breakers, bad-request
  distinction, and empty/silence behavior.

Sarvam:

- update the STT default to `saaras:v3` and support its returned language metadata;
- add an OpenAI-compatible Sarvam LLM adapter with default `sarvam-30b`;
- retain the TTS adapter with configurable default `bulbul:v2`;
- update authentication and request shapes to current Sarvam documentation.

Do not put vendor-specific calls into kiosk routes, `IntakeEngine`, or the web app.
Use mocked HTTP transports in automated tests; live keys are only for the later
smoke gate.

Expected file area:

- `backend/app/config.py`
- `backend/app/providers/{stt,llm,tts,registry,runtime}.py`
- provider contract/registry/runtime/pricing tests
- `.env.example`
- price-book seed/configuration

Commit: `S VOICE1: add OpenAI and Sarvam voice adapters`

### Unit 3 — Bind profiles to deterministic intake

Create one resolver that maps a profile to an STT/LLM/TTS trio. Feature code asks
for that trio and never assembles vendor names itself.

Required mapping:

```text
local_oss    → local_whisper + local_vllm + configured local TTS
openai_cloud → openai STT + openai LLM + openai TTS
sarvam_cloud → sarvam STT + sarvam LLM + sarvam TTS
```

Keep the pathway invariant:

```text
audio → STT → constrained answer interpreter
      → existing validation/Walk.save → deterministic next node and red flags
      → TTS for the already-approved patient-facing text
```

The LLM may propose a value for the current node. It may not call a tool that
selects a node, department, red flag, severity, token priority, or final clinical
summary fact. Reject out-of-schema responses and return to taps after the existing
single clarification limit.

Fallback policy:

- a failed component first uses its configured same-interface fallback only when
  that fallback belongs to the same approved profile;
- no automatic OpenAI↔Sarvam transfer;
- provider exhaustion downgrades to deterministic V3/taps without losing answers;
- an operator-visible event explains the downgrade without PHI.

Commit: `S VOICE1: route kiosk voice through snapshotted profiles`

### Unit 4 — Add safe credential and profile controls

Extend the existing encrypted, write-only provider credential store for:

- `llm:openai`, `stt:openai`, `tts:openai` sharing the OpenAI API key safely;
- `llm:sarvam`, `stt:sarvam`, `tts:sarvam` sharing the Sarvam API key safely.

The admin API/UI may report configured, missing, source, last test, health, model,
and active profile. It must never return a credential value. Add:

- a test action per provider component;
- a pre-publish readiness check for the complete profile;
- a profile selector with a confirmation that it affects new intakes;
- an audit record containing profile names and field names, never secret values.

If shared-key storage would duplicate ciphertext rows, store one vendor credential
set (`vendor:openai`, `vendor:sarvam`) and let all three adapters consume its
allow-listed key.

Commit: `S VOICE1: add audited profile readiness controls`

### Unit 5 — Tests and real-provider acceptance

Automated tests must prove:

- all three mappings and unknown-profile rejection;
- profile snapshot isolation across a live configuration change;
- OpenAI and Sarvam request/response contracts for success, silence, 4xx, 5xx,
  timeout, malformed structured output, and missing audio;
- deterministic `Walk` and red-flag results are identical for tap, OpenAI, and
  Sarvam inputs that resolve to the same answer;
- no cross-vendor audio transfer;
- fallback retains answers and returns a usable tap question;
- usage records contain provider/model/quantities and no transcript;
- production safety refuses an enabled uncredentialed profile;
- credentials are write-only and never appear in logs or API responses.

With real keys supplied outside Git, run a short Hindi and English smoke for all six
cloud components. Record date, region, latency, model reported, and pass/fail. For
deprecated `gpt-4o-mini-tts` or legacy `bulbul:v2`, record the exact vendor response.

Run `make test`, `make lang-qa`, production web build, kiosk E2E, and provider-focused
tests. Close as `sessions/SESSION-VOICE1.md`.

Commit: `S VOICE1: verify selectable kiosk voice profiles`

## Omen deployment — simple operator path

Do not install another reverse proxy and do not run `docker compose down`.

```bash
git fetch origin
git switch kiosk-voice-profiles
git pull --ff-only origin kiosk-voice-profiles
cp .env .env.before-voice1
```

Add the real keys and explicit model configuration to the Omen secret environment
or the write-only admin credential screen:

```dotenv
KIOSK_VOICE_PROFILE=local_oss
OPENAI_STT_MODEL=gpt-4o-mini-transcribe
OPENAI_MODEL=gpt-5.6-luna
OPENAI_TTS_MODEL=gpt-4o-mini-tts
SARVAM_STT_MODEL=saaras:v3
SARVAM_LLM_MODEL=sarvam-30b
SARVAM_TTS_MODEL=bulbul:v2
NEXT_PUBLIC_KIOSK_SERVER_STT=1
NEXT_PUBLIC_KIOSK_SERVER_TTS=1
NEXT_PUBLIC_KIOSK_ADAPTIVE=1
INTAKE_ADAPTIVE=1
```

Then build and replace only application services:

```bash
docker compose build api voice-gw worker beat web
docker compose up -d --no-deps api voice-gw worker beat web
docker compose ps
curl -fsS http://127.0.0.1:18080/health
curl -fsS https://opd.radpretation.ai/api/health
```

Run one intake on `local_oss`, publish `openai_cloud`, run one, then publish
`sarvam_cloud` and run one. Restore the accepted default profile after the test.

Rollback:

1. publish `local_oss` if cloud voice is unhealthy;
2. if the application build itself is unhealthy, redeploy the previous committed
   SHA with the same targeted `docker compose up` command;
3. restore `.env.before-voice1` only if environment parsing caused the failure;
4. never replace the database volume during rollback.

## Acceptance checklist

- [ ] Three exact profile names exist and are operator-selectable.
- [ ] New-intake-only switching is proven.
- [ ] Both cloud stacks complete real Hindi and English kiosk turns.
- [ ] Deterministic routing and red flags are unchanged.
- [ ] Cloud failure returns to usable taps without losing intake state.
- [ ] Credentials remain encrypted/write-only.
- [ ] Omen deploy and rollback are recorded with commit SHA.
- [ ] All repository gates are green.

