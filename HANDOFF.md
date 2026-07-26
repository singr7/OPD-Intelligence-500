# HANDOFF — after Session S18-late (Admin console remainder)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`.
>
> **⚠️ CI is off (2026-07-23, operator).** `.github/workflows/ci.yml` is intact but
> its `push`/`pull_request` triggers are commented out. Run it by hand:
> `gh workflow run ci.yml`. **`make test` locally is the only gate** — plus `make lang-qa`.
>
> **🚩 Adaptive intake (S-ADAPT) is on `main` but NEVER PROVEN with its flags on.**
> `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the defaults. Unchanged by S18L.

**Repo state:** **`main`**, last commit `S 18L: session close`. `make test` green: backend
**1082** (was 1071), voice-gw **22**, web typecheck+lint+**48** conformance, **Android 6 JVM**.
`make lang-qa` clean across [en,hi,mr,te]. **Migration `cb011d62f829`** (`protocol_banks` +
`checkins.grading_rules`) — run `make migrate` before anything. Postgres on host port **5433**;
voice-gw on **8090**. **The sequence is now doc 12's go-live track: next is S-GL.1** (S19 is
superseded — see below).

⚠️ `make lint` still fails on the **same pre-existing unformatted files** (none of S18L's).
There is also one pre-existing unused import (`tests/test_tree_bank.py:31`, `Lang`). Not in
`make test`. Still worth one `ruff format . && ruff check --fix .` commit.

**One paragraph:** S18L finished the console. The headline S18 criterion — "a non-technical
user edits a tree, publishes, sees it live with no deploy" — was previously true only of
someone willing to edit JSON; there is now a **visual editor** that draws the tree as a spine
in ask order, branches indented under the option that leads to them, red-flag stations stamped,
with the words a patient reads editable in all four languages and a try-it panel that dry-walks
the edit through the real walker. It edits **words, not shape**: adding or rewiring a question
stays in the seed file and a pull request, where the validator's unreachable-question and cycle
checks get read by a person. The **check-in protocol bank moved into a table** the way the trees
did — versioned, draft/publish/rollback, `parse()` still the only constructor, the seed file
still the floor — which forced the one new clinical invariant of the session: because a grade is
recomputed on every answer, an editable bank would let an afternoon's publish re-decide
Tuesday's answers, so `Checkin.grading_rules` now freezes the rules beside the questions.
Doc 03 §11's **tier-mix what-if** exists and is measured rather than modelled; when a tier has
never run on a channel it says so instead of pricing phone V2 off kiosk intakes.

## Next session — S-GL.1 (the channel switchboard) — **Phase 1, go-live**

**The mainline sequence is redirected.** After a planning pass on 2026-07-26 the operator chose
a **kiosk-first go-live**: kiosk open on the box, WhatsApp/telephony/app intake dark. Doc 06's
S19 is superseded for the pilot by **S-GL.6** (AWS becomes the GPU-free disaster-recovery
profile, not the primary deployment). Full reasoning, evidence and every session:
**[doc 12](docs/12-GO-LIVE-PLAN.md)**; the phase table is doc 06's tail.

- **Objective:** doc 12 §7 S-GL.1 — per-channel enable/disable + tier ladder + GPU seat share,
  editable from the admin console; **runtime provider credentials** (set-and-test for Meta and
  Exotel, no restart); campaign channel-mix weights. Without this there is no honest "off",
  and today a patient who messages the hospital's WhatsApp number reaches a bot that fails per
  message.
- **Load:** doc 12 (§1, §4, §7), doc 02 §9, doc 08 §3/§5, `config/tiers.yaml`, `app/tiers.py`,
  `app/config.py`, `app/providers/registry.py`, `sessions/SESSION-18L.md`.
- **AC:** with every channel but kiosk disabled, a WhatsApp inbound and a phone call are both
  refused politely, nothing 500s, the kiosk is untouched; entering + testing Meta credentials
  in the console makes the bot answer **with no restart**; a test fills the phone seat share
  and a kiosk session is still admitted; campaign dry-run at 30/70 produces the documented
  split.
- **What S18L gives it:** the versioned draft→publish→resolve pattern now exists **twice**
  (`app/trees/store.py`, `app/checkins/store.py`) with the file as the floor and a validator as
  the only constructor. Channel config is the third instance — **reuse it, do not invent a
  third shape**. `app/admin.py` + the console's tab structure are the template for the UI.
- **One design warning:** provider credentials are secrets, and nothing in this repo stores a
  secret in the database yet. They must be **write-only over the wire** (set and test, never
  read back) and encrypted at rest, and `.env` stays the floor exactly as the seed files do.
  If that cannot be done well in the session, ship enable/disable + ladder + seats and leave
  credentials in `.env` — a half-secure secret store is worse than an honest env var.

- **Then:** S-GL.2 (staff onboarding + roster import — a doctor cannot be added without editing
  a seed file today) → S-GL.3 (the on-box reality pass; builds almost nothing, and every item
  in it is something no human has looked at). That is the go-live cut.

- **Start from `main`.** First commands:
```
make dev && make migrate && make seed && make slots   # 12 slot templates -> ~800 slots
make test                                             # 1082 backend / 22 voice-gw / 48 web / 6 android
make lang-qa                                          # expect clean across [en,hi,mr,te]
make checkin-demo                                     # a plan, a red D+2, a pending D+7
```
To see the console (it needs a live api with S18L code — the dockerised image may be older):
```
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  OTP_DEBUG_ECHO=true OTP_RESEND_COOLDOWN_SECONDS=0 \
  JWT_SECRET=local-dev-secret-padded-to-32-chars-plus .venv/bin/python -m uvicorn app.main:app --port 8123
