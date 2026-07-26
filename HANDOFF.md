# HANDOFF — after Session S-GL.1 (the switchboard)

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
> `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the defaults. Unchanged by S-GL.1.

**Repo state:** **`main`**, last commit `S GL.1: the closed notice joins the language harness`.
`make test` green: backend **1156** (was 1082), voice-gw **25** (was 22), web typecheck+lint+**48**
conformance, **Android 6 JVM**. `make lang-qa` clean across [en,hi,mr,te]. **`make lint` is green
for the first time** — the carried "worth one ruff format commit" item is closed.
**Migration `2c978d44c900`** (`channel_configs` + `provider_secrets`) — run `make migrate` before
anything. **New backend dependency: `cryptography`** — `pip install -e '.[dev]'` in
`backend/.venv`, and the box's api/voice-gw images must be rebuilt. Postgres on host port **5433**;
voice-gw on **8090**. **Next is S-GL.2** (doc 12 §7).

**One paragraph:** S-GL.1 gave the system an honest "off". Before it, a patient who messaged the
hospital's WhatsApp number reached a bot that tried and failed per message, and going live was
therefore conditional on Meta and Exotel existing. There is now a **channel document** —
versioned, published and resolved exactly like the trees and the protocol bank, with
`config/tiers.yaml` as the floor — carrying per-channel `enabled`, `ladder`, `max_concurrent` and
the campaign mix. Every entry point is gated on **start** and never mid-flow, so a closed channel
takes no new patients but abandons nobody mid-sentence: the kiosk and app 503 with the line in her
own language, WhatsApp answers once per thread and runs no bot logic, and both voice-gw applets
answer, speak, and hang up without taking consent for an intake that will not happen. The switch
is deliberately **not** the whole story — whether a vendor is actually provisioned is computed
from settings and cannot be asserted from a console, so a hospital that forgets to close WhatsApp
still has a closed WhatsApp. Vendor credentials can now be entered in the console, encrypted, and
are live within about ten seconds with no restart; they are write-only over the wire, restricted
to their own vendor's fields, and `.env` stays the floor. Rendering the tab found a **false
green** no test would have: the box runs `ENV=local`, so a `fake` provider read as "configured".

## Next session — S-GL.2 (staff onboarding + roster) — **Phase 1, go-live**

- **Objective:** doc 12 §7 S-GL.2 — an admin **People** tab (create/deactivate a user with a role,
  create a doctor against a department, invite by phone — the OTP login already exists, so an
  invite is just "this number can now sign in"), and the **slot-template editor**: the weekly
  clinic grid per doctor, a CSV/XLSX roster import with a **dry run that shows what would be
  created before it writes**, and a "generate slots" button over `app.scheduling.generate_slots`.
  Deactivation must not orphan booked appointments — surface them and make the admin decide.
- **Load:** doc 12 §6/§7, doc 03 §2/§10, `app/scheduling.py`, `app/models/org.py`,
  `seeds/{doctors,slot_templates}.json`, `sessions/SESSION-GL1.md`.
- **AC:** a new doctor is onboarded, given a Tuesday clinic by CSV import, has slots generated,
  and appears in the receptionist's inventory and the doctor console — entirely from the console,
  on a box, with no seed run and no deploy; the import dry-run refuses a row naming an unknown
  doctor and **says which row**.
- **What S-GL.1 gives it:** the versioned draft→publish→resolve pattern now exists **three**
  times (`app/trees/store.py`, `app/checkins/store.py`, `app/channels/store.py`) — but note that
  people and slot templates are probably **not** a fourth instance of it: a doctor is not
  authored content with a review cycle, and forcing them into draft/publish would make hiring
  somebody a two-step act for no safety gained. The tab structure, the `useLoad` hook and the
  write-only form pattern in `ChannelsTab.tsx` are the reusable parts. `app/routes/admin.py` now
  has an example of an explicit `await session.commit()` and **why it is needed** (see below).
- **One design warning:** the slot-template editor writes to inventory that already has
  appointments booked against it. Regenerating slots after an edit must not silently orphan or
  double-book — S15's `generate_slots` is idempotent but was never asked to run against a
  *changed* template. Decide what happens to a booked slot whose template moved before writing
  the button, not after.

- **Then:** S-GL.3 (the on-box reality pass — builds almost nothing, and every item in it is
  something no human has looked at). That is the go-live cut.

- **Start from `main`.** First commands:
```
cd backend && .venv/bin/pip install -e '.[dev]'   # cryptography is new in S-GL.1
make dev && make migrate && make seed && make slots
make test        # 1156 backend / 25 voice-gw / 48 web / 6 android
make lang-qa     # expect clean across [en,hi,mr,te]
make lint        # green — keep it that way
```
To see the console (it needs a live api with S-GL.1 code — the dockerised image may be older):
```
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  OTP_DEBUG_ECHO=true OTP_RESEND_COOLDOWN_SECONDS=0 \
  JWT_SECRET=local-dev-secret-padded-to-32-chars-plus .venv/bin/python -m uvicorn app.main:app --port 8123
