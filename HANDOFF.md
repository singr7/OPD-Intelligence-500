# HANDOFF — after Session S-GL.2 (staff onboarding + roster)

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
> `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the defaults. Unchanged by S-GL.2.

**Repo state:** **`main`**, last commit `S GL.2: session close`. `make test` green: backend
**1212** (was 1156), voice-gw **25**, web typecheck+lint+**48** conformance, **Android 6 JVM**.
`make lang-qa` clean across [en,hi,mr,te]. `make lint` green. **No migration in S-GL.2** —
`users`, `doctors` and `slot_templates` have existed since S2/S15 and this session only writes to
them from a new place. The last migration is still S-GL.1's **`2c978d44c900`**; run `make migrate`
if you are coming from before that. Postgres on host port **5433**; voice-gw on **8090**.
**Next is S-GL.3** (doc 12 §7) — **the go-live cut**.

**One paragraph:** S-GL.2 made hiring somebody something a hospital administrator does rather than
something a deploy does. Before it, onboarding a doctor meant editing `seeds/doctors.json` on the
box and re-running the seed, and the slot-template panel was the console's last honest deferral
marker. There is now a **People & roster** tab: create a doctor (a login and a clinical profile in
one transaction), invite them (which mints nothing — the OTP login already *is* the credential, so
an invite is an SMS saying the number works), author their weekly clinics, or import a whole
hospital's roster from CSV/XLSX with a dry run that refuses a bad row **by the line number in the
administrator's own spreadsheet** and writes nothing at all if any row fails. Deactivation is two
steps by construction: it lists the patients already booked, by name, and refuses without an
acknowledgement. The session's real engineering was the handoff's own design warning, whose
obvious answer was wrong — see *Watch out for*.

## Next session — S-GL.3 (go-live hardening on the box) — **Phase 1, the go-live cut**

- **Objective:** doc 12 §7 S-GL.3 — pay down the on-box debt that blocks a real patient. This
  session **builds almost nothing**; every item in it is a first-contact-with-reality item, and
  the pilot's first day is the wrong time for all of them.
- **Load:** docs 09, 10, doc 01 §5, this file's "Owed on omen", `sessions/SESSION-GL2.md`.
- **The list:** Telugu kiosk render checked on the actual screen; the admin console walked tab by
  tab against real data (now **eight** tabs — Channels and People & roster are both unrendered on
  the box); a real Qwen3 run of `checkin_personalize` and `summarize` read by a human; one full
  kiosk intake per language on the box; the downtime drill executed on the box, not in a test; GPU
  contention measured, and `max_oss_sessions` set from the measurement rather than doc 08's
  estimate.
- **AC:** a written record in the session log of each item observed on the box, with the measured
  concurrency envelope and the value `max_oss_sessions` was set to and why.
- **What S-GL.2 gives it:** one more tab to walk, and a genuinely useful on-box task — **onboard
  the hospital's real doctors and import their real roster from the console**, which is both the
  feature's first contact with reality and something the pilot needs anyway. Do it on the box, not
  by editing a seed file.

## ⚠ Before any deploy to omen — read `docs/13-OMEN-UPGRADE-RUNBOOK.md`

Preparing the box upgrade found a **blocking bug**: `cryptography` went into
`backend/pyproject.toml` in S-GL.1 but not into `backend/requirements.txt`, which is what the
**Docker image** installs. The whole suite was green (tests run in the venv, from pyproject) and
the api image would have crash-looped on `import app.main` — taking the kiosk down. Fixed, and
guarded permanently:

```
make preflight   # builds the api + voice-gw images and proves they can import
```

**Run it before every box deploy.** It is the gap between "tests are green" and "the container
boots", and this repo has fallen into that gap twice now (`python-multipart` → `1e4f0ce`, then
`cryptography`). `make test` cannot see it by construction.

`deploy/omen-checkpoint.sh` and `deploy/omen-rollback.sh` are the restore point and the way back:
the checkpoint saves the commit, the **running images** (retagged, so a rollback is a retag rather
than a rebuild) and a pg_dump; the rollback restores code + images and **deliberately leaves the
database alone**, because the S-GL.1 migration only *adds* tables and old code is happy against
the new schema — restoring the dump would discard every intake since the checkpoint.

## Watch out for (S-GL.2 fragile edges)

- **`generate_slots` dedupes on `(doctor, instant)` regardless of `blocked`.** This is the trap the
  previous handoff flagged, and the obvious fix was wrong: blocking a template's future slots and
  regenerating leaves them blocked forever, because generation skips every instant that already
  has a row — the clinic empties out silently. `roster._reconcile` blocks only the instants the new
  shape no longer runs and updates the rest in place. **Anything new that edits a template must go
  through `save_clinic`**, and the instants must keep coming from `scheduling.instants_on` (public
  for this reason) rather than from a second copy of the rule.
- **`web/e2e/people.spec.ts` really writes** — like `admin.spec.ts` and `channels.spec.ts`. Each
  run creates a doctor and a clinic on whatever database it points at; the last test deactivates
  the doctor it made, but the rows stay (deactivation is not deletion, deliberately). Dev boxes
  only. Names are suffixed with a timestamp so re-runs do not collide on `users.phone` /
  `doctors.reg_no`.
- **The invite SMS is free text, not a DLT template.** On a real Indian gateway it may be rejected
  in a way `FakeSMSProvider` never shows. `send_invite` records the vendor's refusal rather than
  raising, so a failed invite is visible and not fatal — but the first real one is a first-contact
  item, and it is staff-facing rather than patient-facing.
- **A clinic edit reconciles inventory inside the request.** A doctor with a year of generated
  slots means a few hundred row updates in one transaction. Fine at pilot scale; remember it if a
  hospital ever generates a much longer horizon.
- **Deactivating a doctor blocks their empty future slots but reactivating does not unblock them**
  — the clinic templates stay retired too. Deliberate ("she is back" and "she is back on Tuesdays
  at ten" are different facts), and the console says so, but it means a reactivated doctor needs
  her clinics re-authored or re-imported.
- Carried from S-GL.1, unchanged: **`SECRETS_KEY` must be set on the box** before entering real
  vendor credentials; **"no restart" is a 10-second TTL**, not an invalidation protocol; **the seat
  share is configured and tested but not wired into the live voice path** (S-OSS.2); **a `fake`
  provider counts as ready on a local box and says so** — if that note is ever dropped, the
  Channels tab starts lying on the box; **the tree and protocol-bank draft routes still have the
  latent commit race** the channel routes fixed.

## Decisions needed from the human

- **Has an oncologist reviewed the *tree bank*?** A kiosk-first pilot makes the trees the **only**
  clinical content in front of a patient, and they are model-drafted and unreviewed. This is still
  the most load-bearing open question in the project. *(carried, sharpest)*
- **The check-in protocol bank still needs an oncologist** — six regimen families, seven question
  sets, 41 grading rules, all model-drafted, none reviewed. It has a publish button. *(carried)*
- **Are check-ins on or off for day one?** With WhatsApp dark the delivery ladder has no first
  rung and falls to SMS. Either hold plans in draft or set `CHECKINS_ENABLED=false`. *(carried)*
- **Who is the coordinator on `COORDINATOR_PHONE`** — they get every red check-in alert. *(carried)*
- **Is the LLM assist allowed to escalate free text to red?** (S17's answer: no.) *(carried)*
- **A live Exotel number + creds** — still blocking three proofs. *(carried)*
- **Does the app go on the Play Store, or sideload at the OPD desk?** *(carried)*
- **mr/te still need a native + clinical review before a patient reads them** (S21), including the
  four channel-closed notices. *(carried)*
- **Who are the pilot's real doctors, and what is their real roster?** *(new)* — S-GL.2 makes this
  answerable from the console in five minutes, but somebody has to supply the names, numbers,
  registration numbers and clinic hours. The five seeded doctors are fictional and their phone
  numbers are unroutable by construction.

## Owed on omen (before the pilot's continuity loop faces real use)

- **The admin console on a screen, on the box** — now **eight** tabs, two of them
  (Channels, People & roster) never rendered anywhere but a local dev stack. Everything in
  `web/screenshots/sgl1/` and `web/screenshots/sgl2/` is local. *(carried, sharpened)*
- **Onboard the real doctors and import the real roster from the console** *(new)* — the feature's
  first contact with reality, and something the pilot needs regardless.
- **Set `SECRETS_KEY` on the box** *(carried)*; **one check-in end to end on the box** *(carried)*;
  **a real Qwen3 personalisation** *(carried)*; **the app on a real handset** *(carried)*;
  **live Exotel smoke, both applets** *(carried)*; **the campaign for one evening on real
  numbers** *(carried)*; **phone-on-GPU contention** *(carried)*; **Telugu kiosk render**
  *(carried)*; **adaptive on** *(carried)*; **doctor console + consult note on-box** *(carried)*.

## Backlog additions (S-GL.2)

- **A DLT-templated staff invite**, once the hospital's sender IDs are registered.
- **Bulk-import people**, not just clinics — the roster import resolves doctors but will not
  create them, deliberately (creating a clinical identity from a spreadsheet row is not a thing to
  do without a person looking). A two-stage "these six names are new, create them?" flow is the
  honest version.
- **Reassign a deactivated doctor's booked appointments** to a colleague from the console. Today
  the console names the patients and a human rings them, which is right for a pilot and thin for a
  hospital.
- **A slot-level view** — block one Tuesday for leave without retiring the weekly clinic.
  `AppointmentSlot.blocked` already exists for exactly this; nothing exposes it per-slot.
- Carried, unchanged: wire the seat share into voice-gw; a confirmation step on publish; fix the
  commit race in the tree and protocol-bank draft routes; a kiosk-first preset button; per-channel
  realtime vendor choice; editable message-template registry; node-level abandonment report;
  voice-pack upload; a per-rung protocol form; backfill `Checkin.grading_rules`; a voice-gw
  check-in applet; check-ins in the Android app; merging a doublet's question sets; a canned
  `checkin_personalize` fake reply; a real task table for the immediate-call task; the app's chemo
  calendar counting real cycles; `Checkin` on the patient timeline; report photos in the care file;
  booking from the app; Play Store signing; `TokenStore` at rest; appointment waitlist; language
  detection from the caller's greeting; campaign observability; V1 continuous caller-audio
  streaming; surface STT confidence instead of the energy proxy; tune VAD/DTMF thresholds on real
  Alwar telephony.

- **Start from `main`.** First commands:
```
make dev && make migrate && make seed && make slots
make test        # 1212 backend / 25 voice-gw / 48 web / 6 android
make lang-qa     # expect clean across [en,hi,mr,te]
make lint        # green — keep it that way
```
To see the console (it needs a live api with S-GL.2 code — the dockerised image may be older):
```
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  OTP_DEBUG_ECHO=true OTP_RESEND_COOLDOWN_SECONDS=0 \
  JWT_SECRET=local-dev-secret-padded-to-32-chars-plus .venv/bin/python -m uvicorn app.main:app --port 8123
cd web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8123 npx next dev -p 3210
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 npm run e2e:people
```
Admin login: `+915550000001` (seeded Priya Sharma); the OTP is echoed locally.