cd web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8123 npx next dev -p 3210
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 npm run e2e:admin
```
Admin login: `+915550000001` (seeded Priya Sharma); the OTP is echoed locally.

## Watch out for (S18L fragile edges)

- **Publishing a protocol bank is a clinical act with no confirmation dialog.** One tap in the
  console changes what every subsequent signed note commits a patient to. Nothing is published
  today (the seed writes v1 as a **draft**, so `resolve_bank` serves the file) — the first
  publish should be an oncologist's, not a developer's.
- **`Checkin.grading_rules` is NULL on pre-S18L rows and those grade against the live bank.**
  That is the intended fallback, but it means a bank published now *would* re-grade an
  in-flight check-in created before this migration. There are none in production yet; if that
  changes before the first publish, backfill the column from the bank first.
- **The tree editor cannot add, delete or rewire a node** — deliberate, and said on the screen.
  Do not "finish" it by adding structural editing without re-reading why: the validator's
  reachability and cycle checks are the review, and a builder that orphans a question silently
  is worse than a diff.
- **`web/e2e/admin.spec.ts` really publishes.** Each run saves and publishes a new version of
  `general_medicine_routing` on whatever database it points at. Fine on a dev box; never point
  it at the pilot.
- **The severity select in the tree editor must stay in step with `Priority`.** It offers
  exactly `urgent` and `semi` because those are the only severities a red flag may carry;
  offering a value the schema lacks silently rewrites a flag's severity on the next save.

## Decisions needed from the human

- **The check-in protocol bank still needs an oncologist** — six regimen families, seven
  question sets, 41 grading rules, all model-drafted, none reviewed. It now has a **publish
  button**, which sharpens this: the review has somewhere to land. *(carried, sharpened)*
- **Is the LLM assist allowed to escalate free text to red?** S18L did not change S17's answer
  (no — it may only raise green→amber). *(carried)*
- **Answered 2026-07-26:** S19 is neither — AWS becomes the **GPU-free DR profile** (S-GL.6)
  and the pilot goes live kiosk-first on the box. Doc 12 records the reasoning.
- **Has an oncologist reviewed the *tree bank*?** Carried since S4 and now sharper than the
  protocol-bank question: a kiosk-first pilot makes the trees the **only** clinical content in
  front of a patient. Same model-drafted, unreviewed status. *(new emphasis)*
- **Are check-ins on or off for day one?** With WhatsApp dark the delivery ladder has no first
  rung and falls to an SMS nudge. Either hold plans in draft (they need a doctor's tap anyway)
  or set `CHECKINS_ENABLED=false` until WhatsApp is live. *(new)*
- **A live Exotel number + creds** — still blocking three proofs: the S14 intake bridge, the
  S15 receptionist/campaign, and S17's voice rung. *(carried)*
- **Who is the coordinator on `COORDINATOR_PHONE`** — they get every red check-in alert.
  *(carried)*
- **Does the app go on the Play Store, or sideload at the OPD desk?** *(carried)*
- **mr/te still need a native + clinical review before a patient reads them** (S21). *(carried)*

## Owed on omen (before the pilot's continuity loop faces real use)

- **The admin console on a screen, on the box** — S18L is the first session to actually render
  it (6 screenshots, `web/screenshots/s18l/`), but only against a local dev stack. The tabs
  S18E built (cost, operations, price book, templates) still have never been looked at with
  real data behind them. *(carried, half-paid)*
- **One check-in, end to end, on the box** — `make checkin-demo`, then
  `python -m app.worker opd.checkins.send`, and answer it from a real WhatsApp thread. Nothing
  in the check-in engine has ever reached a handset. *(carried)*
- **A real Qwen3 personalisation** — on a fake stack `checkin_personalize` has no canned reply,
  so every demo message is the plain fallback. *(carried)*
- **The app on a real handset** *(carried)*; **live Exotel smoke, both applets** *(carried)*;
  **the campaign for one evening on real numbers** *(carried)*; **phone-on-GPU contention**
  *(carried)*; **Telugu kiosk render** *(carried)*; **adaptive on** *(carried)*; **doctor
  console + consult note on-box** *(carried)*.

## Backlog additions (S18L)

- **The four S18-late items still unbuilt**, in the order they are worth doing:
  **slot-template editor** (the last deferred placeholder; `SlotTemplate` has existed since
  S15, so this is CRUD + a "regenerate slots" button); **editable message-template registry**
  (needs a DB overlay over the code-defined registry, and a story for what happens when the
  repo and Meta disagree); **node-level abandonment report** (needs per-node answer timestamps
  the intake path does not emit — the telemetry is the work, not the report); **voice-pack
  upload** (still blocked on S7's pack format).
- **A per-rung protocol form**, now that the bank is a table — the reading view already shows
  the structure; editing a day offset or a threshold in a field beats editing the document.
- **Backfill `Checkin.grading_rules`** for any rows created before `cb011d62f829`, if the
  pilot creates check-ins before the first bank publish.
- **A confirmation step on publish** (both trees and banks) naming what changes and who will
  be affected — cheap, and this is the one console button with clinical reach.
- Carried, unchanged: `make lint` red on pre-existing files; a voice-gw check-in applet
  (`WS /exotel/checkin`); check-ins in the Android app; merging a doublet's question sets; a
  canned `checkin_personalize`/`checkin_triage` fake reply; a real task table for the
  "immediate call task"; the app's chemo calendar counting real cycles; `Checkin` on the
  patient timeline; mr/te unreviewed (S21); report photos in the care file; booking from the
  app; Play Store signing; `TokenStore` at rest; appointment waitlist; language detection from
  the caller's greeting; campaign observability; V1 continuous caller-audio streaming; surface
  STT confidence instead of the energy proxy; tune VAD/DTMF thresholds on real Alwar telephony.
