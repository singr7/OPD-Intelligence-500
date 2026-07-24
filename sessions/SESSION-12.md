# SESSION-12 — WhatsApp bot

**Date:** 2026-07-24 · **Scope ref:** docs/06-BUILD-PLAN.md → S12

## Acceptance criteria checklist
- [x] **Meta webhook + session windows.** `app/routes/whatsapp.py`: GET echoes the
  subscription challenge on a matching verify token; POST verifies the app-secret
  signature, parses Meta's envelope, always 200s. The 24h window lives on the
  `Conversation` (`last_inbound_at`), consulted before every proactive free-text send.
- [x] **Intake via buttons and voice notes.** The bot walks the same tree as the
  kiosk, rendered as reply buttons (≤3 options) or a list (4–5); the chief complaint
  is typed or a voice note (downloaded → STT). A full intake completes to a token.
- [x] **Token status.** "status"/"token" (en+hi) → token number + people ahead.
- [x] **Rx re-send.** "prescription"/"dawai" → the latest prescription as text.
- [x] **Template registry + seed templates.** `app/whatsapp/templates.py` — the
  "pre-approved list in repo", bilingual, variable counts validated at import and
  before the wire. Seeded: `intake_invite`, `token_status`, `prescription_ready`.
- [x] **Voice-note replies (TTS).** Each reply's text is synthesized and attached as
  an audio message, behind `WHATSAPP_VOICE_NOTES` (default off), best-effort.
- [x] **Out-of-window template path** (the AC's third leg): S11 Rx delivery now sends
  the `prescription_ready` template when the window is closed.

**AC caveat:** the build-plan AC asks for an e2e "via Meta test number". There is no
live Meta number here, so the flow is proven end-to-end against the provider fake +
a simulated webhook payload — the same stance every channel takes (STATE.md → Stubs).
The first live send needs a human on a real handset (registered in Stubs & fakes).

## What was built
- **`app/whatsapp/` package** — `templates.py` (registry), `conversation.py`
  (`Conversation` + store, the 24h window, `window_is_open`), `render.py`
  (Node → buttons/list/prompt), `bot.py` (`WhatsAppBot`, the FSM + commands).
- **`app/routes/whatsapp.py`** — the GET/POST webhook; signature + verify-token auth.
- **Messaging provider** — `MetaWhatsAppProvider.upload_media` (voice-note replies go
  by uploaded id) + an interactive-list payload; the fake records uploads.
- **`app/routes/prescription.py`** — `deliver` is now window-aware (template out of window).
- **Config** — `meta_verify_token`, `meta_app_secret`, `whatsapp_voice_notes`; `.env.example`.
- **Wiring** — router + `wa_conversation_store` on the lifespan; conftest sets the store.

## Decisions made
- **WhatsApp reuses the kiosk's V3 walk, not a new dialogue.** The questions are taps
  (buttons); the one model call is the chief-complaint classifier. A voice note is
  transcribed to a complaint, not a spoken turn — that is V1/V2 (telephony, S14).
- **The bot never sends; it returns messages.** The webhook does the sending and the
  single commit, so `handle` is a pure function of (state, inbound) the tests drive
  without a live Meta. The window state, not the branch, is what makes the channel safe.
- **Commands reply free-text, never a template.** A patient who just messaged is in
  the window by definition; the template path is only for *proactive* sends.
- **Message-id dedup on the conversation** (`last_message_id`) drops Meta's exact
  redeliveries so a re-tap never answers a question twice.

## Deviations from spec
- None material. doc 03 §1d also lists book/reschedule/cancel over WhatsApp — those are
  S15 (appointments), not S12; §1d's intake/status/Rx-resend legs are the S12 ones.

## Tests & evidence
- `make test`: **816** backend (781 → 816), voice-gw 1, web typecheck+lint clean, 48 conformance.
- New tests: `test_whatsapp_foundation.py` (18), `test_whatsapp_bot.py` (8),
  `test_whatsapp_webhook.py` (8); +2 provider (list, media upload), +2 prescription
  (in/out-of-window delivery), +1 provider audio-by-id.
- Screenshots: none — S12 adds no web UI; WhatsApp renders in Meta's own client.

## Known gaps / stubs introduced (mirrored to STATE.md)
- **No live Meta number has ever answered.** Webhook + bot proven against the fake and
  a simulated payload; the first real send needs a human on a handset.
- **No template has been approved at Meta.** The repo registry guarantees we never send
  a shape/arg-count Meta hasn't seen from *us*; approval is a WhatsApp Manager action.
- **Multi-select over WhatsApp picks one option.** A `multi`/`body_map` node wraps a
  single tap into a one-element list (a list reply is single-select too). Multi-pick is backlog.
- **Voice notes answer only the chief complaint, not tree questions.** A voice note on a
  tap question falls back to the buttons — spoken tree answers need the adaptive
  interpreter (doc 11), which is flag-gated and off by default.
- **The conversation store is Redis/in-memory, single-process** — same multi-replica
  caveat as the session store and the queue hub.

## Commits
- 440ae34 — S 12: WhatsApp foundation — template registry, media upload, window, node render
- 40fa08a — S 12: the WhatsApp bot and its Meta webhook — the engine's second channel
- (this) — S 12: prescription delivery is window-aware — template out of the 24h window
- (close) — S 12: session close
