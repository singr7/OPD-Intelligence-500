# SESSION-VOICE1 — Selectable Kiosk Voice Profiles

**Date:** 2026-07-28 · **Scope ref:** `sessions/SESSION-VOICE1-PLAN.md`

## Acceptance criteria checklist

- [x] Exactly `local_oss`, `openai_cloud`, and `sarvam_cloud` are typed and
  operator-selectable.
- [x] The selected profile and exact STT/LLM/TTS models are snapshotted per intake;
  later publishes affect new intakes only.
- [x] OpenAI transcription, Luna LLM, and speech adapters implement the documented
  wire contracts behind the provider interfaces.
- [x] Sarvam Saaras v3, Sarvam-30B, and Bulbul v2 implement the documented wire
  contracts behind the same interfaces.
- [x] Profile routing never appends a cross-vendor fallback. Exhaustion returns the
  unchanged deterministic node to taps.
- [x] Usage records carry profile/provider/model/quantities and no transcript/audio.
- [x] One encrypted write-only credential row supplies all three components of each
  cloud vendor; component tests and publish readiness are audited.
- [x] An enabled cloud profile cannot publish until its STT, LLM, and TTS tests pass.
- [x] Automated repository, language, build, migration, container, kiosk, and
  Channels gates pass.
- [ ] Real Hindi and English cloud smoke: blocked because neither API key is
  configured in this environment.
- [ ] Omen deploy/rollback: not performed; no Omen access was available in-session.

## What was built

- `app.providers.profiles` owns the three names, exact immutable snapshot, and the
  sole profile-to-provider-trio resolver.
- `SessionState` persists the snapshot. Kiosk adaptive interpretation, STT, TTS,
  engine turns, and summaries resolve from it.
- OpenAI STT/TTS and Sarvam LLM adapters were added; Sarvam STT/TTS shapes and
  model defaults were updated to the approved requested versions.
- Same-profile chains are explicit one-component tuples. Generic configured
  OpenAI/Sarvam/Google fallbacks are never used for a snapshotted kiosk profile.
- `usage_events.voice_profile` plus migration `a4d5e6f7b801` provides non-PHI
  profile attribution.
- Shared encrypted `vendor:openai` and `vendor:sarvam` credentials overlay `.env`,
  rebuild cached components on rotation, and never return values.
- The Channels admin shows active/readiness state and exact models, offers STT/LLM/TTS
  tests, and confirms that activation applies only to new intakes.
- Publishing records old/new profile names and refuses an enabled cloud profile
  unless all three latest component tests passed.
- Price-book rows cover each selected model; OpenAI audio rows are explicitly
  conservative estimates pending invoice reconciliation.

## Decisions made

- Shared vendor keys live in one ciphertext row rather than three duplicated rows.
- A profile has no implicit fallback. A future fallback must be explicitly approved
  inside that same profile.
- Credential tests use fixed non-patient fixtures. Vendor errors are redacted against
  every allow-listed credential before entering an API response or stored test detail.
- `gpt-4o-mini-tts` and `bulbul:v2` remain the requested configurable defaults even
  though vendor documentation marks them deprecated/legacy; no silent substitution.
- Pre-session chief-complaint speech uses the currently published profile; all
  post-start speech includes the session id and therefore uses the immutable snapshot.

## Deviations from spec

- Real-provider and Omen acceptance are honestly deferred, not simulated. Both keys
  reported unconfigured by presence-only checks.
- The in-app inspection surface could not connect because its sandbox metadata was
  unavailable. The equivalent live-stack Playwright kiosk and Channels suites passed.

## Tests & evidence

- `make test`: backend **1,261**, voice-gw **25**, web conformance **48**, Android
  JVM tests green.
- Provider/control focused slice: **107 passed**.
- Profile-routing/kiosk/metering slice: **95 passed**.
- `make lang-qa`: en/hi/mr/te clean.
- `make migrate`: `2c978d44c900 -> a4d5e6f7b801` applied.
- `npm run build`: optimized production build passed.
- `make preflight`: API and voice-gateway images built and imported.
- `npm run e2e`: **3 passed** (full Hindi intake, tablet matrix, English welcome).
- `npm run e2e:channels`: **4 passed** after repairing two stale selectors and
  reusing one OTP inside the serial suite.
- Updated screenshots: `web/screenshots/sgl1/01-channels.png` and
  `web/screenshots/sgl1/02-credentials.png`. Self-critique: the screen is dense,
  but the active profile, readiness reason, exact models, and new-intake-only action
  are visually explicit; credential fields remain blank write-only controls.

## Known gaps / stubs introduced

- No cloud component has made a live request. Supply keys outside Git and run all six
  component tests plus Hindi/English kiosk turns.
- OpenAI TTS price is a blended per-character estimate because the vendor bills audio
  output tokens; replace it from invoices.
- Omen deployment and rollback evidence remain required before this branch is accepted
  as the predecessor of `SESSION-CLOUD1`.

## Commits

- `a418bc6` — S VOICE1: define versioned kiosk voice profiles
- `d5d9b6c` — S VOICE1: add OpenAI and Sarvam voice adapters
- `e2e2bb5` — S VOICE1: route kiosk voice through snapshotted profiles
- `06563a1` — S VOICE1: add audited profile readiness controls
- `92a0813` — S VOICE1: verify selectable kiosk voice profiles
- final close commit — S VOICE1: session close — local gates green

---
