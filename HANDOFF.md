# HANDOFF — after SESSION-VOICE1

**Repo state:** branch `kiosk-voice-profiles`; final close commit is the branch tip.
`make test`, language QA, production web build, migration, container preflight,
kiosk E2E, and Channels E2E are green. The pre-existing local
`web/tsconfig.tsbuildinfo` modification remains intentionally uncommitted.

Selectable kiosk voice profiles are implemented and safety-gated. Each intake
snapshots exactly one local/OpenAI/Sarvam STT+LLM+TTS trio; no profile crosses cloud
vendors, and provider exhaustion preserves the deterministic walk and returns taps.
Cloud credentials are encrypted/write-only, shared once per vendor, independently
tested per component, visible only as status/model metadata, and required before an
enabled cloud profile can publish.

## Immediate release gate

Do not start `SESSION-CLOUD1` until these external acceptance items are recorded:

1. Supply real OpenAI and Sarvam keys outside Git (console or secret environment).
2. In Channels, test STT, LLM, and TTS for each vendor.
3. Run short Hindi and English kiosk turns on both cloud profiles; record date,
   region, latency, reported model, and exact legacy/deprecation responses.
4. Deploy the committed branch to the Omen with targeted Compose replacement only.
5. Demonstrate local → OpenAI → Sarvam → local new-intake switching.
6. Demonstrate a broken cloud component returning the same unanswered node to taps.
7. Execute and record Omen rollback with deployed commit SHA.

Neither cloud key was configured locally on 2026-07-28, and this session had no Omen
access, so none of that evidence was fabricated.

## Next session

Once the release gate above is accepted, execute
`sessions/SESSION-CLOUD1-PLAN.md` on branch `aws-gpu-free-standby` created from the
accepted `kiosk-voice-profiles` commit.

Start with:

```bash
git status --short
git log -1 --oneline
make dev && make test
```

Then follow `docs/07-SESSION-PROTOCOL.md` and the CLOUD1 plan exactly.

## Watch out for

- `gpt-4o-mini-tts` is deprecated and `bulbul:v2` is legacy. Keep the requested
  configured names unless an operator explicitly approves replacements.
- Publishing an enabled cloud profile intentionally fails until all three latest
  component tests pass. Rotating its key clears that evidence.
- Stored cloud credentials are `vendor:openai` / `vendor:sarvam`, not three copies.
- The profile snapshot freezes models, not secrets; a credential rotation rebuilds
  the same snapshotted components with the new key.
- `usage_events.voice_profile` requires migration `a4d5e6f7b801`.

## Decisions needed from the human

- Provide/authorize the real keys and Omen access for the remaining VOICE1 release
  gate, then explicitly accept the branch as CLOUD1's predecessor.

## Backlog additions

- Reconcile OpenAI audio and local amortized price-book estimates against invoices
  and measured Omen utilization.
- If either requested legacy TTS model is rejected, record the exact response and
  approve a configured replacement; do not silently substitute one.
