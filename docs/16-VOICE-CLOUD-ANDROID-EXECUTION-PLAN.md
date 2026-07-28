# 16 — Kiosk Voice Profiles, AWS Standby, And Android Distribution

Status: approved plan; not yet built  
Sequence: `SESSION-VOICE1` → `SESSION-CLOUD1` → `SESSION-ANDROID1`  
Starting point: the exact `uiux-kiosk-rx-hardening` commit accepted on the Omen

This is the build contract for three fresh, execution-only sessions. It turns the
current local voice implementation into three selectable kiosk profiles, creates a
GPU-free AWS environment, and ships one signed APK that can pair with either server.

## 1. Decisions already made

There are no architecture choices to reopen in the execution sessions.

### 1.1 The three kiosk voice profiles

| Profile | STT | Interpretation | Clinical pathway | TTS |
|---|---|---|---|---|
| `local_oss` | local Whisper | local vLLM | existing deterministic tree + rules | local TTS/Voicebox |
| `openai_cloud` | `gpt-4o-mini-transcribe` | `gpt-5.6-luna` | existing deterministic tree + rules | `gpt-4o-mini-tts` |
| `sarvam_cloud` | `saaras:v3` | `sarvam-30b` | existing deterministic tree + rules | `bulbul:v2` |

“Interpretation” means mapping the patient's spoken answer to the current node,
asking a constrained clarification, and producing patient-facing wording. It does
not mean selecting the next question, routing a department, detecting a red flag,
changing queue priority, or writing a clinical fact. Those remain outputs of
`Walk`, `ToolDispatcher`, and the existing rules engine.

The selected profile is snapshotted when an intake starts. Publishing a different
profile affects new intakes only; an in-progress intake never changes vendor halfway
through. Provider failure falls back to the deterministic tap/text path. It does not
silently send patient audio to the other cloud vendor.

### 1.2 Current vendor-model caveat

The exact user-requested model names remain the configured defaults. At plan time:

- OpenAI lists `gpt-4o-mini-transcribe` and `gpt-5.6-luna` as current models.
- OpenAI lists `gpt-4o-mini-tts` as deprecated.
- Sarvam recommends `saaras:v3` and `sarvam-30b`.
- Sarvam still exposes `bulbul:v2`, but labels it legacy and recommends `bulbul:v3`.

Therefore every model name is configuration, not a constant. `SESSION-VOICE1` must
run a real availability smoke before enabling a cloud profile. If a requested legacy
model is unavailable, stop at the gate and record the vendor response; do not silently
substitute a different voice. A later operator-approved configuration may select the
current replacement without another code change.

Primary references:

- [OpenAI gpt-4o-mini-transcribe](https://developers.openai.com/api/docs/models/gpt-4o-mini-transcribe)
- [OpenAI gpt-5.6-luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [OpenAI gpt-4o-mini-tts](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts)
- [Sarvam Saaras](https://docs.sarvam.ai/api/getting-started/models/saaras)
- [Sarvam-30B](https://docs.sarvam.ai/api/getting-started/models/sarvam-30b)
- [Sarvam TTS API](https://docs.sarvam.ai/api-reference/text-to-speech/convert)

### 1.3 AWS is a separate application environment

AWS is a warm, GPU-free application environment using `openai_cloud` or
`sarvam_cloud`. It has its own PostgreSQL and Redis containers and never reaches
into the Omen database over the public internet.

Omen and AWS are not simultaneous database writers and this build does not pretend
to provide zero-data-loss automatic failover. Promotion is deliberate:

1. stop new writes on the old primary when reachable;
2. take or select the newest encrypted database backup;
3. restore and verify it on the target;
4. enable the target writer;
5. switch the stable DNS/environment pairing;
6. keep the old environment read-only until failback is explicitly approved.

The acceptance target is a documented recovery point objective of 15 minutes and a
recovery time objective of 30 minutes. If the measured drill misses either target,
the session records the actual result instead of claiming success.

### 1.4 Android pairs to an API, never a database

The release APK contains two approved HTTPS environments—Omen and AWS—and stores the
operator's selection in app preferences. It talks only to the selected HTTP API.
PostgreSQL credentials, OpenAI keys, Sarvam keys, JWT signing secrets, and Android
signing material never enter the APK.

One signed APK is served from `/downloads/opd-patient-latest.apk` together with a
version manifest and SHA-256 checksum. Updates use the same Android application ID
and signing certificate.

## 2. Session order and branch flow

Do not run these sessions in parallel.

1. Finish doc 15's Omen/tablet/printer physical acceptance and record the accepted
   commit.
2. Create `kiosk-voice-profiles` from that exact commit and execute
   `sessions/SESSION-VOICE1-PLAN.md`.
3. Create `aws-gpu-free-standby` from accepted `kiosk-voice-profiles` and execute
   `sessions/SESSION-CLOUD1-PLAN.md`.
4. Create `android-pairing-release` from accepted `aws-gpu-free-standby` and execute
   `sessions/SESSION-ANDROID1-PLAN.md`.
5. Merge only after the final combined Omen/AWS/APK demonstration matrix passes.

Each session follows `docs/07-SESSION-PROTOCOL.md`, writes its permanent session log,
updates `HANDOFF.md`, and leaves no real secret or signing file in Git.

## 3. Combined release gate

The work is complete only when all of these are demonstrated:

- A new kiosk intake can start with any of the three profiles.
- The active profile and exact model names are visible to an administrator and in
  non-PHI operational logs/usage events.
- Switching profiles changes new intakes only.
- Disabling or breaking a provider returns the kiosk to usable deterministic taps.
- Omen deploy and rollback are performed from committed artifacts.
- AWS boots without an NVIDIA runtime or model container, pulls commit-addressed
  images, terminates TLS in nginx, and passes backup/restore/promotion drills.
- One signed APK installs on the target tablet, upgrades over itself, downloads over
  HTTPS, pairs to Omen, pairs to AWS, and completes online plus offline/re-sync flows.
- The app never exposes or accepts a database URL.
- Hindi and English voice paths pass real-device acceptance; Marathi and Telugu keep
  the current tap/text experience unless the selected vendor/model is explicitly
  accepted for those languages.

