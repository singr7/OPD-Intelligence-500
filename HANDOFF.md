# HANDOFF — after Session S12 (WhatsApp bot)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`.
>
> **⚠️ CI is off (2026-07-23, operator).** `.github/workflows/ci.yml` is intact but
> its `push`/`pull_request` triggers are commented out (they were burning free
> Actions minutes). Run it by hand: `gh workflow run ci.yml`. **`make test` locally
> is the only gate right now.**
>
> **🚩 Adaptive intake (S-ADAPT) is on `main` but NEVER PROVEN with its flags on
> (2026-07-23).** `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the
> defaults, so `main` behaves as the pure-tap kiosk until a human flips them. The
> on-box validation is unclaimed work — see **"Owed on omen"** below. This is
> unchanged by S12.

**Repo state:** **`main`** — S12 built straight on it (three feature commits +
close). `make test` green: backend **816** (781 → 816), voice-gw 1, web
typecheck+lint clean, 48 conformance. **No migration in S12** — the WhatsApp
conversation state lives in Redis (like `SessionState`), not the DB. Head is still
S-ADAPT's `a1b2c3d4e5f6`. Postgres on host port **5433**; voice-gw on 8090.
**Start S13 from `main`.**

⚠️ `make lint` is still **failing on 11 pre-existing unformatted files** (none of
them S12's — the new code is `ruff format`-clean). It is not in `make test`, so it
has been red a while. Worth one `ruff format .` commit before it grows.

**One paragraph:** S12 gave the intake engine its **second channel**. Where the
kiosk (S6) was the engine's first HTTP surface, `app/whatsapp/bot.py` is its
sibling — the same `IntakeEngine`, the same four-tool contract, the same tree
walker and red-flag rules, driven one Meta webhook message at a time instead of
one browser screen. The flow mirrors the kiosk's (language → chief complaint,
typed or a voice note → STT → chooser → the tree as buttons/lists → read-back →
confirm → token) and ends the same way: a Visit + QueueEntry on `Channel.WHATSAPP`.
The one thing WhatsApp forces that no other channel has is the **24-hour session
window** (doc 03 §1d): free text is allowed only within 24h of the patient's last
message, so the window state and the wa_id → session mapping live in a
`Conversation` (Redis, like `SessionState`). Two patient-initiated commands
short-circuit at any idle point — "token status" and "resend prescription" — both
free-text, because a patient who just messaged is in the window by definition. The
out-of-window path is proactive-only: S11's Rx delivery now sends the registered
`prescription_ready` **template** when the window is closed, inviting a reply that
reopens it. The bot never sends — `handle` returns messages, the webhook sends and
commits — so it is a pure function the tests drive without a live Meta.

## Next session — S13 (Multilingual completion: mr, te + language QA harness)
- Objective: mr + te text for **all trees**, UI strings, summaries and the WhatsApp
  templates; a language QA harness (round-trip STT/TTS smoke per language, glossary
  consistency check) that runs in CI; a font/line-height audit per doc 04 §4.
- **Load:** doc 03 §1, the tree bank (`seeds/trees/`), doc 04 §4.
- **S12 left mr/te template bodies unwritten** — `app/whatsapp/templates.py` carries
  en + hi only, and `get_template` raises (deliberately, not a silent English
  fallback) for a missing language. S13 must add mr + te bodies to every template,
  or the harness will flag them. Same for the kiosk/board English-only strings
  already logged (S13 in STATE.md → Stubs).
- **Start from `main`.**
- Exact first commands:
```
make dev && make migrate && make seed && make test    # expect 816 backend green
```

## Watch out for (S12 fragile edges)
- **The bot never sends and must not start to.** `handle` returns `BotReply`; the
  webhook does the sending and the single `session.commit()`. Moving a send into the
  bot breaks the "pure function of (state, inbound)" property every bot test relies on.
- **Commands reply free-text on purpose.** A patient who messaged is in the 24h
  window; do not "consistently" route `_token_status`/`_resend_rx` through a template.
  The template path is proactive-only (`prescription.deliver`, `window_is_open`).
- **`get_template` raising on a missing language is the feature, not a bug** — an
  out-of-window message a patient cannot read is worse than none. S13 fills mr/te
  rather than adding an English fallback.
- **Multi-select over WhatsApp wraps one tap into a one-element list** (`_parse_answer`,
  `multi`/`body_map`). A list reply is single-select too; true multi-pick is backlog.
- **Message-id dedup is conversation-scoped** (`Conversation.last_message_id`), not a
  global seen-set. It catches Meta's *exact* redelivery of the last message, which is
  the real retry case; it is not a general idempotency ledger.

## Decisions needed from the human
- **Whoever next has the box: "Owed on omen" is still unclaimed** (below) — the only
  remaining reason to doubt anything in `main`. S12 added nothing to it (WhatsApp runs
  on the provider fakes; a live Meta number is a separate account action).
- When the GPU box work resumes, S-OSS.1 is unblocked and unchanged.

## Owed on omen (unchanged from S11 — do before adaptive faces a patient)
- **Adaptive on:** flags to `1`, mark 1–2 live-tree nodes `adaptive: true`, re-seed,
  `docker compose up -d --build api web` (**web rebuild required** — the flag is a
  build arg). Provoke a vague answer (one clarify then taps), a volunteered fact
  (later node pre-filled), an unmappable answer (falls to taps). Then read
  `app/intake/adaptive_report.py` and tune node wording. **Rollback is the flags back
  to `0` + a web rebuild.**
- **Doctor console + consult note on-box** (`/doctor`, `+915550001001`) — never run on
  omen; the real-Qwen3 dictation + `_was_said` pass is still owed.
- `make eval-dictation` wants the same session.

## Backlog additions (S12)
- **WhatsApp templates lack mr/te bodies** — `app/whatsapp/templates.py` is en+hi;
  S13 completes them (the registry raises for a missing language by design).
- **No live Meta send has happened** — webhook + bot are proven on the fake and a
  simulated payload. First live send + template approval in the WhatsApp Manager
  needs a human on a real number (mirrors every other channel's first-send caveat).
- **Multi-select over WhatsApp is single-pick** — a `multi` node wraps one tap; real
  multi-select (Meta list replies are single-select) is a UX decision for later.
- **Voice notes answer only the chief complaint** — a voice note on a tree question
  falls back to buttons; spoken tree answers want the adaptive interpreter (doc 11),
  which is flag-gated and off. Pairs with the S-ADAPT omen work.
- **Conversation-level WhatsApp billing is still not attributed** — the messaging
  provider still meters `messages=1` per send (over-counts vs Meta's per-conversation
  billing). The window state now exists to fix it; deferred to S18's invoice reconcile
  (`app/providers/messaging.py` docstring).
- Carried, unchanged: `make lint` red on 11 pre-existing files (one `ruff format .`
  clears it); RxPanel silent read error (S11); server-side PDF (S19/S21); adaptive-tree
  on-box scenario testing; the S11/S10 backlog items in the prior handoff's list.

## Run the WhatsApp bot locally (no live Meta)
It has no browser surface — drive it by POSTing a Meta-shaped webhook payload:
```
# with a local api on :8123 (see the S10/S11 demo commands, unchanged)
curl -X POST localhost:8123/whatsapp/webhook -H 'content-type: application/json' -d '{
  "entry":[{"changes":[{"value":{
    "contacts":[{"wa_id":"919812300001","profile":{"name":"Test"}}],
    "messages":[{"from":"919812300001","id":"m1","type":"text","text":{"body":"hi"}}]}}]}]}'
```
With `MESSAGING_PROVIDER=fake` the reply is recorded on the fake, not sent. Signature
checking is skipped when `META_APP_SECRET` is empty. The bot flow is fully covered by
`tests/test_whatsapp_bot.py` (a buttons intake to a token, a voice-note complaint,
token-status, Rx-resend) and `tests/test_whatsapp_webhook.py`.