cd web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8123 npx next dev -p 3210
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 npm run e2e:channels
```
Admin login: `+915550000001` (seeded Priya Sharma); the OTP is echoed locally.

## Watch out for (S-GL.1 fragile edges)

- **`web/e2e/channels.spec.ts` really publishes**, like `admin.spec.ts`. Each run leaves published
  channel documents on whatever database it points at; its last test reopens everything. Fine on
  a dev box; **never point it at the pilot** — a failed run mid-suite can leave channels shut.
- **A `yield` dependency's cleanup runs *after* the response is sent.** The channel draft/publish
  routes commit explicitly because of it; the e2e caught a client publishing a version before its
  own draft had landed. **The tree and protocol-bank draft routes still rely on the dependency**
  and have the same latent race — they have not been hit because their console flow pauses for a
  human between save and publish. Worth fixing when either is next touched.
- **The credential encryption key is derived from `JWT_SECRET` unless `SECRETS_KEY` is set.**
  Rotating the JWT secret makes every stored credential unreadable — honestly (each row records
  which key wrote it, and the console says so), but they must then be entered again. **Set
  `SECRETS_KEY` on the box before entering real Meta/Exotel credentials**; generate one with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- **"No restart" is a 10-second TTL, not an invalidation protocol.** Three processes read the
  overlay independently. Deliberate — a missed invalidation message is a vendor that silently
  stays off — but do not describe it to an operator as instant.
- **The seat share is configured, capped and tested, but not wired into the live voice path.**
  Routing an over-share call down its ladder is still S-OSS.2. The console shows the share; it
  does not yet bite on a real call.
- **A `fake` provider counts as ready on a local box** (which the pilot box is) and says so with
  a note. If that note is ever dropped from the tab, the tab starts lying on the box.

## Decisions needed from the human

- **Has an oncologist reviewed the *tree bank*?** A kiosk-first pilot makes the trees the **only**
  clinical content in front of a patient, and they are model-drafted and unreviewed. This is now
  the most load-bearing open question in the project. *(carried, sharpest)*
- **The check-in protocol bank still needs an oncologist** — six regimen families, seven question
  sets, 41 grading rules, all model-drafted, none reviewed. It has a publish button. *(carried)*
- **Are check-ins on or off for day one?** With WhatsApp dark the delivery ladder has no first
  rung and falls to SMS. Either hold plans in draft or set `CHECKINS_ENABLED=false`. S-GL.1 does
  not decide this — the channel switch closes WhatsApp *intake*, and the check-in ladder is a
  separate path. *(carried, sharpened)*
- **Who is the coordinator on `COORDINATOR_PHONE`** — they get every red check-in alert. *(carried)*
- **Is the LLM assist allowed to escalate free text to red?** (S17's answer: no.) *(carried)*
- **A live Exotel number + creds** — still blocking three proofs; S-GL.1 removes the *deploy* from
  turning it on but not the account. *(carried)*
- **Does the app go on the Play Store, or sideload at the OPD desk?** *(carried)*
- **mr/te still need a native + clinical review before a patient reads them** (S21) — now
  including the four channel-closed notices. *(carried)*

## Owed on omen (before the pilot's continuity loop faces real use)

- **The admin console on a screen, on the box** — now with a **Channels** tab that is the first
  thing an operator sees, and the one place a wrong answer means a patient cannot register.
  Everything in `web/screenshots/sgl1/` is a local dev stack. *(carried, sharpened)*
- **Set `SECRETS_KEY` on the box** *(new)*; **one check-in end to end on the box** *(carried)*;
  **a real Qwen3 personalisation** *(carried)*; **the app on a real handset** *(carried)*;
  **live Exotel smoke, both applets** *(carried)*; **the campaign for one evening on real
  numbers** *(carried)*; **phone-on-GPU contention** *(carried)*; **Telugu kiosk render**
  *(carried)*; **adaptive on** *(carried)*; **doctor console + consult note on-box** *(carried)*.

## Backlog additions (S-GL.1)

- **Wire the seat share into voice-gw** (S-OSS.2's item, now with the config half built).
- **A confirmation step on publish**, naming what changes and who is affected — carried for trees
  and banks, and now sharper: publishing a channel document can shut the OPD in one tap, and the
  Channels tab has no "are you sure".
- **Fix the commit race in the tree and protocol-bank draft routes** (see Watch out for).
- **A "kiosk-first" preset button** on the Channels tab — three taps is not many, but the go-live
  configuration is a named thing and could be one.
- **Per-channel realtime vendor choice** in the Channels tab — S-GL.5's item, which assumes the
  tab this session built.
- Carried, unchanged: the four S18-late items (slot-template editor → **now S-GL.2**, editable
  message-template registry, node-level abandonment report, voice-pack upload); a per-rung
  protocol form; backfill `Checkin.grading_rules`; a voice-gw check-in applet; check-ins in the
  Android app; merging a doublet's question sets; a canned `checkin_personalize` fake reply; a
  real task table for the immediate-call task; the app's chemo calendar counting real cycles;
  `Checkin` on the patient timeline; report photos in the care file; booking from the app; Play
  Store signing; `TokenStore` at rest; appointment waitlist; language detection from the caller's
  greeting; campaign observability; V1 continuous caller-audio streaming; surface STT confidence
  instead of the energy proxy; tune VAD/DTMF thresholds on real Alwar telephony.
